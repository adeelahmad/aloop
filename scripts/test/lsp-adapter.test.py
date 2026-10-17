#!/usr/bin/env python3
"""ROADMAP §4.6: the LSP/structural adapter seam. `targets refs|callers|callees` answer from
the server in AGENTIC_LSP_COMMAND (references, incoming/outgoing call hierarchy). A missing
server or capability is surfaced (exit 4), never guessed; a planned target has none yet.
Uses a deterministic fake server (scripts/test/fixtures/fake-lsp.py).
    python3 scripts/test/lsp-adapter.test.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.realpath(os.path.join(HERE, "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-lsp-")
FAKE = "%s %s" % (sys.executable, os.path.join(HERE, "fixtures", "fake-lsp.py"))
ENV = dict(os.environ, HOME=os.path.join(T, "home"), PATH="/usr/bin:/bin")
for k in ("AGENTIC_HOST", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA", "AGENTIC_LSP_COMMAND"):
    ENV.pop(k, None)
PASS = [0]
TG = os.path.join(BIN, "targets")
SD = os.path.join(T, "docs", "agents", "sprint1", "s1-01-export")


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def run(*args, **env):
    return subprocess.run(args, cwd=T, env=dict(ENV, **env), capture_output=True, text=True, timeout=60)


def write(rel, text):
    p = os.path.join(T, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)


subprocess.run(["git", "init", "-q"], cwd=T, check=True)
write("src/report.py", "def helper(x):\n    return str(x)\n\n\ndef render(rows):\n    return [helper(r) for r in rows]\n")
write("src/app.py", "from report import render\n\n\ndef main():\n    print(render([1, 2]))\n\n\ndef page():\n    return render([])\n")
write(os.path.relpath(os.path.join(SD, "tasks.md"), T), "## T1 — a\n## T2 — b\n")
write(os.path.relpath(os.path.join(SD, "targets.json"), T), json.dumps({"targets": [
    {"change_ref": "C1", "task": "T1", "operation": "create", "path": "src/export.py", "planned_symbol": "write_csv",
     "reason": "new", "evidence_refs": [], "constraints": []},
    {"change_ref": "C2", "task": "T2", "operation": "modify", "path": "src/report.py", "symbol": "render",
     "reason": "offer export", "evidence_refs": [], "constraints": []}]}))

# 1) references, callers and callees come from the server
r = run(TG, "refs", "C2", SD, AGENTIC_LSP_COMMAND=FAKE)
assert r.returncode == 0, r.stderr
assert r.stdout.split() == ["src/app.py:1:19", "src/app.py:5:10", "src/app.py:9:11"], r.stdout
r = run(TG, "callers", "C2", SD, AGENTIC_LSP_COMMAND=FAKE)
assert r.returncode == 0 and r.stdout.splitlines() == ["src/app.py:4 main", "src/app.py:8 page"], (r.stdout, r.stderr)
r = run(TG, "callees", "C2", SD, AGENTIC_LSP_COMMAND=FAKE)
assert r.returncode == 0 and r.stdout.splitlines() == ["src/report.py:1 helper"], (r.stdout, r.stderr)
ok("refs / callers / callees answered by the LSP server, as repo-relative locations")

# 2) configured in defaults.md works the same
write("docs/agents/defaults.md", "AGENTIC_LSP_COMMAND=%s\n" % FAKE)
assert run(TG, "callers", "C2", SD).stdout.splitlines() == ["src/app.py:4 main", "src/app.py:8 page"]
os.remove(os.path.join(T, "docs", "agents", "defaults.md"))
ok("AGENTIC_LSP_COMMAND in defaults.md selects the server")

# 3) gaps are surfaced, never guessed
r = run(TG, "callers", "C2", SD, AGENTIC_LSP_COMMAND=FAKE, FAKE_LSP_CAPS="refs")
assert r.returncode == 4 and "does not advertise callHierarchyProvider" in r.stderr and not r.stdout, r
r = run(TG, "refs", "C2", SD)
assert r.returncode == 4 and "no structural (LSP) backend" in r.stderr, r
r = run(TG, "refs", "C2", SD, AGENTIC_LSP_COMMAND="/nonexistent/lsp")
assert r.returncode == 4 and "cannot start" in r.stderr, r
ok("no server, a missing capability or a broken command → exit 4 with the reason")

# 4) a planned (shadow) target has no references yet, without asking any server
r = run(TG, "refs", "C1", SD, AGENTIC_LSP_COMMAND="/nonexistent/lsp")
assert r.returncode == 0 and "planned (shadow)" in r.stdout, r
ok("a planned target reports none yet and never touches the accepted tree")

print("\n%d checks passed" % PASS[0])
