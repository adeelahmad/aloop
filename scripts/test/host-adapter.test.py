#!/usr/bin/env python3
"""ROADMAP §4.9: the HostAdapter interface. Claude Code is the reference; Codex and
OpenCode implement the same interface. Gates are enforced natively or from outside, never
as a warning; a capability a host lacks raises Unsupported instead of being faked; the
worktree seam works through the adapter.
    python3 scripts/test/host-adapter.test.py
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-hostad-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"), AGENTIC_NO_AUTOINSTALL="1",
           GIT_AUTHOR_NAME="A", GIT_AUTHOR_EMAIL="a@a", GIT_COMMITTER_NAME="A", GIT_COMMITTER_EMAIL="a@a")
ENV.pop("AGENTIC_HOST", None)
PASS = [0]
H = os.path.join(BIN, "host")
sys.path.insert(0, BIN)
_host = __import__("_host")


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def run(*args, **env):
    return subprocess.run(args, cwd=T, env=dict(ENV, **env), capture_output=True, text=True)


subprocess.run(["git", "init", "-q", "-b", "main"], cwd=T, check=True)
open(os.path.join(T, "a.py"), "w").write("x = 1\n")
subprocess.run(["git", "add", "-A"], cwd=T, check=True)
subprocess.run(["git", "-c", "user.name=A", "-c", "user.email=a@a", "commit", "-q", "-m", "init"], cwd=T, check=True)

# 1) one interface, three adapters, gates never downgraded
for n, cls in _host.ADAPTERS.items():
    assert _host.conforms(cls) == [], (n, _host.conforms(cls))
    assert cls.ENFORCE_GATE in ("native", "outer")
r = run(H, "check")
assert r.returncode == 0 and "opencode  interface complete · gates enforced: outer" in r.stdout, r
ok("every adapter implements the whole interface; gate enforcement is native or outer, never warn")

# 2) describe: the host's contract, with Claude Code as the reference
d = {h: json.loads(run(H, "describe", "--host", h, "--json").stdout) for h in ("claude", "codex", "opencode")}
assert d["claude"]["isolation"].startswith("host-native") and "Agent tool" in d["claude"]["spawn"]
assert "spawn_agent" in d["codex"]["spawn"] and "aloop bind" in d["codex"]["isolation"]
assert d["opencode"]["rotate_session"] == "/new" and d["claude"]["rotate_session"] == "/clear"
assert d["claude"]["models"] == {"planning": {"group": "planning", "host": "claude", "route": "strongest", "model": "claude-opus-5-5",
                                              "source": "AGENTIC_MODEL_PLANNING"},
                                 "worker": {"group": "worker", "host": "claude", "route": "strong", "model": "sonnet",
                                            "source": "AGENTIC_MODEL_WORKER"}}, d["claude"]["models"]
assert d["codex"]["models"]["worker"]["model"] == ""
assert run(H, "describe", "--host", "vim").returncode == 64
ok("host describe: capabilities, dispatch vocabulary, boundary, isolation, gates and models per host")

# 3) what a host cannot do is Unsupported, never faked
for h in ("claude", "codex", "opencode"):
    assert "unsupported" in d[h]["send"], (h, d[h]["send"])            # no host has live messaging
assert d["claude"]["compact"].startswith("compact the session")
assert "unsupported" in d["codex"]["compact"] and "unsupported" in d["opencode"]["compact"]
try:
    _host.adapter("codex").compact()
    raise AssertionError("codex compact should be Unsupported")
except _host.Unsupported:
    pass
ok("compaction and live messaging: Unsupported where not advertised, so the harness rotates / records")

# 4) the worktree seam through the adapter, same on every host
wt = run(H, "worktree", "create", "S1-01-T1", AGENTIC_HOST="opencode").stdout.strip()
assert os.path.isdir(os.path.join(wt, ".git")) or os.path.isfile(os.path.join(wt, ".git")), wt
open(os.path.join(wt, "b.py"), "w").write("y = 2\n")
subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
subprocess.run(["git", "-c", "user.name=A", "-c", "user.email=a@a", "commit", "-q", "-m", "S1-01-T1 green"], cwd=wt, check=True)
open(os.path.join(wt, "a.py"), "a").write("z = 3\n")
assert run(H, "worktree", "diff", "S1-01-T1", AGENTIC_HOST="codex").stdout.split() == ["a.py", "b.py"]
assert run(H, "worktree", "remove", "S1-01-T1").returncode == 0 and not os.path.exists(wt)
assert run(H, "worktree", "diff", "S1-01-T1").returncode == 1
ok("worktree create / diff (committed + uncommitted) / remove through the adapter")

# 5) usage and transcripts go through the same seam as the budget hook
a = _host.adapter("claude")
log = os.path.join(T, "agent.jsonl")
open(log, "w").write(json.dumps({"timestamp": "2026-10-18T00:00:00Z", "message": {"id": "m1", "model": "claude-sonnet-5", "usage": {
    "input_tokens": 100, "cache_read_input_tokens": 50, "cache_creation_input_tokens": 10, "output_tokens": 20}}}) + "\n")
u = a.usage(log)
assert u["in"] == 100 and u["accuracy"] == "exact" and a.context_pressure(log) == 160, u
assert a.usage(os.path.join(T, "missing.jsonl"))["accuracy"] == "none"
assert a.resolve_transcript({"agent_transcript_path": log}, "x") == log
ok("usage, context pressure and transcript resolution through the adapter")

print("\n%d checks passed" % PASS[0])
