#!/usr/bin/env python3
"""Regression: the time box must find a worker's worktree (and its task.env budget) even
when every tool call reports the MAIN repo as cwd — workers that use `git -C <wt>` or
absolute paths instead of `cd`. Before the fix the hook never saw the override and killed
real TDD attempts at the tiny default.   python3 scripts/test/budget-worktree.test.py
"""
import json
import os
import subprocess
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-budget-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"))
ENV.pop("AGENTIC_HOST", None)
PASS = [0]


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def sh(*args, cwd=T, inp=None):
    return subprocess.run(args, cwd=cwd, env=ENV, capture_output=True, text=True, input=inp, check=True).stdout.strip()


def state(agent):
    return json.load(open(os.path.join(T, ".agentic", "budget", agent + ".json")))


def call(agent, tool, tool_input, mode="post", tokens=None, role="green-worker"):
    tp = os.path.join(T, "cc", "sess.jsonl")
    sub = os.path.join(T, "cc", "sess", "subagents", "agent-%s.jsonl" % agent)
    os.makedirs(os.path.dirname(sub), exist_ok=True)
    if tokens is not None:
        with open(sub, "a") as f:
            f.write(json.dumps({"timestamp": "2026-09-23T10:00:00Z", "message": {"id": "m%d" % tokens, "model": "claude-sonnet-5",
                                "usage": {"input_tokens": tokens, "output_tokens": 0}}}) + "\n")
    payload = {"hook_event_name": "PostToolUse" if mode == "post" else "PreToolUse", "session_id": "sess",
               "transcript_path": tp, "cwd": T, "agent_id": agent, "agent_type": "aloop:" + role,
               "tool_name": tool, "tool_input": tool_input, "tool_response": "ok"}
    return sh(os.path.join(BIN, "budget"), "hook", mode, inp=json.dumps(payload))


sh("git", "init", "-q"); sh("git", "config", "user.email", "a@a"); sh("git", "config", "user.name", "A")
sh("git", "commit", "-q", "--allow-empty", "-m", "init")
os.makedirs(os.path.join(T, "docs", "agents", "sprint1", "s1-01-x"))
open(os.path.join(T, "docs", "STYLE.md"), "w").write("style\n")
wt = sh(os.path.join(BIN, "task-worktree"), "add", "S1-01-T1")
open(os.path.join(wt, ".agentic", "task.env"), "w").write(
    "TASK_ID=S1-01-T1\nATTEMPT=1\nAGENT_ROLE=green-worker\nSTORY_DIR=%s\nBUDGET_TOKENS_SOFT=300000\nBUDGET_TOKENS_HARD=400000\n"
    % os.path.join(T, "docs", "agents", "sprint1", "s1-01-x"))

# defaults are no longer the 40k/60k that killed real attempts
b = sh(os.path.join(BIN, "budget"), "resolve")
assert "BUDGET_TOKENS_SOFT=150000" in b and "BUDGET_TOKENS_HARD=250000" in b and "BUDGET_TIME_HARD_MIN=30" in b
ok("built-in defaults are 150k/250k tokens, 20/30 min")

# 1) cwd = main repo, the worker addresses its worktree with `git -C`: override found
call("a1", "Bash", {"command": "git -C %s status" % wt}, tokens=5000)
s = state("a1")
assert s["worktree"] == wt and s["budget"]["TOKENS_HARD"] == 400000 and s["budget_source"] == "task.env BUDGET_*", s
ok("git -C <worktree> from the main repo: task.env override applied")

# 2) absolute file path
call("a2", "Edit", {"file_path": os.path.join(wt, "pkg", "x.go"), "old_string": "a", "new_string": "b"}, tokens=5000)
assert state("a2")["budget"]["TOKENS_HARD"] == 400000; ok("absolute file path into the worktree: override applied")

# (a1/a2 still hold that worktree; finish them so it is unclaimed again)
for a in ("a1", "a2"):
    call(a, "Bash", {"command": "true"}, mode="stop")

# 3) first call reads a MAIN-tree file (style guide) -> the only unclaimed green worktree is used
call("a3", "Read", {"file_path": os.path.join(T, "docs", "STYLE.md")}, tokens=5000)
s3 = state("a3")
assert s3["worktree"] == wt and s3["budget"]["TOKENS_HARD"] == 400000, s3; ok("first call only reads main-tree files: the single matching worktree is used")

# 4) worktree ambiguous at first (two green worktrees), found on a later call: budget re-resolved, clock kept,
#    and a hard verdict reached under the default budget is withdrawn
wt2 = sh(os.path.join(BIN, "task-worktree"), "add", "S1-01-T2")
open(os.path.join(wt2, ".agentic", "task.env"), "w").write("TASK_ID=S1-01-T2\nATTEMPT=1\nAGENT_ROLE=green-worker\nBUDGET_TOKENS_HARD=500000\n")
for a in ("a1", "a2", "a3"):
    os.remove(os.path.join(T, ".agentic", "budget", a + ".json"))
    os.remove(os.path.join(T, ".agentic", "state", "agents", a + ".json"))
out = call("a4", "Read", {"file_path": os.path.join(T, "docs", "STYLE.md")}, tokens=260000)
s4 = state("a4")
assert s4["budget"]["TOKENS_HARD"] == 250000 and s4["hard"] and not s4["task_env_found"], s4
start = s4["start"]
out = call("a4", "Bash", {"command": "go test ./... -C %s" % wt2}, tokens=260001)
s4 = state("a4")
assert s4["worktree"] == wt2 and s4["budget"]["TOKENS_HARD"] == 500000 and not s4["hard"] and s4["start"] == start, s4
assert "HARD BUDGET" not in out
ok("worktree found late: budget re-resolved, clock kept, premature hard verdict withdrawn")

# 5) budget status explains where the budget came from
st = sh(os.path.join(BIN, "budget"), "status")
assert "task.env BUDGET_*" in st and "path in tool call" in st; ok("budget status shows the budget source and how the worktree was found")

# 6) docs/agents/defaults.md still applies from the first call (the user's working fix)
sh(os.path.join(BIN, "aloop-init"), "--apply", "TOKENS_SOFT=180000", "TOKENS_HARD=260000")
b = sh(os.path.join(BIN, "budget"), "resolve")
assert "BUDGET_TOKENS_HARD=260000" in b and "defaults.md" in b; ok("defaults.md applies project-wide")

print("\n%d checks passed" % PASS[0])
