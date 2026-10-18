#!/usr/bin/env python3
"""budget contract: one command writes the supervisor's task contract
(<run-data>/state/tasks/<TASK_ID>.env) with BASE_REF and the resolved BUDGET_* lines.

Reason (live run 26): the supervisor hand-wrote every contract as a heredoc (TASK_ID, ATTEMPT,
AGENT_ROLE, STORY_DIR, SCOPE_GLOBS, BASE_REF and five BUDGET_* lines), 9 times a run, each one
model output re-read on every later turn. The keys and the budget are facts the harness owns.

    python3 scripts/test/budget-contract.test.py
"""
import atexit
import json
import os
import shutil
import subprocess
import tempfile

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugin", "bin")
T = tempfile.mkdtemp(prefix="aloop-contract-")
atexit.register(shutil.rmtree, T, ignore_errors=True)
ENV = {k: v for k, v in os.environ.items() if not k.startswith(("AGENTIC_", "ALADDIN_")) and k != "SKIP_HOOKS"}
ENV["HOME"] = os.path.join(T, "home")
n = 0


def ok(msg):
    global n
    n += 1
    print("ok - " + msg)


def budget(*args):
    return subprocess.run([os.path.join(BIN, "budget"), "contract"] + list(args), cwd=T, env=ENV,
                          capture_output=True, text=True)


def kv(task):
    out = {}
    for line in open(os.path.join(T, ".aladdin", "state", "tasks", task + ".env")):
        k, _, v = line.rstrip("\n").partition("=")
        out[k] = v.strip('"')
    return out


subprocess.run(["git", "init", "-q"], cwd=T, check=True)
subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init"], cwd=T, check=True)
head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=T, capture_output=True, text=True).stdout.strip()
os.makedirs(os.path.join(T, ".aladdin"))
os.makedirs(os.path.join(T, "docs", "agents"))
open(os.path.join(T, "docs", "agents", "defaults.md"), "w").write(
    "---\ntype: agentic-defaults\n---\n```conf\nAGENTIC_MODEL_WORKER=haiku\nAGENTIC_TOKENS_HARD=200000\n```\n")
story = os.path.join(T, "docs", "agents", "sprint1", "s1-01-count")
os.makedirs(story)
open(os.path.join(story, "tasks.md"), "w").write(
    "---\ntype: tasks\n---\n## T1 — count\nbudget: tokens_hard=90000 time_hard=12\n\n## T2 — top\n")

r = budget("S1-01-T2", "--role", "red-worker", "--story", "docs/agents/sprint1/s1-01-count", "--scope", "tests/**")
assert r.returncode == 0, r
c = kv("S1-01-T2")
assert c["TASK_ID"] == "S1-01-T2" and c["ATTEMPT"] == "1" and c["AGENT_ROLE"] == "red-worker", c
assert c["STORY_DIR"] == os.path.realpath(story) and c["SCOPE_GLOBS"] == "tests/**" and c["BASE_REF"] == head, c
assert c["BUDGET_TOKENS_HARD"] == "200000" and c["BUDGET_TOKEN_METER"] == "fresh", c
assert len(r.stdout.strip().splitlines()) == 1 and "S1-01-T2.env" in r.stdout, r.stdout
ok("writes TASK_ID, ATTEMPT=1, role, absolute STORY_DIR, scope, BASE_REF=HEAD and defaults.md's budget; prints one line")

r = budget("S1-01-T1", "--role", "green-worker", "--story", story, "--attempt", "2", "--symbols", "count_words Counter")
assert r.returncode == 0, r
c = kv("S1-01-T1")
assert c["ATTEMPT"] == "2" and c["SCAFFOLD_SYMBOLS"] == "count_words Counter", c
assert c["BUDGET_TOKENS_HARD"] == "90000" and c["BUDGET_TIME_HARD_MIN"] == "12", c
assert "tasks.md" in r.stdout, r.stdout
ok("the story's tasks.md `budget:` line for the task id's Tn wins; --attempt and --symbols are written")

r = budget("S1-SR-W1", "--role", "structural-reviewer", "--story", os.path.dirname(story))
assert r.returncode == 0 and kv("S1-SR-W1")["STORY_DIR"] == os.path.realpath(os.path.dirname(story)), r
ok("a whole-sprint role is contracted on the sprint dir")

gate = subprocess.run([os.path.join(BIN, "gate-dispatch")], cwd=T, env=ENV, capture_output=True, text=True, input=json.dumps(
    {"session_id": "s", "cwd": T, "tool_name": "Agent", "tool_input": {
        "subagent_type": "aloop:red-worker", "prompt": "TASK_ID: S1-01-T2\nDo RED.", "isolation": "worktree", "model": "haiku"}}))
assert gate.returncode == 0, gate
ok("gate-dispatch accepts a dispatch on the written contract")

before = sorted(os.listdir(os.path.join(T, ".aladdin", "state", "tasks")))
for args, why in (
        (("S1-01-T3", "--role", "red-worker", "--story", "docs/agents/sprint1/nope"), "does not exist"),
        (("S1-01-T3", "--role", "red-worker", "--story", ".aladdin"), "docs/agents/sprintN"),
        (("S1-01-T3", "--role", "wizard", "--story", story), "role"),
        (("S1-01-T3", "--story", story), "role"),
        (("../x", "--role", "red-worker", "--story", story), "TASK_ID"),
        (("S1-01-T3", "--role", "red-worker", "--story", story, "--attempt", "x"), "attempt")):
    r = budget(*args)
    assert r.returncode == 64 and why in r.stderr, (args, r)
assert sorted(os.listdir(os.path.join(T, ".aladdin", "state", "tasks"))) == before
ok("a missing or run-data story dir, an unknown role, a bad TASK_ID or attempt is refused (exit 64) and writes nothing")

print("%d checks passed" % n)
