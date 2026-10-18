#!/usr/bin/env python3
"""ROADMAP §3.5: no post-kill narration. At the hard limit the worker is stopped at once
(no "one chance to write your report"), every further call is refused (a report write
too), and the HARNESS appends a gate-valid `status: re-plan` block to output.md and
records `task.token_hard_limit` / `task.time_hard_timeout`.
    python3 scripts/test/budget-hard-stop.test.py
"""
import json
import os
import subprocess
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-hard-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"))
ENV.pop("AGENTIC_HOST", None)
PASS = [0]
SEEN = {}   # tokens = the fresh-input charge so far: each record adds the delta


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def sh(*args, cwd=T, inp=None, check=True, env=ENV):
    r = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, input=inp, check=check)
    return r if not check else r.stdout.strip()


def state(agent):
    return json.load(open(os.path.join(T, ".aladdin", "budget", agent + ".json")))


def events():
    p = os.path.join(T, ".aladdin", "logs", "events.jsonl")
    return [json.loads(l) for l in open(p)] if os.path.exists(p) else []


def call(agent, wt, tokens, mode="post", tool="Bash", tool_input=None):
    sub = os.path.join(T, "cc", "sess", "subagents", "agent-%s.jsonl" % agent)
    os.makedirs(os.path.dirname(sub), exist_ok=True)
    with open(sub, "a") as f:
        f.write(json.dumps({"timestamp": "2026-10-17T10:00:00Z", "message": {"id": "m%d" % tokens, "model": "claude-sonnet-5",
                            "usage": {"input_tokens": tokens - SEEN.get(agent, 0), "output_tokens": 0}}}) + "\n")
        SEEN[agent] = tokens
    payload = {"hook_event_name": "PostToolUse" if mode == "post" else "PreToolUse", "session_id": "sess",
               "transcript_path": os.path.join(T, "cc", "sess.jsonl"), "cwd": T, "agent_id": agent,
               "agent_type": "agentic-agile:green-worker", "tool_name": tool,
               "tool_input": tool_input or {"command": "git -C %s status" % wt}, "tool_response": "ok"}
    out = sh(os.path.join(BIN, "budget"), "hook", mode, inp=json.dumps(payload))
    return json.loads(out) if out else {}


sh("git", "init", "-q"); sh("git", "config", "user.email", "a@a"); sh("git", "config", "user.name", "A")
sh("git", "commit", "-q", "--allow-empty", "-m", "init")
story = os.path.join(T, "docs", "agents", "sprint1", "s1-01-x")
os.makedirs(story)
open(os.path.join(story, "init.md"), "w").write("---\ntype: init\n---\n")
out_md = os.path.join(story, "output.md")


def worktree(task, extra=""):
    wt = sh(os.path.join(BIN, "task-worktree"), "add", task)
    open(os.path.join(wt, ".agentic", "task.env"), "w").write(
        "TASK_ID=%s\nATTEMPT=2\nAGENT_ROLE=green-worker\nSTORY_DIR=%s\nBUDGET_TOKENS_HARD=200000\n%s" % (task, story, extra))
    return wt


wt = worktree("S1-01-T1")
open(os.path.join(wt, "new.txt"), "w").write("partial\n")
sh("git", "-C", wt, "add", "new.txt")

# 1) the call that crosses the hard limit stops the worker at once — no report request
call("w1", wt, 1000)
r = call("w1", wt, 210000)
assert r.get("continue") is False, r
ctx = r["hookSpecificOutput"].get("additionalContext", "")
assert "append" not in ctx.lower() and "do nothing else" in ctx.lower(), ctx
assert state("w1")["hook_killed"]
ok("hard crossing: stopped on that call (continue:false), not asked to narrate")

# 2) the harness wrote the output.md block, gate-valid, once
text = open(out_md).read()
assert text.startswith("---\ntype: output\n---\n"), text
assert text.count("## S1-01-T1 · attempt 2 · green-worker · ") == 1 and "status: re-plan" in text, text
assert "HARD (token)" in text and "1 file changed" in text and "agent-w1.jsonl" in text, text
v = sh("bash", "-c", '. "%s/_gatelib.sh"; validate_comms' % BIN, check=False,
       env=dict(ENV, STORY_DIR=story, AGENT_ROLE="green-worker", GATE_NAME="test"))
assert v.returncode == 0, v.stderr
ok("harness-written output.md block passes validate_comms")

# 3) every later call is refused, a report write included; nothing appended twice
r = call("w1", wt, 211000, mode="pre", tool="Edit", tool_input={"file_path": out_md, "old_string": "a", "new_string": "b"})
assert r["hookSpecificOutput"].get("permissionDecision") == "deny", r
call("w1", wt, 212000)
assert open(out_md).read() == text
ev = [e["event"] for e in events()]
assert ev.count("task.token_hard_limit") == 1, ev
ok("after the stop: report writes refused too; block and task.token_hard_limit recorded once")

# 4) a time-box breach records task.time_hard_timeout
wt2 = worktree("S1-01-T2", "BUDGET_TIME_HARD_MIN=0\n")
r = call("w2", wt2, 1000)
assert r.get("continue") is False, r
assert "task.time_hard_timeout" in [e["event"] for e in events()]
assert open(out_md).read().count("status: re-plan") == 2 and "HARD (time)" in open(out_md).read()
ok("time breach: stopped, task.time_hard_timeout, harness block")

print("\n%d checks passed" % PASS[0])
