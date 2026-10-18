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
import tempfile

GATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugin", "bin", "gate-dispatch")
T = tempfile.mkdtemp(prefix="aloop-dispatch-")
atexit.register(shutil.rmtree, T, ignore_errors=True)
ENV = {k: v for k, v in os.environ.items() if k != "SKIP_HOOKS"}
n = 0


def ok(msg):
    global n
    n += 1
    print("ok - " + msg)


def run(subagent_type, prompt, agent_type=None):
    p = {"session_id": "s", "cwd": T, "tool_name": "Agent",
         "tool_input": {"subagent_type": subagent_type, "prompt": prompt, "isolation": "worktree"}}
    if agent_type:
        p["agent_type"] = agent_type
    return subprocess.run([GATE], input=json.dumps(p), cwd=T, env=ENV, capture_output=True, text=True)


subprocess.run(["git", "init", "-q"], cwd=T, check=True)
os.makedirs(os.path.join(T, ".agentic", "state", "tasks"))

r = run("aloop:green-worker", "TASK_ID: S1-01-T1\nDo GREEN.")
assert r.returncode == 2 and "S1-01-T1.env" in r.stderr, r
ok("a worker dispatched before its contract exists is denied")

r = run("aloop:structural-reviewer", "Review the story.")
assert r.returncode == 2 and "TASK_ID:" in r.stderr, r
ok("a worker dispatch whose prompt names no TASK_ID is denied")

contract = os.path.join(T, ".agentic", "state", "tasks", "S1-01-T1.env")
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

open(os.path.join(T, ".agentic", "state", "tasks", "S1-SR-W1.env"), "w").write("TASK_ID=S1-SR-W1\nAGENT_ROLE=structural-reviewer\n")
r = run("aloop:structural-reviewer", "TASK_ID: S1-SR-W1\nReview wave 1.")
assert r.returncode == 2 and "sprint dir" in r.stderr, r
ok("a whole-wave structural review without STORY_DIR is told to use the sprint dir")

open(os.path.join(T, ".agentic", "state", "tasks", "S1-SR-W1.env"), "w").write("TASK_ID=S1-SR-W1\nSTORY_DIR='%s'\n" % os.path.join(T, "docs/agents/sprint1"))
r = run("aloop:structural-reviewer", "TASK_ID: S1-SR-W1\nReview wave 1.")
assert r.returncode == 0, r
ok("a structural review contracted on the sprint dir is allowed")

for st in ("aloop:planner", "aloop:intake", "general-purpose", "Explore"):
    r = run(st, "Plan it.")
    assert r.returncode == 0, (st, r)
ok("planning roles and non-aloop agents are not affected")

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

print("\n%d checks passed" % n)
