#!/usr/bin/env python3
"""ROADMAP §3.6: abstract model routes. Roles map to strongest | strong | economical; each
host resolves the route to its own id; a pinned id passes through; host-specific settings
win on their host; the route is observable (conformance 18); `aloop install` renders the
resolved id for Codex and OpenCode.
    python3 scripts/test/model-routes.test.py
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-routes-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"))
ENV.pop("AGENTIC_HOST", None)
PASS = [0]
M = os.path.join(BIN, "models")
DEF = os.path.join(T, "docs", "agents", "defaults.md")


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def run(*args, **env):
    return subprocess.run(args, cwd=T, env=dict(ENV, **env), capture_output=True, text=True)


def resolve(group, host=None, as_json=True):
    args = [M, "resolve", group] + (["--host", host] if host else []) + (["--json"] if as_json else [])
    r = run(*args)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if as_json else r.stdout.strip()


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


subprocess.run(["git", "init", "-q"], cwd=T, check=True)
os.makedirs(os.path.join(T, "docs", "agents"))

# 1) defaults are routes; each host resolves them to its own id; roles map to their group
assert resolve("planning") == {"group": "planning", "host": "claude", "route": "strongest",
                               "model": "claude-opus-5-5", "source": "AGENTIC_MODEL_PLANNING"}
assert resolve("green-worker", as_json=False) == "sonnet"
assert resolve("planner", "opencode")["model"] == "anthropic/claude-opus-5-5"
assert resolve("worker", "opencode")["model"] == "anthropic/claude-sonnet-5"
c = resolve("worker", "codex")
assert c["route"] == "strong" and c["model"] == "", c       # Codex: the session's own model
assert resolve("worker", as_json=False, ) == "sonnet"
ok("defaults are routes (planning=strongest, worker=strong); each host resolves them itself")

# 2) a different route, a pinned id, and a host-specific pin
open(DEF, "w").write("AGENTIC_MODEL_WORKER=economical\nAGENTIC_MODEL_PLANNING=claude-opus-5\n")
assert resolve("worker")["model"] == "haiku" and resolve("worker", "opencode")["model"] == "anthropic/claude-haiku-4-5"
p = resolve("planning")
assert p["route"] == "pinned" and p["model"] == "claude-opus-5", p
assert resolve("planning", "opencode")["model"] == "anthropic/claude-opus-5"
assert resolve("planning", "codex")["model"] == ""           # a Claude id does not exist on Codex
open(DEF, "a").write("AGENTIC_MODEL_WORKER_CODEX=gpt-5.5\n")
c = resolve("worker", "codex")
assert c == {"group": "worker", "host": "codex", "route": "pinned", "model": "gpt-5.5", "source": "AGENTIC_MODEL_WORKER_CODEX"}, c
ok("a route change, a pinned id (passes through, mapped per host) and a host-specific pin that wins there")

# 3) the route table is printed; unknown host/role are errors, never a guess
assert "economical=haiku" in run(M, "routes", "--host", "claude").stdout
assert run(M, "resolve", "worker", "--host", "vim").returncode == 2
assert run(M, "resolve", "janitor").returncode == 2
ok("models routes prints the table; unknown host or role → exit 2")

# 4) install renders the host's resolved id (what Codex / OpenCode agents actually get)
ins = load("aloop_install_routes", os.path.join(BIN, "install"))
st = dict(ins._paths.settings(T))
assert ins.model_for("opencode", False, st) == "anthropic/claude-haiku-4-5"
assert ins.model_for("codex", False, st) == "gpt-5.5" and ins.model_for("codex", True, st) == ""
ok("aloop install renders the resolved id per host from the same table")

print("\n%d checks passed" % PASS[0])
