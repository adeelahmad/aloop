#!/usr/bin/env python3
"""ROADMAP M10.4: one stop for a whole project. `aloop stop [REASON]` sets a stop flag in the
project's main tree and records `run.stopped`. While it is set, every agent of the project (the
supervisor, a worker in its task worktree, a mission role) is refused every tool call that acts,
and its next read ends it (`continue: false`), on every host (the check sits in `budget hook pre`,
which all four hosts run). `mission tick` answers STOPPED. Only the human clears it, from their own
terminal: `aloop stop --clear` refuses a shell an agent host runs, and records `run.resumed`.
    python3 scripts/test/project-stop.test.py
"""
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
BIN = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugin", "bin"))
T = os.path.realpath(tempfile.mkdtemp(prefix="aloop-stop-"))
atexit.register(shutil.rmtree, T, ignore_errors=True)
REPO = os.path.join(T, "repo")
WT = os.path.join(T, "wt")
AGENT_VARS = ("CLAUDECODE", "DSH_SHELL", "CODEX_THREAD_ID", "ALOOP_AGENT_SHELL", "AGENTIC_HOST", "SKIP_HOOKS",
              "AGENTIC_NO_BUDGET")
ENV = {k: v for k, v in os.environ.items() if k not in AGENT_VARS}
ENV.update(HOME=os.path.join(T, "home"), PYTHONDONTWRITEBYTECODE="1")
n = [0]


def ok(msg):
    n[0] += 1
    print("ok - " + msg)


def run(*args, cwd=REPO, env=None, inp=None):
    return subprocess.run(args, cwd=cwd, env=env or ENV, capture_output=True, text=True, input=inp)


def sh(*args, cwd=REPO):
    r = run(*args, cwd=cwd)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout


def events(name):
    p = os.path.join(REPO, ".aladdin", "logs", "events.jsonl")
    return [e for e in map(json.loads, open(p)) if e["event"] == name] if os.path.exists(p) else []


def hook(mode, tool, cwd, **who):
    payload = dict(hook_event_name="PreToolUse" if mode == "pre" else "PostToolUse", tool_name=tool,
                   tool_input={"command": "git push"} if tool == "Bash" else {"file_path": "x"},
                   cwd=cwd, session_id="sess-1", **who)
    r = run(os.path.join(BIN, "budget"), "hook", mode, cwd=cwd, inp=json.dumps(payload))
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if r.stdout.strip() else {}


def denied(out):
    return (out.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"


os.makedirs(REPO)
sh("git", "init", "-q", "-b", "main")
sh("git", "config", "user.email", "ada@x.io")
sh("git", "config", "user.name", "Ada")
open(os.path.join(REPO, "README.md"), "w").write("x\n")
sh("git", "add", "-A")
sh("git", "commit", "-qm", "init")
sh("git", "worktree", "add", "-q", "-b", "task", WT)
STOP = os.path.join(BIN, "stop")
SUPERVISOR = {}
WORKER = {"agent_id": "a1", "agent_type": "aloop:green-worker"}
ROLE = {"agent_id": "a2", "agent_type": "market-researcher"}

# 1) running: status says so, and no agent is refused
r = run(STOP, "--status")
assert r.returncode == 0 and "running" in r.stdout, r
assert not denied(hook("pre", "Bash", REPO, **SUPERVISOR)) and not denied(hook("pre", "Write", WT, **WORKER))
ok("a running project: `stop --status` exits 0 and the hooks refuse nothing")

# 2) stop, from a task worktree (any shell may stop): the flag lands in the main tree, with an event
r = run(STOP, "the deploy looks wrong", cwd=WT, env=dict(ENV, CLAUDECODE="1"))
assert r.returncode == 0 and "stopped" in r.stdout, r
r = run(STOP, "--status")
assert r.returncode == 3 and "the deploy looks wrong" in r.stdout, r
ev = events("run.stopped")
assert len(ev) == 1 and ev[0]["data"]["reason"] == "the deploy looks wrong", ev
ok("`aloop stop REASON` from any shell or worktree stops the whole project and records run.stopped")

# 3) every agent is refused every acting tool call, wherever it runs, and a read ends it
for who, cwd, tool in ((SUPERVISOR, REPO, "Bash"), (SUPERVISOR, REPO, "Agent"), (WORKER, WT, "Write"),
                       (WORKER, WT, "Bash"), (ROLE, REPO, "WebFetch"), (ROLE, REPO, "Edit")):
    out = hook("pre", tool, cwd, **who)
    assert denied(out), (who, tool, out)
    assert "stopped" in out["hookSpecificOutput"]["permissionDecisionReason"]
for who, cwd in ((SUPERVISOR, REPO), (WORKER, WT), (ROLE, REPO)):
    assert not denied(hook("pre", "Read", cwd, **who))
    out = hook("post", "Read", cwd, **who)
    assert out.get("continue") is False and "stopped" in out.get("stopReason", ""), out
ok("while stopped every agent (supervisor, worker in its worktree, mission role) is refused every acting call, and its next read ends it")

# 4) the stop survives the budget opt-outs a session might carry
r = run(os.path.join(BIN, "budget"), "hook", "pre", cwd=WT, env=dict(ENV, AGENTIC_NO_BUDGET="1", SKIP_HOOKS="1"),
        inp=json.dumps(dict(hook_event_name="PreToolUse", tool_name="Bash", tool_input={"command": "ls"}, cwd=WT, **WORKER)))
assert denied(json.loads(r.stdout)), r
ok("AGENTIC_NO_BUDGET and SKIP_HOOKS do not lift a stop")

# 5) mission tick honours it before anything else
r = run(os.path.join(BIN, "mission"), "tick", "--json", "--peek")
d = json.loads(r.stdout)
assert d["action"] == "STOPPED" and d["who"] == "human" and r.returncode == 3, (r.returncode, d)
assert "the deploy looks wrong" in d["reason"] and "aloop stop --clear" in d["reason"], d
ok("mission tick answers STOPPED (the human's) with the reason while the project is stopped")

# 6) only the human clears it: an agent host's shell is refused, the human's own terminal is not
for var in ("CLAUDECODE", "DSH_SHELL", "CODEX_THREAD_ID", "ALOOP_AGENT_SHELL"):
    r = run(STOP, "--clear", env=dict(ENV, **{var: "1"}))
    assert r.returncode == 2 and "terminal" in r.stderr, r
assert run(STOP, "--status").returncode == 3
assert not events("run.resumed")
r = run(STOP, "--clear")
assert r.returncode == 0 and "running" in r.stdout, r
assert run(STOP, "--status").returncode == 0 and len(events("run.resumed")) == 1
assert not denied(hook("pre", "Bash", REPO, **SUPERVISOR)) and not denied(hook("pre", "Write", WT, **WORKER))
r = run(os.path.join(BIN, "mission"), "tick", "--json", "--peek")
assert json.loads(r.stdout)["action"] != "STOPPED", r.stdout
ok("`stop --clear` is refused in an agent's shell on every host (Claude Code, dsh, Codex, OpenCode); from the human's terminal it resumes everything and records run.resumed")

# 7) the stop records are the human's: `events emit` cannot forge them
for name in ("run.stopped", "run.resumed"):
    r = run(os.path.join(BIN, "events"), "emit", name)
    assert r.returncode != 0, (name, r)
ok("events emit refuses run.stopped and run.resumed")

# 8) with a mission, the stop is a committed ledger row too: a fresh clone (another machine, a scheduled
#    run) stays stopped until the human clears it there, and that clear is a ledger row as well
LEDGER = os.path.join(REPO, "docs", "agents", "mission", "ledger.jsonl")
os.makedirs(os.path.dirname(LEDGER))
open(LEDGER, "w").close()
sh("git", "add", "-A")
sh("git", "commit", "-qm", "mission ledger")
assert run(STOP, "the scheduled run is burning money").returncode == 0
rows = [json.loads(ln) for ln in open(LEDGER)]
assert [r["event"] for r in rows] == ["run.stopped"] and rows[0]["data"]["reason"] == "the scheduled run is burning money", rows
assert len(events("run.stopped")) == 2, events("run.stopped")       # one event per stop, not two
sh("git", "add", "-A")
sh("git", "commit", "-qm", "stop")
CLONE = os.path.join(T, "clone")
sh("git", "clone", "-q", REPO, CLONE, cwd=T)
assert not os.path.exists(os.path.join(CLONE, ".aladdin", "state", "STOP"))
r = run(STOP, "--status", cwd=CLONE)
assert r.returncode == 3 and "burning money" in r.stdout, r
assert denied(hook("pre", "Bash", CLONE, **SUPERVISOR)) and denied(hook("pre", "Write", CLONE, **WORKER))
r = run(os.path.join(BIN, "mission"), "tick", "--json", "--peek", cwd=CLONE)
assert json.loads(r.stdout)["action"] == "STOPPED", r.stdout
r = run(STOP, "--clear", cwd=CLONE, env=dict(ENV, CLAUDECODE="1"))
assert r.returncode == 2 and run(STOP, "--status", cwd=CLONE).returncode == 3, r
r = run(STOP, "--clear", cwd=CLONE)
assert r.returncode == 0 and run(STOP, "--status", cwd=CLONE).returncode == 0, r
crows = [json.loads(ln) for ln in open(os.path.join(CLONE, "docs", "agents", "mission", "ledger.jsonl"))]
assert [x["event"] for x in crows] == ["run.stopped", "run.resumed"] and crows[1]["role"] == "human", crows
assert not denied(hook("pre", "Bash", CLONE, **SUPERVISOR))
# the original checkout still holds its own stop until the human clears it there, with one resume row
assert run(STOP, "--status").returncode == 3
assert run(STOP, "--clear").returncode == 0 and run(STOP, "--status").returncode == 0
assert [json.loads(ln)["event"] for ln in open(LEDGER)] == ["run.stopped", "run.resumed"]
ok("with a mission the stop is a committed ledger row: a fresh clone stays stopped (hooks, tick) until the human clears it there")

print("\n%d checks passed" % n[0])
