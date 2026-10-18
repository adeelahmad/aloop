#!/usr/bin/env python3
"""ROADMAP §4.7: the normalized `aladdin.event/1` stream. Every harness event shares one
envelope (ids, parent/caused_by, role, host, model, authority, decision, refs, data),
references transcripts without copying them, rejects unknown event types, and answers a
lineage query for a task: worktree → limits/gates → merged or discarded.
    python3 scripts/test/events.test.py
"""
import json
import os
import subprocess
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-events-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"))
ENV.pop("AGENTIC_HOST", None)
PASS = [0]


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def run(*args, cwd=T, env=None):
    return subprocess.run(args, cwd=cwd, env=env or ENV, capture_output=True, text=True)


def sh(*args, cwd=T, env=None):
    r = run(*args, cwd=cwd, env=env)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout.strip()


def events():
    return [json.loads(l) for l in open(os.path.join(T, ".agentic", "logs", "events.jsonl"))]


sh("git", "init", "-q"); sh("git", "config", "user.email", "a@a"); sh("git", "config", "user.name", "A")
sh("git", "commit", "-q", "--allow-empty", "-m", "init")
EV = os.path.join(BIN, "events")

# 1) the envelope
root = sh(EV, "emit", "task.dispatched", "--task", "S1-02-T3", "--attempt", "1", "--role", "green-worker",
          "--ref", "transcript=/x/agent.jsonl", "--data", "wave=2")
child = sh(EV, "emit", "decision.recorded", "--task", "S1-02-T3", "--parent", root, "--caused-by", root,
           "--data", "reason=split into vertical slices")
e = events()
assert all(x["schema"] == "aladdin.event/1" and len(x["event_id"]) == 32 for x in e)
assert e[0]["ids"] == {"sprint": "S1", "story": "S1-02", "task": "S1-02-T3", "attempt": "1"}, e[0]["ids"]
assert e[0]["refs"] == {"transcript": "/x/agent.jsonl"} and e[0]["data"] == {"wave": "2"} and e[0]["host"] == "claude"
assert e[1]["parent_event_id"] == root and e[1]["caused_by"] == root and e[1]["event_id"] == child
for k in ("parent_event_id", "caused_by", "role", "host", "model", "authority", "decision", "refs", "data"):
    assert k in e[0], k
ok("aladdin.event/1 envelope: ids derived from the task, parent/caused_by, refs, data")

# 2) unknown event types are refused
r = run(EV, "emit", "task.exploded", "--task", "S1-02-T3")
assert r.returncode == 64 and "unknown event type" in r.stderr
ok("unknown event types are refused (exit 64)")

# 3) worktree lifecycle: created → discarded (no commits) / merged (commits in HEAD)
wt = sh(os.path.join(BIN, "task-worktree"), "add", "S1-02-T4")
sh(os.path.join(BIN, "task-worktree"), "remove", "S1-02-T4")
wt = sh(os.path.join(BIN, "task-worktree"), "add", "S1-02-T5")
open(os.path.join(wt, "f.txt"), "w").write("x\n")
sh("git", "-C", wt, "add", "f.txt"); sh("git", "-C", wt, "commit", "-q", "-m", "work")
sh("git", "merge", "-q", "--ff-only", "agentic/S1-02-T5")
sh(os.path.join(BIN, "task-worktree"), "remove", "S1-02-T5")
seq = [(x["event"], x["ids"].get("task")) for x in events() if x["event"].startswith("worktree.")]
assert seq == [("worktree.created", "S1-02-T4"), ("worktree.discarded", "S1-02-T4"),
               ("worktree.created", "S1-02-T5"), ("worktree.merged", "S1-02-T5")], seq
ok("task-worktree emits worktree.created and worktree.merged / worktree.discarded")

# 4) gate verdicts land in the stream (and a self-check does not)
gate_env = dict(ENV, GATE_NAME="gate-test", TASK_ID="S1-02-T3", ATTEMPT="1", AGENT_ROLE="green-worker")
script = '. "%s/_gatelib.sh"; exit %s'
run("bash", "-c", script % (BIN, 0), env=gate_env)
run("bash", "-c", script % (BIN, 2), env=gate_env)
run("bash", "-c", script % (BIN, 2), env=dict(gate_env, AGENTIC_SELFCHECK="1"))
gates = [(x["event"], x["data"]["gate"]) for x in events() if x["event"].startswith("gate.")]
assert gates == [("gate.passed", "gate-test"), ("gate.failed", "gate-test")], gates
ok("gate verdicts are gate.passed / gate.failed events; self-checks are not")

# 5) lineage: the task's chain, children indented under their parent, refs not transcripts
lin = sh(EV, "lineage", "S1-02-T3")
lines = lin.splitlines()
assert "task.dispatched" in lines[0] and "transcript=/x/agent.jsonl" in lines[0]
assert lines[1].startswith("  ") and "decision.recorded" in lines[1]
assert any("gate.failed" in l for l in lines) and "agent.jsonl" in lin
assert run(EV, "lineage", "S9-09-T9").returncode == 1
q = sh(EV, "query", "--event", "worktree.merged")
assert "S1-02-T5" in q and "S1-02-T4" not in q
ok("events lineage / query answer from the stream alone")

# 6) authority + decisions: a decision without an authority is refused; with one it links
r = run(EV, "decide", "split", "--task", "S1-02-T3", "--reason", "too large")
assert r.returncode == 64 and "no authority" in r.stderr, r.stderr
auth = sh(EV, "authority", "--initiated-by", "adeel", "--mode", "autonomous", "--scope", "sprint 1 as approved",
          "--override", "TOKENS_HARD=300000")
assert run(EV, "authority", "--initiated-by", "x", "--mode", "yolo", "--scope", "s").returncode == 64
a = json.load(open(os.path.join(T, ".agentic", "state", "authority.json")))
assert a["event_id"] == auth and a["autonomy_mode"] == "autonomous" and a["overrides"] == {"TOKENS_HARD": "300000"}
d = sh(EV, "decide", "split", "--task", "S1-02-T3", "--reason", "TASK_TOO_LARGE: two behaviours in one task",
       "--evidence", ".agentic/logs/events.jsonl#task.token_hard_limit", "--parent", root)
assert run(EV, "decide", "split", "--reason", "line one\nline two").returncode == 64
assert run(EV, "decide", "vibes", "--reason", "x").returncode == 64
ev = [x for x in events() if x["event_id"] == d][0]
assert ev["decision"] == {"kind": "split", "reason": "TASK_TOO_LARGE: two behaviours in one task",
                          "evidence_refs": [".agentic/logs/events.jsonl#task.token_hard_limit"], "authority_ref": auth}
assert ev["caused_by"] == auth and ev["parent_event_id"] == root
assert "split: TASK_TOO_LARGE: two behaviours in one task" in sh(EV, "lineage", "S1-02-T3")
ok("authority record + one-line decisions with evidence and authority refs; multi-line or unknown kinds refused")

print("\n%d checks passed" % PASS[0])
