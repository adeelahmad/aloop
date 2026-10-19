#!/usr/bin/env python3
"""The gates hold on any language, not only Rust. RED must fail by assertion. Before this, gate-red-verify ran
tests only in a cargo repo; in a Python repo a RED whose tests already passed was accepted with
a warning, so the loop's first guarantee (the test exercises something) was not checked.

The test runner is the one the project's own standards.md gate matrix declares. pytest's exit
codes tell an assertion failure (1) from a collection error such as a missing import (2) and
from no tests at all (5); the fixture's `pytest` is a small stand-in with those exit codes, so
the test needs no pytest install.
GREEN and the final gate refuse a skipped test in Python, Go and JS as they do `#[ignore]` in
Rust, and the final gate finds a planned `def test_x` / `func TestX` on main.
    python3 scripts/test/any-language.test.py
"""
import atexit
import os
import shutil
import stat
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
BIN = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugin", "bin"))
T = os.path.realpath(tempfile.mkdtemp(prefix="aloop-red-any-"))
atexit.register(shutil.rmtree, T, ignore_errors=True)
MAIN = os.path.join(T, "repo")
STORY = os.path.join(MAIN, "docs", "agents", "sprint1", "s1-01-calc")
FAKE = os.path.join(T, "fakebin")
ENV = {k: v for k, v in os.environ.items()
       if k not in ("AGENTIC_HOST", "SKIP_HOOKS", "STORY_DIR", "AGENT_ROLE", "REPO_DIR", "BASE_REF", "SCOPE_GLOBS", "TEST_GLOBS")}
ENV.update(HOME=os.path.join(T, "home"), PYTHONDONTWRITEBYTECODE="1", PATH=FAKE + os.pathsep + os.environ["PATH"])
n = [0]

# pytest's documented exit codes: 0 all passed, 1 tests failed, 2 collection error, 5 none collected
PYTEST = r'''#!/usr/bin/env python3
import glob, importlib.util, os, sys, traceback
sys.path.insert(0, os.getcwd())
files = sorted(glob.glob("tests/**/test_*.py", recursive=True))
fns, failed = [], 0
for f in files:
    spec = importlib.util.spec_from_file_location(f[:-3].replace("/", "."), f)
    m = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(m)
    except Exception:
        traceback.print_exc(); print("ERROR collecting " + f); sys.exit(2)
    fns += [(f, k, v) for k, v in vars(m).items() if k.startswith("test_") and callable(v)]
if not fns:
    print("no tests ran"); sys.exit(5)
for f, k, v in fns:
    try:
        v(); print("%s::%s PASSED" % (f, k))
    except AssertionError:
        failed += 1; print("%s::%s FAILED" % (f, k))
sys.exit(1 if failed else 0)
'''


def ok(msg):
    n[0] += 1
    print("ok - " + msg)


def sh(*args, cwd=MAIN):
    r = subprocess.run(args, cwd=cwd, env=ENV, capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(text)


os.makedirs(FAKE)
write(os.path.join(FAKE, "pytest"), PYTEST)
os.chmod(os.path.join(FAKE, "pytest"), 0o755)
os.makedirs(MAIN)
sh("git", "init", "-q", "-b", "main")
sh("git", "config", "user.email", "ada@x.io")
sh("git", "config", "user.name", "Ada")
write(os.path.join(MAIN, "calc.py"), "def add(a, b):\n    return a + b\n")
write(os.path.join(MAIN, "tests", "test_add.py"), "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
write(os.path.join(MAIN, ".gitignore"), ".aladdin/\n.agentic/\ndocs/agents/\n__pycache__/\n")
sh("git", "add", "-A")
sh("git", "commit", "-qm", "init")
write(os.path.join(STORY, "init.md"), "---\ntype: init\n---\n# init\n\n## S1-01-T1 · attempt 1 · supervisor · 2026-10-19T00:00:00Z\ncontract\n")
# Where the standards role writes it: the sprint dir, git-ignored, so absent from every worker's
# worktree (live run 36: the gate looked only in the worktree and ran the Rust matrix instead)
STANDARDS = os.path.join(os.path.dirname(STORY), "standards.md")
write(STANDARDS, "# standards\n\n## Gate matrix\n\n```\npytest -q\n```\n")


def red(name, files, rows):
    """A RED worker's committed worktree with its report block."""
    wt = os.path.join(T, name)
    sh("git", "worktree", "add", "-q", "-b", "agentic/" + name, wt)
    write(os.path.join(wt, ".aladdin", "task.env"),
          "TASK_ID=S1-01-T1\nATTEMPT=1\nAGENT_ROLE=red-worker\nSTORY_DIR=%s\nBASE_REF=main\n" % STORY)
    for p, text in files.items():
        write(os.path.join(wt, p), text)
    sh("git", "add", "-A", cwd=wt)
    sh("git", "commit", "-qm", "red " + name, cwd=wt)
    table = "\n".join("| %s | %s |" % r for r in rows)
    write(os.path.join(STORY, "output.md"),
          "---\ntype: output\n---\n# output\n\n## S1-01-T1 · attempt 1 · red-worker · 2026-10-19T00:01:00Z\n"
          "### Summary\nwork\n### Result\n| Check | Status |\n|---|---|\n%s\nregressed=0\n### Next\nnext\n" % table)
    env = dict(ENV, REPO_DIR=wt)
    return subprocess.run([os.path.join(BIN, "gate-red-verify")], cwd=wt, env=env, capture_output=True, text=True, input="")


SHIM = "def sub(a, b):  # agentic:shim\n    return 0\n"

# 1) a RED that fails by assertion against a conftest/test-side shim is accepted
r = red("assert", {"tests/shim.py": SHIM,
                   "tests/test_sub.py": "from tests.shim import sub\n\n\ndef test_sub():\n    assert sub(3, 1) == 2\n"},
        [("tests/test_sub.py::test_sub", "FAIL")])
assert r.returncode == 0 and "RED verified" in r.stderr, r.stderr
ok("Python RED that fails by assertion (pytest exit 1) is accepted")

# 2) a RED whose new test already passes is rejected, even when its report says FAIL
r = red("passes", {"tests/test_sub.py": "def test_sub():\n    assert 3 - 1 == 2\n"},
        [("tests/test_sub.py::test_sub", "FAIL")])
assert r.returncode == 2 and "PASS with no production code" in r.stderr, r.stderr
ok("Python RED whose tests pass (pytest exit 0) is rejected, whatever the report says")

# 3) a RED that cannot import what it tests fails by collection error, not assertion
r = red("import", {"tests/test_sub.py": "from calc import sub\n\n\ndef test_sub():\n    assert sub(3, 1) == 2\n"},
        [("tests/test_sub.py::test_sub", "FAIL")])
assert r.returncode == 2 and "collection" in r.stderr and "shim" in r.stderr, r.stderr
ok("Python RED that fails to import (pytest exit 2) is rejected, and the block says to shim")

# 4) a RED with no test the runner collects is rejected
r = red("none", {"tests/notes.txt": "todo\n"}, [("tests/test_sub.py::test_sub", "FAIL")])
assert r.returncode == 2, r.stderr
ok("Python RED with no collected test (pytest exit 5) is rejected")

# 4b) no gate matrix anywhere in a non-cargo project: a block that names standards.md, not the
#     Rust default (`cargo fmt` cannot pass in a Python repo)
os.rename(STANDARDS, STANDARDS + ".off")
r = red("nomatrix", {"tests/shim.py": SHIM,
                     "tests/test_sub.py": "from tests.shim import sub\n\n\ndef test_sub():\n    assert sub(3, 1) == 2\n"},
        [("tests/test_sub.py::test_sub", "FAIL")])
os.rename(STANDARDS + ".off", STANDARDS)
assert r.returncode == 2 and "standards.md" in r.stderr and "cargo fmt" not in r.stderr, r.stderr
ok("the matrix is found in the story's sprint dir from a worker's worktree; with none at all, a non-cargo repo blocks on that, not on cargo")

# 4c) the standards role cannot stop on a standards.md whose matrix the gates cannot read
#     (live run 37: RED blocked 3x on "no gate matrix found" until standards was re-run)
cited = os.path.join(BIN, "gate-standards-cited")
bad = os.path.join(T, "std-table.md")
write(bad, "# standards\n\n## Gate matrix\n\n| gate | command |\n|---|---|\n| test | `python3 -m pytest -q` |\n")
r = subprocess.run([cited, bad], cwd=MAIN, env=ENV, capture_output=True, text=True)
assert r.returncode == 2 and "## Gate matrix" in r.stderr and "fenced" in r.stderr, r.stderr
r = subprocess.run([cited, STANDARDS], cwd=MAIN, env=ENV, capture_output=True, text=True)
assert r.returncode == 0, r.stderr
ok("the standards role's stop gate refuses a matrix with no fenced commands (a table only) and accepts a fenced one")

# 5) an earlier task's test listed as PASS is context, not a new passing test (Python `def`)
r = red("context", {"tests/shim.py": SHIM,
                    "tests/test_sub.py": "from tests.shim import sub\n\n\ndef test_sub():\n    assert sub(3, 1) == 2\n"},
        [("tests/test_add.py::test_add", "PASS"), ("tests/test_sub.py::test_sub", "FAIL")])
assert r.returncode == 0, r.stderr
ok("a PASS row for a test already on the base (Python `def`) is accepted as context")

# 5b) GREEN drops RED's shim from the test file: any language's test file is in GREEN's scope,
#     not only Rust's tests/*.rs (live run 39: a Go GREEN removing the shim from roman_test.go was
#     blocked 10 times as out of scope)
wt = os.path.join(T, "green")
sh("git", "worktree", "add", "-q", "-b", "agentic/green", wt, "agentic/assert")
write(os.path.join(wt, ".aladdin", "task.env"),
      "TASK_ID=S1-01-T1\nATTEMPT=1\nAGENT_ROLE=green-worker\nSTORY_DIR=%s\nBASE_REF=main\nSCOPE_GLOBS=calc.py\n" % STORY)
write(os.path.join(wt, "calc.py"), "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n")
os.remove(os.path.join(wt, "tests", "shim.py"))
write(os.path.join(wt, "tests", "test_sub.py"), "from calc import sub\n\n\ndef test_sub():\n    assert sub(3, 1) == 2\n")
write(os.path.join(wt, "sub_test.go"), "package calc\n")
sh("git", "add", "-A", cwd=wt)
sh("git", "commit", "-qm", "green", cwd=wt)
write(os.path.join(STORY, "output.md"),
      "---\ntype: output\n---\n# output\n\n## S1-01-T1 · attempt 1 · green-worker · 2026-10-19T00:02:00Z\n"
      "### Summary\nwork\n### Result\n| Check | Status |\n|---|---|\n| tests/test_sub.py::test_sub | PASS |\nregressed=0\n### Next\nnext\n")
r = subprocess.run([os.path.join(BIN, "gate-green-verify")], cwd=wt, env=dict(ENV, REPO_DIR=wt), capture_output=True, text=True, input="")
assert "out-of-scope" not in r.stderr, r.stderr
assert r.returncode == 0, r.stderr
write(os.path.join(wt, "other.py"), "x = 1\n")
sh("git", "add", "-A", cwd=wt)
sh("git", "commit", "-qm", "green 2", cwd=wt)
r = subprocess.run([os.path.join(BIN, "gate-green-verify")], cwd=wt, env=dict(ENV, REPO_DIR=wt), capture_output=True, text=True, input="")
assert r.returncode == 2 and "out-of-scope path: other.py" in r.stderr, r.stderr
ok("GREEN may edit Python and Go test files (to drop RED's shims) besides its scope; any other file is out of scope")

# 6) suppressions in Python, Go and JS are refused like #[ignore]; the final gate finds a planned
#    Python test on main by its `def`
def final(files, plan_refs):
    for p_, text in files.items():
        write(os.path.join(MAIN, p_), text)
    write(os.path.join(STORY, "plan.md"), "---\ntype: plan\nstory: S1-01\n---\n# plan\n\n## T1 tests\n\n"
          + "".join("- `%s`\n  - planned.\n" % ref for ref in plan_refs))
    env = dict(ENV, REPO_DIR=MAIN, STORY_DIR=STORY, AGENTIC_SELFCHECK="1",
               STANDARDS_FILE=os.path.join(T, "std-none.md"))
    return subprocess.run([os.path.join(BIN, "gate-final")], cwd=MAIN, env=env, capture_output=True, text=True, input="")


write(os.path.join(T, "std-none.md"), "# standards\n\n## Gate matrix\n\n```\ntrue\n```\n")
r = final({}, ["tests/test_add.py::test_add"])
assert r.returncode == 0, r.stderr
r = final({}, ["tests/test_add.py::test_add", "tests/test_add.py::test_dropped"])
assert r.returncode == 2 and "test_dropped" in r.stderr, r.stderr
ok("final gate: a planned Python test is found on main by its `def`; a dropped one blocks")
for name, text in (("tests/test_skip.py", "import pytest\n\n\n@pytest.mark.skip(reason='later')\ndef test_x():\n    assert False\n"),
                   ("tests/test_xfail.py", "import pytest\n\n\n@pytest.mark.xfail\ndef test_x():\n    assert False\n"),
                   ("tests/test_inline.py", "import pytest\n\n\ndef test_x():\n    pytest.skip('later')\n"),
                   ("tests/test_ut.py", "import unittest\n\n\n@unittest.skip('later')\ndef test_x():\n    assert False\n"),
                   ("calc_test.go", "package calc\n\nimport \"testing\"\n\nfunc TestX(t *testing.T) { t.Skip(\"later\") }\n"),
                   ("web/a.test.js", "describe.skip('a', () => {});\n"),
                   ("web/b.test.ts", "xdescribe('b', () => {});\n")):
    r = final({name: text}, ["tests/test_add.py::test_add"])
    assert r.returncode == 2 and "test-suppression" in r.stderr, (name, r.stderr)
    os.remove(os.path.join(MAIN, name))
r = final({"tests/test_ok.py": "def test_skip_words():\n    assert 'skip'\n"}, ["tests/test_add.py::test_add"])
assert r.returncode == 0, r.stderr
ok("final gate: pytest/unittest skip and xfail, Go t.Skip and JS .skip/xdescribe are suppressions; a test named skip is not")

# 7) Go: the same rule through `go test`, whose exit code is 1 for both a build failure and a
#    failed assertion; the build failure is told apart by its "[build failed]" line
GO = shutil.which("go")
if GO:
    G = os.path.join(T, "gorepo")
    os.makedirs(G)
    for c in (("git", "init", "-q", "-b", "main"), ("git", "config", "user.email", "ada@x.io"), ("git", "config", "user.name", "Ada")):
        sh(*c, cwd=G)
    write(os.path.join(G, "go.mod"), "module calc\n\ngo 1.20\n")
    write(os.path.join(G, "calc.go"), "package calc\n\nfunc Add(a, b int) int { return a + b }\n")
    write(os.path.join(G, ".gitignore"), ".aladdin/\n")
    write(os.path.join(G, "standards.md"), "# standards\n\n## Gate matrix\n\n```\ngo test ./...\n```\n")
    sh("git", "add", "-A", cwd=G)
    sh("git", "commit", "-qm", "init", cwd=G)
    ENV.update(GOCACHE=os.path.join(T, "gocache"), GOFLAGS="-mod=mod", GOTOOLCHAIN="local")
    for name, files, want in (
            ("go-build", {"calc_sub_test.go": 'package calc\n\nimport "testing"\n\nfunc TestSub(t *testing.T) { if Sub(3, 1) != 2 { t.Fatal("sub") } }\n'}, 2),
            ("go-assert", {"calc_sub_test.go": 'package calc\n\nimport "testing"\n\nfunc Sub(a, b int) int { return 0 } // agentic:shim\n\n'
                           'func TestSub(t *testing.T) { if Sub(3, 1) != 2 { t.Fatal("sub") } }\n'}, 0)):
        wt = os.path.join(T, name)
        sh("git", "worktree", "add", "-q", "-b", "agentic/" + name, wt, cwd=G)
        write(os.path.join(wt, ".aladdin", "task.env"),
              "TASK_ID=S1-01-T1\nATTEMPT=1\nAGENT_ROLE=red-worker\nSTORY_DIR=%s\nBASE_REF=main\n" % STORY)
        for p_, text in files.items():
            write(os.path.join(wt, p_), text)
        sh("git", "add", "-A", cwd=wt)
        sh("git", "commit", "-qm", name, cwd=wt)
        write(os.path.join(STORY, "output.md"),
              "---\ntype: output\n---\n# output\n\n## S1-01-T1 · attempt 1 · red-worker · 2026-10-19T00:01:00Z\n"
              "### Summary\nwork\n### Result\n| Check | Status |\n|---|---|\n| calc_sub_test.go::TestSub | FAIL |\nregressed=0\n### Next\nnext\n")
        r = subprocess.run([os.path.join(BIN, "gate-red-verify")], cwd=wt, input="", capture_output=True, text=True,
                           env=dict(ENV, REPO_DIR=wt, STANDARDS_FILE=os.path.join(G, "standards.md")))
        assert r.returncode == want, (name, r.stderr)
        assert want == 0 or "do NOT compile" in r.stderr, r.stderr
    ok("Go RED: a build failure is rejected (shim it in a _test.go file), a failed assertion is accepted")
else:
    print("skip - go not on PATH")

print("\n%d checks passed" % n[0])
