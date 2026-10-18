#!/usr/bin/env python3
"""ROADMAP §3.3: every model role is metered, not only the three workers. Planners,
reviewers and final-gate run against their own limit profile (overridable in
defaults.md), observe-only: crossings become events, the agent is never told, stopped,
or reported to the supervisor as a KILL.
    python3 scripts/test/budget-roles.test.py
"""
import json
import os
import subprocess
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-roles-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"))
ENV.pop("AGENTIC_HOST", None)
PASS = [0]
SEEN = {}


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def sh(*args, cwd=T, inp=None):
    return subprocess.run(args, cwd=cwd, env=ENV, capture_output=True, text=True, input=inp, check=True).stdout.strip()


def state(agent):
    return json.load(open(os.path.join(T, ".agentic", "budget", agent + ".json")))


def events(agent):
    p = os.path.join(T, ".agentic", "logs", "events.jsonl")
    return [json.loads(l)["event"] for l in open(p) if json.loads(l)["agent_id"] == agent] if os.path.exists(p) else []


def call(agent, role, tokens, mode="post"):
    sub = os.path.join(T, "cc", "sess", "subagents", "agent-%s.jsonl" % agent)
    os.makedirs(os.path.dirname(sub), exist_ok=True)
    with open(sub, "a") as f:
        f.write(json.dumps({"timestamp": "2026-10-17T10:00:00Z", "message": {"id": "m%d" % tokens, "model": "claude-opus-5-5",
                            "usage": {"input_tokens": tokens - SEEN.get(agent, 0), "output_tokens": 0}}}) + "\n")
    SEEN[agent] = tokens
    payload = {"hook_event_name": {"post": "PostToolUse", "pre": "PreToolUse", "stop": "SubagentStop"}[mode],
               "session_id": "sess", "transcript_path": os.path.join(T, "cc", "sess.jsonl"), "cwd": T,
               "agent_id": agent, "agent_type": "agentic-agile:" + role, "tool_name": "Read",
               "tool_input": {"file_path": os.path.join(T, "x")}, "tool_response": "ok"}
    return sh(os.path.join(BIN, "budget"), "hook", mode, inp=json.dumps(payload))


sh("git", "init", "-q"); sh("git", "config", "user.email", "a@a"); sh("git", "config", "user.name", "A")
sh("git", "commit", "-q", "--allow-empty", "-m", "init")

# 1) a planner is metered against the planning profile, and nothing reaches it
assert call("p1", "planner", 10000) == ""
s = state("p1")
assert s["observe"] and s["budget"]["TOKENS_HARD"] == 600000 and s["budget_source"] == "built-in planner profile", s
assert call("p1", "planner", 350000) == "" and call("p1", "planner", 700000, mode="pre") == ""
assert events("p1") == ["task.token_soft_limit", "task.token_hard_limit"], events("p1")
assert state("p1")["tokens"] == 700000
ok("planner: metered on its profile; soft + hard crossings are events, never messages")

# 2) reviewers and final-gate have the worker-sized profile
for agent, role in (("r1", "structural-reviewer"), ("g1", "final-gate")):
    call(agent, role, 260000)
    assert state(agent)["budget"]["TOKENS_HARD"] == 250000 and events(agent)[-1] == "task.token_hard_limit"
ok("structural-reviewer and final-gate: 150k/250k profile, hard crossing recorded")

# 3) the supervisor's watch never KILLs an observe-only role
out = subprocess.run([os.path.join(BIN, "budget"), "watch", "--interval", "1", "--grace", "0"], cwd=T, env=ENV,
                     capture_output=True, text=True, timeout=20).stdout
assert "KILL" not in out and "STOPPED" not in out, out
ok("budget watch ignores observe-only roles")

# 4) defaults.md overrides a role profile; SubagentStop marks it done
os.makedirs(os.path.join(T, "docs", "agents"), exist_ok=True)
open(os.path.join(T, "docs", "agents", "defaults.md"), "a").write('AGENTIC_BUDGET_ARCHIVIST="tokens_hard=90000 time_hard=15"\n')
call("a1", "archivist", 1000)
b = state("a1")["budget"]
assert b["TOKENS_HARD"] == 90000 and b["TIME_HARD_MIN"] == 15 and b["TIME_SOFT_MIN"] == 15, b
call("a1", "archivist", 1000, mode="stop")
assert state("a1")["done"] and "observe" in sh(os.path.join(BIN, "budget"), "status")
ok("AGENTIC_BUDGET_<ROLE> in defaults.md overrides the profile; stop marks it done")

print("\n%d checks passed" % PASS[0])
