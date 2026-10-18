#!/usr/bin/env python3
"""ROADMAP §3.2: the token meter keeps classes apart. The default charge is fresh input +
output. Cache read and cache write are recorded as their own classes and never folded
into context size. The legacy `context` meter stays available per project or per task.
    python3 scripts/test/budget-meter.test.py
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
sys.path.insert(0, BIN)
import _usage  # noqa: E402

T = tempfile.mkdtemp(prefix="agentic-meter-")
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


# A cached session, the common shape: 10 calls, each re-reading a ~190k cached prefix.
def cached_calls(agent, n=10):
    sub = os.path.join(T, "cc", "sess", "subagents", "agent-%s.jsonl" % agent)
    os.makedirs(os.path.dirname(sub), exist_ok=True)
    with open(sub, "w") as f:
        for i in range(n):
            f.write(json.dumps({"timestamp": "2026-10-17T10:00:%02dZ" % i, "message": {
                "id": "m%d" % i, "model": "claude-sonnet-5", "usage": {
                    "input_tokens": 2000, "cache_read_input_tokens": 190000 + i * 1000,
                    "cache_creation_input_tokens": 1000, "output_tokens": 1000}}}) + "\n")
    return sub


# 1) _usage.classes keeps each class separate; context is the LAST call's, not a sum
c = _usage.classes(cached_calls("u1"))
assert c.pop("accuracy") == "exact" and c.pop("unreported") == [], c
assert c == {"in": 20000, "cache_read": sum(190000 + i * 1000 for i in range(10)), "cache_write": 10000,
             "out": 10000, "context": 2000 + 199000 + 1000}, c
assert _usage.charge(c) == 30000 and _usage.charge(c, "context") == 202000 + 10000
ok("classes kept apart; fresh charge 30k vs context charge 212k for the same log")

# 2) the hook charges fresh+output by default and records the classes
sh("git", "init", "-q"); sh("git", "config", "user.email", "a@a"); sh("git", "config", "user.name", "A")
sh("git", "commit", "-q", "--allow-empty", "-m", "init")
story = os.path.join(T, "docs", "agents", "sprint1", "s1-01-x")
os.makedirs(story)


def worktree(task, extra=""):
    wt = sh(os.path.join(BIN, "task-worktree"), "add", task)
    open(os.path.join(wt, ".agentic", "task.env"), "w").write(
        "TASK_ID=%s\nATTEMPT=1\nAGENT_ROLE=green-worker\nSTORY_DIR=%s\n%s" % (task, story, extra))
    return wt


def call(agent, wt):
    cached_calls(agent)
    payload = {"hook_event_name": "PostToolUse", "session_id": "sess", "transcript_path": os.path.join(T, "cc", "sess.jsonl"),
               "cwd": T, "agent_id": agent, "agent_type": "agentic-agile:green-worker", "tool_name": "Bash",
               "tool_input": {"command": "git -C %s status" % wt}, "tool_response": "ok"}
    return sh(os.path.join(BIN, "budget"), "hook", "post", inp=json.dumps(payload))


wt = worktree("S1-01-T1")
call("w1", wt)
s = state("w1")
assert s["tokens"] == 30000 and s["budget"]["TOKEN_METER"] == "fresh", s
assert s["usage"]["cache_read"] > 1900000 and s["usage"]["context"] == 202000, s["usage"]
assert not s.get("token_soft_limit") and not s["hard"], s
ok("default meter: a 2M-cache-read session charges 30k, under the 150k soft limit")

# 3) the legacy context meter, per task: the same log is past the soft limit
wt2 = worktree("S1-01-T2", "BUDGET_TOKEN_METER=context\n")
call("w2", wt2)
s = state("w2")
assert s["tokens"] == 212000 and s["budget"]["TOKEN_METER"] == "context" and s.get("token_soft_limit"), s
ok("BUDGET_TOKEN_METER=context: context + output (212k) is charged")

# 4) resolve and status show the meter
assert "BUDGET_TOKEN_METER=fresh" in sh(os.path.join(BIN, "budget"), "resolve")
st = sh(os.path.join(BIN, "budget"), "status")
assert "30.0k/250.0k f" in st and "212.0k/250.0k c" in st, st
ok("budget resolve / status show the meter in force")

# 5) usage is exact or names what the host does not report (conformance 12)
roll = os.path.join(T, "rollout.jsonl")
with open(roll, "w") as f:
    for i, (inp, cached, out) in enumerate(((1000, 0, 100), (3000, 1500, 300))):
        f.write(json.dumps({"timestamp": "2026-10-17T10:00:0%dZ" % i, "type": "event_msg", "payload": {
            "type": "token_count", "info": {"total_token_usage": {"input_tokens": inp, "cached_input_tokens": cached,
                                                                  "output_tokens": out},
                                            "last_token_usage": {"input_tokens": inp}}}}) + "\n")
c = _usage.classes(roll)
assert c["accuracy"] == "exact" and c["unreported"] == ["cache_write"] and c["in"] == 1500 and c["cache_read"] == 1500, c
ok("usage is exact; a class the host never reports (Codex cache write) is named, not zero-filled silently")

print("\n%d checks passed" % PASS[0])
