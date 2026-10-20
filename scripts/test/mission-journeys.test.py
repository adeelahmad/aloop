#!/usr/bin/env python3
"""ROADMAP §4.12 (M9.3): user journeys and metrics. Each step emits a valid GA4 event, each
metric has a query definition, every source ref resolves to a research claim, a pass
criterion never gates on usage telemetry, the pinned runner's output is scored, and a
journey is never dropped or weakened without evidence and, when it weakens the
evaluation, a human approval.
    python3 scripts/test/mission-journeys.test.py
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "fixtures"))
from mission_fixture import Project, M  # noqa: E402

PASS = [0]


def ok(name):
    PASS[0] += 1
    print("ok -", name)


p = Project()
rid = p.research(["c1", "c2"])

# 1) the harness issues the id; a skeleton is not a journey yet; a complete draft is
r = p.run("journeys", "new", "--title", "Add a task", "--persona", "first-time user")
assert r.returncode == 0, r.stderr
skel = json.load(open(r.stdout.strip()))
assert skel["journey_id"].startswith("jrn_") and skel["status"] == "proposed"
r = p.run("journeys", "check", skel["journey_id"])
assert r.returncode == 2 and "steps needs at least 1" in r.stderr and "source_refs needs at least 1" in r.stderr, r.stderr
os.remove(p.path("journey", skel["journey_id"]))
jid = p.journey(p.draft(rid, journey_id="jrn_00000000000000000000", status="active"))
doc = p.load("journey", jid)
assert jid != "jrn_00000000000000000000" and doc["status"] == "proposed", doc   # a draft cannot pick its id or status
r = p.run("journeys", "check")
assert r.returncode == 0, r.stderr
assert [e["data"]["journey_id"] for e in p.events("journey.proposed")][-1] == jid
ok("journeys new: harness id and status; a skeleton fails the check; a complete journey passes")

# 2) the deterministic checks on a journey's structure
sys.path.insert(0, M.AREAS_DIR)
import journeys as J  # noqa: E402

base = p.load("journey", jid)


def bad(**over):
    return J.problems(p.dir, p.path("journey", jid), dict(base, **over))


steps = [dict(base["steps"][0], event="open app"), dict(base["steps"][1], event="google_task_add"), base["steps"][2]]
x = J.problems(p.dir, "", dict(base, steps=steps[:1] + base["steps"][1:]))
assert any("$.steps[0].event does not match" in m for m in x), x
x = bad(steps=steps[1:])
assert any("GA4-reserved prefix" in m for m in x), x
x = bad(steps=base["steps"][:2] + [dict(base["steps"][2], event="session_start")])
assert any("a name GA4 reserves for itself" in m for m in x), x
x = bad(runner={"command": ["python3", "tools/journey.py"]})
assert any("an unpinned runner is not reproducible" in m for m in x), x
m_ga = {"id": "dau", "name": "daily users", "unit": "users", "direction": "higher",
        "query": {"kind": "ga4", "report": {"metrics": ["activeUsers"]}}, "source_refs": [rid + "#c2"]}
x = bad(metrics=base["metrics"] + [m_ga], pass_criteria=base["pass_criteria"] + [
    {"id": "popular", "metric": "dau", "op": ">=", "value": 100}])
assert any("usage telemetry informs goals and is never a gate" in m for m in x), x
assert bad(metrics=base["metrics"] + [m_ga]) == []          # a GA4 metric that informs, not gates, is fine
x = bad(metrics=base["metrics"] + [dict(m_ga, id="ux", query={"kind": "judge"})])
assert any("names no rubric" in m for m in x), x
x = bad(pass_criteria=[{"id": "fast", "metric": "nope", "op": "<=", "value": 1}, {"id": "fast", "steps": "all"}])
assert any("does not define" in m for m in x) and any("pass_criteria ids repeat: fast" in m for m in x), x
x = bad(source_refs=["https://example.com", rid + "#c9", "rsr_" + "0" * 20 + "#c1"])
assert any("must be <research_id>#<claim_id>" in m for m in x) and any("has no claim c9" in m for m in x), x
assert any("does not exist" in m for m in x), x
x = bad(runner={"command": ["python3", "tools/journey.py"], "pinned": {"python3": {"version": "3"}}})
assert any("pinned entries need a version and a probe" in m for m in x), x
ok("checks: GA4 event names, reserved prefixes, query definitions, criteria never on telemetry, sourced claims")

# 3) run: the pinned runner's result line is scored per criterion
r = p.run("journeys", "run", jid, "--json")
assert r.returncode == 0, r.stderr
rec = json.loads(r.stdout)
crit = {c["id"]: c for c in rec["criteria"]}
assert crit["all"]["passed"] is False and "see" in crit["all"]["why"], crit
assert crit["fast"]["passed"] is False and "secs = 9.0 <= 5" in crit["fast"]["why"], crit
assert rec["passed"] is False and rec["sha256"] == M.digest(p.load("journey", jid))
p.app(seconds_to_first_task=2.0, working_steps=["open", "add", "see"], tasks_shown=1)
assert json.loads(p.run("journeys", "run", jid, "--json").stdout)["passed"] is True
assert [e["data"]["passed"] for e in p.events("journey.run")] == [False, True]
judged = dict(base, metrics=base["metrics"] + [dict(m_ga, id="ux", query={"kind": "judge", "rubric": "rub_" + "1" * 20})],
              pass_criteria=base["pass_criteria"] + [{"id": "delight", "metric": "ux", "op": ">=", "value": 4}])
sc = J.score(judged, {"steps": {"open": True, "add": True, "see": True}, "metrics": {"secs": 1}})
assert [c["passed"] for c in sc] == [True, True, None] and J.verdict(sc) is None, sc
bad_pin = dict(base, runner=dict(base["runner"], pinned={"python3": {"version": "Python 2.7", "probe": ["python3", "--version"]}}))
rec, err = J.run_journey(p.dir, bad_pin)
assert rec is None and "pinned python3 Python 2.7, but its probe printed" in err, err
ok("journeys run: pinned runner scored per criterion; a judge metric stays pending; a wrong pin refuses to run")

# 4) diff: what a new version drops or weakens
stricter = p.journey(dict(p.draft(rid), pass_criteria=[{"id": "all", "steps": "all"},
                                                       {"id": "fast", "metric": "secs", "op": "<=", "value": 3},
                                                       {"id": "shown", "metric": "tasks_shown", "op": ">=", "value": 1}],
                          supersedes=jid, evidence_refs=[rid + "#c2"]))
assert p.run("journeys", "diff", jid, stricter).returncode == 0
looser = p.journey(dict(p.draft(rid), steps=base["steps"][:2] + [dict(base["steps"][2], event="task_visible")],
                        pass_criteria=[{"id": "fast", "metric": "secs", "op": "<=", "value": 8}],
                        runner=dict(base["runner"], timeout_s=5)))
r = p.run("journeys", "diff", jid, looser, "--json")
w = json.loads(r.stdout)["weakenings"]
assert r.returncode == 2, r.stderr
assert any("criterion all is dropped" in x for x in w) and any("looser: secs <= 8 instead of 5" in x for x in w), w
assert any("emits task_visible instead of task_seen" in x for x in w) and any("the runner changed" in x for x in w), w
ok("journeys diff: a stricter version keeps everything; dropped criteria, looser thresholds, renamed events, a new runner weaken")

# 5) retire: never without evidence; retiring a pinned journey in a weaker way needs the human, for that replacement
twin = p.journey(p.draft(rid, title="Add a task and see it (second persona)", persona="returning user"))
p.app(seconds_to_first_task=9.0, working_steps=["open", "add"], tasks_shown=0)
r = p.run("baseline", "new", "--definition", "a first task in under 5 seconds", "--journey", jid, "--journey", twin)
assert r.returncode == 0, r.stderr
bid = json.load(open(r.stdout.strip()))["baseline_id"]
assert p.run("baseline", "measure", bid).returncode == 0 and p.run("baseline", "confirm", bid, "--by", "Adeel").returncode == 0
assert p.load("journey", jid)["status"] == "active" == p.load("journey", twin)["status"]
assert "never retired without evidence" in p.run("journeys", "retire", jid, "--replaced-by", stricter).stderr
r = p.run("journeys", "retire", jid, "--replaced-by", stricter, "--evidence", "x")
assert r.returncode == 2 and "must be <research_id>#<claim_id> or an eval, state or research id" in r.stderr, r.stderr
r = p.run("journeys", "retire", jid, "--replaced-by", jid, "--evidence", rid + "#c2")
assert r.returncode == 2 and "cannot replace itself" in r.stderr, r.stderr
r = p.run("journeys", "retire", jid, "--replaced-by", looser, "--evidence", rid + "#c2")
assert r.returncode == 3 and "ASK[journeys]" in r.stderr and "looser" in r.stderr and "--replaced-by " + looser in r.stderr, r.stderr
r = p.run("journeys", "retire", jid, "--evidence", rid + "#c2")
assert r.returncode == 3 and "no replacement" in r.stderr, r.stderr
assert p.run("journeys", "approve", jid, "--by", "Adeel").returncode == 64        # a reason is required
assert p.run("journeys", "approve", jid, "--by", "Adeel", "--reason", "drop it").returncode == 0     # no replacement
r = p.run("journeys", "retire", jid, "--replaced-by", looser, "--evidence", rid + "#c2")
assert r.returncode == 3, r.stderr                 # the approval covered retiring with no replacement, not this one
assert p.run("journeys", "approve", jid, "--replaced-by", looser, "--by", "Adeel", "--reason", "the 5s target was a guess").returncode == 0
assert p.run("journeys", "diff", jid, looser).returncode == 0         # a human approved exactly this pair
r = p.run("journeys", "retire", jid, "--replaced-by", looser, "--evidence", rid + "#c2", rid + "#c1")
assert r.returncode == 0, r.stderr
d = p.load("journey", jid)
assert d["retirement"]["evidence_refs"] == [rid + "#c2", rid + "#c1"]          # REF… takes every value
assert d["status"] == "retired" and d["retirement"]["approval_event"] and d["sha256"] == M.digest(d)
assert d["retirement"]["replaced_by_sha256"] == M.digest(p.load("journey", looser))
r = p.run("journeys", "retire", twin, "--replaced-by", stricter, "--evidence", rid + "#c2")
assert r.returncode == 0, r.stderr                                    # a stricter replacement: no human needed
assert p.load("journey", twin)["retirement"]["approval_event"] is None
nxt = p.journey(dict(p.draft(rid), pass_criteria=p.load("journey", stricter)["pass_criteria"]))
r = p.run("journeys", "retire", stricter, "--evidence", rid + "#c1")
assert r.returncode == 3, r.stderr                    # it took over a frozen journey: pinned like one
assert p.run("journeys", "retire", nxt, "--evidence", rid + "#c1").returncode == 0     # proposed: the researchers' call
assert [e["data"]["journey_id"] for e in p.events("journey.retired")] == [jid, twin, nxt]
r = p.run("journeys", "check", "--json")
assert r.returncode == 0 and jid in r.stdout, r.stderr                          # retired journeys are checked too
ok("journeys retire: real evidence; never by itself; a pinned journey retired weaker needs a human for that replacement")

# 6) a replacement is pinned at the content its retirement recorded; a status field proves nothing
d = p.load("journey", stricter)
weak = dict(d, pass_criteria=[{"id": "fast", "metric": "secs", "op": "<=", "value": 500}])
p.save("journey", stricter, weak)
r = p.run("journeys", "check", stricter)
assert r.returncode == 2 and "changed after it was recorded as a replacement" in r.stderr, r.stderr
r = p.run("journeys", "retire", stricter, "--evidence", rid + "#c1")
assert r.returncode == 2 and "restore it before retiring it" in r.stderr, r.stderr
p.save("journey", stricter, d)
x = dict(d, status="retired", retirement={"at": M.now(), "evidence_refs": [rid + "#c1"], "replaced_by": None})
M.seal(x)
p.save("journey", stricter, x)
r = p.run("journeys", "check", stricter)
assert r.returncode == 2 and "says retired but its records say proposed" in r.stderr, r.stderr
p.save("journey", stricter, d)
fresh = p.journey(p.draft(rid))
y = dict(p.load("journey", fresh), status="active")
M.seal(y)
p.save("journey", fresh, y)
r = p.run("journeys", "check", fresh)
assert r.returncode == 2 and "says active but its records say proposed" in r.stderr, r.stderr
p.save("journey", fresh, dict(y, status="proposed"))
ok("a replacement cannot be weakened after it took over; a hand-set status or retirement block is not a record")

# 7) the runner is measured as pinned: its script, its tools, its output; bad input blocks, never crashes
runner = os.path.join(p.dir, "tools", "journey.py")
assert p.load("journey", fresh)["runner"]["files"] == {"tools/journey.py": hashlib.sha256(open(runner, "rb").read()).hexdigest()}
src = open(runner).read()
open(runner, "w").write(src.replace('app["seconds_to_first_task"]', '0.1'))
r = p.run("journeys", "run", fresh)
assert r.returncode == 2 and "runner file tools/journey.py changed since it was pinned" in r.stderr, r.stderr
open(runner, "w").write(src)
assert p.run("journeys", "run", fresh).returncode == 0
import _runner as R  # noqa: E402
assert R.version_seen("Python 3", "Python 3.12.4") and R.version_seen("3.12", "Python 3.12.4")
assert not R.version_seen("3.1", "Python 3.12.4") and not R.version_seen("18", "v20.18.1")
open(os.path.join(p.dir, "tools", "run.sh"), "w").write("echo hi\n")
rec, err = J.run_journey(p.dir, dict(base, runner={"command": ["tools/run.sh"]}))
assert rec is None and "could not start" in err, err
open(os.path.join(p.dir, "tools", "nan.py"), "w").write('print(\'{"steps": {"open": true}, "metrics": {"secs": NaN}}\')\n')
rec, err = J.run_journey(p.dir, dict(base, runner={"command": ["python3", "tools/nan.py"]}))
assert rec is None and "NaN is not a measurement" in err, err
assert R.drift(float("nan"), 9.0) == float("inf") and not R.drift(float("inf"), 9.0) <= 0.25
broken = p.journey(dict(p.draft(rid), steps=[{"id": "open", "action": "open"}]))
r = p.run("journeys", "diff", fresh, broken)
assert r.returncode == 2 and "only valid journeys compare" in r.stderr, r.stderr
r = p.run("journeys", "retire", fresh, "--replaced-by", broken, "--evidence", rid + "#c1")
assert r.returncode == 2 and "replacement %s" % broken in r.stderr, r.stderr
os.remove(p.path("journey", broken))
open(p.path("journey", "jrn_" + "f" * 20), "w").write('{"journey_id": ')
r = p.run("journeys", "check")
assert r.returncode == 2 and "not a JSON object" in r.stderr, r.stderr
os.remove(p.path("journey", "jrn_" + "f" * 20))
gone = p.load("journey", stricter)
os.remove(p.path("journey", stricter))
r = p.run("journeys", "check")
assert r.returncode == 2 and "%s: pinned (a recorded replacement) but its file is gone" % stricter in r.stderr, r.stderr
p.save("journey", stricter, gone)
open(os.path.join(p.dir, "tools", "quiet.py"), "w").write('print(\'{"steps": {"open": true}, "metrics": {}}\')\n')
rec, err = J.run_journey(p.dir, dict(base, runner={"command": ["python3", "tools/quiet.py"]}))
assert rec["unreported"] == ["add", "see", "secs", "tasks_shown"], rec
ok("a runner script edited after it was pinned refuses to run; versions match whole; NaN, no-start, malformed: blocked")

print("mission-journeys: %d checks passed" % PASS[0])
