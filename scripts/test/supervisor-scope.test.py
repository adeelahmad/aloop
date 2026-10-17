#!/usr/bin/env python3
"""gate-supervisor-scope: the supervisor may not write production source while a sprint is
live (the lock is this session's, or another session refreshed it recently).

Regression: on Linux `stat -f %m` prints filesystem info instead of failing cleanly, so the
recency arm crashed (`File: unbound variable`), the hook exited 1 (non-blocking) and the
supervisor's write went through. Found by a live Claude Code smoke run.

    python3 scripts/test/supervisor-scope.test.py
"""
import json
import os
import subprocess
import tempfile
import time

GATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugin", "bin", "gate-supervisor-scope")
T = tempfile.mkdtemp(prefix="aloop-scope-")
ENV = {k: v for k, v in os.environ.items() if k not in ("SKIP_HOOKS", "AGENTIC_SPRINT_LOCK_TTL")}
LOCK = os.path.join(T, ".agentic", "state", "sprint.lock")
n = 0


def ok(msg):
    global n
    n += 1
    print("ok - " + msg)


def run(path, session="s-main", agent_type=None):
    p = {"session_id": session, "cwd": T, "tool_name": "Write", "tool_input": {"file_path": os.path.join(T, path), "content": "x"}}
    if agent_type:
        p["agent_type"] = agent_type
    return subprocess.run([GATE], input=json.dumps(p), cwd=T, env=ENV, capture_output=True, text=True)


subprocess.run(["git", "init", "-q"], cwd=T, check=True)
os.makedirs(os.path.dirname(LOCK))

r = run("src/lib.rs")
assert r.returncode == 0, r
ok("no sprint lock: the supervisor may write source")

open(LOCK, "w").write("s-main\n")
r = run("src/lib.rs")
assert r.returncode == 2 and "may not write production source" in r.stderr, r
ok("lock armed by this session: source write blocked")

open(LOCK, "w").write("s-other\n")
r = run("src/lib.rs")
assert r.returncode == 2 and "may not write production source" in r.stderr, r
assert "unbound variable" not in r.stderr, r.stderr
ok("recent lock from another session (resumed sprint): source write blocked")

old = time.time() - 5 * 3600
os.utime(LOCK, (old, old))
r = run("src/lib.rs")
assert r.returncode == 0 and not r.stderr.strip(), r
ok("stale lock from another session: inert, write allowed")

open(LOCK, "w").write("s-main\n")
r = run("src/lib.rs", agent_type="aloop:green-worker")
assert r.returncode == 0, r
ok("a dispatched sub-agent may write source (its own gate constrains it)")

# The task contract is the supervisor's (M0.7): a worker may not write it, in the main tree or its worktree.
for path in (".agentic/state/tasks/S1-01-T1.env", ".agentic/task.env", ".agentic/state/agents/a1.json"):
    r = run(path, agent_type="aloop:green-worker")
    assert r.returncode == 2 and "task contract" in r.stderr, (path, r)
ok("a sub-agent may not write the task contract or the supervisor's state")
r = run(".agentic/scaffold-symbols", agent_type="aloop:red-worker")
assert r.returncode == 0, r
ok("a sub-agent may still write its own .agentic/ files (scaffold-symbols)")



def bash(cmd, agent_type="aloop:green-worker"):
    p = {"session_id": "s-main", "cwd": T, "tool_name": "Bash", "tool_input": {"command": cmd}, "agent_type": agent_type}
    return subprocess.run([GATE], input=json.dumps(p), cwd=T, env=ENV, capture_output=True, text=True)


for cmd in ("echo SCOPE_GLOBS=* >> .agentic/task.env",
            "sed -i 's/BUDGET_TOKENS_HARD=.*/BUDGET_TOKENS_HARD=9999999/' /repo/.agentic/state/tasks/S1-01-T1.env",
            "cp /tmp/x .agentic/state/tasks/S1-01-T1.env",
            "printf 'x' | tee .agentic/task.env"):
    r = bash(cmd)
    assert r.returncode == 2 and "task contract" in r.stderr, (cmd, r)
ok("a sub-agent may not rewrite the task contract through Bash")
for cmd in ("cat .agentic/task.env", "grep SCOPE .agentic/state/tasks/S1-01-T1.env", "cargo test > out.txt"):
    r = bash(cmd)
    assert r.returncode == 0, (cmd, r)
ok("a sub-agent may still read the contract and redirect other output")

print("\n%d checks passed" % n)
