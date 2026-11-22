#!/usr/bin/env python3
"""ROADMAP M11.12: a task's diff stays inside the fileset its plan names, and a worker steps out of
it only with a declared, reasoned, two-way exemption that the gate decides with no human.

Each row is a fresh repo and task worktree judged by the real gate-green-verify (matrix skipped):
true positives and negatives, the bypasses found in the 2026-11-19 audit (another task's test
weakened or deleted, a file hidden under a nested target/, SCOPE_GLOBS or a planner glob widening
the plan, a task with no targets, a quoted non-ASCII path), the exemption flow (taken and recorded,
one-way path, thin reason, glob, test weakening, over the cap), and RED's existing-test rule.
On master before M11.12, 9 of the 19 GREEN rows held.
    python3 scripts/test/task-scope.test.py
"""
import json
import os
import shutil
import subprocess
import tempfile

PLUGIN = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(PLUGIN, "libexec")
ENV = dict(os.environ, PATH="/usr/bin:/bin", AGENTIC_NO_AUTOINSTALL="1",
           GIT_AUTHOR_NAME="A", GIT_AUTHOR_EMAIL="a@a", GIT_COMMITTER_NAME="A", GIT_COMMITTER_EMAIL="a@a")
for k in ("AGENTIC_HOST", "SCOPE_GLOBS", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA", "BASE_REF", "ALOOP_SCOPE_EXEMPT_MAX"):
    ENV.pop(k, None)
FAILS = []


def sh(args, cwd, **env):
    return subprocess.run(args, cwd=cwd, env=dict(ENV, **env), capture_output=True, text=True)


def write(p, t):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(t)


def target(ref, task, op, path, sym):
    key = "planned_symbol" if op == "create" else "symbol"
    return {"change_ref": ref, "task": task, "operation": op, "path": path, key: sym, "reason": "r",
            "evidence_refs": [], "constraints": []}


TARGETS = {"tasks": [
    {"id": "T1", "title": "app", "tests": [{"path": "tests/test_app.py", "fn": "test_main", "assert": "a"}]},
    {"id": "T2", "title": "export", "tests": [{"path": "tests/test_export.py", "fn": "test_csv", "assert": "a"}]},
    {"id": "T3", "title": "none", "tests": [{"path": "tests/test_x.py", "fn": "test_x", "assert": "a"}]}],
    "targets": [target("C1", "T2", "modify", "src/report.py", "render"),
                target("C2", "T2", "create", "src/export.py", "write_csv"),
                target("C3", "T1", "modify", "src/app.py", "main")]}


def setup(task, targets, out_extra, role):
    T = tempfile.mkdtemp(prefix="aloop-taskscope-")
    sd = os.path.join(T, "docs", "agents", "sprint1", "s1-01-export")
    sh(["git", "init", "-q", "-b", "main"], T)
    write(os.path.join(T, "src", "report.py"), "def render(rows):\n    return rows\n")
    write(os.path.join(T, "src", "app.py"), "def main():\n    pass\n")
    write(os.path.join(T, "tests", "test_app.py"), "def test_main():\n    assert 1\n")
    write(os.path.join(T, ".gitignore"), "/docs/\n.agentic/\n.aladdin/\n")
    sh(["git", "add", "-A"], T)
    sh(["git", "commit", "-q", "-m", "init"], T)
    write(os.path.join(sd, "tasks.md"), "## T1 — a\n## T2 — b\n## T3 — c\n")
    if targets is not None:
        write(os.path.join(sd, "targets.json"), json.dumps(targets))
    write(os.path.join(sd, "init.md"), "---\ntype: init\n---\n")
    write(os.path.join(sd, "output.md"), "---\ntype: output\n---\n## S1-01-%s · attempt 1 · %s · now\n"
          "### Summary\nok\n### Result\nok\n%s### Next\nnone\n" % (task, role, out_extra))
    e = dict(HOME=os.path.join(T, "home"))
    wt = sh([os.path.join(BIN, "task-worktree"), "add", "S1-01-" + task], T, **e).stdout.strip()
    assert wt and os.path.isdir(wt), "task-worktree add failed"
    return T, sd, wt, e


def edit(wt, edits):
    for op, path, text in edits:
        full = os.path.join(wt, path)
        if op == "w":
            write(full, text)
        elif op == "rm":
            os.remove(full)
        elif op == "mv":
            os.makedirs(os.path.dirname(os.path.join(wt, text)), exist_ok=True)
            os.rename(full, os.path.join(wt, text))
    sh(["git", "add", "-A"], wt)
    sh(["git", "commit", "-q", "-m", "work"], wt)


def check(name, ok, detail=""):
    print("%s - %s%s" % ("ok" if ok else "NOT OK", name, "" if ok else "\n    " + detail[-600:]))
    if not ok:
        FAILS.append(name)


def green(name, ideal, edits, task="T2", targets=TARGETS, out_extra="", want="", **env):
    T, sd, wt, e = setup(task, targets, out_extra, "green-worker")
    try:
        edit(wt, edits)
        g = dict(e, AGENT_ROLE="green-worker", TASK_ID="S1-01-" + task, ATTEMPT="1", STORY_DIR=sd,
                 GATE_RUN_MATRIX="0", **env)
        r = sh([os.path.join(BIN, "gate-green-verify")], wt, **g)
        got = {0: "pass", 2: "block"}.get(r.returncode, "rc%d" % r.returncode)
        check("GREEN %s → %s" % (name, ideal), got == ideal and want in r.stderr, "got %s: %s" % (got, r.stderr))
        return r, T
    finally:
        shutil.rmtree(T, ignore_errors=True)


W = "def write_csv(rows):\n    return ''\n"
X = "x = 1\n"

# true negatives and positives
green("in-scope edit", "pass", [("w", "src/export.py", W)], want="planned fileset")
green("in-scope edit plus its own new test", "pass", [("w", "src/export.py", W), ("w", "tests/test_export.py", "def test_csv(): pass\n")])
green("another task's production file", "block", [("w", "src/app.py", X)], want="src/app.py: outside this task's planned fileset")
green("a new production file no target names", "block", [("w", "src/util.py", X)], want="### Scope exemptions")
green("renaming an in-scope file out of the plan", "block", [("mv", "src/report.py", "lib/report.py")], want="lib/report.py")
green("deleting another task's production file", "block", [("rm", "src/app.py", "")], want="src/app.py")
green("a new unplanned test file", "pass", [("w", "src/export.py", W), ("w", "tests/test_extra.py", "def test_e(): pass\n")])
green("adding lines to another task's test file", "pass", [("w", "tests/test_app.py", "def test_main():\n    assert 1\n\ndef test_more():\n    assert 2\n")])

# bypasses found in the audit
green("weakening another task's existing test", "block", [("w", "tests/test_app.py", "def test_main():\n    pass\n")],
      want="removes or changes 1 line(s) of an existing test")
green("deleting another task's existing test", "block", [("rm", "tests/test_app.py", "")], want="deletes an existing test file")
green("a production file hidden under a nested target/", "block", [("w", "src/target/evil.py", X)], want="src/target/evil.py")
green("a contract SCOPE_GLOBS='*' never widens the plan", "block", [("w", "src/app.py", X)], SCOPE_GLOBS="*")
green("a contract SCOPE_GLOBS narrows the plan", "block", [("w", "src/report.py", "def render(rows):\n    return 1\n")],
      SCOPE_GLOBS="src/export.py", want="src/report.py")
green("a task with no targets gets no free scope", "block", [("w", "src/app.py", X)], task="T3", want="(tests/test_x.py)")
wide = json.loads(json.dumps(TARGETS))
wide["targets"].append(target("C4", "T2", "create", "src/*", "x"))
green("a planner glob path is literal, never a wildcard", "block", [("w", "src/app.py", X)], targets=wide)
uni = json.loads(json.dumps(TARGETS))
uni["targets"].append(target("C5", "T2", "create", "src/réport.py", "x"))
green("a non-ASCII planned path matches (no git quoting)", "pass", [("w", "src/réport.py", X)], targets=uni)

# the exemption flow: decided by the gate, recorded, no human
EX = "### Scope exemptions\n- src/util.py: the CSV writer needs a shared quoting helper that no target names\n"
r, _ = green("a declared, reasoned two-way exemption is taken", "pass", [("w", "src/export.py", W), ("w", "src/util.py", X)],
             out_extra=EX, want="scope exemption taken: src/util.py")
green("an exemption for a CI workflow (one-way) is refused", "block", [("w", ".github/workflows/ci.yml", "x: 1\n")],
      out_extra="### Scope exemptions\n- .github/workflows/ci.yml: the new module needs its own CI job here\n", want="one-way path")
green("an exemption for .gitignore (one-way) is refused", "block", [("w", ".gitignore", "/docs/\n.agentic/\n.aladdin/\nsrc/\n")],
      out_extra="### Scope exemptions\n- .gitignore: the build writes a cache file we should ignore\n", want="one-way path")
green("an exemption with no reason is refused", "block", [("w", "src/util.py", X)],
      out_extra="### Scope exemptions\n- src/util.py\n", want="is not `- <path>: <reason>`")
green("an exemption with a thin reason is refused", "block", [("w", "src/util.py", X)],
      out_extra="### Scope exemptions\n- src/util.py: needed\n", want="too thin")
green("an exemption naming a glob is refused", "block", [("w", "src/util.py", X)],
      out_extra="### Scope exemptions\n- src/*: many helpers were needed for this change\n", want="never a glob")
green("an exemption never weakens another task's test", "block", [("w", "tests/test_app.py", "def test_main():\n    pass\n")],
      out_extra="### Scope exemptions\n- tests/test_app.py: the old assertion no longer holds after this change\n",
      want="removes or changes")
many = "### Scope exemptions\n" + "".join("- src/u%d.py: a helper the plan did not foresee here\n" % i for i in range(4))
green("more exemptions than the cap is a re-plan", "block", [("w", "src/u%d.py" % i, X) for i in range(4)],
      out_extra=many, want="exceed the cap of 3")
green("the cap is configurable", "pass", [("w", "src/u%d.py" % i, X) for i in range(4)], out_extra=many,
      ALOOP_SCOPE_EXEMPT_MAX="4")
green("an exemption in another task's block does not count", "block", [("w", "src/util.py", X)],
      out_extra=EX.replace("### Scope", "## S1-01-T1 · attempt 1 · green-worker · now\n### Summary\nx\n### Scope"))

# a taken exemption is a ledger row
T, sd, wt, e = setup("T2", TARGETS, EX, "green-worker")
try:
    edit(wt, [("w", "src/export.py", W), ("w", "src/util.py", X)])
    g = dict(e, AGENT_ROLE="green-worker", TASK_ID="S1-01-T2", ATTEMPT="1", STORY_DIR=sd, GATE_RUN_MATRIX="0")
    r = sh([os.path.join(BIN, "gate-green-verify")], wt, **g)
    rows = []
    for root, _, files in os.walk(T):
        for f in files:
            if f.endswith(".jsonl"):
                for ln in open(os.path.join(root, f), errors="replace"):
                    if '"scope.exempted"' in ln:
                        rows.append(json.loads(ln))
    check("a taken exemption is a scope.exempted ledger row with its path and reason",
          r.returncode == 0 and any(x["data"].get("path") == "src/util.py" and "quoting helper" in x["data"].get("reason", "")
                                    for x in rows), "rc=%s rows=%s %s" % (r.returncode, rows, r.stderr))
finally:
    shutil.rmtree(T, ignore_errors=True)

# targets check refuses a glob path; RED may add to an existing test, never rewrite one
T, sd, wt, e = setup("T2", wide, "", "red-worker")
try:
    r = sh([os.path.join(BIN, "targets"), "check", sd], T, **e)
    check("targets check refuses a glob target path", r.returncode == 2 and "not a glob" in r.stderr, r.stderr)
finally:
    shutil.rmtree(T, ignore_errors=True)
for name, edits, rc in (("RED adding a test to an existing file", [("w", "tests/test_app.py", "def test_main():\n    assert 1\n\ndef test_b():\n    assert 0\n")], 0),
                        ("RED rewriting another task's test", [("w", "tests/test_app.py", "def test_main():\n    assert 0\n")], 2),
                        ("RED deleting another task's test", [("rm", "tests/test_app.py", "")], 2)):
    T, sd, wt, e = setup("T2", TARGETS, "", "red-worker")
    try:
        edit(wt, edits)
        base = sh(["git", "rev-parse", "main"], T).stdout.strip()
        r = sh([os.path.join(BIN, "targets"), "diffscope", "T2", sd, "--base", base, "--root", wt, "--role", "red",
                "--tests", "tests/* test_*.py"], wt, **e)
        check("%s → exit %d" % (name, rc), r.returncode == rc, r.stderr)
    finally:
        shutil.rmtree(T, ignore_errors=True)

if FAILS:
    raise SystemExit("\n%d check(s) failed: %s" % (len(FAILS), "; ".join(FAILS)))
print("\nall task-scope checks passed")
