#!/usr/bin/env python3
"""ROADMAP §4.11 (M8): natural-language auto-trigger inside hosts. A plain build/fix request
typed into an aloop project, with no sprint running, is answered by the aladdin loop: the
UserPromptSubmit hook `intent-trigger` adds context telling the host to treat the prompt as
`/aladdin "<intent>"`. It never blocks, never writes, and stays quiet for questions, slash
commands, short replies, a live sprint, a non-aloop repo, or when switched off.
    python3 scripts/test/intent-trigger.test.py
"""
import json
import os
import subprocess
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-trigger-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"))
for k in list(ENV):
    if k.startswith(("AGENTIC_", "ALADDIN_")):
        ENV.pop(k)
PASS = [0]


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def hook(prompt, cwd=T, **extra):
    env = dict(ENV, **extra)
    payload = {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "cwd": cwd, "prompt": prompt}
    r = subprocess.run([os.path.join(BIN, "intent-trigger"), "hook"], cwd=cwd, env=env, input=json.dumps(payload),
                       capture_output=True, text=True)
    assert r.returncode == 0, (prompt, r.returncode, r.stderr)
    if not r.stdout.strip():
        return ""
    return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]


subprocess.run(["git", "init", "-q", T], check=True)
os.makedirs(os.path.join(T, "docs", "agents"))

ctx = hook("add a CSV export to the report command, with a header row")
assert "aladdin" in ctx and "add a CSV export" in ctx and "ALADDIN_AUTO_TRIGGER" in ctx, ctx
assert hook("fix the flaky parser test in tests/parse.rs") and hook("I want the CLI to accept a --json flag")
ok("a build/fix request in an aloop project adds the aladdin context, quoting the intent")

for p in ("why does the parser test fail?", "/aladdin \"add a CSV export\"", "ok", "yes, go ahead",
          "thanks, looks good", "add"):
    assert hook(p) == "", p
ok("questions, slash commands and short replies stay silent")

lock_dir = os.path.join(T, ".aladdin", "state"); os.makedirs(lock_dir)
open(os.path.join(lock_dir, "sprint.lock"), "w").write("sprint 1\n")
assert hook("add a CSV export to the report command, with a header row") == ""
os.remove(os.path.join(lock_dir, "sprint.lock"))
ok("a live sprint lock means the prompt is feedback to the running loop: silent")

assert hook("add a CSV export to the report command, with a header row", AGENTIC_AUTO_TRIGGER="off") == ""
assert hook("add a CSV export to the report command, with a header row", ALADDIN_AUTO_TRIGGER="0") == ""
open(os.path.join(T, ".aladdin", "config.yaml"), "w").write("intent:\n  auto_trigger: false\n")
assert hook("add a CSV export to the report command, with a header row") == ""
os.remove(os.path.join(T, ".aladdin", "config.yaml"))
assert hook("add a CSV export to the report command, with a header row")
ok("AGENTIC_/ALADDIN_AUTO_TRIGGER=off or `intent: {auto_trigger: false}` in config.yaml switches it off")

other = tempfile.mkdtemp(prefix="agentic-trigger-plain-")
subprocess.run(["git", "init", "-q", other], check=True)
assert hook("add a CSV export to the report command, with a header row", cwd=other) == ""
ok("a repo without docs/agents or a run-data dir is not an aloop project: silent")

r = subprocess.run([os.path.join(BIN, "intent-trigger"), "hook"], cwd=T, env=ENV, input="not json", capture_output=True, text=True)
assert r.returncode == 0 and r.stdout.strip() == "", r
ok("bad input never blocks: exit 0, no output")

hooks = json.load(open(os.path.join(ROOT, "hooks", "hooks.json")))["hooks"]["UserPromptSubmit"]
cmds = [h["command"] for g in hooks for h in g["hooks"]]
assert any("intent-trigger\" hook" in c for c in cmds), cmds
js = open(os.path.join(ROOT, "hosts", "opencode", "aloop.js")).read()
assert 'run("intent-trigger"' in js, "OpenCode's chat.message runs the trigger too"
ok("wired into hooks.json UserPromptSubmit and the OpenCode plugin")

print("\n%d checks passed" % PASS[0])
