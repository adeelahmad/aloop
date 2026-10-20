#!/usr/bin/env python3
"""ROADMAP §4.12 (M9) M9.11: goals and the Aladdin handoff. The Evaluator's gaps (M9.10, in its
own priority order) become a goal (autonomous: the top N; spoon-fed: the gaps the human picks);
the goal names its targets, the mission it serves, the eval it came from, the standing
principles for the mission's mode and the Aladdin autonomy that mode maps to; it passes the
ethics gate before any intent exists; then each gap becomes an Intent Contract (source
`mission`), `events trace` walks a commit back through intent → goal → eval → ethics →
mission → the human who approved it, and an evaluation of the active goal closes it.
    python3 scripts/test/mission-goal.test.py
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "fixtures"))
from mission_fixture import ASKED, Project, M, BIN  # noqa: E402

sys.path.insert(0, M.AREAS_DIR)
import _events  # noqa: E402
import _paths  # noqa: E402
import ethics as E  # noqa: E402
import evaluate as EV  # noqa: E402
import goal as G  # noqa: E402
import principles as P  # noqa: E402

INTENT, EVENTS = os.path.join(BIN, "intent"), os.path.join(BIN, "events")
PASS = [0]
GENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def made(r):
    assert r.returncode == 0, r.stderr
    return json.load(open(r.stdout.strip().splitlines()[-1]))


def tool(p, *args):
    return subprocess.run(list(args), cwd=p.dir, env=p.env, capture_output=True, text=True)


def review(p, subject, findings=(), tier="clear"):
    path = p.run("ethics", "new", "--subject", subject).stdout.strip()
    doc = json.load(open(path))
    doc.update(findings=list(findings), tier=tier)
    json.dump(doc, open(path, "w"))
    return p.run("ethics", "record", path)


def evaluate(p):
    """assess the app as it is now, then evaluate it: the gap list (narrowed to the active goal, if any)."""
    return made(p.run("evaluate", made(p.run("assess"))["state_id"]))


def setup(mode, extra=()):
    """A project with research, the fixture's journey (criteria `all` and `fast`), a passing one, any
    extra drafts, a confirmed baseline and a first evaluation of the whole mission."""
    p = Project(mode=mode, veto_window_h=0)
    subprocess.run(["git", "add", "-A"], cwd=p.dir, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=p.dir, env=dict(p.env, **GENV), check=True)
    rid = p.research(["c1"])
    js = [p.journey(p.draft(rid)),
          p.journey(p.draft(rid, title="The first task stays under 10s",
                            pass_criteria=[{"id": "ok10", "metric": "secs", "op": "<=", "value": 10}]))]
    js += [p.journey(p.draft(rid, **x)) for x in extra]
    b = made(p.run("baseline", "new", "--definition", "a first task in under 5 seconds, and it shows up"))
    assert p.run("baseline", "measure", b["baseline_id"]).returncode == 0
    assert p.run("baseline", "confirm", b["baseline_id"], "--by", "Adeel").returncode == 0
    return p, js, evaluate(p)


# 1) derive refuses until its facts hold: a recorded eval, the mission's ethics, a goal not yet met
p, (j1, j2), e1 = setup("autonomous")
ev1 = e1["eval_id"]
assert [g["gap_id"] for g in e1["gaps"]] == [j1 + "#fast", j1 + "#all"], e1["gaps"]   # M9.10's own order
r = p.run("goal", "derive")
assert r.returncode == 2 and "BLOCK[ethics]" in r.stderr and "no ethics review" in r.stderr, r.stderr
assert review(p, "mission").returncode == 0
copy = dict(e1, eval_id=M.new_id("eval"))                # the same gap list under another id, never recorded
copy.pop("sha256")
M.seal(copy)
p.save("eval", copy["eval_id"], copy)
r = p.run("goal", "derive", "--eval", copy["eval_id"])
assert r.returncode == 2 and "not recorded" in r.stderr, r.stderr
forged = dict(e1, gaps=e1["gaps"][1:])                   # a gap list that does not follow from its state
forged.pop("sha256")
M.seal(forged)
p.save("eval", ev1, forged)
r = p.run("goal", "derive", "--eval", ev1)
assert r.returncode == 2 and "does not follow from state" in r.stderr, r.stderr
p.save("eval", ev1, dict(e1, created_at="2020-01-01T00:00:00Z", sha256=M.digest(dict(e1, created_at="2020-01-01T00:00:00Z"))))
r = p.run("goal", "derive", "--eval", ev1)                # edited and resealed after it was recorded
assert r.returncode == 2 and "not the content its eval.completed recorded" in r.stderr, r.stderr
p.save("eval", ev1, e1)
st = p.load("state", e1["state_id"])                     # a state `mission assess` never measured: all passing
fake = dict(st, state_id=M.new_id("state"))
for run in fake["runs"]:
    for rec in run["journeys"].values():
        rec["passed"] = True
        for c in rec.get("criteria") or []:
            c["passed"] = True
fake.pop("sha256")
M.seal(fake)
p.save("state", fake["state_id"], fake)
fe = p.run("evaluate", fake["state_id"])
assert fe.returncode in (0, 2), fe.stderr
if fe.returncode == 0:                                    # M9.10 evaluates it; the goal module still refuses it
    r = p.run("goal", "derive", "--eval", json.load(open(fe.stdout.strip().splitlines()[-1]))["eval_id"])
    assert r.returncode == 2 and "not measured by mission assess" in r.stderr, r.stderr
    e1 = evaluate(p)                                      # the latest eval is a real one again
    ev1 = e1["eval_id"]
assert not M.artifacts(p.dir, "goal")
ok("derive refuses with no mission ethics review, an unrecorded eval, a gap list that does not follow from its state, "
   "or a state the assessor never measured")

# 2) options: the eval's gaps as M9.10 ordered them, each with a one-line summary in the journey's words
r = p.run("goal", "options", "--eval", ev1, "--json")
assert r.returncode == 0, r.stderr
opts = json.loads(r.stdout)
assert [(g["gap_id"], g["rank"]) for g in opts["options"]] == [(j1 + "#fast", 1), (j1 + "#all", 2)], opts
assert opts["options"][0]["summary"] == "Add a task and see it (fast): secs <= 5" and opts["held"] == []
r = p.run("goal", "options")
assert r.returncode == 0 and r.stdout.splitlines()[0].startswith("1. %s#fast (open)" % j1), r.stdout
ok("options: the eval's gaps in the Evaluator's priority order, summarized in the journey's words")

# 3) derive (autonomous): the top N gaps as given, bound to the mission, the eval and the principles block
assert p.run("goal", "derive", "--eval", ev1, "--pick", j1 + "#all").returncode == 64   # --pick is spoon-fed's
assert p.run("goal", "derive", "--eval", ev1, "--top", "0").returncode == 64
r = p.run("goal", "derive", "--eval", ev1)
assert r.returncode == 0, r.stderr
gpath = r.stdout.strip()
g = json.load(open(gpath))
gid = g["goal_id"]
assert gid.startswith("gol_") and M.ID.match(gid) and os.path.basename(gpath) == gid + ".json"
assert [x["gap_id"] for x in g["gaps"]] == [j1 + "#fast", j1 + "#all"], g["gaps"]
assert g["targets"] == [{"journey_id": j1, "criterion_id": "fast"}, {"journey_id": j1, "criterion_id": "all"}]
mdoc = p.load("mission")
assert g["mission"] == {"mission_id": mdoc["mission_id"], "sha256": M.digest(mdoc)}, g["mission"]
assert g["eval"] == {"eval_id": ev1, "sha256": e1["sha256"]}
assert g["mode"] == "autonomous" and g["autonomy"] == "autonomous" and g["status"] == "derived"
assert g["principles"] == P.stamp("autonomous") and g["sha256"] == M.digest(g)
assert "Add a task and see it (fast): secs <= 5" in g["statement"] and M.problems(g, "goal", gpath) == []
d = p.events("goal.derived")
ec = [e for e in p.events("eval.completed") if e["data"]["eval_id"] == ev1][0]
assert len(d) == 1 and d[0]["data"]["goal_id"] == gid and d[0]["data"]["eval_event"] == ec["event_id"], d
rows = M.records(p.dir, "goal.derived", M.rel(p.dir, gpath))   # a ledger record: it survives a fresh checkout
assert [r["event_id"] for r in rows] == [d[0]["event_id"]] and rows[0]["data"]["sha256"] == g["sha256"], rows
assert d[0]["data"]["mission_id"] == mdoc["mission_id"] and d[0]["data"]["sha256"] == g["sha256"]
assert d[0]["data"]["gaps"] == [j1 + "#fast", j1 + "#all"]
ok("derive: the top N gaps as given; targets, mission, eval, principles stamp and autonomy bound; goal.derived")

# 4) check: the goal's gate before any intent (ethics; autonomous needs no goal approval)
r = p.run("goal", "check", gid)
assert r.returncode == 2 and "no ethics review" in r.stderr, r.stderr
assert review(p, gid, [{"category": "other", "tier": "gray", "reason": "the list shows more of the user's tasks"}],
              "gray").returncode == 3
r = p.run("goal", "check", gid)
assert r.returncode == 3 and "ASK[goal]: ethics: gray area" in r.stderr, r.stderr
r = p.run("goal", "intents", gid)
assert r.returncode == 3 and not os.path.isdir(os.path.join(p.dir, "docs", "agents", "intents")), r.stderr
assert p.run("ethics", "consent", "--subject", gid, "--by", "Adeel", "--reason", "tasks are the user's own").returncode == 0
r = p.run("goal", "check", gid, "--json")
assert r.returncode == 0, r.stderr
f = json.loads(r.stdout)
assert f["exit"] == 0 and f["ethics"]["tier"] == "gray" and f["ethics"]["state"] == "ok" and f["autonomy"] == "autonomous"
assert f["principles_stale"] is False
crafted = dict(g, goal_id=M.new_id("goal"))              # the same content under an id derive never issued
crafted.pop("sha256")
M.seal(crafted)
p.save("goal", crafted["goal_id"], crafted)
assert review(p, crafted["goal_id"]).returncode == 0
r = p.run("goal", "check", crafted["goal_id"])
assert r.returncode == 2 and "no goal.derived" in r.stderr, r.stderr
assert p.run("goal", "intents", crafted["goal_id"]).returncode == 2
r = p.run("goal", "close", crafted["goal_id"], "--eval", ev1)
assert r.returncode == 2 and "no goal.derived" in r.stderr, r.stderr
os.remove(p.path("goal", crafted["goal_id"]))
edited = dict(g, gaps=g["gaps"][:1], targets=g["targets"][:1])   # derived, then edited and resealed
edited.pop("sha256")
M.seal(edited)
p.save("goal", gid, edited)
assert review(p, gid).returncode == 0
r = p.run("goal", "check", gid)
assert r.returncode == 2 and "not the content goal.derived recorded" in r.stderr, r.stderr
p.save("goal", gid, dict(p.load("goal", gid), **{k: g[k] for k in ("gaps", "targets", "sha256")}))
assert p.run("goal", "check", gid).returncode == 0
ok("check: no intent until ethics passes (gray waits for consent); autonomous needs no goal approval; the goal is "
   "the one derive recorded (a crafted or edited-and-resealed goal blocks)")

# 5) intents: one Intent Contract per gap, source mission, filled from the gap, the mission and the principles
r = p.run("goal", "intents", gid)
assert r.returncode == 0, r.stderr
paths = r.stdout.split()
assert len(paths) == 2, paths
intents = [json.load(open(x)) for x in paths]
for it, gap in zip(intents, g["gaps"]):
    assert it["source"] == "mission" and it["provenance"]["goal_id"] == gid and it["provenance"]["gap_id"] == gap["gap_id"]
    assert it["want"] == gap["summary"] and gid in it["why"] and "world's best to-do app" in it["why"]
    assert any(P.digest("autonomous")[:12] in c and "autonomous" in c for c in it["constraints"]), it["constraints"]
    assert gap["gap_id"] in it["done_looks_like"] and gap["criterion_id"] in it["success_scenarios"][0]
    assert M.rel(p.dir, p.path("journey", j1)) in it["connections"]
    assert tool(p, INTENT, "check", paths[intents.index(it)], "--mode", "autonomous").returncode == 0
rec = p.events("intent.received")
assert [e["data"]["goal_id"] for e in rec] == [gid, gid] and all(e["caused_by"] == d[0]["event_id"] for e in rec)
assert p.load("goal", gid)["status"] == "active" and M.digest(p.load("goal", gid)) == g["sha256"]   # content unchanged
r = p.run("goal", "intents", gid)
assert r.returncode == 0 and r.stdout.split() == paths and len(p.events("intent.received")) == 2   # idempotent
assert [e["data"]["gap_id"] for e in rec] == [x["gap_id"] for x in g["gaps"]]
posed = tool(p, INTENT, "new", "--source", "prompt", "--raw", "x").stdout.strip()   # a prompt intent posing as one
pd = json.load(open(posed))
pd.update(source="mission", provenance=dict(pd["provenance"], goal_id=gid, gap_id=g["gaps"][0]["gap_id"]))
json.dump(pd, open(posed, "w"))
assert p.run("goal", "intents", gid).stdout.split() == paths     # only intents its own intent.received names
os.remove(posed)
ok("intents: one contract per gap (source mission, goal + gap provenance, principles); goal content unchanged; idempotent")

# 6) next: the next intent without a plan is normalized (so the sprint's plan.created links it)
r = p.run("goal", "next", gid)
assert r.returncode == 0 and r.stdout.strip() == paths[0], r.stderr
newest = max(glob.glob(os.path.join(p.dir, "docs", "agents", "intents", "int_*.json")), key=os.path.getmtime)
assert os.path.basename(newest) == os.path.basename(paths[0]), newest   # gate-intake re-checks the newest: the one handed off
norm = p.events("intent.normalized")[-1]
assert norm["data"]["intent_id"] == intents[0]["intent_id"] and norm["data"]["mode"] == "autonomous"
_events.emit(p.dir, "plan.created", role="planner", caused_by=norm["event_id"], sprint="S1", intent_id=intents[0]["intent_id"])
assert p.run("goal", "next", gid).stdout.strip() == paths[1]
norm2 = p.events("intent.normalized")[-1]
_events.emit(p.dir, "plan.created", role="planner", caused_by=norm2["event_id"], sprint="S2", intent_id=intents[1]["intent_id"])
r = p.run("goal", "next", gid)
assert r.returncode == 1 and "every intent of" in r.stderr, r.stderr
ok("next: normalizes the next unplanned intent in the goal's autonomy mode; exit 1 when all are planned")

# 7) events trace: commit → plan → intent → goal → eval → ethics → mission → the human who approved it
_events.emit(p.dir, "commit.created", task="S1-01-T1", sha="abc123def4567890", subject="feat: faster first task")
r = tool(p, EVENTS, "trace", "abc123def456")
assert r.returncode == 0, r.stderr
for want in ("intent   %s" % intents[0]["intent_id"], "goal     %s" % gid, "eval %s" % ev1,
             "ethics   gray (consent by Adeel)", "mission  %s approved by Adeel" % mdoc["mission_id"]):
    assert want in r.stdout, (want, r.stdout)
for ev in ("goal.derived", "goal.met", "eval.completed", "state.assessed", "intent.received"):   # the lineage the trace and close read is written only by goal's commands
    r = tool(p, EVENTS, "emit", ev, "--data", "goal_id=%s" % gid)
    assert r.returncode == 64 and "only its own command" in r.stderr, (ev, r.stderr)
ok("events trace reaches goal → eval → ethics → mission → approver; `events emit` cannot forge the lineage")

# 8) close: an evaluation of the active goal (M9.10 narrows to its targets) that says goal_met
p.app(seconds_to_first_task=4.0, working_steps=["open", "add"], tasks_shown=0)    # fast now passes; `all` still fails
e2 = evaluate(p)
assert e2["goal_id"] == gid and [x["gap_id"] for x in e2["gaps"]] == [j1 + "#all"], e2
r = p.run("goal", "close", gid, "--eval", e2["eval_id"])
assert r.returncode == 2 and j1 + "#all" in r.stderr and "still open" in r.stderr, r.stderr
r = p.run("goal", "close", gid, "--eval", ev1)
assert r.returncode == 2 and "not an evaluation of goal" in r.stderr, r.stderr   # the whole-mission eval it came from
p.app(seconds_to_first_task=2.0, working_steps=["open", "add", "see"], tasks_shown=1)
e3 = evaluate(p)
assert e3["goal_id"] == gid and e3["goal_met"] is True
r = p.run("goal", "close", gid, "--eval", e3["eval_id"])
assert r.returncode == 0, r.stderr
assert p.load("goal", gid)["status"] == "met" and p.events("goal.met")[-1]["data"]["goal_id"] == gid
assert [r["data"]["eval_id"] for r in M.records(p.dir, "goal.met", M.rel(p.dir, gpath))] == [e3["eval_id"]], "goal.met is a ledger record"
assert M.digest(p.load("goal", gid)) == g["sha256"]                      # closing changes only the status
r = p.run("goal", "close", gid, "--eval", e3["eval_id"])
assert r.returncode == 0 and len(p.events("goal.met")) == 1, r.stderr   # already met: no second goal.met
e4 = evaluate(p)                                                          # no active goal: the whole mission again
assert e4["goal_id"] is None and e4["goal_met"] is True
r = p.run("goal", "derive", "--eval", e4["eval_id"])
assert r.returncode == 2 and "raise the bar" in r.stderr, r.stderr
r = p.run("goal", "derive", "--eval", ev1)                                # an older eval: its gaps are closed now
assert r.returncode == 2 and "not the latest" in r.stderr, r.stderr
ok("close: met only by a newer evaluation of this goal that says goal_met (once); then the whole mission is met: "
   "raise the bar, not a goal from an older eval")

# 9) the fixed point: a goal is stale once the mission's content changes; a tampered goal fails its seal;
#    an eval narrowed to an active goal is not a source of new goals
p.app(seconds_to_first_task=4.0, working_steps=["open", "add"], tasks_shown=0)
gid2 = made(p.run("goal", "derive", "--eval", evaluate(p)["eval_id"], "--top", "1"))["goal_id"]
r = p.run("goal", "close", gid2, "--eval", e3["eval_id"])
assert r.returncode == 2 and "only an active goal closes" in r.stderr, r.stderr
assert review(p, gid2).returncode == 0 and p.run("goal", "check", gid2).returncode == 0
assert p.run("goal", "intents", gid2).returncode == 0
e5 = evaluate(p)
assert e5["goal_id"] == gid2
r = p.run("goal", "derive", "--eval", e5["eval_id"])
assert r.returncode == 2 and "narrowed to goal %s" % gid2 in r.stderr, r.stderr
g2 = p.load("goal", gid2)
gp2 = p.path("goal", gid2)
json.dump(dict(g2, principles=dict(g2["principles"], sha256="0" * 64)), open(gp2, "w"))
r = p.run("goal", "check", gid2)
assert r.returncode == 2 and "sha256 does not match" in r.stderr, r.stderr
r = p.run("goal", "close", gid2, "--eval", e5["eval_id"])
assert r.returncode == 2 and "sha256 does not match" in r.stderr, r.stderr   # close checks the seal too
with open(gp2, "w") as fh:
    fh.write(json.dumps(dict(g2, statement="x \ud800 y")))                  # not valid Unicode: a block, not a crash
r = p.run("goal", "check", gid2)
assert r.returncode == 2 and "Traceback" not in r.stderr, r.stderr
json.dump(g2, open(gp2, "w"))
assert review(p, "mission", [{"category": "other", "tier": "gray", "reason": "a list of the user's tasks"}],
              "gray").returncode == 3
r = p.run("goal", "check", gid2)
assert r.returncode == 3 and "ASK[goal]: ethics:" in r.stderr, r.stderr   # the mission's verdict holds its goals
m = p.load("mission")
m["constraints"].append("works offline")
m["status"] = "draft"
p.save("mission", "", m)
assert p.run("approve", "--by", "Adeel").returncode == 0
r = p.run("goal", "check", gid2)
assert r.returncode == 2 and "the mission changed" in r.stderr, r.stderr
ok("check: a changed mission makes its goals stale (2); an edited goal fails its seal; a narrowed eval derives nothing")

# 10) spoon-fed: the human picks the gaps (that is the goal's approval); one intent at a time, reviewed mode
s, (s1, _), es = setup("spoon-fed")
assert review(s, "mission").returncode == 0
ev = es["eval_id"]
r = s.run("goal", "derive", "--eval", ev)
assert r.returncode == 64 and "--pick" in r.stderr, r.stderr
assert s.run("goal", "derive", "--eval", ev, "--pick", s1 + "#all").returncode == 64     # who picked: --by
assert s.run("goal", "derive", "--eval", ev, "--pick", "nope", "--by", "Adeel").returncode == 2
r = s.raw("goal", "derive", "--eval", ev, "--pick", "%s#all,%s#fast" % (s1, s1), "--by", "Adeel")
assert r.returncode == 3 and "then aloop mission goal approve" in r.stderr, r.stderr   # the pick waits for their words
gpath = r.stdout.strip()
assert json.load(open(gpath))["status"] == "derived" and not s.events("goal.approved")
s.say(ASKED.search(r.stderr).group(1))
assert s.raw("goal", "approve", json.load(open(gpath))["goal_id"], "--by", "Adeel", "--reason", "picked").returncode == 0
sg = json.load(open(gpath))
assert [x["gap_id"] for x in sg["gaps"]] == [s1 + "#all", s1 + "#fast"] and sg["autonomy"] == "reviewed"
assert sg["status"] == "approved" and sg["principles"] == P.stamp("spoon-fed")
ap = s.events("goal.approved")
assert len(ap) == 1 and ap[0]["data"]["sha256"] == sg["sha256"] and ap[0]["data"]["by"] == "Adeel"
assert review(s, sg["goal_id"]).returncode == 0
assert s.run("goal", "check", sg["goal_id"]).returncode == 0
sp = s.path("goal", sg["goal_id"])
one = dict(sg, gaps=sg["gaps"][:1], targets=sg["targets"][:1])
one.pop("sha256")
M.seal(one)
json.dump(one, open(sp, "w"))
r = s.run("goal", "check", sg["goal_id"])
assert r.returncode == 2 and "not the content goal.derived recorded" in r.stderr, r.stderr
json.dump(sg, open(sp, "w"))
r = s.run("goal", "intents", sg["goal_id"])
assert r.returncode == 0 and len(r.stdout.split()) == 2, r.stderr
s.run("goal", "next", sg["goal_id"])
assert s.events("intent.normalized")[-1]["data"]["mode"] == "reviewed"
r = s.run("goal", "approve", sg["goal_id"])
assert r.returncode == 64, r.stderr
ok("spoon-fed: --pick --by is the approval (goal.approved for that content); reviewed autonomy; edits re-ask")

# 11) a hard line holds its gaps: a goal derived again never takes them (the loop moves on, the human sees
#     them as held), and a goal keeps the principles stamp it was derived under (stale, not invalid)
BAD = {"title": "Pay strangers to praise us under made-up names",
       "pass_criteria": [{"id": "quick", "metric": "secs", "op": "<=", "value": 8}]}
h, (h1, _, hb), eh = setup("autonomous", extra=[BAD])
assert review(h, "mission").returncode == 0
assert eh["gaps"][0]["gap_id"] == hb + "#quick", eh["gaps"]              # closest to target: first
gh1 = made(h.run("goal", "derive", "--eval", eh["eval_id"], "--top", "1"))["goal_id"]
en = evaluate(h)                                                         # an eval of the active goal
assert en["goal_id"] == gh1, en
assert review(h, gh1, [{"category": "other", "tier": "hard", "reason": "paid fake praise deceives buyers"}],
              "hard").returncode == 2
r = h.run("goal", "check", gh1)
assert r.returncode == 2 and "hard line" in r.stderr, r.stderr
r = h.run("goal", "options", "--eval", eh["eval_id"], "--json")
o2 = json.loads(r.stdout)
assert r.returncode == 0, r.stderr
assert [x["gap_id"] for x in o2["options"]] == [h1 + "#fast", h1 + "#all"], o2
assert [x["gap_id"] for x in o2["held"]] == [hb + "#quick"], o2
assert h.events("ethics.blocked")[-1]["data"]["gaps"] == [hb + "#quick"]
quick = o2["held"][0]
assert G.split_held(h.dir, [dict(quick, summary="Other words for the same gap")])[1]   # held by its id as well
r = h.run("goal", "close", gh1, "--eval", en["eval_id"])
assert r.returncode == 2 and "blocked" in r.stderr, r.stderr                        # a hard-lined goal never closes
r = h.run("goal", "derive", "--eval", en["eval_id"], "--top", "1")          # M9.29: retired, so evaluate again
assert r.returncode == 2 and "an ethics hard line has retired" in r.stderr and "mission evaluate" in r.stderr, r.stderr
assert EV.active_goal(h.dir) is None                                      # a hard-lined goal is never active
e2 = evaluate(h)
assert e2["goal_id"] is None and [g["gap_id"] for g in e2["gaps"]] == [g["gap_id"] for g in eh["gaps"]], e2
g2 = made(h.run("goal", "derive", "--eval", e2["eval_id"], "--top", "1"))   # the loop moves on
assert [x["gap_id"] for x in g2["gaps"]] == [h1 + "#fast"], g2["gaps"]
r = h.run("goal", "close", g2["goal_id"], "--eval", e2["eval_id"])
assert r.returncode == 2 and "only an active goal closes" in r.stderr, r.stderr   # derived, never handed off
newer = os.path.join(h.dir, "principles-next.md")                 # the standing block changes after derive
with open(P.SOURCE, encoding="utf-8") as fh:
    text = fh.read()
with open(newer, "w", encoding="utf-8") as fh:
    fh.write(text.replace("Ship Small", "Ship Small, always", 1) if "Ship Small" in text else text + "\n")
was, P.SOURCE = P.SOURCE, newer
try:
    rc, blocks, _, facts = G.check(h.dir, g2["goal_id"])
finally:
    P.SOURCE = was
assert P.digest("autonomous", newer) != g2["principles"]["sha256"]
assert facts["principles_stale"] is True and not [b for b in blocks if "principles" in b], (facts, blocks)
g2p = h.path("goal", g2["goal_id"])
old = dict(g2, principles=dict(g2["principles"], mode="spoon-fed"))
old.pop("sha256")
M.seal(old)
M.save(g2p, old)
r = h.run("goal", "check", g2["goal_id"])
assert r.returncode == 2 and "not the content goal.derived recorded" in r.stderr, r.stderr
assert E.word_key("Pay strangers to praise us under made-up names (quick): secs <= 8") in E.hard_words(h.dir)
ok("a hard line holds its gaps out of options and derive and retires its goal (the next eval covers the whole "
   "mission, M9.29); an older principles stamp is stale, not invalid")

# 12) the intent tool: `mission` is a source, and a mission intent must name its goal
r = tool(p, INTENT, "new", "--source", "mission", "--raw", "x")
assert r.returncode == 64 and "--goal" in r.stderr, r.stderr
r = tool(p, INTENT, "new", "--source", "mission", "--goal", "gol_madeup", "--raw", "x")
assert r.returncode == 64, r.stderr
r = tool(p, INTENT, "new", "--source", "mission", "--goal", gid2, "--raw", "x")
assert r.returncode == 64 and "--gap" in r.stderr, r.stderr                         # a mission intent names its gap
r = tool(h, INTENT, "new", "--source", "mission", "--goal", gh1, "--gap", hb + "#quick", "--raw", "x")
assert r.returncode == 2 and "BLOCK[goal]" in r.stderr, r.stderr                    # the goal's gate: hard-lined
r = tool(h, INTENT, "new", "--source", "mission", "--goal", "gol_" + "0" * 20, "--gap", "x", "--raw", "x")
assert r.returncode == 2 and "no goal" in r.stderr, r.stderr
r = tool(s, INTENT, "new", "--source", "mission", "--goal", sg["goal_id"], "--gap", "nope", "--raw", "x")
assert r.returncode == 2 and "not a gap of goal" in r.stderr, r.stderr
sp0 = os.path.join(s.dir, "docs", "agents", "intents", s.events("intent.received")[0]["data"]["intent_id"] + ".json")
r = tool(s, INTENT, "check", sp0, "--mode", "autonomous")
assert r.returncode == 2 and "reviewed" in r.stderr, r.stderr                       # the goal's autonomy, not the caller's
sd = json.load(open(sp0))
json.dump(dict(sd, provenance=dict(sd["provenance"], gap_id=s1 + "#fast")), open(sp0, "w"))
r = tool(s, INTENT, "check", sp0, "--mode", "reviewed")
assert r.returncode == 2 and "intent.received" in r.stderr, r.stderr                # the record, not the file, says which gap
json.dump(sd, open(sp0, "w"))
it = json.load(open(paths[0]))
json.dump(dict(it, provenance=dict(it["provenance"], goal_id="")), open(paths[0], "w"))
r = tool(p, INTENT, "check", paths[0])
assert r.returncode == 2 and "names its goal" in r.stderr, r.stderr
json.dump(it, open(paths[0], "w"))
ok("intent: a mission intent names its goal and gap, passes the goal's gate, and is checked in the goal's autonomy")

# 13) retire (M9.50): an active goal whose every intent was sprinted, and whose newest evaluation, made after
#     its sprints closed from a state measured after them, still lists its gaps, is set aside by a goal.retired
#     ledger record (the planner's): `mission evaluate` then covers the whole mission, the retired goal's open
#     gaps are held (its sprints tried them), and a retired goal takes no more intents, sprints or close
p4, (k1, k2), ek = setup("autonomous")
assert review(p4, "mission").returncode == 0
gold = made(p4.run("goal", "derive"))["goal_id"]                    # an older goal nobody works
gk = made(p4.run("goal", "derive"))["goal_id"]
assert review(p4, gk).returncode == 0
gkref = M.rel(p4.dir, p4.path("goal", gk))


def refused(eid, *words):
    r = p4.run("goal", "retire", gk, "--eval", eid)
    assert r.returncode == 2 and all(w in r.stderr for w in words), (words, r.returncode, r.stderr)
    assert not M.records(p4.dir, "goal.retired", gkref)


refused(ek["eval_id"], "only an active goal")
assert p4.run("goal", "retire", gk).returncode == 64
assert len(p4.run("goal", "intents", gk).stdout.split()) == 2
ek2 = evaluate(p4)
assert ek2["goal_id"] == gk and not ek2["goal_met"]
refused(ek2["eval_id"], "has no plan yet")                          # sprint every intent first
for n in (1, 2):
    path = p4.run("goal", "next", gk).stdout.strip()
    _events.emit(p4.dir, "plan.created", role="planner", sprint="S%d" % n, intent_id=json.load(open(path))["intent_id"])
refused(ek2["eval_id"], "sprint 1 is not closed")                   # its sprints still run
closes, FMT = os.path.join(_paths.home(p4.dir, "state"), "sprints.json"), "%Y-%m-%dT%H:%M:%SZ"
past = time.strftime(FMT, time.gmtime(time.time() - 10))
junk = {"sprint": float("inf"), "at": past}                         # a hand-edited row is skipped, never a crash
json.dump({"closed": [{"sprint": 1, "at": past}, junk]}, open(closes, "w"))
refused(ek2["eval_id"], "sprint 2 is not closed")
em = evaluate(p4)                                                   # after sprint 1 closed, before sprint 2
time.sleep(1.05)
stamp = time.strftime(FMT, time.gmtime())
json.dump({"closed": [{"sprint": 1, "at": past}, {"sprint": 2, "at": stamp}, junk]}, open(closes, "w"))
refused(em["eval_id"], "made before goal %s's last sprint closed" % gk)
refused(ek["eval_id"], "not an evaluation of goal")
r = tool(p4, INTENT, "new", "--source", "mission", "--goal", gk, "--gap", em["gaps"][0]["gap_id"], "--raw", "try again")
assert r.returncode == 0, r.stderr                                 # a second intent for a gap is the goal's too
i3 = p4.events("intent.received")[-1]["data"]["intent_id"]
refused(em["eval_id"], "intent %s has no plan yet" % i3)
ip3 = os.path.join(p4.dir, "docs", "agents", "intents", i3 + ".json")
d3, d1 = json.load(open(ip3)), json.load(open(G.goal_intents(p4.dir, gk)[em["gaps"][0]["gap_id"]][0]))
d3.update({k: d1[k] for k in ("actor", "want", "why", "done_looks_like", "success_scenarios", "failure_scenarios")})
json.dump(d3, open(ip3, "w"))                                      # the planner fills it, as intake would
r = p4.run("goal", "next", gk)
assert os.path.basename(r.stdout.strip()) == i3 + ".json", (r.returncode, r.stdout, r.stderr)   # `next` hands it out
_events.emit(p4.dir, "plan.created", role="planner", sprint="S3", intent_id=i3)
assert p4.run("goal", "next", gk).returncode == 1                  # every intent has a plan
refused(em["eval_id"], "sprint 3 is not closed")
json.dump({"closed": [{"sprint": 1, "at": past}, {"sprint": 2, "at": stamp}, {"sprint": 3, "at": stamp}, junk]},
          open(closes, "w"))
time.sleep(1.05)                                    # a close stamp keeps the second: after it is the next one
eo = made(p4.run("evaluate", ek2["state_id"]))                     # after the closes, from a state before them
refused(eo["eval_id"], "measured before goal %s's last sprint closed" % gk, "assess")
ek3 = evaluate(p4)
assert ek3["goal_id"] == gk and not ek3["goal_met"]
assert p4.run("assess").returncode == 0                            # a newer state report: evaluate it first
refused(ek3["eval_id"], "not the newest state report")
ek3 = made(p4.run("evaluate"))
refused(ek2["eval_id"], "not the newest eval")                     # the loop reads ek3 now
p4.app(seconds_to_first_task=2.0, working_steps=["open", "add", "see"], tasks_shown=1)
emet = evaluate(p4)
assert emet["goal_id"] == gk and emet["goal_met"] is True
refused(emet["eval_id"], "is met in eval", "close it")             # a met goal is closed, never retired
p4.app(seconds_to_first_task=12.0, working_steps=["open", "add"], tasks_shown=0)   # and the 10s journey regresses
ek3 = evaluate(p4)
assert not ek3["goal_met"] and {"gap_id": k2 + "#ok10", "kind": "regression"}.items() <= \
    next(x for x in ek3["gaps"] if x["gap_id"] == k2 + "#ok10").items(), ek3["gaps"]
gpath = p4.path("goal", gk)
orig = open(gpath).read()
gd = json.loads(orig)
gd["gaps"] = gd["gaps"][:1]
M.seal(gd)
json.dump(gd, open(gpath, "w"))
refused(ek3["eval_id"], "not the content goal.derived recorded")   # an edited, resealed goal is not the derived one
open(gpath, "w").write(orig)
refused("evl_" + "0" * 20, "no evaluation")
lrows = open(M.ledger(p4.dir)).read()
open(M.ledger(p4.dir), "a").write('{"event": "goal.retired", "role": "mission-planner"}\n')
refused(ek3["eval_id"], "BLOCK")                                   # a broken ledger decides nothing
open(M.ledger(p4.dir), "w").write(lrows)
with open(_events.path(p4.dir), "a") as f:                         # a malformed run-log line crashes nothing
    f.write(json.dumps({"event": "plan.created", "data": "text", "ids": "S9"}) + "\n")
json.dump(dict(json.loads(orig), status="retired"), open(gpath, "w"))   # a status with no record: retire repairs it
r = p4.run("goal", "retire", gk, "--eval", ek3["eval_id"])
assert r.returncode == 0, r.stderr
recs = M.records(p4.dir, "goal.retired", gkref)
assert len(recs) == 1 and recs[0]["role"] == "mission-planner" and recs[0]["data"]["eval_id"] == ek3["eval_id"], recs
assert recs[0]["data"]["sha256"] == M.digest(p4.load("goal", gk)) and p4.load("goal", gk)["status"] == "retired"
own = [x["gap_id"] for x in p4.load("goal", gk)["gaps"]]
assert recs[0]["data"]["open"] == [x["gap_id"] for x in ek3["gaps"] if x["gap_id"] in own], recs[0]["data"]
assert sorted(recs[0]["data"]["open"]) == sorted(own) and k2 + "#ok10" not in recs[0]["data"]["open"]   # not the regression
assert recs[0]["data"]["baseline_id"] == ek3["baseline_id"], recs[0]["data"]
r = p4.run("goal", "retire", gk, "--eval", ek3["eval_id"])
assert r.returncode == 0 and len(M.records(p4.dir, "goal.retired", gkref)) == 1, r.stderr   # once
json.dump(dict(json.loads(orig), status="active"), open(gpath, "w"))    # the record decides, not the status
c = json.loads(p4.run("goal", "check", gk, "--json").stdout)
assert c["exit"] == 2 and c["state"] == "retired" and len(c["blocks"]) == 1, c   # the planner's finding (roles.json)
for args in (("next", gk), ("intents", gk), ("check", gk), ("close", gk, "--eval", emet["eval_id"])):
    r = p4.run("goal", *args)
    assert r.returncode == 2 and "retired" in r.stderr, (args, r.returncode, r.stderr)
assert not M.records(p4.dir, "goal.met", gkref)
r = p4.run("goal", "derive")
assert r.returncode == 2 and "was retired with its gaps still open" in r.stderr, r.stderr   # the eval narrowed to it
assert EV.active_goal(p4.dir) is None                         # not the older goal nobody worked either
p4.app(seconds_to_first_task=9.0, working_steps=["open", "add"], tasks_shown=0)
ek4 = evaluate(p4)
assert ek4["goal_id"] is None
r = p4.run("goal", "derive")                                  # its sprints already tried these gaps: held
assert r.returncode == 2 and "2 held: 2 by retired goal %s" % gk in r.stderr, r.stderr
o4 = json.loads(p4.run("goal", "options", "--json").stdout)
assert o4["options"] == [] and {x["held_by"] for x in o4["held"]} == {"retired goal %s" % gk}, o4
assert not G.split_held(p4.dir, [dict(o4["held"][0], kind="regression")], ek4)[1]   # a regression is never held
assert p4.run("approve", "--by", "Adeel").returncode == 0     # the human's approval of the mission frees them
assert made(p4.run("goal", "derive"))["goal_id"] != gk
r = tool(p4, EVENTS, "emit", "goal.retired", "--data", "goal_id=%s" % gk)
assert r.returncode == 64 and "only its own command" in r.stderr, r.stderr
ok("retire: an active goal whose every intent was sprinted (sprints closed) and whose newest evaluation, made after "
   "the closes from a state measured after them, still lists gaps is retired by a goal.retired record (once); the "
   "record, not the status, decides; it takes no more work, the whole mission is evaluated again and its open gaps "
   "are held until the human approves the mission again")

# 14) the area is discovered; --help is the docstring
assert "goal" in p.run("--list").stdout.split() and "Aladdin handoff" in p.run("goal", "--help").stdout
assert p.run("goal", "nope").returncode == 64 and p.run("goal", "check").returncode == 64
for x in (p, s, h, p4):
    shutil.rmtree(x.dir, ignore_errors=True)
ok("discovered as an area; usage errors are 64")

print("mission-goal: %d checks passed" % PASS[0])
