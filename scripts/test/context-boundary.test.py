#!/usr/bin/env python3
"""ROADMAP §4.4 / §4.9: the host capability matrix and the context-boundary rule. Rotation
is the default; compaction only when chosen AND advertised; keeping a session needs a
recorded reason; SessionStart records rotations and compactions as events; the sprint-close
message names the host's own rotate command.
    python3 scripts/test/context-boundary.test.py
"""
import json
import os
import subprocess
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-boundary-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"), AGENTIC_NO_AUTOINSTALL="1")
ENV.pop("AGENTIC_HOST", None)
PASS = [0]
CAP = os.path.join(BIN, "capabilities")


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def run(*args, inp=None, **env):
    return subprocess.run(args, cwd=T, env=dict(ENV, **env), capture_output=True, text=True, input=inp)


def events():
    try:
        return [json.loads(x) for x in open(os.path.join(T, ".agentic", "logs", "events.jsonl"))]
    except OSError:
        return []


subprocess.run(["git", "init", "-q"], cwd=T, check=True)
os.makedirs(os.path.join(T, "docs", "agents"))
DEF = os.path.join(T, "docs", "agents", "defaults.md")

# 1) the matrix is explicit for every host
out = run(CAP).stdout
for h in ("claude", "codex", "opencode"):
    assert h in out and "gate_blocking=native" in out, out
assert "codex     usage=exact; cache write not reported  compaction=unknown" in out, out
assert run(CAP, "vim").returncode == 64
ok("capability matrix: usage, compaction, live messaging, lifecycle, gate blocking per host")

# 2) the boundary rule: rotate by default; compact only if chosen AND advertised
assert run(CAP, "boundary", "claude").stdout.strip() == "rotate: start a fresh session with /clear"
assert run(CAP, "boundary", "opencode").stdout.strip() == "rotate: start a fresh session with /new"
open(DEF, "w").write("AGENTIC_CONTEXT_BOUNDARY=compact\n")
assert run(CAP, "boundary", "claude").stdout.startswith("compact:")
assert "compaction is not advertised by codex" in run(CAP, "boundary", "codex").stdout
assert run(CAP, "boundary", "--keep").returncode == 2
assert run(CAP, "boundary", "--keep", "--reason", "one-task hotfix sprint").stdout.strip() == "keep"
assert "keep session across the sprint boundary" in events()[-1]["decision"]["reason"]
os.remove(DEF)
ok("rotate by default; compaction only when chosen and advertised; keeping a session needs a recorded reason")

# 3) SessionStart records the boundary that actually happened
open(os.path.join(T, "docs", "agents", "NEXT.md"), "w").write("# Next\n")
for src, ev in (("clear", "context.rotated"), ("compact", "context.compacted")):
    run(os.path.join(BIN, "session-start"), inp=json.dumps({"hook_event_name": "SessionStart", "cwd": T, "source": src}))
    assert events()[-1]["event"] == ev, events()[-1]
n = len(events())
run(os.path.join(BIN, "session-start"), inp=json.dumps({"hook_event_name": "SessionStart", "cwd": T, "source": "startup"}))
assert len(events()) == n
ok("SessionStart: clear → context.rotated, compact → context.compacted, startup → nothing")

# 4) the sprint-close message names the host's own rotate command
os.makedirs(os.path.join(T, ".agentic", "state"), exist_ok=True)
for host, cmd in (("claude", "/clear"), ("opencode", "/new")):
    open(os.path.join(T, ".agentic", "state", "pending-close"), "w").write("Sprint 1 closed.")
    r = run(os.path.join(BIN, "stats"), "hook", inp=json.dumps({"hook_event_name": "Stop", "cwd": T}), AGENTIC_HOST=host)
    msg = json.loads(r.stdout)["reason"]
    assert "rotate: start a fresh session with %s" % cmd in msg, (host, msg)
ok("sprint close tells the supervisor the host's rotate command (/clear, /new)")

print("\n%d checks passed" % PASS[0])
