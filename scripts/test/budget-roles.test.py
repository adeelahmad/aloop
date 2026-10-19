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
    return json.load(open(os.path.join(T, ".aladdin", "budget", agent + ".json")))


def events(agent):
    p = os.path.join(T, ".aladdin", "logs", "events.jsonl")
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

# 5) an observed role dispatched into a host worktree gets the contract copy too, so its own
# `selfcheck` finds the task (live runs 15–17: every structural reviewer's first self-check
# blocked "STORY_DIR unset" because only the three workers got the copy)
wt = os.path.join(T, ".claude", "worktrees", "agent-r2")
sh("git", "worktree", "add", "-q", "-b", "worktree-agent-r2", wt)
os.makedirs(os.path.join(T, ".agentic", "state", "tasks"), exist_ok=True)
story = os.path.join(T, "docs", "agents", "sprint1"); os.makedirs(story, exist_ok=True)
open(os.path.join(T, ".agentic", "state", "tasks", "S1-SR-W1.env"), "w").write(
    "TASK_ID=S1-SR-W1\nATTEMPT=1\nAGENT_ROLE=structural-reviewer\nSTORY_DIR=%s\n" % story)
sub = os.path.join(T, "cc", "sess", "subagents", "agent-r2.jsonl"); os.makedirs(os.path.dirname(sub), exist_ok=True)
open(sub, "w").write(json.dumps({"type": "user", "message": {"role": "user", "content": "TASK_ID: S1-SR-W1\nReview wave 1."}}) + "\n")
payload = {"hook_event_name": "PreToolUse", "session_id": "sess", "transcript_path": os.path.join(T, "cc", "sess.jsonl"),
           "cwd": wt, "agent_id": "r2", "agent_type": "agentic-agile:structural-reviewer", "tool_name": "Bash",
           "tool_input": {"command": "selfcheck"}}
assert sh(os.path.join(BIN, "budget"), "hook", "pre", inp=json.dumps(payload)) == ""
copy = os.path.join(wt, ".aladdin", "task.env")      # a fresh project: markers live in .aladdin/
assert os.path.isfile(copy) and "TASK_ID=S1-SR-W1" in open(copy).read(), copy
s = state("r2")
assert s["observe"] and s["task"] == "S1-SR-W1" and s["story_dir"] == story and os.path.realpath(s["worktree"]) == os.path.realpath(wt), s
ok("an observed role in a host worktree gets the contract copy (selfcheck finds its task); state names the task")

# 6) Claude Code prepends a session-context attachment line (renderedRole "user", no TASK_ID)
# to a sub-agent transcript; the dispatch prompt is a later user line. The worker must still be
# bound to its task (live run 18: the first RED self-check blocked "STORY_DIR unset"), and the
# story's output.md must exist with its frontmatter before the worker appends its report (run 18:
# 4 blocks "output.md missing top frontmatter" after a worker's `cat >>` created the file bare).
wt = os.path.join(T, ".claude", "worktrees", "agent-w6")
sh("git", "worktree", "add", "-q", "-b", "worktree-agent-w6", wt)
story6 = os.path.join(T, "docs", "agents", "sprint1", "s1-02-top-n"); os.makedirs(story6, exist_ok=True)
open(os.path.join(story6, "init.md"), "w").write("---\ntype: init\nstory: S1-02\n---\n## S1-02-T3 · attempt 1 · red-worker · now\n")
open(os.path.join(T, ".agentic", "state", "tasks", "S1-02-T3.env"), "w").write(
    "TASK_ID=S1-02-T3\nATTEMPT=1\nAGENT_ROLE=red-worker\nSTORY_DIR=%s\n" % story6)
sub = os.path.join(T, "cc", "sess", "subagents", "agent-w6.jsonl")
open(sub, "w").write(
    json.dumps({"type": "attachment", "attachment": {"type": "session_context", "context": {"userEmail": "x"}},
                "renderedRole": "user", "userType": "external"}) + "\n" +
    json.dumps({"type": "user", "message": {"role": "user", "content": "TASK_ID: S1-02-T3\nWrite the T3 tests."}}) + "\n")
payload.update(cwd=wt, agent_id="w6", agent_type="agentic-agile:red-worker")
assert sh(os.path.join(BIN, "budget"), "hook", "pre", inp=json.dumps(payload)) == ""
copy = os.path.join(wt, ".aladdin", "task.env")
assert os.path.isfile(copy) and "TASK_ID=S1-02-T3" in open(copy).read(), copy
out6 = os.path.join(story6, "output.md")
assert os.path.isfile(out6) and open(out6).read() == "---\ntype: output\nstory: S1-02\n---\n", out6
open(out6, "a").write("## S1-02-T3 · attempt 1 · red-worker · now\n")
sh(os.path.join(BIN, "budget"), "hook", "pre", inp=json.dumps(payload))
assert open(out6).read().endswith("red-worker · now\n"), "an existing output.md is never rewritten"
ok("TASK_ID is read past a session-context line; the story's output.md is seeded with its frontmatter once")

# 7) a structural reviewer or final gate dispatched WITHOUT a worktree runs in the main tree, where
# nothing ever copied the contract (live run 19: its self-check blocked "STORY_DIR unset"). The
# copy goes to the main tree's marker dir for the dispatch and is removed when the agent stops.
sprint = os.path.join(T, "docs", "agents", "sprint1")
os.remove(copy := os.path.join(T, ".claude", "worktrees", "agent-r2", ".aladdin", "task.env"))   # case 5's reviewer is gone
open(os.path.join(T, ".agentic", "state", "tasks", "S1-SR-W3.env"), "w").write(
    "TASK_ID=S1-SR-W3\nATTEMPT=1\nAGENT_ROLE=structural-reviewer\nSTORY_DIR=%s\n" % sprint)
sub = os.path.join(T, "cc", "sess", "subagents", "agent-r7.jsonl")
open(sub, "w").write(json.dumps({"type": "user", "message": {"role": "user", "content": "TASK_ID: S1-SR-W3\nReview wave 2."}}) + "\n")
payload.update(cwd=T, agent_id="r7", agent_type="agentic-agile:structural-reviewer")
assert sh(os.path.join(BIN, "budget"), "hook", "pre", inp=json.dumps(payload)) == ""
cands = [os.path.join(T, h, "task.env") for h in (".aladdin", ".agentic")]   # this project keeps .agentic/
main_copy = next((c for c in cands if os.path.isfile(c)), cands[0])
assert os.path.isfile(main_copy) and "TASK_ID=S1-SR-W3" in open(main_copy).read(), main_copy
assert state("r7")["task"] == "S1-SR-W3" and state("r7")["story_dir"] == sprint
payload["hook_event_name"] = "SubagentStop"
sh(os.path.join(BIN, "budget"), "hook", "stop", inp=json.dumps(payload))
assert not os.path.exists(main_copy), "the main-tree copy is removed when the observed agent stops"
ok("an observed role in the main tree gets the contract copy for its dispatch; stop removes it")

print("\n%d checks passed" % PASS[0])
