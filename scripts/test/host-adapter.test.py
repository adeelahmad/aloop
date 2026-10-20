#!/usr/bin/env python3
"""ROADMAP §4.9: the HostAdapter interface. Claude Code is the reference; Codex, OpenCode
and DeepSeek Harness (dsh) implement the same interface. Gates are enforced natively or from outside, never
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


def run(*args, inp=None, **env):
    return subprocess.run(args, cwd=T, env=dict(ENV, **env), capture_output=True, text=True, input=inp)


subprocess.run(["git", "init", "-q", "-b", "main"], cwd=T, check=True)
open(os.path.join(T, "a.py"), "w").write("x = 1\n")
subprocess.run(["git", "add", "-A"], cwd=T, check=True)
subprocess.run(["git", "-c", "user.name=A", "-c", "user.email=a@a", "commit", "-q", "-m", "init"], cwd=T, check=True)

# 1) one interface, four adapters, gates never downgraded
caps = json.load(open(os.path.join(ROOT, "hosts", "capabilities.json")))["hosts"]
assert sorted(_host.ADAPTERS) == sorted(caps) == ["claude", "codex", "dsh", "opencode"], (sorted(_host.ADAPTERS), sorted(caps))
for n, cls in _host.ADAPTERS.items():
    assert _host.conforms(cls) == [], (n, _host.conforms(cls))
    assert cls.ENFORCE_GATE in ("native", "outer")
    assert caps[n]["gate_blocking"] == cls.ENFORCE_GATE, (n, caps[n]["gate_blocking"], cls.ENFORCE_GATE)
r = run(H, "check")
assert r.returncode == 0 and "opencode  interface complete · gates enforced: outer" in r.stdout, r
assert "dsh       interface complete · gates enforced: native" in r.stdout, r.stdout
ok("every adapter implements the whole interface; gate enforcement is native or outer, never warn, "
   "and the capability matrix says the same")

# 2) describe: the host's contract, with Claude Code as the reference
d = {h: json.loads(run(H, "describe", "--host", h, "--json").stdout) for h in ("claude", "codex", "opencode", "dsh")}
assert d["claude"]["isolation"].startswith("host-native") and "Agent tool" in d["claude"]["spawn"]
assert "spawn_agent" in d["codex"]["spawn"] and "aloop bind" in d["codex"]["isolation"]
assert d["opencode"]["rotate_session"] == "/new" and d["claude"]["rotate_session"] == "/clear"
assert d["claude"]["models"] == {"planning": {"group": "planning", "host": "claude", "route": "strongest", "model": "claude-opus-5-5",
                                              "source": "AGENTIC_MODEL_PLANNING"},
                                 "worker": {"group": "worker", "host": "claude", "route": "strong", "model": "sonnet",
                                            "source": "AGENTIC_MODEL_WORKER"}}, d["claude"]["models"]
assert d["codex"]["models"]["worker"]["model"] == ""
assert "agent_preset: aloop-<role>" in d["dsh"]["spawn"] and "subagent_planning" in d["dsh"]["spawn"]
assert "aloop bind" in d["dsh"]["isolation"] and "interrupt_agent" in d["dsh"]["cancel"] and "dsh --profile aloop" in d["dsh"]["rotate_session"]
assert [d["dsh"]["models"][g]["model"] for g in ("planning", "worker")] == ["deepseek-v4-pro", "deepseek-v4-pro"], d["dsh"]["models"]
assert run(H, "describe", "--host", "vim").returncode == 64
ok("host describe: capabilities, dispatch vocabulary, boundary, isolation, gates and models per host")

# 3) what a host cannot do is Unsupported, never faked
for h in ("claude", "codex", "opencode", "dsh"):
    assert "unsupported" in d[h]["send"], (h, d[h]["send"])            # no host has live messaging (dsh: unproven)
assert d["claude"]["compact"].startswith("compact the session")
assert all("unsupported" in d[h]["compact"] for h in ("codex", "opencode", "dsh"))
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

# 6) dsh worker confinement (host-adapter hook, AGENTIC_HOST=dsh): dsh cannot rewrite a call, so a
# bound writer is confined by denial. The dsh plugin passes the directory a command runs in as
# tool_input.workdir (bash's own workdir, else the agent's) and joins relative paths to it.
os.makedirs(os.path.join(T, "docs", "agents"), exist_ok=True)
wt = run(os.path.join(BIN, "task-worktree"), "add", "S1-02-T1").stdout.strip()
os.makedirs(os.path.join(wt, ".aladdin"), exist_ok=True)
open(os.path.join(wt, ".aladdin", "task.env"), "w").write("TASK_ID=S1-02-T1\nATTEMPT=1\nAGENT_ROLE=red-worker\n")


def pre(tool, ti, role="red-worker", agent="dsh-child-1"):
    p = {"hook_event_name": "PreToolUse", "cwd": T, "session_id": "root", "agent_id": agent, "agent_type": role,
         "transcript_path": os.path.join(T, ".aladdin", "host", "dsh", "root.jsonl"), "tool_name": tool, "tool_input": ti}
    out = run(os.path.join(BIN, "host-adapter"), "hook", AGENTIC_HOST="dsh", inp=json.dumps(p)).stdout.strip()
    return json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"] if out else ""


assert "bind your worktree first" in pre("Bash", {"command": "ls", "workdir": wt})
assert "bind your worktree first" in pre("Write", {"file_path": os.path.join(wt, "a.rs"), "workdir": wt})
assert pre("Read", {"file_path": os.path.join(T, "a.py"), "workdir": T}) == ""
assert pre("Bash", {"command": "ls", "workdir": T}, role="planner", agent="dsh-planner") == ""     # readers aren't confined
assert pre("Bash", {"command": "aloop bind %s" % wt, "workdir": T}) == ""
reg = json.load(open(os.path.join(T, ".aladdin", "state", "agents", "dsh-child-1.json")))
assert reg["worktree"] == wt and reg["host"] == "dsh", reg
ok("dsh: a writer is refused until `aloop bind`, which host-adapter records; readers are not confined")
assert pre("Bash", {"command": "cargo test", "workdir": wt}) == ""
assert pre("Bash", {"command": "cargo test", "workdir": os.path.join(wt, "src")}) == ""
assert "inside your worktree" in pre("Bash", {"command": "cargo test", "workdir": T})
assert pre("Bash", {"command": "cd %s && cargo test" % wt, "workdir": T}) == ""
assert "stay in your worktree" in pre("Bash", {"command": "cd %s && git commit -am x" % T, "workdir": wt})
assert "stay in your worktree" in pre("Bash", {"command": "cd .. && touch x", "workdir": wt})
assert "stay in your worktree" in pre("Bash", {"command": "cd && touch x", "workdir": wt})
os.makedirs(os.path.join(wt, "src"), exist_ok=True)
assert pre("Bash", {"command": "cd src && cargo test", "workdir": wt}) == ""
assert pre("Bash", {"command": "cd src; cargo test", "workdir": wt}) == ""
ok("dsh: a bound writer's command runs only in (or `cd`s into) its worktree, never `cd`s out of it")
assert pre("Write", {"file_path": os.path.join(wt, "tests", "a.rs"), "workdir": wt}) == ""
assert pre("Edit", {"file_path": "tests/a.rs", "workdir": wt}) == ""
assert "edit only files inside your worktree" in pre("Edit", {"file_path": "a.py", "workdir": T})
assert "edit only files inside your worktree" in pre("Write", {"file_path": os.path.join(T, "src", "x.rs"), "workdir": wt})
assert pre("Read", {"file_path": os.path.join(T, "docs", "agents", "x.md"), "workdir": wt}) == ""
ok("dsh: edits only under the worktree (relative paths resolve against the working directory); reads anywhere")
assert pre("working_directory", {"cd": os.path.join(wt, "src"), "workdir": wt}) == ""
assert pre("working_directory", {"workdir": wt}) == ""
assert "stay in your worktree" in pre("working_directory", {"cd": T, "workdir": wt})
assert "stay in your worktree" in pre("working_directory", {"cd": "..", "workdir": wt})
ok("dsh: working_directory moves only within the worktree")
os.symlink(os.path.join(T, "docs"), os.path.join(wt, "lnk"))
assert "edit only files inside your worktree" in pre("Write", {"file_path": os.path.join(wt, "lnk", "..", "x.md"), "workdir": wt})
assert "stay in your worktree" in pre("working_directory", {"cd": os.path.join(wt, "lnk", ".."), "workdir": wt})
assert "inside your worktree" in pre("Bash", {"command": "touch x", "workdir": os.path.join(wt, "lnk", "..")})
ok("dsh: a symlink out of the worktree is followed before `..` (as dsh does), so `lnk/..` is outside it")
assert "inside your worktree" in pre("Bash", {"command": "touch x # aloop bind %s" % wt, "workdir": T})
assert "inside your worktree" in pre("Bash", {"command": "make gen; aloop bind %s" % wt, "workdir": T})
assert pre("Bash", {"command": "cd %s && aloop bind %s" % (wt, wt), "workdir": T}) == ""
assert "inside your worktree" in pre("Bash", {"command": "touch x; aloop bind %s" % wt, "workdir": T}, agent="dsh-child-2")
assert json.load(open(os.path.join(T, ".aladdin", "state", "agents", "dsh-child-2.json")))["worktree"] == wt
ok("only a bare `aloop bind` skips the checks: a bind in a comment or chained to a command is confined (and still recorded)")

print("\n%d checks passed" % PASS[0])
