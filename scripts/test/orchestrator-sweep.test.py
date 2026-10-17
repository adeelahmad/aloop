#!/usr/bin/env python3
"""ROADMAP §4.2: the interruptible orchestrator loop's inspect step. `sweep` decides
accept / retry / escalate / replan / rollover / spawn / wait per task from harness facts
only, and `sweep wait` sleeps (no model call) until the stream or an attempt changes.
    python3 scripts/test/orchestrator-sweep.test.py
"""
import json
import os
import subprocess
import tempfile
import threading
import time

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-sweep-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"))
ENV.pop("AGENTIC_HOST", None)
PASS = [0]
SW = os.path.join(BIN, "sweep")
SP = os.path.join(T, "docs", "agents", "sprint1")


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def run(*args):
    return subprocess.run(args, cwd=T, env=ENV, capture_output=True, text=True)


def emit(event, task, *extra):
    r = run(os.path.join(BIN, "events"), "emit", event, "--task", task, *extra)
    assert r.returncode == 0, r.stderr


def actions():
    return {r["task"]: r["action"] for r in json.loads(run(SW, "--json").stdout)}


subprocess.run(["git", "init", "-q"], cwd=T, check=True)
os.makedirs(os.path.join(SP, "s1-01-x"))
open(os.path.join(SP, "s1-01-x", "tasks.md"), "w").write("".join("## T%d — t\n" % i for i in range(1, 9)))
BD = os.path.join(T, ".agentic", "budget")
os.makedirs(BD)

# T1 merged · T2 gate passed · T3 failed attempt 1 · T4 failed attempt 3 · T5 hard-stopped
# T6 running · T7 never started · T8 carried in rollover.md
emit("worktree.created", "S1-01-T1"); emit("gate.passed", "S1-01-T1", "--data", "gate=gate-green-verify"); emit("worktree.merged", "S1-01-T1")
emit("worktree.created", "S1-01-T2"); emit("gate.passed", "S1-01-T2", "--data", "gate=gate-green-verify")
emit("worktree.created", "S1-01-T3"); emit("gate.failed", "S1-01-T3", "--attempt", "1", "--data", "gate=gate-red-verify")
emit("worktree.created", "S1-01-T4"); emit("gate.failed", "S1-01-T4", "--attempt", "3", "--data", "gate=gate-green-verify")
emit("worktree.created", "S1-01-T5")
emit("task.token_hard_limit", "S1-01-T5", "--role", "green-worker", "--data", "breach_class=TASK_TOO_LARGE", "--data", "breach_evidence=14 files")
emit("worktree.created", "S1-01-T6")
json.dump({"agent_id": "a6", "task": "S1-01-T6", "role": "green-worker", "attempt": "1", "done": None},
          open(os.path.join(BD, "a6.json"), "w"))
open(os.path.join(SP, "rollover.md"), "w").write("---\ntype: rollover\n---\n- S1-01-T8 not started\n")

a = actions()
assert a == {"S1-01-T1": "DONE", "S1-01-T2": "ACCEPT", "S1-01-T3": "RETRY", "S1-01-T4": "ESCALATE", "S1-01-T5": "REPLAN",
             "S1-01-T6": "WAIT", "S1-01-T7": "SPAWN", "S1-01-T8": "ROLLOVER"}, a
out = run(SW).stdout
assert "REPLAN   S1-01-T5   — task.token_hard_limit (TASK_TOO_LARGE) — 14 files" in out, out
ok("one action per task from facts: DONE ACCEPT RETRY ESCALATE REPLAN WAIT SPAWN ROLLOVER")

# a re-dispatch opens a new worktree: the old failure no longer decides; a passing verdict does
emit("worktree.created", "S1-01-T3"); emit("gate.passed", "S1-01-T3", "--data", "gate=gate-red-verify")
assert actions()["S1-01-T3"] == "ACCEPT"
# an observe-only crossing by a non-worker is never a REPLAN
emit("task.token_hard_limit", "S1-01-T2", "--role", "structural-reviewer", "--data", "observe=1")
assert actions()["S1-01-T2"] == "ACCEPT"
ok("only the current attempt's facts count; observe-only crossings never trigger a replan")

# admission closed → a not-started task rolls over instead of spawning
json.dump({"sprint": "sprint1", "admission_closed": 1}, open(os.path.join(BD, "sprint1.sprint.json"), "w"))
assert actions()["S1-01-T7"] == "ROLLOVER"
ok("admission closed: not-started tasks roll over")

# sweep wait: sleeps until the stream changes, then sweeps; times out with exit 4
r = run(SW, "wait", "--timeout", "1")
assert r.returncode == 4 and "nothing changed" in r.stdout, r
t0 = time.time()
threading.Timer(1.5, lambda: emit("worktree.merged", "S1-01-T2")).start()
r = run(SW, "wait", "--timeout", "30", "--json")
assert r.returncode == 0 and 1.0 < time.time() - t0 < 20, (r, time.time() - t0)
assert {x["task"]: x["action"] for x in json.loads(r.stdout)}["S1-01-T2"] == "DONE"
ok("sweep wait blocks without polling a model and wakes on the next event")

print("\n%d checks passed" % PASS[0])
