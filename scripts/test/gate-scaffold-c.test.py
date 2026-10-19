#!/usr/bin/env python3
"""Regression: gate-scaffold-verify must count C function DEFINITIONS. Before the fix
ctx-symbols did not parse C, so every C stub read as "defined 0 times" and the gate
blocked a correct scaffold.   python3 scripts/test/gate-scaffold-c.test.py

  one definition (+ prototype in a header + a call)  -> pass (rc 0)
  two definitions                                     -> block (rc 2, "defined 2 times")
  prototype only                                      -> block (rc 2, "defined 0 times")

Needs a real ctx-symbols: $CTX_SYMBOLS, else plugin/tools/ctx-symbols/target/release/ctx-symbols.
"""
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
GATE = os.path.join(REPO, "plugin", "bin", "gate-scaffold-verify")
CTX = os.environ.get("CTX_SYMBOLS") or os.path.join(
    REPO, "plugin", "tools", "ctx-symbols", "target", "release", "ctx-symbols")
if not os.access(CTX, os.X_OK):
    sys.exit("FAIL: ctx-symbols binary not found at %s (run `make build` or set CTX_SYMBOLS)" % CTX)

T = tempfile.mkdtemp(prefix="agentic-scaffold-c-")
TOOLS = os.path.join(T, "data", "bin")
os.makedirs(TOOLS)
shutil.copy(CTX, os.path.join(TOOLS, "ctx-symbols"))   # the plugin data dir is resolved first
ENV = dict(os.environ, HOME=os.path.join(T, "home"), CLAUDE_PLUGIN_DATA=os.path.join(T, "data"),
           AGENTIC_NO_AUTOINSTALL="1", AGENT_ROLE="scaffolder")
for k in ("CLAUDE_PLUGIN_ROOT", "SCAFFOLD_SYMBOLS", "REPO_DIR", "SKIP_HOOKS"):
    ENV.pop(k, None)
PASS = [0]

DEF = """#include <stdint.h>
#define TODO(fn) fprintf(stderr, "SUB-AGENT-TODO: %s\\n", fn)

void *core_new(uint32_t n)
{
    (void)n;
    TODO("core_new");
    return 0;
}
"""
PROTO = "void *core_new(uint32_t n);\n"
CALL = '#include "core.h"\nint main(void) { core_new(1); return 0; } /* SUB-AGENT-TODO */\n'


def write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)


def fixture(name, files):
    # A scaffolder must run in a LINKED worktree (gate invariant), so each fixture is one.
    base, root = os.path.join(T, name + "-base"), os.path.join(T, name)
    subprocess.run(["git", "init", "-q", base], check=True)
    subprocess.run(["git", "-C", base, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                    "--allow-empty", "-m", "base"], check=True)
    subprocess.run(["git", "-C", base, "worktree", "add", "-q", root], check=True)
    story = os.path.join(root, "docs", "agents", "sprint1", "s1-01-x")
    write(story, "init.md", "---\ntype: init\n---\n\n## T1 · dispatch\n")
    write(story, "output.md", "---\ntype: output\n---\n\n## T1 · attempt 1 · scaffolder · now\n"
          "### Summary\nx\n### Result\nx\n### Next\nx\n")
    for rel, text in files.items():
        write(root, rel, text)
    return root, story


def gate(root, story):
    env = dict(ENV, REPO_DIR=root, STORY_DIR=story, SCAFFOLD_SYMBOLS="core_new")
    return subprocess.run([GATE], cwd=root, env=env, capture_output=True, text=True)


def ok(name):
    PASS[0] += 1
    print("ok -", name)


r = gate(*fixture("one", {"src/core.c": DEF, "src/core.h": PROTO, "src/main.c": CALL}))
assert r.returncode == 0, "one C definition must pass, got rc=%d\n%s" % (r.returncode, r.stderr + r.stdout)
ok("one C definition (plus prototype and call) passes")

r = gate(*fixture("two", {"src/a.c": DEF, "src/b.c": DEF}))
assert r.returncode == 2, "two C definitions must block, got rc=%d\n%s" % (r.returncode, r.stderr)
assert "defined 2 times" in r.stderr, r.stderr
ok("two C definitions block")

r = gate(*fixture("proto", {"src/core.h": PROTO, "src/main.c": CALL}))
assert r.returncode == 2, "prototype only must block, got rc=%d\n%s" % (r.returncode, r.stderr)
assert "defined 0 times" in r.stderr, r.stderr
ok("prototype-only counts zero and blocks")

shutil.rmtree(T, ignore_errors=True)
print("gate-scaffold-c: %d passed" % PASS[0])
