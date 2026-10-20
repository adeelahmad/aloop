#!/usr/bin/env python3
"""`aloop install dsh`: dsh has one global profile ($DSH_HOME/profiles/aloop), so it records the
project it serves, and only that project re-renders or removes it (no network, no tokens).

  python3 scripts/test/dsh-install.test.py
"""
import importlib.machinery
import importlib.util
import atexit
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True                 # loading bin/ scripts must not leave __pycache__ there

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
BIN = os.path.join(ROOT, "plugin", "bin")
T = os.path.realpath(tempfile.mkdtemp(prefix="aloop-dsh-install-"))
atexit.register(shutil.rmtree, T, ignore_errors=True)
ENV = dict(os.environ, HOME=os.path.join(T, "home"), DSH_HOME=os.path.join(T, "dsh-home"),
           CLAUDE_PLUGIN_DATA=os.path.join(T, "pdata"), PYTHONDONTWRITEBYTECODE="1", AGENTIC_NO_AUTOINSTALL="1")
PROFILE = os.path.join(ENV["DSH_HOME"], "profiles", "aloop", "cordis.patch.yml")
n = 0


def ok(msg):
    global n
    n += 1
    print("ok - " + msg)


def run(*args, cwd):
    r = subprocess.run(args, cwd=cwd, env=ENV, capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout


def project(name):
    p = os.path.join(T, name)
    os.makedirs(p)
    for c in (["git", "init", "-q"], ["git", "config", "user.email", "ada@x.io"], ["git", "config", "user.name", "Ada"],
              ["git", "commit", "-q", "--allow-empty", "-m", "init"]):
        run(*c, cwd=p)
    run(os.path.join(BIN, "aloop-init"), "--apply", "MODEL_WORKER=economical", cwd=p)
    return p


profile = lambda: open(PROFILE).read()  # noqa: E731
A, B = project("a"), project("b")
run(os.path.join(BIN, "aloop"), "install", "dsh", "--project", A, cwd=A)
assert "# aloop-project: %s\n" % A in profile() and 'subagent: "deepseek-flash"' in profile(), profile()[:600]
assert 'project: "%s"' % A in profile()                      # the plugin warns a session in another project
assert "dsh" in run(os.path.join(BIN, "install"), "status", cwd=A)
ok("the profile records the project it was rendered for, with that project's models")

before = profile()
run(os.path.join(BIN, "aloop-init"), "--apply", "MODEL_WORKER=strongest", cwd=B)
assert profile() == before, "another project's aloop-init re-rendered A's profile"
st = run(os.path.join(BIN, "install"), "status", cwd=B)
assert "(serves %s; its roles run on this project's models)" % A in st, st
out = run(os.path.join(BIN, "aloop-init"), "--apply", "MODEL_WORKER=strongest", cwd=B)
assert "kept the dsh profile: it serves %s; its roles run on this project's models" % A in out and "install dsh --project" not in out, out
out = run(os.path.join(BIN, "install"), "dsh", "--uninstall", "--project", B, cwd=B)
assert os.path.isfile(PROFILE) and "kept" in out and A in out, out
ok("another project's init, status and uninstall leave A's profile alone, and say its roles run on B's own models (no re-install there)")

run(os.path.join(BIN, "aloop-init"), "--apply", "MODEL_WORKER=strongest", cwd=A)
assert 'subagent: "deepseek-v4-pro"' in profile() and "# aloop-project: %s\n" % A in profile(), profile()[:600]
ok("the project it serves re-renders it with its new models")

run(os.path.join(BIN, "aloop"), "install", "dsh", "--global", cwd=A)
assert "# aloop-project: (global)\n" in profile()
run(os.path.join(BIN, "aloop-init"), "--apply", "MODEL_WORKER=economical", cwd=B)
assert 'subagent: "deepseek-flash"' in profile() and "# aloop-project: (global)\n" in profile(), profile()[:600]
open(PROFILE, "w").write("\n".join(ln for ln in before.split("\n") if not ln.startswith("# aloop-project:")))
assert "dsh" in run(os.path.join(BIN, "install"), "status", cwd=B)
ok("a --global profile (or one from before the owner was recorded) is every project's, as Codex's global agents are")
run(os.path.join(BIN, "aloop-init"), "--apply", "MODEL_WORKER=strongest", cwd=B)
assert "# aloop-project:" not in profile() and 'subagent: "deepseek-v4-pro"' in profile(), profile()[:600]
run(os.path.join(BIN, "aloop-init"), "--apply", "MODEL_WORKER=economical", cwd=A)
assert "# aloop-project: %s\n" % A in profile(), profile()[:600]
ok("an unrecorded profile stays unrecorded when a project without its own skills re-renders it; the one that installed it claims it")

run(os.path.join(BIN, "install"), "dsh", "--uninstall", "--project", A, cwd=A)
assert not os.path.exists(os.path.dirname(PROFILE))
ok("the project it serves uninstalls it")

W, SUB = os.path.join(T, "a-wt"), os.path.join(A, "sub")
run("git", "worktree", "add", "-q", W, cwd=A)
os.makedirs(SUB)
for where in (W, SUB):
    run(os.path.join(BIN, "aloop"), "install", "dsh", "--project", ".", cwd=where)
    assert "# aloop-project: %s\n" % A in profile(), (where, profile()[:400])
    out = run(os.path.join(BIN, "aloop-init"), "--apply", "MODEL_WORKER=strongest", cwd=where)
    assert 'subagent: "deepseek-v4-pro"' in profile() and "kept the dsh profile" not in out, (where, out)
    run(os.path.join(BIN, "install"), "dsh", "--uninstall", "--project", ".", cwd=where)
    assert not os.path.exists(os.path.dirname(PROFILE)), where
    run(os.path.join(BIN, "aloop-init"), "--apply", "MODEL_WORKER=economical", cwd=A)
ok("a linked worktree or a subdirectory is its project: it records, re-renders and removes the project's profile")

spec = importlib.util.spec_from_loader("aloop_install", importlib.machinery.SourceFileLoader("aloop_install", os.path.join(BIN, "install")))
ins = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ins)
assert ins.dsh_agent_options("openrouter/deepseek/deepseek-chat-v3") == {"provider": "openrouter", "model": "deepseek/deepseek-chat-v3"}
assert ins.dsh_agent_options("deepseek-flash") == {"provider": "deepseek-official", "model": "deepseek-flash"}
assert ins.dsh_agent_options("") is None
ok("a provider-qualified model splits at the first '/' (model ids may hold one); a bare id is DeepSeek's")

sys.path.insert(0, BIN)
import _routes  # noqa: E402
mjs = open(os.path.join(ROOT, "plugin", "hosts", "dsh", "aloop.mjs")).read()
hint = re.search(r"\(subagent: ([^;]+); subagent_planning: ([^)]+)\)", mjs)
note = ins.host_body("x", "dsh")
for tool, group in (("subagent", "worker"), ("subagent_planning", "planning")):
    names = set(_routes.GROUPS[group])
    assert set(hint.group(1 if group == "worker" else 2).split(", ")) == names, (tool, hint.groups())
    assert "`%s` (%s)" % (tool, ", ".join(_routes.GROUPS[group])) in note, note
ok("the dispatch hint and the roles' dsh note name every role of each route group (mission roles included)")
assert re.search(r'const PROVIDER = "([^"]+)"', mjs).group(1) == ins.DSH_PROVIDER, ins.DSH_PROVIDER
ok("the plugin routes a bare model id to the provider the profile names (a project's pinned id is that provider's)")

print("\n%d checks passed" % n)
