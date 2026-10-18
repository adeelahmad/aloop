#!/usr/bin/env python3
"""ROADMAP M1: the data layout moves to .aladdin/ with a read-old fallback. A fresh project
keeps its run data in .aladdin/ (beside the committed config.yaml), protected by its own
.gitignore so transcripts can never reach a commit; a project that still has .agentic/ keeps
using it until `aloop layout migrate` moves it; ALADDIN_<KEY> equals AGENTIC_<KEY>; every
reader (Python, bash gates, the OpenCode plugin's dir rule) agrees on the one dir.
    python3 scripts/test/layout.test.py
"""
import json
import os
import subprocess
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
PASS = [0]


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def fresh():
    t = tempfile.mkdtemp(prefix="agentic-layout-")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=t, check=True)
    return t


def run(t, *args, **env):
    e = dict(os.environ, HOME=os.path.join(t, "home"), AGENTIC_NO_AUTOINSTALL="1", GIT_AUTHOR_NAME="A", GIT_AUTHOR_EMAIL="a@a",
             GIT_COMMITTER_NAME="A", GIT_COMMITTER_EMAIL="a@a")
    for k in ("AGENTIC_HOST", "ALADDIN_HOST"):
        e.pop(k, None)
    e.update(env)
    return subprocess.run(args, cwd=t, env=e, capture_output=True, text=True)


def sh_home(t, **env):
    return run(t, "bash", "-c", '. "%s/_paths.sh"; echo "$AGENTIC_HOME"' % BIN, **env).stdout.strip()


# 1) a fresh project: run data lands in .aladdin/, its .gitignore keeps only config.yaml committable
T = fresh()
assert run(T, os.path.join(BIN, "events"), "emit", "sprint.started", "--role", "supervisor").returncode == 0
assert os.path.isfile(os.path.join(T, ".aladdin", "logs", "events.jsonl")) and not os.path.exists(os.path.join(T, ".agentic"))
assert os.path.isfile(os.path.join(T, ".aladdin", ".gitignore"))
open(os.path.join(T, "docs", "agents", "defaults.md") if os.makedirs(os.path.join(T, "docs", "agents"), exist_ok=True) is None else "", "w").write("AGENTIC_TOKENS_HARD=1\n")
assert run(T, os.path.join(BIN, "config"), "migrate").returncode == 0
st = run(T, "git", "status", "--porcelain", "--untracked-files=all").stdout
assert ".aladdin/config.yaml" in st and ".aladdin/.gitignore" in st and "events.jsonl" not in st and "ledger" not in st, st
assert sh_home(T) == os.path.join(T, ".aladdin")
assert run(T, os.path.join(BIN, "layout"), "check").returncode == 0
assert "run data: .aladdin/" in run(T, os.path.join(BIN, "layout"), "show").stdout
ok("fresh project: run data in .aladdin/, only config.yaml (and the guard) are committable; python, bash and layout agree")

# 2) a legacy project keeps .agentic/ until migrated; nothing is written to .aladdin/ run dirs
L = fresh()
os.makedirs(os.path.join(L, ".agentic", "logs"))
open(os.path.join(L, ".agentic", "logs", "events.jsonl"), "w").write("")
open(os.path.join(L, ".gitignore"), "w").write(".agentic/\n")
assert run(L, os.path.join(BIN, "events"), "emit", "sprint.started", "--role", "supervisor").returncode == 0
assert os.path.getsize(os.path.join(L, ".agentic", "logs", "events.jsonl")) > 0
assert not any(os.path.isdir(os.path.join(L, ".aladdin", d)) for d in ("logs", "state", "ledger"))
assert sh_home(L) == os.path.join(L, ".agentic")
assert "run data: .agentic/" in run(L, os.path.join(BIN, "layout"), "show").stdout
# config.yaml in .aladdin/ does not flip a legacy project (it is the human's file, not run data)
assert run(L, os.path.join(BIN, "config"), "migrate").returncode == 0 and sh_home(L) == os.path.join(L, ".agentic")
ok("legacy .agentic/ keeps working unchanged (read-old fallback), even with .aladdin/config.yaml present")

# 3) migrate moves everything, writes the guard, and every later write goes to .aladdin/
os.makedirs(os.path.join(L, ".agentic", "state", "tasks"))
open(os.path.join(L, ".agentic", "state", "tasks", "S1-01-T1.env"), "w").write("TASK_ID=S1-01-T1\n")
r = run(L, os.path.join(BIN, "layout"), "migrate")
assert r.returncode == 0 and "moved .agentic/ -> .aladdin/" in r.stdout, r
assert not os.path.exists(os.path.join(L, ".agentic")) and os.path.isfile(os.path.join(L, ".aladdin", "state", "tasks", "S1-01-T1.env"))
assert os.path.isfile(os.path.join(L, ".aladdin", ".gitignore")) and os.path.isfile(os.path.join(L, ".aladdin", "config.yaml"))
before = os.path.getsize(os.path.join(L, ".aladdin", "logs", "events.jsonl"))
assert run(L, os.path.join(BIN, "events"), "emit", "sprint.started", "--role", "supervisor").returncode == 0
assert os.path.getsize(os.path.join(L, ".aladdin", "logs", "events.jsonl")) > before and sh_home(L) == os.path.join(L, ".aladdin")
assert run(L, os.path.join(BIN, "layout"), "migrate").returncode == 0          # idempotent
st = run(L, "git", "status", "--porcelain", "--untracked-files=all").stdout
assert "events.jsonl" not in st and "S1-01-T1.env" not in st, st
ok("layout migrate moves .agentic/ into .aladdin/, guards it, and later writes follow; idempotent")

# 4) ALADDIN_<KEY> is the new spelling of AGENTIC_<KEY>, for python and bash readers alike
r = run(T, os.path.join(BIN, "host"), "describe", "--json", ALADDIN_HOST="opencode")
assert r.returncode == 0 and json.loads(r.stdout)["host"] == "opencode", r
assert run(T, "bash", "-c", '. "%s/_paths.sh"; echo "$AGENTIC_HOST"' % BIN, ALADDIN_HOST="codex").stdout.strip() == "codex"
assert run(T, "bash", "-c", '. "%s/_paths.sh"; echo "$AGENTIC_HOST"' % BIN, ALADDIN_HOST="codex", AGENTIC_HOST="claude").stdout.strip() == "claude"
open(os.path.join(T, "docs", "agents", "defaults.md"), "a").write("ALADDIN_TIME_HARD_MIN=7\n")
assert run(T, "bash", "-c", '. "%s/_paths.sh"; agentic_setting TIME_HARD_MIN' % BIN).stdout.strip() == "7"
ok("ALADDIN_* overrides and settings lines are read everywhere; AGENTIC_* still wins when both are set")

# 5) a run-data dir that could reach git is refused by layout check
os.remove(os.path.join(T, ".aladdin", ".gitignore"))
r = run(T, os.path.join(BIN, "layout"), "check")
assert r.returncode == 2 and "not git-ignored" in r.stderr, r
assert run(T, os.path.join(BIN, "events"), "emit", "sprint.started", "--role", "supervisor").returncode == 0   # any writer restores the guard
assert run(T, os.path.join(BIN, "layout"), "check").returncode == 0
ok("layout check refuses an unprotected run-data dir; the next writer restores the guard")

# 6) the task contract is found in either dir: a supervisor that writes the literal legacy path
#    .agentic/state/tasks/<id>.env in a fresh .aladdin/ project (or the reverse) is still understood
C = fresh()
assert run(C, os.path.join(BIN, "events"), "emit", "sprint.started", "--role", "supervisor").returncode == 0   # .aladdin/ is live
sd = os.path.join(C, "docs", "agents", "sprint1", "s1-01-x")
os.makedirs(sd)
os.makedirs(os.path.join(C, ".agentic", "state", "tasks"))
open(os.path.join(C, ".agentic", "state", "tasks", "T1.env"), "w").write("TASK_ID=T1\nATTEMPT=1\nAGENT_ROLE=green\nSTORY_DIR=%s\n" % sd)
assert sh_home(C) == os.path.join(C, ".aladdin"), "the one stray contract must not flip the live dir"
out = run(C, "bash", "-c", '. "%s/_paths.sh"; agentic_contract_path T1; agentic_contract_path nope; echo "rc=$?"' % BIN).stdout
assert out.splitlines()[0] == os.path.join(C, ".agentic", "state", "tasks", "T1.env") and out.splitlines()[-1] == "rc=1", out
assert run(C, "python3", "-c", 'import sys; sys.path.insert(0, "%s"); import _paths; print(_paths.contract_path("%s", "T1"))' % (BIN, C)).stdout.strip() == os.path.join(C, ".agentic", "state", "tasks", "T1.env")
payload = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": C,
                      "tool_input": {"subagent_type": "aloop:green-worker", "prompt": "TASK_ID: T1\ndo it", "model": "sonnet"}})
r = run(C, os.path.join(BIN, "gate-dispatch"), input=payload) if False else subprocess.run(
    [os.path.join(BIN, "gate-dispatch")], cwd=C, input=payload, capture_output=True, text=True,
    env=dict(os.environ, HOME=os.path.join(C, "home"), AGENTIC_NO_AUTOINSTALL="1"))
assert r.returncode == 0, (r.returncode, r.stderr)
payload2 = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": C,
                       "tool_input": {"subagent_type": "aloop:green-worker", "prompt": "TASK_ID: T9\ndo it", "model": "sonnet"}})
r = subprocess.run([os.path.join(BIN, "gate-dispatch")], cwd=C, input=payload2, capture_output=True, text=True,
                   env=dict(os.environ, HOME=os.path.join(C, "home"), AGENTIC_NO_AUTOINSTALL="1"))
assert r.returncode == 2 and ".aladdin/state/tasks/T9.env" in r.stderr, (r.returncode, r.stderr)
ok("the task contract is found in .aladdin/ or .agentic/ (bash, python, gate-dispatch); a missing one is named at the live dir")

# 7) worktree-local markers: written to the worktree's .aladdin/ (guarded) in a fresh project, to
#    .agentic/ in a legacy one; a legacy .agentic/task.env in a worktree is still read (bind, selfcheck, gatelib)
W = fresh()
subprocess.run(["git", "-C", W, "commit", "-q", "--allow-empty", "-m", "init"], check=True, env=dict(os.environ, GIT_AUTHOR_NAME="A", GIT_AUTHOR_EMAIL="a@a", GIT_COMMITTER_NAME="A", GIT_COMMITTER_EMAIL="a@a"))
assert run(W, os.path.join(BIN, "task-worktree"), "add", "T1").returncode == 0
wt = os.path.join(W, ".aladdin", "worktrees", "T1")
assert os.path.isdir(wt), "task worktree under .aladdin/worktrees"
py = 'import sys; sys.path.insert(0, "%s"); import _paths; print(_paths.marker_dir("%s")); print(_paths.marker("%s", "task.env"))' % (BIN, wt, wt)
out = run(W, "python3", "-c", py).stdout.split()
assert out == [os.path.join(wt, ".aladdin"), os.path.join(wt, ".aladdin", "task.env")], out
assert os.path.isfile(os.path.join(wt, ".aladdin", ".gitignore")), "marker dir guarded"
open(os.path.join(wt, ".aladdin", "scaffold-symbols"), "w").write("Foo\n")
assert ".aladdin" not in run(W, "git", "-C", wt, "status", "--porcelain", "--untracked-files=all").stdout, "markers never reach the task's diff"
os.makedirs(os.path.join(wt, ".agentic"), exist_ok=True)
open(os.path.join(wt, ".agentic", "task.env"), "w").write("TASK_ID=T1\nATTEMPT=1\nAGENT_ROLE=green-worker\n")
assert run(W, "python3", "-c", py).stdout.split()[-1] == os.path.join(wt, ".agentic", "task.env"), "legacy marker read first"
r = run(W, os.path.join(BIN, "bind"), wt)
assert r.returncode == 0 and "AGENT_ROLE=green-worker" in r.stdout, r
sh = run(W, "bash", "-c", 'cd "%s" && . "%s/_paths.sh" && agentic_marker "$PWD" task.env && agentic_marker "$PWD" budget-exceeded; echo "rc=$?"; agentic_marker_dir "$PWD"' % (wt, BIN)).stdout.split()
assert sh == [os.path.join(wt, ".agentic", "task.env"), os.path.join(wt, ".aladdin", "budget-exceeded"), "rc=1", os.path.join(wt, ".aladdin")], sh
L2 = fresh()
os.makedirs(os.path.join(L2, ".agentic", "logs"))
assert run(L2, "python3", "-c", 'import sys; sys.path.insert(0, "%s"); import _paths; print(_paths.marker_dir("%s"))' % (BIN, L2)).stdout.strip() == os.path.join(L2, ".agentic")
ok("worktree markers go to .aladdin/ (guarded), .agentic/ in a legacy project; a legacy .agentic/task.env is still read")

print("\n%d checks passed" % PASS[0])
