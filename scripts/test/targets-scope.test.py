#!/usr/bin/env python3
"""ROADMAP §4.6 "derive anything that can be derived": a GREEN worker's diff scope comes
from the planner's change targets when the supervisor gave no SCOPE_GLOBS. The real
gate-green-verify runs in a real task worktree (matrix skipped).
    python3 scripts/test/targets-scope.test.py
"""
import json
import os
import subprocess
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-scope-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"), PATH="/usr/bin:/bin", AGENTIC_NO_AUTOINSTALL="1",
           GIT_AUTHOR_NAME="A", GIT_AUTHOR_EMAIL="a@a", GIT_COMMITTER_NAME="A", GIT_COMMITTER_EMAIL="a@a")
for k in ("AGENTIC_HOST", "SCOPE_GLOBS", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA"):
    ENV.pop(k, None)
PASS = [0]
SD = os.path.join(T, "docs", "agents", "sprint1", "s1-01-export")


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def sh(*args, cwd=T, check=True, **env):
    r = subprocess.run(args, cwd=cwd, env=dict(ENV, **env), capture_output=True, text=True)
    assert not check or r.returncode == 0, (args, r.stdout, r.stderr)
    return r


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(text)


sh("git", "init", "-q", "-b", "main")
write(os.path.join(T, "src", "report.py"), "def render(rows):\n    return rows\n")
write(os.path.join(T, ".gitignore"), "docs/\n.agentic/\n")
sh("git", "add", "-A"); sh("git", "commit", "-q", "-m", "init")
write(os.path.join(SD, "tasks.md"), "## T1 — a\n## T2 — b\n")
write(os.path.join(SD, "targets.json"), json.dumps({"targets": [
    {"change_ref": "C1", "task": "T2", "operation": "modify", "path": "src/report.py", "symbol": "render",
     "reason": "r", "evidence_refs": [], "constraints": []},
    {"change_ref": "C2", "task": "T2", "operation": "create", "path": "src/export.py", "planned_symbol": "write_csv",
     "reason": "r", "evidence_refs": [], "constraints": []},
    {"change_ref": "C3", "task": "T1", "operation": "modify", "path": "src/app.py", "symbol": "main",
     "reason": "r", "evidence_refs": [], "constraints": []}]}))
write(os.path.join(SD, "init.md"), "---\ntype: init\n---\n")
write(os.path.join(SD, "output.md"), "---\ntype: output\n---\n## S1-01-T2 · attempt 1 · green-worker · now\n"
      "### Summary\nok\n### Result\nok\n### Next\nnone\n")

# 1) scope is the task's target paths
assert sh(os.path.join(BIN, "targets"), "scope", "T2", SD).stdout.strip() == "src/export.py src/report.py"
assert sh(os.path.join(BIN, "targets"), "scope", "T9", SD, check=False).returncode == 1
ok("targets scope T2 → its targets' paths only")

# 2) the real GREEN gate derives SCOPE_GLOBS from targets.json
wt = sh(os.path.join(BIN, "task-worktree"), "add", "S1-01-T2").stdout.strip()
gate = dict(AGENT_ROLE="green-worker", TASK_ID="S1-01-T2", ATTEMPT="1", STORY_DIR=SD, GATE_RUN_MATRIX="0")
write(os.path.join(wt, "src", "report.py"), "def render(rows):\n    return list(rows)\n")
write(os.path.join(wt, "src", "export.py"), "def write_csv(rows):\n    return ''\n")
r = sh(os.path.join(BIN, "gate-green-verify"), cwd=wt, check=False, **gate)
assert r.returncode == 0 and "SCOPE_GLOBS derived from targets.json: src/export.py src/report.py" in r.stderr, r.stderr
ok("GREEN gate passes an in-scope diff, with the scope derived from targets.json")

write(os.path.join(wt, "src", "app.py"), "def main():\n    pass\n")
r = sh(os.path.join(BIN, "gate-green-verify"), cwd=wt, check=False, **gate)
assert r.returncode == 2 and "out-of-scope path: src/app.py" in r.stderr, r.stderr
ok("a file outside the task's targets (another task's) is blocked")

# 3) an explicit SCOPE_GLOBS still wins
r = sh(os.path.join(BIN, "gate-green-verify"), cwd=wt, check=False, SCOPE_GLOBS="src/*", **gate)
assert r.returncode == 0 and "derived" not in r.stderr, r.stderr
ok("an explicit SCOPE_GLOBS from the supervisor is used as given")

print("\n%d checks passed" % PASS[0])
