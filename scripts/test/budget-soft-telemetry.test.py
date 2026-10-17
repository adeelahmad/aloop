#!/usr/bin/env python3
"""ROADMAP §3.1: the soft TOKEN limit is telemetry only. Crossing it must not tell the
worker anything (it keeps going), must record `task.token_soft_limit` once, and must reach
the next planning cycle through stats.md / NEXT.md. The soft TIME limit still warns.
    python3 scripts/test/budget-soft-telemetry.test.py
"""
import json
import os
import subprocess
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-soft-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"))
ENV.pop("AGENTIC_HOST", None)
PASS = [0]
SEEN = {}   # tokens = the fresh-input charge so far: each record adds the delta


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def sh(*args, cwd=T, inp=None):
    return subprocess.run(args, cwd=cwd, env=ENV, capture_output=True, text=True, input=inp, check=True).stdout.strip()


def state(agent):
    return json.load(open(os.path.join(T, ".agentic", "budget", agent + ".json")))


def events():
    p = os.path.join(T, ".agentic", "logs", "events.jsonl")
    evs = [json.loads(l) for l in open(p)] if os.path.exists(p) else []
    return [e for e in evs if e["event"].startswith("task.")]      # budget events only


def call(agent, wt, tokens, mode="post"):
    sub = os.path.join(T, "cc", "sess", "subagents", "agent-%s.jsonl" % agent)
    os.makedirs(os.path.dirname(sub), exist_ok=True)
    with open(sub, "a") as f:
        f.write(json.dumps({"timestamp": "2026-10-17T10:00:00Z", "message": {"id": "m%d" % tokens, "model": "claude-sonnet-5",
                            "usage": {"input_tokens": tokens - SEEN.get(agent, 0), "output_tokens": 0}}}) + "\n")
        SEEN[agent] = tokens
    payload = {"hook_event_name": "PostToolUse" if mode == "post" else "PreToolUse", "session_id": "sess",
               "transcript_path": os.path.join(T, "cc", "sess.jsonl"), "cwd": T, "agent_id": agent,
               "agent_type": "agentic-agile:green-worker", "tool_name": "Bash",
               "tool_input": {"command": "git -C %s status" % wt}, "tool_response": "ok"}
    return sh(os.path.join(BIN, "budget"), "hook", mode, inp=json.dumps(payload))


sh("git", "init", "-q"); sh("git", "config", "user.email", "a@a"); sh("git", "config", "user.name", "A")
sh("git", "commit", "-q", "--allow-empty", "-m", "init")
story = os.path.join(T, "docs", "agents", "sprint1", "s1-01-x")
os.makedirs(story)
open(os.path.join(story, "tasks.md"), "w").write("## T1 — x\n")
wt = sh(os.path.join(BIN, "task-worktree"), "add", "S1-01-T1")
open(os.path.join(wt, ".agentic", "task.env"), "w").write(
    "TASK_ID=S1-01-T1\nATTEMPT=1\nAGENT_ROLE=green-worker\nSTORY_DIR=%s\n"
    "BUDGET_TOKENS_SOFT=100000\nBUDGET_TOKENS_HARD=400000\n" % story)

# 1) under the soft limit: nothing
assert call("w1", wt, 50000) == "" and not events()
ok("below soft: silent, no event")

# 2) past the soft token limit (well under 90% of hard): the worker is NOT told
out = call("w1", wt, 150000)
assert out == "", out
s = state("w1")
assert s.get("token_soft_limit") and not s["soft_warned"] and not s["hard"], s
ev = events()
assert len(ev) == 1 and ev[0]["event"] == "task.token_soft_limit" and ev[0]["data"]["tokens"] == 150000 \
    and ev[0]["data"]["tokens_soft"] == 100000 and ev[0]["task"] == "S1-01-T1", ev
ok("soft token crossing: worker not told, task.token_soft_limit recorded")

# 3) recorded once, worker keeps working silently
assert call("w1", wt, 200000) == "" and call("w1", wt, 210000, mode="pre") == "" and len(events()) == 1
ok("event emitted once; later calls stay silent")

# 4) the hard limit is unchanged: 90% warning, then the stop
assert "FINAL BUDGET WARNING" in call("w1", wt, 365000)
assert "HARD BUDGET" in call("w1", wt, 400000)
ok("hard-limit behaviour unchanged")

# 5) the soft TIME limit still warns (graceful wrap-up stays for time)
open(os.path.join(wt, ".agentic", "task.env"), "a").write("BUDGET_TIME_SOFT_MIN=0\n")
call("w1", wt, 1, mode="stop")
os.remove(os.path.join(T, ".agentic", "budget", "w1.json"))
os.remove(os.path.join(wt, ".agentic", "budget-exceeded"))
out = call("w2", wt, 1000)
assert "SOFT TIME LIMIT" in out and state("w2")["soft_warned"], out
ok("soft time limit still tells the worker to wrap up")

# 6) the next planning cycle sees it: stats.md + NEXT.md
st = state("w2")
st.update({"token_soft_limit": st["start"], "tokens": 150000, "done": st["start"]})
json.dump(st, open(os.path.join(T, ".agentic", "budget", "w2.json"), "w"))
sh(os.path.join(BIN, "stats"), "sprint-close", "1")
stats = open(os.path.join(T, "docs", "agents", "sprint1", "stats.md")).read()
nxt = open(os.path.join(T, "docs", "agents", "NEXT.md")).read()
assert "| Attempts past the soft token limit (telemetry) | 1 |" in stats, stats
assert "Planner input: tasks that ran past the soft token limit" in nxt and "S1-01-T1 green-worker" in nxt, nxt
ok("stats.md counts it and NEXT.md hands it to the planner")

print("\n%d checks passed" % PASS[0])
