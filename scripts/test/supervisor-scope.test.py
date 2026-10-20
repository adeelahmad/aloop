#!/usr/bin/env python3
"""gate-supervisor-scope: the supervisor may not write production source while a sprint is
live (the lock is this session's, or another session refreshed it recently).

Regression: on Linux `stat -f %m` prints filesystem info instead of failing cleanly, so the
recency arm crashed (`File: unbound variable`), the hook exited 1 (non-blocking) and the
supervisor's write went through. Found by a live Claude Code smoke run.

    python3 scripts/test/supervisor-scope.test.py
"""
import json
import atexit
import os
import shutil
import subprocess
import tempfile
import time

GATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugin", "bin", "gate-supervisor-scope")
T = tempfile.mkdtemp(prefix="aloop-scope-")
atexit.register(shutil.rmtree, T, ignore_errors=True)
ENV = {k: v for k, v in os.environ.items() if k not in ("SKIP_HOOKS", "AGENTIC_SPRINT_LOCK_TTL")}
LOCK = os.path.join(T, ".aladdin", "state", "sprint.lock")
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
for path in (".aladdin/state/tasks/S1-01-T1.env", ".agentic/task.env", ".aladdin/task.env", ".aladdin/state/agents/a1.json"):
    r = run(path, agent_type="aloop:green-worker")
    assert r.returncode == 2 and "task contract" in r.stderr, (path, r)
ok("a sub-agent may not write the task contract or the supervisor's state")
for path in (".aladdin/scaffold-symbols", ".agentic/scaffold-symbols"):
    r = run(path, agent_type="aloop:red-worker")
    assert r.returncode == 0, (path, r)
ok("a sub-agent may still write its own worktree markers (scaffold-symbols, in .aladdin/ or .agentic/)")
for path in (".aladdin/worktrees/S1-01-T1/src/main.rs", ".agentic/worktrees/S1-01-T1/src/main.rs"):
    r = run(path)
    assert r.returncode == 2 and "may not write production source" in r.stderr, (path, r)
for path in (".aladdin/worktrees/S1-01-T1/.aladdin/task.env", ".aladdin/state/tasks/S1-01-T1.env", "docs/agents/sprint1/init.md"):
    r = run(path)
    assert r.returncode == 0, (path, r)
assert run(".aladdin/worktrees/S1-01-T1/src/main.rs", agent_type="aloop:green-worker").returncode == 0
ok("live sprint: the supervisor may not edit source inside a task worktree (only its marker dir); a worker may")
for path in (".aladdin/../src/x.rs", "docs/agents/sprint1/../../../src/x.rs", ".aladdin/worktrees/S1-01-T1/.aladdin/../src/lib.rs"):
    r = run(path)
    assert r.returncode == 2 and "may not write production source" in r.stderr, (path, r)
p = {"session_id": "s-main", "cwd": T, "tool_name": "Write", "tool_input": {"file_path": ".aladdin/../src/y.rs", "content": "x"}}
r = subprocess.run([GATE], input=json.dumps(p), cwd=T, env=ENV, capture_output=True, text=True)
assert r.returncode == 2 and "may not write production source" in r.stderr, r
os.makedirs(os.path.join(T, "docs", "agents", "sprint1"), exist_ok=True)
os.makedirs(os.path.join(T, "src", "deep"), exist_ok=True)
os.symlink(os.path.join(T, "src", "deep"), os.path.join(T, "docs", "agents", "sprint1", "lnk"))
r = run("docs/agents/sprint1/lnk/../main.rs")
assert r.returncode == 2 and "may not write production source" in r.stderr, r
r = run(".aladdin/statex/../state/tasks/S1-01-T1.env", agent_type="aloop:green-worker")
assert r.returncode == 2 and "task contract" in r.stderr, r
ok("a path is judged where it lands: `..` and a symlink before `..` never turn source into a control file or docs")



def bash(cmd, agent_type="aloop:green-worker"):
    p = {"session_id": "s-main", "cwd": T, "tool_name": "Bash", "tool_input": {"command": cmd}, "agent_type": agent_type}
    return subprocess.run([GATE], input=json.dumps(p), cwd=T, env=ENV, capture_output=True, text=True)


for cmd in ("echo SCOPE_GLOBS=* >> .agentic/task.env",
            "sed -i 's/BUDGET_TOKENS_HARD=.*/BUDGET_TOKENS_HARD=9999999/' /repo/.aladdin/state/tasks/S1-01-T1.env",
            "cp /tmp/x .aladdin/state/tasks/S1-01-T1.env",
            "printf 'x' | tee .agentic/task.env", "echo ATTEMPT=9 > .aladdin/task.env"):
    r = bash(cmd)
    assert r.returncode == 2 and "task contract" in r.stderr, (cmd, r)
ok("a sub-agent may not rewrite the task contract through Bash")
for cmd in ("cat .agentic/task.env", "grep SCOPE .aladdin/state/tasks/S1-01-T1.env", "cargo test > out.txt",
            # live e2e run 5: a read-only probe was blocked (a redirect elsewhere + a contract path later)
            "cat .aladdin/state/agents/*.json 2>/dev/null | head -40; echo ---; ls -la .agentic/task.env 2>&1",
            "rm -rf target; cat .agentic/task.env"):
    r = bash(cmd)
    assert r.returncode == 0, (cmd, r)
ok("a sub-agent may still read the contract and redirect other output")


# ── The supervisor never commits on main (live e2e run 16: a `Track Cargo.lock` commit on main
# traced to nothing). A `git commit` from the MAIN agent in the main tree, while the sprint
# lock is live, is blocked; a worker's commit in its worktree, the initial commit of an
# unborn repo, `task-worktree merge` and a stale lock all pass.
subprocess.run(["git", "config", "user.email", "t@t"], cwd=T, check=True)
subprocess.run(["git", "config", "user.name", "t"], cwd=T, check=True)


def sup(cmd, cwd=None, session="s-main"):
    p = {"session_id": session, "cwd": cwd or T, "tool_name": "Bash", "tool_input": {"command": cmd}}
    return subprocess.run([GATE], input=json.dumps(p), cwd=cwd or T, env=ENV, capture_output=True, text=True)


open(LOCK, "w").write("s-main\n")
r = sup("git add -A && git commit -m init")
assert r.returncode == 0, r
ok("unborn HEAD: the initial commit (the worktree base) is allowed even with a live lock")

open(os.path.join(T, "README"), "w").write("x\n")
subprocess.run(["git", "add", "-A"], cwd=T, check=True)
subprocess.run(["git", "commit", "-qm", "init"], cwd=T, check=True)
for cmd in ("git add Cargo.lock && git commit -m 'Track Cargo.lock'", "git commit -am wip",
            "cargo build; git -C %s commit -m x" % T, "git commit --amend --no-edit"):
    r = sup(cmd)
    assert r.returncode == 2 and "never commits on main" in r.stderr, (cmd, r)
ok("live sprint: the supervisor's git commit on main is blocked")

for cmd in ("git status", "git log --oneline", "aloop task-worktree merge S1-01-T1", "git add -A", "git diff", "echo commit"):
    r = sup(cmd)
    assert r.returncode == 0, (cmd, r)
ok("live sprint: reads, staging and task-worktree merge pass")

WT = os.path.join(T, ".aladdin", "worktrees", "S1-01-T1")
subprocess.run(["git", "worktree", "add", "-q", "-b", "t1", WT], cwd=T, check=True)
r = sup("git add -A && git commit -m 'S1-01-T1 RED'", cwd=WT)
assert r.returncode == 0, r
r = sup("git -C %s commit -m 'S1-01-T1 GREEN'" % WT)
assert r.returncode == 0, r
ok("a commit inside a task worktree passes (that is where work is committed)")
r = bash("git add -A && git commit -m 'S1-01-T1 RED'")
assert r.returncode == 0, r
ok("a dispatched worker's git commit is never judged here")

# the supervisor's hook can run inside a task worktree (after its `cd` there): the worktree's
# task.env names a worker, but the supervisor's payload has no agent_id, so it is still judged
os.makedirs(os.path.join(WT, ".aladdin"), exist_ok=True)
open(os.path.join(WT, ".aladdin", "task.env"), "w").write("TASK_ID=S1-01-T1\nAGENT_ROLE=green-worker\n")
open(LOCK, "w").write("s-main\n")
for target in (os.path.join(WT, "src", "main.rs"), os.path.join(T, "src", "lib.rs")):
    p = {"session_id": "s-main", "cwd": WT, "tool_name": "Write", "tool_input": {"file_path": target, "content": "x"}}
    r = subprocess.run([GATE], input=json.dumps(p), cwd=WT, env=ENV, capture_output=True, text=True)
    assert r.returncode == 2 and "may not write production source" in r.stderr, (target, r)
    p["agent_id"] = "w1"
    r = subprocess.run([GATE], input=json.dumps(p), cwd=WT, env=ENV, capture_output=True, text=True)
    assert r.returncode == 0, (target, r)
ok("the task.env fallback exempts only a sub-agent (it has an agent_id), never the supervisor's hook run inside a worktree")

# ── The supervisor never writes an agent's output.md block (live e2e run 29: the final gate's
# report was lost and the supervisor appended a final-gate block to output.md itself). The
# report is the agent's word; the supervisor re-dispatches instead.
open(LOCK, "w").write("s-main\n")
r = run("docs/agents/sprint1/output.md")
assert r.returncode == 2 and "re-dispatch" in r.stderr, r
r = run("docs/agents/sprint1/s1-01-x/output.md", agent_type="aloop:final-gate")
assert r.returncode == 0, r
ok("Write to a story's output.md: blocked for the supervisor, allowed for a dispatched agent")
for cmd in ("cat >> docs/agents/sprint1/output.md <<EOF\n## S1-FINAL\nEOF",
            "printf x >> '%s/docs/agents/sprint1/s1-01-x/output.md'" % T,
            "echo x | tee -a docs/agents/sprint1/output.md"):
    r = sup(cmd)
    assert r.returncode == 2 and "re-dispatch" in r.stderr, (cmd, r)
ok("live sprint: the supervisor's shell append to an output.md is blocked")
for cmd in ("tail -5 docs/agents/sprint1/output.md", "cat docs/agents/sprint1/output.md | head",
            "cat >> docs/agents/sprint1/init.md <<EOF\nx\nEOF", "grep -c '^## ' docs/agents/sprint1/output.md > /tmp/n"):
    r = sup(cmd)
    assert r.returncode == 0, (cmd, r)
ok("live sprint: reading output.md and appending init.md pass")
r = bash("cat >> docs/agents/sprint1/output.md <<EOF\nx\nEOF")
assert r.returncode == 0, r
ok("a dispatched agent's shell append to output.md passes (that is how it reports)")
r = sup("cat >> %s/docs/agents/sprint1/output.md <<EOF\nx\nEOF" % T, cwd=os.path.join(T, ".aladdin", "worktrees", "S1-01-T1"))
assert r.returncode == 0, r
ok("a shell append from inside a linked task worktree passes (Codex/OpenCode workers carry no agent_type)")

# ── The supervisor never schedules a wakeup while a sprint is live (live e2e run 32: a 1200s
# "fallback heartbeat" set while the planner ran outlived the sprint, and the headless session
# sat idle 12 minutes after the final gate). A sub-agent's result arrives on its own.
def wake(session="s-main", agent_type=None):
    p = {"session_id": session, "cwd": T, "tool_name": "ScheduleWakeup", "tool_input": {"delaySeconds": 1200, "prompt": "check planner"}}
    if agent_type:
        p["agent_type"] = agent_type
    return subprocess.run([GATE], input=json.dumps(p), cwd=T, env=ENV, capture_output=True, text=True)


open(LOCK, "w").write("s-main\n")
r = wake()
assert r.returncode == 2 and "notification" in r.stderr, r
ok("live sprint: the supervisor's ScheduleWakeup is blocked")
r = wake(agent_type="aloop:planner")
assert r.returncode == 0, r
ok("a dispatched agent's ScheduleWakeup is not judged here")
os.utime(LOCK, (old, old)); open(LOCK, "w").write("s-other\n"); os.utime(LOCK, (old, old))
r = wake()
assert r.returncode == 0, r
ok("stale lock from another session: ScheduleWakeup is allowed (no sprint is live)")

os.utime(LOCK, (old, old))
open(LOCK, "w").close(); open(LOCK, "w").write("s-other\n"); os.utime(LOCK, (old, old))
r = sup("git commit -am wip")
assert r.returncode == 0, r
ok("stale lock: a commit on main outside any live sprint is allowed")

print("\n%d checks passed" % n)
