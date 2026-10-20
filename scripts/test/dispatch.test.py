#!/usr/bin/env python3
"""gate-dispatch: the supervisor may not dispatch an execution worker before its task contract
exists. Regression (live e2e run 6): the contract was written after the structural reviewer was
dispatched, its gate could not find STORY_DIR and blocked 25 times — a block the worker cannot fix.

    python3 scripts/test/dispatch.test.py
"""
import json
import atexit
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures"))
from plan_fixture import approve_plan  # noqa: E402

GATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugin", "bin", "gate-dispatch")
T = tempfile.mkdtemp(prefix="aloop-dispatch-")
atexit.register(shutil.rmtree, T, ignore_errors=True)
ENV = {k: v for k, v in os.environ.items() if k not in ("SKIP_HOOKS", "AGENTIC_HOST")}
n = 0


def ok(msg):
    global n
    n += 1
    print("ok - " + msg)


def run(subagent_type, prompt, agent_type=None, model="haiku"):
    p = {"session_id": "s", "cwd": T, "tool_name": "Agent",
         "tool_input": {"subagent_type": subagent_type, "prompt": prompt, "isolation": "worktree"}}
    if model:
        p["tool_input"]["model"] = model
    if agent_type:
        p["agent_type"] = agent_type
    return subprocess.run([GATE], input=json.dumps(p), cwd=T, env=ENV, capture_output=True, text=True)


subprocess.run(["git", "init", "-q"], cwd=T, check=True)
os.makedirs(os.path.join(T, ".aladdin", "state", "tasks"))
os.makedirs(os.path.join(T, "docs", "agents"))
open(os.path.join(T, "docs", "agents", "defaults.md"), "w").write(
    "---\ntype: agentic-defaults\n---\n```conf\nAGENTIC_MODEL_PLANNING=haiku\nAGENTIC_MODEL_WORKER=haiku\n```\n")
approve_plan(T, ENV, 1)     # the human approved sprint 1's plan (M0.9; plan-approval.test.py checks that gate)

r = run("aloop:green-worker", "TASK_ID: S1-01-T1\nDo GREEN.")
assert r.returncode == 2 and "S1-01-T1.env" in r.stderr, r
ok("a worker dispatched before its contract exists is denied")

r = run("aloop:structural-reviewer", "Review the story.")
assert r.returncode == 2 and "TASK_ID:" in r.stderr, r
ok("a worker dispatch whose prompt names no TASK_ID is denied")

contract = os.path.join(T, ".aladdin", "state", "tasks", "S1-01-T1.env")
open(contract, "w").write("TASK_ID=S1-01-T1\n")
r = run("aloop:green-worker", "TASK_ID: S1-01-T1\nDo GREEN.")
assert r.returncode == 2 and "STORY_DIR" in r.stderr and "sN-NN-<slug>" in r.stderr, r
ok("a contract without STORY_DIR is denied (the worker's gates could never find its comms)")

open(contract, "w").write("TASK_ID=S1-01-T1\nSTORY_DIR=%s\n" % os.path.join(T, "docs/agents/sprint1/s1-01-nope"))
r = run("aloop:green-worker", "TASK_ID: S1-01-T1\nDo GREEN.")
assert r.returncode == 2 and "does not exist" in r.stderr, r
ok("a contract whose STORY_DIR does not exist is denied")

story = os.path.join(T, "docs/agents/sprint1/s1-01-x")
os.makedirs(story)
open(contract, "w").write("TASK_ID=S1-01-T1\nSTORY_DIR=%s\n" % story)
r = run("aloop:green-worker", "TASK_ID: S1-01-T1\nDo GREEN.")
assert r.returncode == 0, r
ok("a worker dispatched after its contract is written is allowed")

# A manual task worktree (`aloop task-worktree add`, the sanctioned equivalent of isolation:
# "worktree") gets the contract copy `aloop bind` checks, refreshed on every dispatch since the role
# changes per stage (live e2e run 28: the supervisor wrote only the main-tree contract, so RED's
# bind failed and its stop blocked twice in the shared tree).
subprocess.run(["git", "-c", "user.email=a@a", "-c", "user.name=A", "commit", "-q", "--allow-empty", "-m", "init"], cwd=T, check=True)
wt = subprocess.run([os.path.join(os.path.dirname(GATE), "task-worktree"), "add", "S1-01-T1"], cwd=T, env=ENV,
                    capture_output=True, text=True).stdout.strip()
assert os.path.isfile(os.path.join(wt, ".git")), wt
open(contract, "w").write("TASK_ID=S1-01-T1\nAGENT_ROLE=red-worker\nSTORY_DIR=%s\n" % story)
r = run("aloop:red-worker", "TASK_ID: S1-01-T1\nYour worktree is %s; first command: aloop bind %s" % (wt, wt))
assert r.returncode == 0, r
assert open(os.path.join(wt, ".aladdin", "task.env")).read() == open(contract).read()
b = subprocess.run([os.path.join(os.path.dirname(GATE), "bind"), wt], cwd=T, env=ENV, capture_output=True, text=True)
assert b.returncode == 0 and "AGENT_ROLE=red-worker" in b.stdout, b
open(contract, "w").write("TASK_ID=S1-01-T1\nAGENT_ROLE=scaffolder\nSTORY_DIR=%s\n" % story)
assert run("aloop:scaffolder", "TASK_ID: S1-01-T1\nWorktree %s" % wt).returncode == 0
assert "AGENT_ROLE=scaffolder" in open(os.path.join(wt, ".aladdin", "task.env")).read()
ok("a dispatch into a task worktree copies (and refreshes) the contract there, so `aloop bind` succeeds")

open(os.path.join(T, ".aladdin", "state", "tasks", "S1-SR-W1.env"), "w").write("TASK_ID=S1-SR-W1\nAGENT_ROLE=structural-reviewer\n")
r = run("aloop:structural-reviewer", "TASK_ID: S1-SR-W1\nReview wave 1.")
assert r.returncode == 2 and "sprint dir" in r.stderr, r
ok("a whole-wave structural review without STORY_DIR is told to use the sprint dir")

open(os.path.join(T, ".aladdin", "state", "tasks", "S1-SR-W1.env"), "w").write("TASK_ID=S1-SR-W1\nSTORY_DIR='%s'\n" % os.path.join(T, "docs/agents/sprint1"))
r = run("aloop:structural-reviewer", "TASK_ID: S1-SR-W1\nReview wave 1.")
assert r.returncode == 0, r
ok("a structural review contracted on the sprint dir is allowed")

for st in ("aloop:planner", "aloop:intake", "general-purpose", "Explore"):
    r = run(st, "Plan it.")
    assert r.returncode == 0, (st, r)
for st in ("general-purpose", "Explore"):
    r = run(st, "Look around.", model="opus")
    assert r.returncode == 0, (st, r)
ok("planning roles need no contract; non-aloop agents are not affected at all")

# The dispatch model is the project's confirmed one (ROADMAP §3.6), not the supervisor's pick.
# Live e2e run 20: defaults.md said haiku for every role and `aloop models resolve` printed it,
# yet the supervisor dispatched planners on opus and workers on sonnet: $2.79 instead of $0.99.
for st, m in (("aloop:green-worker", "sonnet"), ("aloop:green-worker", "claude-opus-5-5"), ("aloop:planner", "opus"),
              ("aloop:intake", "claude-sonnet-5-5"), ("aloop:archivist", "sonnet")):
    r = run(st, "TASK_ID: S1-01-T1\nDo it.", model=m)
    assert r.returncode == 2 and "model" in r.stderr and "haiku" in r.stderr and "aloop models resolve" in r.stderr, (st, m, r)
r = run("aloop:green-worker", "TASK_ID: S1-01-T1\nDo GREEN.", model=None)
assert r.returncode == 2 and "model: haiku" in r.stderr, r
r = run("aloop:planner", "Plan it.", model=None)
assert r.returncode == 2 and "model: haiku" in r.stderr, r
ok("a dispatch on a model other than the project's resolved one, or with no model, is denied for every aloop role")
for st, m in (("aloop:green-worker", "haiku"), ("aloop:green-worker", "claude-haiku-5-5"), ("aloop:planner", "haiku")):
    r = run(st, "TASK_ID: S1-01-T1\nDo it.", model=m)
    assert r.returncode == 0, (st, m, r)
open(os.path.join(T, "docs", "agents", "defaults.md"), "w").write(
    "---\ntype: agentic-defaults\n---\n```conf\nAGENTIC_MODEL_PLANNING=strongest\nAGENTIC_MODEL_WORKER=strong\n```\n")
r = run("aloop:planner", "Plan it.", model="claude-opus-5-5")
assert r.returncode == 0, r
r = run("aloop:green-worker", "TASK_ID: S1-01-T1\nDo GREEN.", model="sonnet")
assert r.returncode == 0, r
r = run("aloop:green-worker", "TASK_ID: S1-01-T1\nDo GREEN.", model="haiku")
assert r.returncode == 2 and "sonnet" in r.stderr, r
ok("the resolved model (pinned id or route, by family) is accepted; a cheaper-than-confirmed pick is denied too")

# The sprint lives in docs/agents/sprintN/, never in the run-data dir (live e2e run 24: a haiku
# supervisor planned and built the whole sprint under .aladdin/sprint1/, which is git-ignored and
# never committed; no gate noticed, and the run's planning checks found no sprint at all).
open(os.path.join(T, "docs", "agents", "defaults.md"), "w").write(
    "---\ntype: agentic-defaults\n---\n```conf\nAGENTIC_MODEL_PLANNING=haiku\nAGENTIC_MODEL_WORKER=haiku\n```\n")
for home in (".aladdin", ".agentic"):
    hidden = os.path.join(T, home, "sprint1", "s1-01-x")
    os.makedirs(hidden, exist_ok=True)
    open(contract, "w").write("TASK_ID=S1-01-T1\nSTORY_DIR=%s\n" % hidden)
    r = run("aloop:green-worker", "TASK_ID: S1-01-T1\nDo GREEN.")
    assert r.returncode == 2 and "docs/agents/sprint" in r.stderr and home in r.stderr, (home, r)
open(contract, "w").write("TASK_ID=S1-01-T1\nSTORY_DIR=%s\n" % story)
r = run("aloop:green-worker", "TASK_ID: S1-01-T1\nDo GREEN.")
assert r.returncode == 0, r
ok("a STORY_DIR inside the run-data dir (.aladdin/, .agentic/) is denied with the docs/agents/sprintN/ path")

r = run("aloop:green-worker", "TASK_ID: S1-09-T9\nDo GREEN.", agent_type="aloop:planner")
assert r.returncode == 0, r
ok("a sub-agent's own Agent calls are not this gate's business")

empty = tempfile.mkdtemp(prefix="aloop-dispatch-none-")
atexit.register(shutil.rmtree, empty, ignore_errors=True)
subprocess.run(["git", "init", "-q"], cwd=empty, check=True)
p = {"session_id": "s", "cwd": empty, "tool_name": "Agent",
     "tool_input": {"subagent_type": "aloop:green-worker", "prompt": "TASK_ID: X\n"}}
r = subprocess.run([GATE], input=json.dumps(p), cwd=empty, env=ENV, capture_output=True, text=True)
assert r.returncode == 0, r
ok("a repo with no aloop state is not affected")

# Every committing worker's prompt orders its finish: output.md block, commit, THEN selfcheck
# (live run 27: all 3 blocks were self-checks run before the block or the commit).
AGENTS = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "plugin", "agents")
for role in ("red-worker", "green-worker", "scaffolder"):
    body = open(os.path.join(AGENTS, role + ".md")).read()
    i = body.index("Finish in this order: append your `output.md` block")
    assert i < body.index("git add -A && git commit", i) < body.index("THEN run `selfcheck`", i), role
ok("red/green/scaffold prompts finish block, commit, then selfcheck")

print("\n%d checks passed" % n)
