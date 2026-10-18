#!/usr/bin/env python3
"""ROADMAP §3.4: a hard breach is CLASSIFIED before it is re-planned. The harness makes a
deterministic first call from facts it owns (usage classes, tool calls, tool results,
which limit tripped) and writes it, with its evidence, into the output.md block, the
event and `budget watch` / `budget classify`; the planner confirms or overrides it.
    python3 scripts/test/budget-classify.test.py
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-classify-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"))
ENV.pop("AGENTIC_HOST", None)
PASS = [0]

# budget is an extension-less script: load its functions directly for the unit checks
src = open(os.path.join(BIN, "budget")).read().replace('if __name__ == "__main__":', "if False:")
sys.path.insert(0, BIN)
B = {"__file__": os.path.join(BIN, "budget"), "__name__": "budget_under_test"}
exec(compile(src, "budget", "exec"), B)


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def transcript(name, calls, usage=(1000, 0, 0, 500)):
    """calls: [(tool, input, result)] → a Claude-shaped agent transcript."""
    p = os.path.join(T, name + ".jsonl")
    with open(p, "w") as f:
        for k, (tool, inp, res) in enumerate(calls):
            f.write(json.dumps({"message": {"id": "m%d" % k, "role": "assistant", "content": [
                {"type": "tool_use", "id": "t%d" % k, "name": tool, "input": inp}],
                "usage": dict(zip(("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
                                   "output_tokens"), usage))}}) + "\n")
            f.write(json.dumps({"message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t%d" % k, "content": res}]}}) + "\n")
    return p


def st(tokens, mins, path=None, context=50000, hard=250000):
    return {"budget": {"TOKENS_HARD": hard, "TIME_HARD_MIN": 30}, "tokens": tokens, "elapsed_min": mins,
            "transcript": path, "usage": {"context": context, "cache_read": 0} if tokens is not None else {}}


def cls(s):
    return B["classify"](s)[0]


bash = lambda c, r="ok": ("Bash", {"command": c}, r)  # noqa: E731
edit = lambda f: ("Edit", {"file_path": f, "old_string": "a", "new_string": "b"}, "ok")  # noqa: E731

assert cls(st(None, 31)) == "HOST_FAILURE"
ok("no usage and no tool calls: HOST_FAILURE")

loop = transcript("loop", [edit("src/a.rs")] + [bash("cargo build", "error[E0308]")] * 6 + [edit("src/b.rs")])
assert cls(st(260000, 12, loop)) == "AGENT_LOOPING"
ok("the same failing call over and over: AGENT_LOOPING")

net = transcript("net", [bash("curl https://api.example.test", "curl: (7) Connection refused"), edit("src/a.rs"),
                         bash("cargo test", "ECONNREFUSED 127.0.0.1:5432"), edit("src/b.rs")])
assert cls(st(260000, 12, net)) == "EXTERNAL_SERVICE"
ok("network/service errors in tool results: EXTERNAL_SERVICE")

assert cls(st(260000, 12, None, context=230000)) == "CONTEXT_PATHOLOGY"
ok("context in use near the limit: CONTEXT_PATHOLOGY")

tests = transcript("tests", [edit("src/a.rs"), bash("cargo test --all"), bash("ls"), bash("cargo test -p x")])
assert cls(st(40000, 31, tests)) == "TEST_SUITE_SLOW"
slow = transcript("slow", [edit("src/a.rs"), bash("cargo build --release"), bash("npm install"), bash("ls")])
assert cls(st(40000, 31, slow)) == "ENVIRONMENT_SLOW"
ok("time breach at low tokens: TEST_SUITE_SLOW when test runs dominate, else ENVIRONMENT_SLOW")

big = transcript("big", [edit("src/m%d.rs" % k) for k in range(12)] + [bash("cargo test")])
assert cls(st(260000, 12, big)) == "TASK_TOO_LARGE"
ok("steady progress until the token limit: TASK_TOO_LARGE")

# end to end: the class lands in the output.md block, the event and `budget classify`
def sh(*args, inp=None):
    return subprocess.run(args, cwd=T, env=ENV, capture_output=True, text=True, input=inp, check=True).stdout.strip()


sh("git", "init", "-q"); sh("git", "config", "user.email", "a@a"); sh("git", "config", "user.name", "A")
sh("git", "commit", "-q", "--allow-empty", "-m", "init")
story = os.path.join(T, "docs", "agents", "sprint1", "s1-01-x")
os.makedirs(story)
wt = sh(os.path.join(BIN, "task-worktree"), "add", "S1-01-T1")
open(os.path.join(wt, ".agentic", "task.env"), "w").write(
    "TASK_ID=S1-01-T1\nATTEMPT=1\nAGENT_ROLE=green-worker\nSTORY_DIR=%s\nBUDGET_TOKENS_HARD=20000\n" % story)
sub = os.path.join(T, "cc", "sess", "subagents", "agent-w1.jsonl")
os.makedirs(os.path.dirname(sub))
os.rename(transcript("w1", [bash("cargo build", "error")] * 8, usage=(3000, 0, 0, 0)), sub)
payload = {"hook_event_name": "PostToolUse", "session_id": "sess", "transcript_path": os.path.join(T, "cc", "sess.jsonl"),
           "cwd": T, "agent_id": "w1", "agent_type": "agentic-agile:green-worker", "tool_name": "Bash",
           "tool_input": {"command": "git -C %s status" % wt}, "tool_response": "ok"}
assert '"continue": false' in sh(os.path.join(BIN, "budget"), "hook", "post", inp=json.dumps(payload))
out = open(os.path.join(story, "output.md")).read()
assert "| breach class | AGENT_LOOPING | the same Bash call repeated 8 of 8 times" in out, out
ev = [json.loads(l) for l in open(os.path.join(T, ".agentic", "logs", "events.jsonl"))]
assert ev[-1]["event"] == "task.token_hard_limit" and ev[-1]["data"]["breach_class"] == "AGENT_LOOPING" and ev[-1]["schema"] == "aladdin.event/1", ev[-1]
assert "AGENT_LOOPING" in sh(os.path.join(BIN, "budget"), "classify", "S1-01-T1")
ok("hard stop: class + evidence in output.md, the event, and `budget classify`")

print("\n%d checks passed" % PASS[0])
