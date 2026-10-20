#!/usr/bin/env python3
"""ROADMAP §4.12 (M9.10): the State Assessor and the Evaluator. `mission assess` runs exactly
what the confirmed baseline froze, twice, and records the numbers with the commit it measured;
`mission evaluate` turns a state into a gap list that is a pure function of the state, the
baseline and the active goal's targets (regressions first, then open gaps closest to target),
and `evaluate check` proves a gap list follows from its inputs.
    python3 scripts/test/mission-evaluate.test.py
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "fixtures"))
from mission_fixture import Project, M  # noqa: E402

sys.path.insert(0, M.AREAS_DIR)
import assess as A  # noqa: E402

PASS = [0]


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def made(r):
    assert r.returncode == 0, r.stderr
    return json.load(open(r.stdout.strip().splitlines()[-1]))


p = Project(veto_window_h=0)
genv = dict(p.env, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
subprocess.run(["git", "add", "-A"], cwd=p.dir, check=True)
subprocess.run(["git", "commit", "-qm", "seed"], cwd=p.dir, env=genv, check=True)
rid = p.research(["c1"])

# 1) assess needs a confirmed baseline; then it measures exactly what the baseline froze
r = p.run("assess")
assert r.returncode == 3 and "no confirmed baseline" in r.stderr, r.stderr
j1 = p.journey(p.draft(rid))
j2 = p.journey(p.draft(rid, title="The first task stays under 10s", pass_criteria=[
    {"id": "ok10", "metric": "secs", "op": "<=", "value": 10}]))
b = made(p.run("baseline", "new", "--definition", "a first task in under 5 seconds, and it shows up"))
assert p.run("baseline", "measure", b["baseline_id"]).returncode == 0
assert p.run("baseline", "confirm", b["baseline_id"], "--by", "Adeel").returncode == 0
s1 = made(p.run("assess"))
head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=p.dir, capture_output=True, text=True).stdout.strip()
assert s1["state_id"].startswith("sta_") and s1["baseline_id"] == b["baseline_id"] and s1["agreed"] is True
assert len(s1["runs"]) == 2 and s1["head"]["commit"] == head and s1["sha256"] == M.digest(s1)
assert set(s1["runs"][0]["journeys"]) == {j1, j2} and p.events("state.assessed")[-1]["data"]["state_id"] == s1["state_id"]
assert p.run("assess", "check").returncode == 0
ok("mission assess: needs a confirmed baseline; measures its frozen journeys twice, with the commit; sealed")

# 2) evaluate: the gap list against the baseline, regressions first, then open gaps closest to target
e1 = made(p.run("evaluate"))
assert e1["goal_met"] is False and e1["regressions"] == 0 and e1["goal_id"] is None
assert [(g["gap_id"], g["kind"]) for g in e1["gaps"]] == [(j1 + "#fast", "open"), (j1 + "#all", "open")], e1["gaps"]
assert e1["gaps"][0]["distance"] == 0.8 and e1["earned"] == []
ec = p.events("eval.completed")[-1]["data"]
assert ec["gaps"] == 2 and ec["eval_id"] == e1["eval_id"] and ec["sha256"] == e1["sha256"] == M.digest(e1), ec
rec = M.newest(p.dir, "eval.completed", M.rel(p.dir, M.path_of(p.dir, "eval", e1["eval_id"])))
assert rec and rec["role"] == "evaluator" and rec["data"]["sha256"] == e1["sha256"], rec   # a ledger row (M9.27)
p.app(seconds_to_first_task=11.0, working_steps=["open", "add"], tasks_shown=0)     # slower: j2's ok10 passed at baseline
s2 = made(p.run("assess"))
e2 = made(p.run("evaluate", s2["state_id"]))
assert e2["gaps"][0]["gap_id"] == j2 + "#ok10" and e2["gaps"][0]["kind"] == "regression" and e2["regressions"] == 1
trend = {(m["journey_id"], m["metric"]): m["trend"] for m in e2["metrics"]}
assert trend[(j1, "secs")] == "worse" and trend[(j1, "tasks_shown")] == "same", trend
p.app(seconds_to_first_task=4.0, working_steps=["open", "add"], tasks_shown=0)
e3 = made(p.run("evaluate", made(p.run("assess"))["state_id"]))
assert [g["gap_id"] for g in e3["gaps"]] == [j1 + "#all"] and [x["gap_id"] for x in e3["earned"]] == [j1 + "#fast"]
assert {(m["journey_id"], m["metric"]): m["trend"] for m in e3["metrics"]}[(j1, "secs")] == "better"
p.app(seconds_to_first_task=2.0, working_steps=["open", "add", "see"], tasks_shown=1)
e4 = made(p.run("evaluate", made(p.run("assess"))["state_id"]))
assert e4["goal_met"] is True and e4["gaps"] == [] and len(e4["earned"]) == 2
ok("mission evaluate: open gaps by distance, a regression comes first, earned criteria listed, goal met when none left")

# 3) the active goal's targets narrow the gap list; a regression elsewhere still counts
gid = M.new_id("goal")
gdoc = {"goal_id": gid, "status": "active", "created_at": M.now(), "targets": [{"journey_id": j1, "criterion_id": "fast"}]}
p.save("goal", gid, gdoc)
assert A.latest(p.dir) and made(p.run("evaluate"))["goal_id"] is None          # a status field activates nothing
M.record(p.dir, "goal.derived", M.rel(p.dir, p.path("goal", gid)), "mission", sha256=M.digest(gdoc), goal_id=gid)
p.app(seconds_to_first_task=4.0, working_steps=["open", "add"], tasks_shown=0)
e5 = made(p.run("evaluate", made(p.run("assess"))["state_id"]))
assert e5["goal_id"] == gid and e5["goal_met"] is True and e5["gaps"] == [], e5["gaps"]
p.app(seconds_to_first_task=11.0, working_steps=["open", "add"], tasks_shown=0)
e6 = made(p.run("evaluate", made(p.run("assess"))["state_id"]))
assert [(g["gap_id"], g["kind"]) for g in e6["gaps"]] == [(j2 + "#ok10", "regression"), (j1 + "#fast", "open")], e6["gaps"]
ok("the goal's targets narrow the gap list to what it promised; a regression anywhere still blocks the goal")

# 4) evaluate check: a gap list must follow from its state, baseline and goal
assert p.run("evaluate", "check").returncode == 0
doc = p.load("eval", e6["eval_id"])
doc["gaps"] = doc["gaps"][1:]
doc["regressions"] = 0
M.seal(doc)
p.save("eval", e6["eval_id"], doc)
r = p.run("evaluate", "check", e6["eval_id"])
assert r.returncode == 2 and "gaps does not follow from state" in r.stderr, r.stderr
st = p.load("state", e6["state_id"])
st["runs"][0]["journeys"][j2]["criteria"][0]["passed"] = True
p.save("state", e6["state_id"], st)
r = p.run("assess", "check", e6["state_id"])
assert r.returncode == 2 and "sha256 does not match" in r.stderr, r.stderr
del st["runs"][1]["journeys"][j2]
M.seal(st)
p.save("state", e6["state_id"], st)
assert "no state.assessed record for this content" in p.run("assess", "check", e6["state_id"]).stderr   # re-sealed
M.record(p.dir, "state.assessed", M.rel(p.dir, p.path("state", e6["state_id"])), "state-assessor", sha256=st["sha256"])
assert "run 2 skipped journeys %s" % j2 in p.run("assess", "check", e6["state_id"]).stderr
ok("evaluate check recomputes the gap list (a re-sealed edit is caught); assess check finds a skipped or edited run")

# 5) a measurement that disagrees between runs is recorded but never evaluated
open(os.path.join(p.dir, "flaky"), "w").close()
r = p.run("assess")
assert r.returncode == 2 and "not reproducible now" in r.stderr, (r.returncode, r.stdout[-500:], r.stderr)
flaky = p.load("state", A.latest(p.dir)[1]["state_id"])
assert flaky["agreed"] is False and flaky["disagreements"]
r = p.run("evaluate", flaky["state_id"])
assert r.returncode == 2 and "its runs disagree" in r.stderr, r.stderr
kept = p.load("eval", e6["eval_id"])
moved = dict(kept, state_id=flaky["state_id"])        # a gap list re-pointed at the flaky state
M.seal(moved)
p.save("eval", e6["eval_id"], moved)
r = p.run("evaluate", "check", e6["eval_id"])
assert r.returncode == 2 and "its runs disagree" in r.stderr, r.stderr
p.save("eval", e6["eval_id"], kept)
os.remove(os.path.join(p.dir, "flaky"))
ok("a state whose runs disagree is recorded (agreed: false) and refused by evaluate and evaluate check")

# 6) judged metrics: filled from the judges area when it scores them, else pending
jd = p.load("journey", j1)
judged = dict(jd, metrics=jd["metrics"] + [{"id": "ux", "name": "delight", "unit": "1-5", "direction": "higher",
                                           "query": {"kind": "judge", "rubric": "rub_" + "1" * 20, "rubric_sha256": "a" * 64},
                                           "source_refs": [rid + "#c1"]}],
              pass_criteria=jd["pass_criteria"] + [{"id": "delight", "metric": "ux", "op": ">=", "value": 4}])
import journeys as J  # noqa: E402
rec = {"steps": {"open": True, "add": True, "see": True}, "metrics": {"secs": 1}}
rec["criteria"] = J.score(judged, rec)
assert J.verdict(rec["criteria"]) is None
p.save("journey", j1, judged)     # unsealed on purpose: only score() reads it here
runs = A.with_judged([{"journeys": {j1: rec}, "benchmarks": {}}], {j1: {"ux": {"value": 5, "refs": ["x"]}}}, p.dir)
assert runs[0]["journeys"][j1]["passed"] is True and runs[0]["journeys"][j1]["metrics"]["ux"] == 5
assert A.judged(p.dir, b) == {}          # no judges area yet: judged metrics stay pending
import types  # noqa: E402
fake = types.ModuleType("judge")
fake.verdict = lambda cwd, rub, subject, rubric_sha256=None: {
    "verdict": "pass" if subject == j1 and rubric_sha256 == "a" * 64 else "blocked",
    "score": 5 if subject == j1 else None, "run_ids": ["r1", "r2", "r3"]}
sys.modules["judge"] = fake
judged_b = dict(b, journeys=[{"journey_id": j1, "sha256": M.digest(judged)}])
assert A.judged(p.dir, judged_b) == {j1: {"ux": {"value": 5, "refs": ["r1", "r2", "r3"]}}}
fake.verdict = lambda cwd, rub, subject: {"verdict": "pass", "score": 5, "run_ids": []}
assert A.judged(p.dir, judged_b) == {}                   # a judge that cannot check the pin: still pending
fake.verdict = lambda cwd, rub, subject, rubric_sha256=None: {"verdict": "inconclusive", "score": None, "run_ids": []}
assert A.judged(p.dir, judged_b) == {}                   # no agreed score: still pending
fake.verdict = lambda cwd, rub, subject, rubric_sha256=None: {"verdict": "pass", "score": 10 ** 400, "run_ids": []}
assert A.judged(p.dir, judged_b) == {}                   # a score no float holds is not a score
del sys.modules["judge"]
p.save("journey", j1, jd)
ok("judged metrics stay pending until the judges area agrees a score for the pinned rubric (subject: the journey id)")

# 7) the gap list is computed from the journeys the baseline froze: an edited or hidden journey blocks
p2 = Project(veto_window_h=0)
r2 = p2.research(["c1"])
k1 = p2.journey(p2.draft(r2))
b2 = made(p2.run("baseline", "new", "--definition", "a first task in under 5 seconds"))
assert p2.run("baseline", "measure", b2["baseline_id"]).returncode == 0
assert p2.run("baseline", "confirm", b2["baseline_id"], "--by", "Adeel").returncode == 0
st = made(p2.run("assess"))
ev = made(p2.run("evaluate", st["state_id"]))
assert ev["goal_met"] is False and p2.run("evaluate", "check", ev["eval_id"]).returncode == 0
kd = p2.load("journey", k1)
p2.save("journey", k1, dict(kd, pass_criteria=[c for c in kd["pass_criteria"] if c["id"] != "fast"]))
r = p2.run("evaluate", st["state_id"])
assert r.returncode == 2 and "is not the content baseline %s froze" % b2["baseline_id"] in r.stderr, r.stderr
r = p2.run("evaluate", "check", ev["eval_id"])
assert r.returncode == 2 and "is not the content baseline" in r.stderr, r.stderr
kp = p2.path("journey", k1)
os.rename(kp, kp + ".aside")
r = p2.run("evaluate", st["state_id"])
assert r.returncode == 2 and "journey %s is gone" % k1 in r.stderr, r.stderr
os.rename(kp + ".aside", kp)
p2.save("journey", k1, kd)
assert p2.run("evaluate", "check", ev["eval_id"]).returncode == 0
ok("evaluate reads the journeys as the baseline froze them: an edited or hidden journey blocks, never GOAL MET")

# 8) every input is the content its record covers: state, baseline, goal; no shape or size crashes
p3 = Project(veto_window_h=0)
r3 = p3.research(["c1"])
m1 = p3.journey(p3.draft(r3))
b3 = made(p3.run("baseline", "new", "--definition", "a first task in under 5 seconds"))
assert p3.run("baseline", "measure", b3["baseline_id"]).returncode == 0
assert p3.run("baseline", "confirm", b3["baseline_id"], "--by", "Adeel").returncode == 0
s3 = made(p3.run("assess"))
assert M.newest(p3.dir, "state.assessed", M.rel(p3.dir, p3.path("state", s3["state_id"])))["data"]["sha256"] == s3["sha256"]
e3 = made(p3.run("evaluate", s3["state_id"]))
forged = dict(s3, state_id=M.new_id("state"))
for run in forged["runs"]:
    run["journeys"][m1]["passed"] = True
M.seal(forged)
p3.save("state", forged["state_id"], forged)
r = p3.run("assess", "check", forged["state_id"])
assert r.returncode == 2 and "no state.assessed record for this content" in r.stderr, r.stderr
r = p3.run("evaluate", forged["state_id"])
assert r.returncode == 2 and "no state.assessed record" in r.stderr, r.stderr
ed = p3.load("eval", e3["eval_id"])
doc = dict(ed, metrics=[dict(m, trend="better") for m in ed["metrics"]])
M.seal(doc)
p3.save("eval", e3["eval_id"], doc)
r = p3.run("evaluate", "check", e3["eval_id"])
assert r.returncode == 2 and "metrics does not follow" in r.stderr, r.stderr
p3.save("eval", e3["eval_id"], ed)
bd = p3.load("baseline", b3["baseline_id"])
bad_b = json.loads(json.dumps(bd))
for run in bad_b["measurements"]["runs"]:
    run["journeys"][m1]["criteria"] = []
p3.save("baseline", b3["baseline_id"], bad_b)
r = p3.run("evaluate", s3["state_id"])
assert r.returncode == 2 and "baseline %s is not the content its confirmation covers" % b3["baseline_id"] in r.stderr, r.stderr
p3.save("baseline", b3["baseline_id"], {k: v for k, v in bd.items() if k != "journeys"})
r = p3.run("evaluate", s3["state_id"])
assert r.returncode == 2 and "Traceback" not in r.stderr, r.stderr
p3.save("baseline", b3["baseline_id"], bd)
gp = p3.path("goal", M.new_id("goal"))
for targets, want in ((["%s#fast" % m1], "targets must be"), (5, "targets must be"), ([{"journey_id": m1}], "targets must be"),
                      ([{"journey_id": m1, "criterion_id": "fastt"}], "which baseline %s does not freeze" % b3["baseline_id"])):
    g = {"goal_id": os.path.basename(gp)[:-5], "status": "active", "created_at": M.now(), "targets": targets}
    M.save(gp, g)
    M.record(p3.dir, "goal.derived", M.rel(p3.dir, gp), "mission", sha256=M.digest(g))
    r = p3.run("evaluate", s3["state_id"])
    assert r.returncode == 2 and want in r.stderr and "Traceback" not in r.stderr, (targets, r.returncode, r.stderr[-300:])
os.remove(gp)
p3.app(seconds_to_first_task=9.0, working_steps=["open", "add"], tasks_shown=10 ** 400)
r = p3.run("assess")
assert r.returncode == 2 and "finite number" in r.stderr and "Traceback" not in r.stderr, r.stderr
st = p3.load("state", s3["state_id"])
for bad_run in ({"journeys": "x", "benchmarks": {}}, {"journeys": {m1: "x"}, "benchmarks": {}},
                {"journeys": {m1: dict(st["runs"][0]["journeys"][m1], criteria=["x"])}, "benchmarks": {}}):
    bad_s = dict(st, runs=[bad_run, bad_run])
    M.seal(bad_s)
    p3.save("state", s3["state_id"], bad_s)
    for args in (["assess", "check", s3["state_id"]], ["evaluate", s3["state_id"]], ["evaluate", "check", e3["eval_id"]]):
        r = p3.run(*args)
        assert r.returncode == 2 and "Traceback" not in r.stderr, (bad_run, args, r.returncode, r.stderr[-300:])
p3.save("state", s3["state_id"], st)
assert p3.run("evaluate", "check", e3["eval_id"]).returncode == 0
ok("a state needs its assess record, a baseline its confirmation, a goal its record and real targets; no shape crashes")

print("mission-evaluate: %d checks passed" % PASS[0])
