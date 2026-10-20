#!/usr/bin/env python3
"""ROADMAP §4.12 (M9.13, offline): one mission end to end with zero tokens. Each step is the action
`mission tick` hands out, done the way its role or the human does it, through the CLI only: the
mission (the human's typed line approves it) → an ethics review → sourced research → a journey → a
baseline measured twice and confirmed by the human → a state report → a gap list → a goal → its ethics
review → one Intent Contract per gap → one Aladdin sprint per intent (planned at `ids freeze`, closed
by `stats sprint-close`) → a state report → GOAL MET → the goal closes → RESEARCH raises the bar. A
raised budget keeps the baseline (M9.25); a fresh clone decides the same.
    python3 scripts/test/mission-e2e.test.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "fixtures"))
from mission_fixture import ASKED, BIN, Project, M  # noqa: E402

PASS = [0]
GIT = dict(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def must(*args, rc=0):
    r = p.run(*args)
    assert r.returncode == rc, (args, r.returncode, r.stdout[-300:], r.stderr[-600:])
    return r


def tool(name, *args, rc=0):
    r = subprocess.run([os.path.join(BIN, name)] + list(args), cwd=p.dir, env=p.env, capture_output=True, text=True)
    assert r.returncode == rc, (name, args, r.returncode, r.stdout[-300:], r.stderr[-600:])
    return r


def sprint(n, gid):
    """One Aladdin sprint on the goal's next intent: `goal next` hands it off, the planner's boundary
    (`ids freeze`) records plan.created, and FINAL-GATE's `stats sprint-close` closes it."""
    nxt = must("goal", "next", gid).stdout.strip()
    sdir = os.path.join(p.dir, "docs", "agents", "sprint%d" % n)
    story = os.path.join(sdir, "s%d-01-gap" % n)
    os.makedirs(story)
    with open(os.path.join(sdir, "stories.md"), "w") as f:
        f.write("# Stories\n\n- S%d-01: close the gap\n" % n)
    for name in ("story.md", "tasks.md"):
        with open(os.path.join(story, name), "w") as f:
            f.write("---\nstory: S%d-01\n---\n\n## T1 close the gap\n" % n)
    tool("ids", "freeze", sdir)
    plan = [e for e in p.events("plan.created") if e["data"].get("sprint") == "S%d" % n]
    assert len(plan) == 1 and plan[0]["data"]["intent_id"] == json.load(open(nxt))["intent_id"], plan
    return sdir


def tick(want, rc=0, cwd=None):
    r = subprocess.run([os.path.join(BIN, "mission"), "tick", "--json"], cwd=cwd or p.dir, env=p.env,
                       capture_output=True, text=True)
    d = json.loads(r.stdout)
    assert (d["action"], r.returncode) == (want, rc), (want, rc, d, r.stderr[-400:])
    STEPS.append(want)
    return d


def review_clear(subject="mission"):
    path = must("ethics", "new", "--subject", subject).stdout.strip()
    doc = json.load(open(path))
    doc.update(findings=[], tier="clear")                     # the reviewer's judgment: nothing to flag
    with open(path, "w") as f:
        json.dump(doc, f)
    must("ethics", "record", path)


STEPS = []
p = Project(mode="autonomous", veto_window_h=0, approve=False)

# 1) the human sets the mission; only their typed line approves it
tick("CONFIRM_MISSION", 3)
r = p.raw("approve", "--by", "Adeel", "--reason", "go")
assert r.returncode == 3 and ASKED.search(r.stderr), r.stderr           # a role cannot approve for them
p.say(ASKED.search(r.stderr).group(1))
assert p.raw("approve", "--by", "Adeel", "--reason", "go").returncode == 0
ok("the mission counts once the human typed its approval line")

# 2) the roles build the evaluation layer; the human confirms the baseline
tick("ETHICS_REVIEW")
review_clear()
tick("RESEARCH")
rsr = must("research", "new", "Rival to-do apps", "--question", "how fast do rivals get a user to a first task?")
rid = json.load(open(rsr.stdout.strip()))["research_id"]
with open(os.path.join(p.dir, "rival.html"), "w") as f:      # a saved page, as the researcher fetched it
    f.write("<html><body><p>Rival A gets a new user to a first task in under 5 seconds.</p></body></html>")
src = must("research", "archive", rid, "https://example.com/rival-a", "--file", "rival.html").stdout.strip()
assert must("research", "claim", rid, "Rival A's first task takes under 5 seconds", "--source", src, "--quote",
            "a first task in under 5 seconds", "--kind", "rival").stdout.strip() == "c1"
must("research", "check", rid)
tick("DEFINE_JOURNEYS")
p.journey(p.draft(rid))
tick("BASELINE")
bid = json.load(open(must("baseline", "new", "--definition", "a first task in under 5 seconds").stdout.strip()))["baseline_id"]
tick("BASELINE")
must("baseline", "measure", bid)
tick("CONFIRM_BASELINE", 3)
must("baseline", "confirm", bid, "--by", "Adeel")
tick("ASSESS")
ok("ethics, research, journeys and a baseline measured twice; the human confirms it")

# 3) a raised budget cap is a new approval and a new review, not a new baseline (M9.25)
md = p.load("mission")
md["budget"]["usd"], md["status"] = 80.0, "draft"
p.save("mission", "", md)
tick("CONFIRM_MISSION", 3)
must("approve", "--by", "Adeel", "--reason", "a bigger budget")
tick("ETHICS_REVIEW")
review_clear()
tick("ASSESS")
assert must("baseline", "current").stdout.strip() == bid
ok("raising the budget keeps the confirmed baseline")

# 4) monitor and analyze: the state report and the gap list hand the planner a goal to derive
must("assess")
tick("EVALUATE")
r = must("evaluate")
assert "gap" in r.stderr, r.stderr
d = tick("DERIVE_GOAL")
assert must("evaluate", "check").returncode == 0
ok("a state report and a gap list; the loop asks for a goal")

# 5) a fresh clone decides the same from the committed artifacts and ledger (no run data, no prompts)
subprocess.run(["git", "add", "-A"], cwd=p.dir, check=True)
subprocess.run(["git", "commit", "-qm", "mission"], cwd=p.dir, env=dict(p.env, **GIT), check=True)
clone = tempfile.mkdtemp(prefix="aloop-clone-")
subprocess.run(["git", "clone", "-q", p.dir, clone], check=True)
assert not any(os.path.exists(os.path.join(clone, ".aladdin", d)) for d in ("logs", "transcripts"))   # run data stays
assert tick("DERIVE_GOAL", cwd=clone)["reason"] == d["reason"]
ok("a fresh clone hands out the same action")

# 6) plan: the goal, its ethics review, then one Intent Contract per gap
r = must("goal", "derive")
g = json.load(open(r.stdout.strip()))
gid = g["goal_id"]
tick("ETHICS_REVIEW")
review_clear(gid)
tick("PLAN_INTENTS")
assert must("goal", "check", gid).returncode == 0
paths = must("goal", "intents", gid).stdout.split()
assert len(paths) == len(g["gaps"]) >= 1 and all(json.load(open(x))["source"] == "mission" for x in paths)
ok("a goal from the gap list, its ethics review, and one intent per gap")

# 7) build: one sprint per intent; the loop waits while one runs and hands out the next when it closes
for n in range(1, len(paths) + 1):
    tick("SPRINT")
    sdir = sprint(n, gid)
    tick("WAIT", 4)                                                       # exit 4: nothing to do until next_check
    if n == len(paths):
        p.app(seconds_to_first_task=3.0, working_steps=["open", "add", "see"], tasks_shown=1)   # the sprints' work
    tool("stats", "sprint-close", sdir)
assert p.run("goal", "next", gid).returncode == 1                        # every intent has its plan
ok("each intent is one sprint: planned at the planner's boundary, closed by sprint-close")

# 8) re-evaluate: the state after the sprints earns every target; the goal closes; the bar is raised
tick("ASSESS")
time.sleep(1.05 - time.time() % 1)     # sprint-close stamps whole seconds: a report in the same second cannot be ordered after it
must("assess")
tick("EVALUATE")
r = must("evaluate")
assert "GOAL MET" in r.stderr and "0 gap(s)" in r.stderr, r.stderr
ev = json.load(open(r.stdout.strip().splitlines()[-1]))
assert ev["goal_id"] == gid and ev["goal_met"] is True, ev
must("goal", "close", gid, "--eval", ev["eval_id"])
assert p.load("goal", gid)["status"] == "met"
tick("RESEARCH")
assert must("evaluate", "check").returncode == 0 and must("check").returncode == 0
assert M.ledger_problems(p.dir) == []
usage = os.path.join(p.dir, ".aladdin", "ledger")
assert not os.path.isdir(usage) or not any(os.scandir(usage)), "an offline run spends no tokens"
ok("the sprints' product meets the goal; it closes and the loop raises the bar; no tokens spent, the ledger intact")

print("loop:", " → ".join(STEPS))
print("mission-e2e: %d checks passed" % PASS[0])
