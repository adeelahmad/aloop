#!/usr/bin/env python3
"""End-to-end test of plugin/hooks/hooks.json under CODEX semantics — no Codex, no tokens.

Emulates Codex's hook runner (codex-rs/hooks): reads the plugin's hooks.json, matches
groups the way Codex does (`*`/empty = all; `A|B` exact alternation; `apply_patch`
also matches `Write`/`Edit`; `spawn_agent` also matches `Agent`; agent_type for
SubagentStart/Stop), runs each command with `sh -c` and CLAUDE_PLUGIN_ROOT set (Codex
exports it for plugin hooks), and feeds Codex-shaped payloads (turn_id, rollout
transcript paths, apply_patch `{command: <patch>}`).   python3 scripts/test/codex-hooks.test.py
"""
import atexit
import json
import os
import shutil
import re
import subprocess
import sys
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
HOOKS = json.load(open(os.path.join(ROOT, "hooks", "hooks.json")))["hooks"]
ALIASES = {"apply_patch": ["Write", "Edit"], "spawn_agent": ["Agent"]}
T = tempfile.mkdtemp(prefix="agentic-codex-")
atexit.register(shutil.rmtree, T, ignore_errors=True)  # a leaked dir held ~500MB
ENV = dict(os.environ, HOME=os.path.join(T, "home"), CLAUDE_PLUGIN_ROOT=ROOT, PLUGIN_ROOT=ROOT,
           CLAUDE_PLUGIN_DATA=os.path.join(T, "pdata"), CODEX_HOME=os.path.join(T, "codex"),
           AGENTIC_NO_AUTOINSTALL="1")  # no background cargo build into the temp dir (it filled the disk)
ENV.pop("AGENTIC_HOST", None)
PASS = [0]


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def sh(*args, cwd=T):
    return subprocess.run(args, cwd=cwd, env=ENV, capture_output=True, text=True, check=True).stdout.strip()


def matches(matcher, names):
    if matcher in ("", "*"):
        return True
    if re.fullmatch(r"[A-Za-z0-9_|:-]+", matcher):
        return any(n in matcher.split("|") for n in names)
    return any(re.search(matcher, n) for n in names)


def fire(event, payload):
    """Run every matching hook like Codex: returns [(exit, stdout, stderr)]."""
    payload = dict(payload, hook_event_name=event, turn_id="turn-1", cwd=payload.get("cwd", T))
    key = payload.get("tool_name") if "Tool" in event else payload.get("agent_type")
    names = [key or ""] + ALIASES.get(key or "", [])
    out = []
    for group in HOOKS.get(event, []):
        if event not in ("UserPromptSubmit", "Stop") and not matches(group.get("matcher", ""), names):
            continue
        for h in group["hooks"]:
            r = subprocess.run(["sh", "-c", h["command"]], input=json.dumps(payload), cwd=payload["cwd"],
                               env=ENV, capture_output=True, text=True, timeout=120)
            out.append((r.returncode, r.stdout.strip(), r.stderr.strip()))
    return out


def denied(results):
    for code, so, se in results:
        if code == 2:
            return se or "blocked"
        for line in so.splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            h = d.get("hookSpecificOutput") or {}
            if h.get("permissionDecision") == "deny":
                return h.get("permissionDecisionReason", "deny")
    return None


def context(results):
    return "\n".join(json.loads(l).get("hookSpecificOutput", {}).get("additionalContext", "")
                     for _, so, _ in results for l in so.splitlines() if l.startswith("{"))


def rollout(thread, rows):
    d = os.path.join(ENV["CODEX_HOME"], "sessions", "2026", "09", "23")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "rollout-2026-09-23T10-00-00-%s.jsonl" % thread)
    tot = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
    with open(p, "w") as f:
        f.write(json.dumps({"timestamp": "2026-09-23T10:00:00Z", "type": "turn_context", "payload": {"model": "gpt-5.5"}}) + "\n")
        for i, (inp, out) in enumerate(rows):
            tot = {"input_tokens": tot["input_tokens"] + inp, "cached_input_tokens": 0, "output_tokens": tot["output_tokens"] + out}
            f.write(json.dumps({"timestamp": "2026-09-23T10:00:%02dZ" % (i + 1), "type": "event_msg", "payload": {
                "type": "token_count", "info": {"total_token_usage": tot, "last_token_usage": {"input_tokens": inp, "output_tokens": out}}}}) + "\n")
    return p


# ── project setup (fake backends so gate-tooling passes without a Rust build) ──
os.makedirs(os.path.join(ENV["CLAUDE_PLUGIN_DATA"], "bin"))
for t in ("md-db", "ctx-symbols"):
    p = os.path.join(ENV["CLAUDE_PLUGIN_DATA"], "bin", t)
    open(p, "w").write("#!/bin/sh\nexit 0\n")
    os.chmod(p, 0o755)
sh("git", "init", "-q"); sh("git", "config", "user.email", "ada@x.io"); sh("git", "config", "user.name", "Ada")
sh("git", "commit", "-q", "--allow-empty", "-m", "init")
sh(os.path.join(BIN, "aloop-init"), "--apply", "TOKENS_SOFT=100", "TOKENS_HARD=200")
os.makedirs(os.path.join(T, "docs/agents/sprint1/s1-01-x"))
open(os.path.join(T, "docs/agents/NEXT.md"), "w").write("NEXT-MARKER\n")
main_tp = rollout("main0", [(50, 5)])
base = {"session_id": "main0", "transcript_path": main_tp}

# SessionStart: handoff + tool orientation reach the model
r = fire("SessionStart", dict(base, source="startup"))
assert "NEXT-MARKER" in context(r) and "aloop" in context(r); ok("SessionStart injects the handoff + tool orientation")

# supervisor (no agent_id) patching source mid-sprint is blocked; apply_patch matches Write|Edit
fire("PreToolUse", dict(base, tool_name="apply_patch", tool_input={"command": "*** Begin Patch\n*** Add File: docs/agents/sprint1/s1-01-x/init.md\n+x\n*** End Patch"}))
why = denied(fire("PreToolUse", dict(base, tool_name="apply_patch", tool_input={"command": "*** Begin Patch\n*** Update File: src/main.rs\n@@\n-a\n+b\n*** End Patch"})))
assert why and "production source" in why; ok("supervisor-scope blocks the supervisor's apply_patch to source")

why = denied(fire("PreToolUse", dict(base, tool_name="Bash", tool_input={"command": "git commit -m 'x\n\nCo-Authored-By: Claude <noreply@anthropic.com>'"})))
assert why and "co-author" in why.lower(); ok("commit-author blocks a Claude trailer")

# worker: SubagentStart registers; unbound writes refused; bind; confinement
worker = dict(base, agent_id="th-red", agent_type="red-worker")
fire("SubagentStart", worker)
assert os.path.isfile(os.path.join(T, ".aladdin/state/agents/th-red.json")); ok("SubagentStart registers the worker")
assert denied(fire("PreToolUse", dict(worker, tool_name="Bash", tool_input={"command": "ls"})))
ok("unbound worker's shell command is refused")
wt = sh(os.path.join(BIN, "task-worktree"), "add", "S1-01-T1")
open(os.path.join(wt, ".aladdin/task.env"), "w").write(
    "TASK_ID=S1-01-T1\nATTEMPT=1\nAGENT_ROLE=red-worker\nSTORY_DIR=%s\n" % os.path.join(T, "docs/agents/sprint1/s1-01-x"))
assert not denied(fire("PreToolUse", dict(worker, tool_name="Bash", tool_input={"command": "aloop bind " + wt}))); ok("`aloop bind` allowed")
assert denied(fire("PreToolUse", dict(worker, tool_name="Bash", tool_input={"command": "cargo test"}))); ok("command outside the worktree refused")
assert not denied(fire("PreToolUse", dict(worker, tool_name="Bash", tool_input={"command": "cd %s && cargo test" % wt}))); ok("command inside the worktree allowed")
assert denied(fire("PreToolUse", dict(worker, tool_name="apply_patch", tool_input={"command": "*** Begin Patch\n*** Add File: tests/a.rs\n+x\n*** End Patch"})))
ok("patch outside the worktree refused")

# time box from the worker's own rollout: soft token telemetry, then hard stop (no report call) via continue:false
rollout("th-red", [(120, 10)])
r = fire("PostToolUse", dict(worker, tool_name="Bash", tool_input={"command": "cd %s && ls" % wt}, tool_response="ok"))
assert "BUDGET" not in context(r)
ev = [e for e in map(json.loads, open(os.path.join(T, ".aladdin/logs/events.jsonl"))) if e["event"].startswith("task.")]
assert [e["event"] for e in ev] == ["task.token_soft_limit"] and ev[0]["agent_id"] == "th-red", ev
ok("soft token limit is telemetry only: worker not told, task.token_soft_limit recorded (rollout tokens)")
rollout("th-red", [(120, 10), (300, 10)])
fire("PostToolUse", dict(worker, tool_name="Bash", tool_input={"command": "cd %s && ls" % wt}, tool_response="ok"))
for _ in range(3):
    assert denied(fire("PreToolUse", dict(worker, tool_name="Bash", tool_input={"command": "cd %s && ls" % wt})))
fire("PreToolUse", dict(worker, tool_name="Bash", tool_input={"command": "cd %s && ls" % wt}))
r = fire("PostToolUse", dict(worker, tool_name="Bash", tool_input={"command": "cd %s && ls" % wt}, tool_response="ok"))
assert any('"continue": false' in so for _, so, _ in r); ok("hard limit: 3 refusals, then continue:false stops the worker")

# SubagentStop from the MAIN cwd: the gate runs in the bound worktree and releases a stopped worker
r = fire("SubagentStop", dict(worker, agent_transcript_path="", last_assistant_message="done", stop_hook_active=False))
assert not any(c == 2 for c, _, _ in r); ok("budget-stopped worker released by its gate (from the bound worktree)")
os.remove(os.path.join(wt, ".aladdin/budget-exceeded"))
r = fire("SubagentStop", dict(worker, agent_transcript_path="", last_assistant_message="done", stop_hook_active=False))
blocks = [se for c, _, se in r if c == 2]
assert blocks and "init.md" in blocks[0] and "s1-01-x" in blocks[0]; ok("SubagentStop gate blocks with the worker's own STORY_DIR (task.env read in its worktree)")

# token ledger covers the main rollout + the registered worker rollout
fire("Stop", dict(base, stop_hook_active=False, last_assistant_message="x"))
rows = [json.loads(l) for l in open(os.path.join(T, ".aladdin/ledger/main0.jsonl"))]
assert {r["agent"] for r in rows} == {"main", "th-red"} and sum(r["out"] for r in rows) == 25; ok("ledger = main rollout + worker rollout")

# Claude Code regression: the same hooks must NOT confine a Claude worker (native isolation)
claude = {"session_id": "c1", "transcript_path": os.path.join(T, "cc", "c1.jsonl"), "agent_id": "a-claude", "agent_type": "aloop:red-worker"}
r = subprocess.run(["sh", "-c", HOOKS["PreToolUse"][-1]["hooks"][0]["command"]], cwd=T, env=ENV, capture_output=True, text=True,
                   input=json.dumps(dict(claude, hook_event_name="PreToolUse", cwd=T, tool_name="Bash", tool_input={"command": "cargo test"})))
assert "deny" not in r.stdout; ok("Claude Code worker (no turn_id, no bind) is not confined by host-adapter")

print("\n%d checks passed" % PASS[0])
