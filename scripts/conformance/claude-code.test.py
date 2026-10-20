#!/usr/bin/env python3
"""Conformance fixtures (ROADMAP §6) around TODAY's behaviour, on the Claude Code wiring.

Each case builds a tiny Rust crate in a throw-away repo, gives a worker a real linked
worktree + .agentic/task.env + story comms (init.md/output.md), plants good or bad work,
and runs the REAL gate script the SubagentStop hook runs. A case passes when the gate's
verdict (0 = accept, 2 = block) is the expected one. No network, no tokens; needs cargo.

    python3 scripts/conformance/claude-code.test.py

Items held by a harness tool rather than a worker gate run that tool's focused test
(HELD_BY). Items no fixture can cover are listed at the end with the reason, so the gap is
explicit rather than silent.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="aloop-conf-")
# HOME is isolated, so pin rustup/cargo to the real ones (rustup finds its toolchain via HOME).
_home = os.path.expanduser("~")
ENV = dict(os.environ, RUSTUP_HOME=os.environ.get("RUSTUP_HOME", os.path.join(_home, ".rustup")),
           CARGO_HOME=os.environ.get("CARGO_HOME", os.path.join(_home, ".cargo")), HOME=os.path.join(T, "home"), CLAUDE_PLUGIN_ROOT=ROOT, PYTHONDONTWRITEBYTECODE="1",
           CARGO_TARGET_DIR=os.path.join(T, "target"), CARGO_TERM_COLOR="never")
for k in ("AGENTIC_HOST", "SKIP_HOOKS", "STORY_DIR", "AGENT_ROLE", "REPO_DIR", "BASE_REF", "SCOPE_GLOBS"):
    ENV.pop(k, None)
MAIN = os.path.join(T, "repo")
STORY = os.path.join(MAIN, "docs", "agents", "sprint1", "s1-01-calc")
results = []                                     # (item, case, ok, detail)


def sh(*args, cwd=MAIN, env=None, check=True):
    r = subprocess.run(args, cwd=cwd, env=env or ENV, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if check and r.returncode != 0:
        raise SystemExit("setup failed: %s\n%s%s" % (" ".join(args), r.stdout, r.stderr))
    return r


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


LIB = "pub fn add(a: i32, b: i32) -> i32 {\n    a + b\n}\n"
SHIM = "// aloop RED shim\npub fn sub(_a: i32, _b: i32) -> i32 {\n    0\n}\n"
RED_TEST = "mod common;\n\n#[test]\nfn sub_works() {\n    assert_eq!(common::sub(3, 1), 2);\n}\n"
GREEN_TEST = "#[test]\nfn sub_works() {\n    assert_eq!(calc::sub(3, 1), 2);\n}\n"
GREEN_LIB = LIB + "\npub fn sub(a: i32, b: i32) -> i32 {\n    a - b\n}\n"


def setup():
    os.makedirs(MAIN)
    sh("git", "init", "-q", "-b", "main")
    sh("git", "config", "user.email", "ada@x.io")
    sh("git", "config", "user.name", "Ada")
    write(os.path.join(MAIN, "Cargo.toml"), '[package]\nname = "calc"\nversion = "0.1.0"\nedition = "2021"\n\n[workspace]\n')
    write(os.path.join(MAIN, "src", "lib.rs"), LIB)
    write(os.path.join(MAIN, "tests", "add.rs"), "#[test]\nfn add_works() {\n    assert_eq!(calc::add(1, 2), 3);\n}\n")
    write(os.path.join(MAIN, ".gitignore"), ".agentic/\ndocs/agents/\ntarget/\n")
    sh("git", "add", "-A")
    sh("git", "commit", "-qm", "init")
    write(os.path.join(STORY, "init.md"), "---\ntype: init\n---\n# init\n\n## S1-01-T1 · attempt 1 · supervisor · 2026-10-17T00:00:00Z\ncontract\n")
    write(os.path.join(MAIN, "standards.md"), "# standards\n\n## Gate matrix\n\n```\ncargo test --workspace\n```\n")


def report(role, rows):
    """Append this attempt's report block to output.md (fresh file per case)."""
    table = "\n".join("| %s | %s |" % r for r in rows)
    write(os.path.join(STORY, "output.md"),
          "---\ntype: output\n---\n# output\n\n## S1-01-T1 · attempt 1 · %s · 2026-10-17T00:01:00Z\n"
          "### Summary\nwork\n### Result\n| Check | Status |\n|---|---|\n%s\nregressed=0\n### Next\nnext\n" % (role, table))


n = [0]


def worktree(role, scope=""):
    n[0] += 1
    wt = os.path.join(T, "wt%d" % n[0])
    sh("git", "worktree", "add", "-q", "-b", "agentic/case%d" % n[0], wt)
    write(os.path.join(wt, ".aladdin", "task.env"),
          "TASK_ID=S1-01-T1\nATTEMPT=1\nAGENT_ROLE=%s\nSTORY_DIR=%s\nBASE_REF=main\nSCOPE_GLOBS='%s'\n" % (role, STORY, scope))
    return wt


def gate(name, cwd, extra=None, agent_type=None, transcript=None):
    env = dict(ENV, REPO_DIR=cwd, STANDARDS_FILE=os.path.join(MAIN, "standards.md"))
    env.update(extra or {})
    payload = {"hook_event_name": "SubagentStop", "session_id": "s1", "agent_id": "a1", "agent_type": agent_type}
    if transcript:
        payload["agent_transcript_path"] = transcript
    payload = json.dumps(payload) if agent_type else ""
    r = subprocess.run([os.path.join(BIN, name)], cwd=cwd, env=env, capture_output=True, text=True, input=payload)
    return r.returncode, (r.stderr or r.stdout).strip().splitlines()


def case(item, title, name, cwd, expect, must_say=None, extra=None, agent_type=None, transcript=None):
    rc, lines = gate(name, cwd, extra, agent_type, transcript)
    blocks = [l for l in lines if l.startswith("BLOCK[")]
    good = rc == expect and (must_say is None or any(must_say in l for l in (blocks if expect else lines)))
    detail = blocks[0] if blocks else (lines[-1] if lines else "")
    results.append((item, title, good, "exit %d (want %d) %s" % (rc, expect, detail if not good else detail[:140])))


def commit(wt, msg):
    sh("git", "add", "-A", cwd=wt)
    sh("git", "commit", "-qm", msg, cwd=wt)


def red_ok():
    wt = worktree("red-worker")
    write(os.path.join(wt, "tests", "common", "mod.rs"), SHIM)
    write(os.path.join(wt, "tests", "sub.rs"), RED_TEST)
    commit(wt, "red")
    report("red-worker", [("tests/sub.rs::sub_works", "FAIL")])
    return wt


setup()

# §6.3 RED is rejected if its tests pass at first run
red_ok_wt = red_ok()
case(3, "RED that fails by assertion is accepted", "gate-red-verify", red_ok_wt, 0)
wt = worktree("red-worker")
write(os.path.join(wt, "tests", "sub.rs"), "#[test]\nfn sub_works() {\n    assert_eq!(1, 1);\n}\n")
commit(wt, "red passes")
report("red-worker", [("tests/sub.rs::sub_works", "FAIL")])          # the report lies; cargo decides
case(3, "RED whose new test passes is rejected (even if the report says FAIL)", "gate-red-verify", wt, 2, "PASS with no production code")
report("red-worker", [("tests/sub.rs::sub_works", "PASS")])
case(3, "RED that reports a PASSing new test is rejected", "gate-red-verify", wt, 2, "PASSED in RED")
wt = worktree("red-worker")
write(os.path.join(wt, "tests", "sub.rs"), "#[test]\nfn sub_works() {\n    assert_eq!(calc::sub(3, 1), 2);\n}\n")
commit(wt, "red no shim")
report("red-worker", [("tests/sub.rs::sub_works", "FAIL")])
case(3, "RED that fails by missing symbol (not by assertion) is rejected", "gate-red-verify", wt, 2, "do NOT compile")
# live e2e run 25: a RED worker blocked 5 times on a bare "add mod-common shims" after its
# mandate let it put a skeleton in src/ and the gate made it revert; the block now names the
# symbol the tests cannot find and where its shim goes.
# (one case: a third identical block in a row would be the stuck release)
case(3, "the RED compile block names the missing symbol and its shim in tests/common/mod.rs", "gate-red-verify", wt, 2,
     "cannot find `sub`. RED's tests must compile and fail by assertion, so each missing item gets a shim in tests/common/mod.rs, marked // agentic:shim: pub fn sub(")

# RED's tests must already pass the matrix's format check: GREEN may not touch them, so an
# unformatted RED file blocks every GREEN attempt (live e2e run 3: 19 GREEN fmt blocks).
FMT_STANDARDS = os.path.join(T, "standards-fmt.md")
write(FMT_STANDARDS, "# standards\n\n## Gate matrix\n\n```\ncargo fmt --all -- --check\ncargo test --workspace\n```\n")
wt = worktree("red-worker")
write(os.path.join(wt, "tests", "common", "mod.rs"), SHIM)
write(os.path.join(wt, "tests", "sub.rs"), "mod common;\n#[test]\nfn sub_works() {   assert_eq!(common::sub(3,1),2); }\n")
commit(wt, "red unformatted")
report("red-worker", [("tests/sub.rs::sub_works", "FAIL")])
case(3, "RED whose tests fail the matrix's format check is rejected", "gate-red-verify", wt, 2, "cargo fmt",
     extra={"STANDARDS_FILE": FMT_STANDARDS})
case(3, "a formatted RED passes the same format check", "gate-red-verify", red_ok_wt, 0,
     extra={"STANDARDS_FILE": FMT_STANDARDS})

# A later task's RED report also lists the earlier tasks' tests, which already pass on the base
# (live e2e run 7: "green (T1, unchanged)" rows blocked T2's RED 22 times). Only a NEW test may not PASS.
report("red-worker", [("tests/add.rs::add_works", "PASS"), ("tests/sub.rs::sub_works", "FAIL")])
case(3, "RED that reports a test already on the base as PASS is accepted", "gate-red-verify", red_ok_wt, 0)

# SCAFFOLD: `.agentic/scaffold-symbols` lines may carry notes after the symbol (live e2e run 6:
# "ParseError (enum) @ src/lib.rs" was split into `(enum)`, `@`, … and each "defined 0 times").
wt = worktree("scaffolder")
write(os.path.join(wt, "src", "lib.rs"), LIB + "\npub fn sub(_a: i32, _b: i32) -> i32 {\n    panic!(\"SUB-AGENT-TODO\")\n}\n")
write(os.path.join(wt, "tests", "sub.rs"), GREEN_TEST)
write(os.path.join(wt, ".aladdin", "scaffold-symbols"), "# symbols\nsub (fn) @ src/lib.rs\n")
commit(wt, "scaffold")
report("scaffolder", [("src/lib.rs::sub", "STUB")])
case(3, "SCAFFOLD with annotated scaffold-symbols lines is accepted", "gate-scaffold-verify", wt, 0)
# ...or a tab-separated table with a header row and trait-impl lines (live e2e run 7: `symbol`
# and `Display` were each "defined 0 times").
write(os.path.join(wt, ".aladdin", "scaffold-symbols"), "symbol\tpath\tkind\nsub\tsrc/lib.rs\tfn\nDisplay for Sub\tsrc/lib.rs\timpl\n")
case(3, "SCAFFOLD with a header row and trait-impl lines in scaffold-symbols is accepted", "gate-scaffold-verify", wt, 0)
# ...or the "### Scaffold" report shape with a title line, a verb, a keyword, a crate path and a
# deletion line (live e2e runs 18–19: `create` was "defined 0 times", four blocks in run 19).
write(os.path.join(wt, ".aladdin", "scaffold-symbols"),
      "S1-01-T1 scaffold\n+ create fn mycrate::sub @ src/lib.rs\n- delete tests/common/mod.rs (redundant shim)\n")
case(3, "SCAFFOLD whose scaffold-symbols carry verbs, keywords, crate paths and deletions is accepted", "gate-scaffold-verify", wt, 0, "1 symbol(s) unique")
write(os.path.join(wt, ".aladdin", "scaffold-symbols"), "create fn mycrate::nosuch @ src/lib.rs\n")
case(3, "SCAFFOLD naming an undefined symbol is still rejected, with the file's shape in the block",
     "gate-scaffold-verify", wt, 2, "one production symbol per line")
write(os.path.join(wt, ".aladdin", "scaffold-symbols"), "sub\n")
# A later task whose tests call only symbols an earlier task already implemented on main (live e2e
# run 31: T2's tests called T1's `parse`; the scaffolder listed it, the gate demanded a TODO panic
# in a body the scaffolder had not touched, and it hit the stuck release). A symbol that already
# existed at BASE_REF, in a file the scaffolder left unchanged, implements nothing.
wt = worktree("scaffolder")
report("scaffolder", [("src/lib.rs::add", "EXISTS")])
case(3, "SCAFFOLD listing a symbol already on main, in an unchanged file, is accepted", "gate-scaffold-verify", wt, 0,
     "1 symbol(s) unique", extra={"SCAFFOLD_SYMBOLS": "add"})
write(os.path.join(wt, "src", "lib.rs"), LIB.replace("a + b", "a + b + 0"))
commit(wt, "scaffold edits an existing body")
case(3, "SCAFFOLD that changes an existing symbol's file with no TODO stub is still rejected",
     "gate-scaffold-verify", wt, 2, "SUB-AGENT-TODO", extra={"SCAFFOLD_SYMBOLS": "add"})

# §6.4 RED cannot write production code
wt = red_ok()
write(os.path.join(wt, "src", "lib.rs"), GREEN_LIB)
commit(wt, "red writes prod")
case(4, "RED that edits src/ is rejected", "gate-red-verify", wt, 2, "non-test path: src/lib.rs")


def green(lib=GREEN_LIB, test=GREEN_TEST, scope="src/*.rs"):
    wt = worktree("green-worker", scope)
    write(os.path.join(wt, "src", "lib.rs"), lib)
    write(os.path.join(wt, "tests", "sub.rs"), test)
    commit(wt, "green")
    report("green-worker", [("tests/sub.rs::sub_works", "PASS")])
    return wt


# §6.5 GREEN cannot suppress tests
green_ok_wt = green()
case(5, "GREEN with passing tests and no suppression is accepted", "gate-green-verify", green_ok_wt, 0)
wt = green(test="#[test]\n#[ignore]\nfn sub_works() {\n    assert_eq!(calc::sub(3, 1), 2);\n}\n")
case(5, "GREEN that adds #[ignore] is rejected", "gate-green-verify", wt, 2, "suppression")
wt = green(lib=GREEN_LIB + "\n#[cfg(not(test))]\npub fn hidden() {}\n")
case(5, "GREEN that adds #[cfg(not(test))] is rejected", "gate-green-verify", wt, 2, "suppression")
# A GREEN worker whose report is not the latest block (RED's is) is told HOW to append it: live
# e2e run 25's haiku GREEN tried the Write tool on the main tree's output.md, was refused by
# worktree isolation, and stopped 3 times on a block that named no way out (stuck release).
wt = green()
report("red-worker", [("tests/sub.rs::sub_works", "FAIL")])
case(5, "GREEN whose report block is missing is told to append it with a shell heredoc", "gate-green-verify", wt, 2,
     "not from 'green-worker'")
case(5, "...and the block names the heredoc way out", "gate-green-verify", wt, 2, "cat >>")
report("green-worker", [("tests/sub.rs::sub_works", "PASS")])

# A worker's `selfcheck <role>` with no AGENT_ROLE in its env must run the gate, not crash it
# (live e2e run: `AGENT_ROLE: unbound variable` made the self-check fail closed on good work).
os.remove(os.path.join(green_ok_wt, ".aladdin", "task.env"))
report("green-worker", [("tests/sub.rs::sub_works", "PASS")])
r = subprocess.run([os.path.join(BIN, "selfcheck"), "green-worker"], cwd=green_ok_wt, capture_output=True, text=True,
                   env=dict(ENV, STORY_DIR=STORY, STANDARDS_FILE=os.path.join(MAIN, "standards.md")), stdin=subprocess.DEVNULL)
results.append((5, "`selfcheck green-worker` with no AGENT_ROLE set passes good work", r.returncode == 0,
                "exit %d %s" % (r.returncode, (r.stderr.strip().splitlines() or [""])[-1][:140])))

# §6.6 A failed gate blocks acceptance
wt = green(lib=LIB + "\npub fn sub(a: i32, b: i32) -> i32 {\n    a + b\n}\n")
case(6, "GREEN whose test still fails is blocked by the standards matrix", "gate-green-verify", wt, 2, "matrix command failed")
wt = green(scope="src/lib.rs")
write(os.path.join(wt, "build.rs"), "fn main() {}\n")
commit(wt, "out of scope")
case(6, "GREEN that touches a path outside its task scope is blocked", "gate-green-verify", wt, 2, "out-of-scope path: build.rs")
# Scope globs are patterns, never expanded against the worktree: `tests/**` expanded to
# `tests/common tests/sub.rs` and missed tests/common/mod.rs (live e2e run 8: 29 blocks).
wt = green(scope="src/*.rs tests/**")
write(os.path.join(wt, "tests", "common", "mod.rs"), "pub fn helper() -> i32 {\n    1\n}\n")
commit(wt, "helper in scope")
case(6, "GREEN inside a `dir/**` scope glob is accepted", "gate-green-verify", wt, 0)
wt = green()
os.remove(os.path.join(STORY, "output.md"))
case(6, "a worker that leaves no output.md report is blocked", "gate-green-verify", wt, 2, "no output.md")
# Claude Code's Write/Edit refuse a path outside the worker's worktree, and docs/agents/ lives only in
# the main tree, so the block must say how to append (live e2e run 10: a RED worker stopped 29 times).
case(6, "the missing-report block says how to append from an isolated worktree", "gate-green-verify", wt, 2, "cat >>")
report("red-worker", [("tests/sub.rs::sub_works", "FAIL")])
case(6, "a stale report from another role is blocked", "gate-green-verify", wt, 2, "not from 'green-worker'")

# Work must be committed on the worktree's branch, so the supervisor MERGES it (live e2e run:
# workers left their edits uncommitted and the supervisor copied files into main instead).
wt = green()
with open(os.path.join(wt, "src", "lib.rs"), "a") as f:
    f.write("\n// tweak\n")
case(6, "a worker that leaves its work uncommitted is blocked", "gate-green-verify", wt, 2, "uncommitted")

# §6.6 (cont.) The worker owns its worktree, including .agentic/task.env. Editing the
# contract must not turn a failing attempt into a pass.
def tamper(lines):
    wt = green(lib=LIB + "\npub fn sub(a: i32, b: i32) -> i32 {\n    a + b\n}\n")   # tests still fail
    with open(os.path.join(wt, ".aladdin", "task.env"), "a") as f:
        f.write("".join(l + "\n" for l in lines))
    return wt


wt = tamper(["exit 0"])
case(6, "a worker that appends `exit 0` to its task.env is still blocked", "gate-green-verify", wt, 2, "matrix command failed")
wt = tamper(["GATE_RUN_MATRIX=0"])
case(6, "a worker that sets GATE_RUN_MATRIX=0 in its task.env is still blocked", "gate-green-verify", wt, 2, "matrix command failed")
wt = tamper(["SCOPE_GLOBS='*'", "true; exit 0"])
case(6, "task.env is data, never executed (`true; exit 0`)", "gate-green-verify", wt, 2, "matrix command failed")

# §6.7 Worktrees stay isolated
write(os.path.join(MAIN, ".aladdin", "task.env"),
      "TASK_ID=S1-01-T1\nATTEMPT=1\nAGENT_ROLE=red-worker\nSTORY_DIR=%s\n" % STORY)
report("red-worker", [("tests/sub.rs::sub_works", "FAIL")])
case(7, "a RED worker running in the shared main tree is blocked", "gate-red-verify", MAIN, 2, "worktree isolation dropped")
with open(os.path.join(MAIN, ".aladdin", "task.env"), "a") as f:
    f.write("SKIP_HOOKS=1\n")
case(7, "SKIP_HOOKS=1 written into task.env does not lift isolation", "gate-red-verify", MAIN, 2, "worktree isolation dropped")
os.remove(os.path.join(MAIN, ".aladdin", "task.env"))
report("green-worker", [("tests/sub.rs::sub_works", "PASS")])
case(7, "a GREEN worker running in the shared main tree is blocked", "gate-green-verify", MAIN, 2, "worktree isolation dropped",
     extra={"AGENT_ROLE": "green-worker", "STORY_DIR": STORY})

# the role comes from the host, not from the worker-editable task.env
write(os.path.join(MAIN, ".aladdin", "task.env"),
      "TASK_ID=S1-01-T1\nATTEMPT=1\nAGENT_ROLE=planner\nSTORY_DIR=%s\n" % STORY)
report("red-worker", [("tests/sub.rs::sub_works", "FAIL")])
case(7, "a RED worker that relabels itself AGENT_ROLE=planner is still caught", "gate-red-verify", MAIN, 2,
     "the host dispatched a 'red-worker'", agent_type="aloop:red-worker")
os.remove(os.path.join(MAIN, ".aladdin", "task.env"))
case(7, "with no task.env role, the host's role still enforces isolation", "gate-red-verify", MAIN, 2,
     "worktree isolation dropped", extra={"STORY_DIR": STORY}, agent_type="aloop:red-worker")

# §6.6/§6.7 (M0.7) The contract is the supervisor's: it lives in the MAIN tree, keyed by the
# TASK_ID the host's dispatch prompt names. On Claude Code `isolation: "worktree"` creates
# the worktree (.claude/worktrees/agent-<id>) at dispatch, so nobody can pre-write its task.env;
# a worktree copy is only a hint, and one that disagrees with the contract is tampering.
CONTRACT = os.path.join(MAIN, ".aladdin", "state", "tasks", "S1-01-T1.env")
TRANSCRIPT = os.path.join(T, "agent-a1.jsonl")
write(TRANSCRIPT, json.dumps({"type": "user", "message": {"role": "user", "content": "TASK_ID: S1-01-T1\nMake the RED test pass."}}) + "\n")
write(CONTRACT, "TASK_ID=S1-01-T1\nATTEMPT=1\nAGENT_ROLE=green-worker\nSTORY_DIR=%s\nBASE_REF=main\nSCOPE_GLOBS='src/*.rs'\n" % STORY)
wt = green()
os.remove(os.path.join(wt, ".aladdin", "task.env"))
case(6, "a worktree with no task.env is gated by the supervisor's contract the dispatch names", "gate-green-verify", wt, 0,
     agent_type="aloop:green-worker", transcript=TRANSCRIPT)
wt = green(scope="*")
write(os.path.join(wt, "build.rs"), "fn main() {}\n")
commit(wt, "widened scope")
case(6, "a worker that widens SCOPE_GLOBS in its worktree copy is blocked", "gate-green-verify", wt, 2,
     "disagrees with the supervisor's contract", agent_type="aloop:green-worker", transcript=TRANSCRIPT)
case(6, "the same tamper is caught by a self-check (no host payload)", "gate-green-verify", wt, 2,
     "disagrees with the supervisor's contract")
wt = green()
with open(os.path.join(wt, ".aladdin", "task.env"), "a") as f:
    f.write("TASK_ID=S1-01-T9\n")
case(6, "a worker that points its task.env at another task is blocked", "gate-green-verify", wt, 2,
     "TASK_ID", agent_type="aloop:green-worker", transcript=TRANSCRIPT)
# The supervisor writes the contract, so the worker can't fix a slip in it; a gate that
# blocks on one loops the worker forever (live e2e run 2: 34 blocks on a comma-separated
# SCOPE_GLOBS). Accept commas; trust the host's role over a mistyped contract role.
write(CONTRACT, "TASK_ID=S1-01-T1\nATTEMPT=1\nAGENT_ROLE=green-wrker\nSTORY_DIR=%s\nBASE_REF=main\nSCOPE_GLOBS=src/lib.rs,tests/sub.rs\n" % STORY)
wt = green()
os.remove(os.path.join(wt, ".aladdin", "task.env"))
case(6, "a comma-separated SCOPE_GLOBS and a mistyped contract role don't block good work", "gate-green-verify", wt, 0,
     agent_type="aloop:green-worker", transcript=TRANSCRIPT)
# A re-dispatch bumps ATTEMPT in the contract while the worktree copy keeps the old value: the
# copy is OLDER than the contract, so it is stale, not tampered (live e2e run 10: 3 blocks).
# A copy edited by the worker is newer than the contract and still blocks (the widen case above).
write(CONTRACT, "TASK_ID=S1-01-T1\nATTEMPT=1\nAGENT_ROLE=green-worker\nSTORY_DIR=%s\nBASE_REF=main\nSCOPE_GLOBS='src/*.rs'\n" % STORY)
wt = green()
write(CONTRACT, "TASK_ID=S1-01-T1\nATTEMPT=2\nAGENT_ROLE=green-worker\nSTORY_DIR=%s\nBASE_REF=main\nSCOPE_GLOBS='src/*.rs'\n" % STORY)
later = os.stat(os.path.join(wt, ".aladdin", "task.env")).st_mtime + 5
os.utime(CONTRACT, (later, later))
case(6, "a worktree copy older than a re-dispatched contract is stale, not tampering", "gate-green-verify", wt, 0,
     agent_type="aloop:green-worker", transcript=TRANSCRIPT)
# Claude Code's worktree is .claude/worktrees/agent-<agent_id>. A worker that `cd`s back to
# the main tree (to read its story) leaves the hook's cwd there; the gate must still check the
# worktree the host made for that agent (live e2e run 5: 27 false "isolation dropped" blocks).
cc = os.path.join(MAIN, ".claude", "worktrees", "agent-a1")
sh("git", "worktree", "add", "-q", "-b", "worktree-agent-a1", cc)
write(os.path.join(cc, "src", "lib.rs"), GREEN_LIB)
write(os.path.join(cc, "tests", "sub.rs"), GREEN_TEST)
commit(cc, "green in the host worktree")
write(CONTRACT, "TASK_ID=S1-01-T1\nATTEMPT=1\nAGENT_ROLE=green-worker\nSTORY_DIR=%s\nBASE_REF=main\nSCOPE_GLOBS='src/*.rs'\n" % STORY)
report("green-worker", [("tests/sub.rs::sub_works", "PASS")])
env = {k: v for k, v in ENV.items() if k != "REPO_DIR"}
env["STANDARDS_FILE"] = os.path.join(MAIN, "standards.md")
r = subprocess.run([os.path.join(BIN, "gate-green-verify")], cwd=MAIN, env=env, capture_output=True, text=True,
                   input=json.dumps({"hook_event_name": "SubagentStop", "session_id": "s1", "agent_id": "a1",
                                     "agent_type": "aloop:green-worker", "agent_transcript_path": TRANSCRIPT, "cwd": MAIN}))
blocks = [l for l in r.stderr.splitlines() if l.startswith("BLOCK[")]
results.append((7, "a worker whose last cwd is the main tree is gated in its host worktree", r.returncode == 0,
                "exit %d %s" % (r.returncode, (blocks or [""])[0][:140])))
sh("git", "worktree", "remove", "--force", cc)
os.remove(CONTRACT)

# §6.8 An accepted GREEN is mergeable
r = sh("git", "merge", "--no-ff", "-qm", "accept", "agentic/case%d" % int(os.path.basename(green_ok_wt)[2:]), check=False)
t = sh("cargo", "test", "--workspace", "-q", check=False) if r.returncode == 0 else r
results.append((8, "the accepted GREEN branch merges into main and main's tests pass", r.returncode == 0 and t.returncode == 0,
                "merge exit %d, cargo test exit %d" % (r.returncode, t.returncode)))

# §6.9 Hard-breached work is discarded (the gate releases the stop UNVERIFIED, never as a pass)
wt = green(lib=LIB + "\npub fn sub(a: i32, b: i32) -> i32 {\n    a + b\n}\n")
write(os.path.join(wt, ".aladdin", "budget-exceeded"), "role=green-worker attempt=1 kind=time_hard\n")
rc, lines = gate("gate-green-verify", wt)
said = any("NOT verified" in l for l in lines) and any("do not merge" in l for l in lines)
results.append((9, "a hard-breached worker is released as 'not a pass, do not merge'", rc == 0 and said,
                "exit %d %s" % (rc, (lines[0] if lines else "")[:140])))

# §6.9 Stuck release: the same block N times in a row (one gate, task, attempt) releases the
# stop UNVERIFIED with a marker for the supervisor; live run 10 lost 29 minutes to a RED worker
# that stopped 29 times on one unchanged reason. A different reason or a new attempt resets.
def stuck_wt(attempt):
    wt = worktree("red-worker")
    write(os.path.join(wt, ".aladdin", "task.env"),
          "TASK_ID=S1-01-T1\nATTEMPT=%d\nAGENT_ROLE=red-worker\nSTORY_DIR=%s\nBASE_REF=main\n" % (attempt, STORY))
    write(os.path.join(wt, "tests", "common", "mod.rs"), SHIM)
    write(os.path.join(wt, "tests", "sub.rs"), RED_TEST.replace("2);", "0);"))   # passes: not RED
    commit(wt, "red")
    return wt


wt = stuck_wt(7)
report("red-worker", [("tests/sub.rs::sub_works", "FAIL")])          # block A: the test passes with no production code
rcs = [gate("gate-red-verify", wt)[0] for _ in range(2)]
rc, lines = gate("gate-red-verify", wt)
marker = os.path.join(MAIN, ".aladdin", "state", "stuck", "S1-01-T1.7")
said = any("STUCK" in l for l in lines) and any("NOT verified" in l for l in lines) and any("do not merge" in l for l in lines)
results.append((9, "the third identical block releases the worker unverified, with a stuck marker", rcs == [2, 2] and rc == 0 and said
                and os.path.isfile(marker) and "attempt=7" in open(marker).read(), "exits %s then %d %s" % (rcs, rc, (lines[0] if lines else "")[:120])))
sh("git", "worktree", "remove", "--force", wt)
wt = stuck_wt(8)
case(9, "a new attempt starts the stuck count over", "gate-red-verify", wt, 2)
sh("git", "worktree", "remove", "--force", wt)
wt = stuck_wt(9)
rcs = [gate("gate-red-verify", wt)[0] for _ in range(2)]                             # A, A
report("red-worker", [("tests/sub.rs::sub_works", "PASS")])          # block B: a PASS row in RED
rcs += [gate("gate-red-verify", wt)[0] for _ in range(2)]                            # B, B
rc, lines = gate("gate-red-verify", wt)                                              # B: the third B
results.append((9, "a different reason resets the count; the third identical one releases", rcs == [2, 2, 2, 2] and rc == 0,
                "exits %s then %d" % (rcs, rc)))
sh("git", "worktree", "remove", "--force", wt)
wt = stuck_wt(10)
case(9, "a self-check never releases a stuck worker", "gate-red-verify", wt, 2, extra={"AGENTIC_SELFCHECK": "1"})
rcs = [gate("gate-red-verify", wt, {"AGENTIC_SELFCHECK": "1"})[0] for _ in range(3)]
results.append((9, "three identical self-check blocks still block", rcs == [2, 2, 2], "exits %s" % rcs))
sh("git", "worktree", "remove", "--force", wt)
r = subprocess.run([os.path.join(BIN, "budget"), "watch", "--interval", "1", "--grace", "1"], cwd=MAIN, env=ENV, capture_output=True, text=True)
results.append((9, "budget watch reports the stuck release once as a STUCK line", "STUCK role=red-worker gate=gate-red-verify task=S1-01-T1 attempt=7" in r.stdout
                and "reason=" in r.stdout, r.stdout.strip().splitlines()[0][:140] if r.stdout.strip() else "no output"))
r = subprocess.run([os.path.join(BIN, "budget"), "watch", "--interval", "1", "--grace", "1"], cwd=MAIN, env=ENV, capture_output=True, text=True)
results.append((9, "a reported stuck release is not reported again", "STUCK" not in r.stdout, r.stdout.strip()[:140]))

# §6.7 Structural review: a stale host worktree checkout inside the main tree (Claude Code keeps
# them under .claude/worktrees/agent-<id>/) duplicates every symbol of main; it is a workspace
# artifact, not a defect (live e2e run 12: 20 false HIGH halts). A real duplicate still halts.
wt = worktree("structural-reviewer")
report("structural-reviewer", [("structural review", "PASS")])
stale = os.path.join(MAIN, ".claude", "worktrees", "agent-a1b2c3", "src", "lib.rs")
write(stale, open(os.path.join(MAIN, "src", "lib.rs")).read())
case(7, "a stale worktree checkout under .claude/worktrees is not a duplicate definition", "gate-structural-integrity", wt, 0)
write(os.path.join(wt, "src", "dup.rs"), "pub fn sub(a: i32, b: i32) -> i32 {\n    a - b\n}\n")
commit(wt, "dup")
case(7, "a real duplicate definition in the reviewed tree halts the chain (HIGH)", "gate-structural-integrity", wt, 2, "HIGH")
rc1 = gate("gate-structural-integrity", wt)[0]
rc, lines = gate("gate-structural-integrity", wt)
results.append((9, "a reviewer blocked 3x on the same HIGH finding is released unverified (it cannot fix code)",
                rc1 == 2 and rc == 0 and any("STUCK" in l for l in lines), "exits %d then %d" % (rc1, rc)))
shutil.rmtree(os.path.join(MAIN, ".claude"), ignore_errors=True)
sh("git", "worktree", "remove", "--force", wt)
# ...and a reviewer whose OWN worktree is a host checkout under .claude/worktrees/ is still scanned:
# the exclusion applies below the walk root, never to the root itself.
hostwt = os.path.join(MAIN, ".claude", "worktrees", "agent-f00dbabe")
sh("git", "worktree", "add", "-q", "-b", "agentic/hostwt", hostwt)
write(os.path.join(hostwt, ".aladdin", "task.env"),
      "TASK_ID=S1-SR-W9\nATTEMPT=1\nAGENT_ROLE=structural-reviewer\nSTORY_DIR=%s\nBASE_REF=main\n" % STORY)
write(os.path.join(hostwt, "src", "dup.rs"), "pub fn sub(a: i32, b: i32) -> i32 {\n    a - b\n}\n")
commit(hostwt, "dup in a host checkout")
case(7, "a real duplicate inside a reviewer's own .claude/worktrees checkout still halts (HIGH)", "gate-structural-integrity", hostwt, 2, "HIGH")
sh("git", "worktree", "remove", "--force", hostwt)
shutil.rmtree(os.path.join(MAIN, ".claude"), ignore_errors=True)

# FINAL: every test the story's plan.md names must exist on main. A RED re-plan that drops a
# planned test otherwise loses coverage silently (live e2e run 6), since plan-ready.md only
# lists what RED actually wrote.
PLAN = "---\ntype: plan\nstory: S1-01\n---\n# plan\n\n## T1 tests\n\n- `tests/add.rs::add_works`\n  — adds.\n"
write(os.path.join(STORY, "plan.md"), PLAN)
fenv = {"STORY_DIR": STORY, "AGENTIC_SELFCHECK": "1"}
case(6, "FINAL passes when every planned test exists on main", "gate-final", MAIN, 0, extra=fenv)
write(os.path.join(STORY, "plan.md"), PLAN + "- `tests/sub.rs::sub_handles_negatives`\n  — dropped by a re-plan.\n")
case(6, "FINAL blocks when a planned test is missing from main", "gate-final", MAIN, 2, "sub_handles_negatives", extra=fenv)
os.remove(os.path.join(STORY, "plan.md"))

# FINAL's own report (live e2e run 29): the final-gate worker's Write to the main tree's output.md
# was refused, it wrote a worktree copy the host discarded, and its stop passed anyway, so the
# supervisor wrote the sign-off block itself. Dispatched under a contract, the final gate must
# have appended its own block, and the block says how (a shell heredoc).
fwt = worktree("final-gate")
report("structural-reviewer", [("structure", "PASS")])
case(6, "FINAL dispatched under a contract blocks until its own output.md block is the latest", "gate-final", fwt, 2, "cat >>")
report("final-gate", [("matrix", "PASS")])
case(6, "FINAL dispatched under a contract passes once it appended its own block", "gate-final", fwt, 0)

# §6.2 Planner artifacts are schema-valid (gate-validate-artifact runs md-db on every planner write)
def md_db():
    """The real md-db binary, put where ensure-tools finds it; built from the bundled source if needed."""
    data_bin = os.path.join(T, "pdata", "bin")
    os.makedirs(data_bin, exist_ok=True)
    ENV["CLAUDE_PLUGIN_DATA"] = os.path.join(T, "pdata")
    r = sh(os.path.join(BIN, "ensure-tools"), "--resolve", "md-db", check=False)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    src = os.path.join(ROOT, "tools", "md-db")
    if shutil.which("cargo") is None:
        return None
    env = dict(ENV)
    env.pop("CARGO_TARGET_DIR")                    # reuse the crate's own (CI-cached) target dir
    sh("cargo", "build", "--release", "-q", cwd=src, env=env)
    dst = os.path.join(data_bin, "md-db")
    shutil.copy(os.path.join(src, "target", "release", "md-db"), dst)
    return dst


STORIES = ("---\ntype: stories\nsprint: 1\n---\n# Sprint 1\n\n## Sprint goal\nsub\n\n## Sprint demo\nrun it\n\n"
           "## Definition of Done\ntests pass\n\n## Out of scope\nmul\n\n## User stories\n### S1-01 sub\n\n"
           "## Story dependency graph\nS1-01\n")
if md_db():
    plan = os.path.join(MAIN, "docs", "agents", "sprint1")
    write(os.path.join(plan, "stories.md"), STORIES)
    write(os.path.join(STORY, "tasks.md"), "---\ntype: tasks\nstory: S1-01\n---\n# Tasks\n\n## T1 — sub\n")
    for f, title in ((os.path.join(plan, "stories.md"), "a schema-valid stories.md is accepted"),
                     (os.path.join(STORY, "tasks.md"), "a schema-valid tasks.md is accepted")):
        r = sh(os.path.join(BIN, "gate-validate-artifact"), f, check=False)
        results.append((2, title, r.returncode == 0, "exit %d %s" % (r.returncode, r.stderr.strip()[:140])))
    # The block carries md-db's diagnostics (what is missing and the hint), since a PostToolUse
    # block hands the model only stderr (live e2e run 11: the planner guessed twice per artifact).
    bad = (("stories.md", plan, STORIES.split("## Sprint demo")[0], "stories.md missing required sections is rejected, naming them", '"Sprint demo"'),
           ("tasks.md", STORY, "---\ntype: tasks\nstory: X1\n---\n# Tasks\n", "tasks.md with a malformed story id is rejected, naming the field", "story"))
    for name, d, text, title, says in bad:
        f = os.path.join(d, name)
        write(f, text)
        r = sh(os.path.join(BIN, "gate-validate-artifact"), f, check=False)
        results.append((2, title, r.returncode == 2 and "BLOCK[gate-validate-artifact]" in r.stderr and says in r.stderr
                        and "hint:" in r.stderr, "exit %d %s" % (r.returncode, r.stderr.strip()[-140:])))
    # memory.md: `aloop init` seeds a schema-valid one, so no agent has to guess its frontmatter
    # (live e2e run 7: the archivist wrote `type: agent-memory`, the supervisor's stop was blocked 15 times).
    seed = os.path.join(T, "seed")
    os.makedirs(seed)
    sh("git", "init", "-q", cwd=seed)
    sh(os.path.join(BIN, "aloop-init"), "--apply", cwd=seed)
    mem = os.path.join(seed, "docs", "agents", "memory.md")
    menv = dict(ENV, CLAUDE_PLUGIN_ROOT=ROOT)
    r = subprocess.run([os.path.join(BIN, "gate-memory"), mem], cwd=seed, env=menv, capture_output=True, text=True) \
        if os.path.isfile(mem) else None
    results.append((2, "aloop init seeds a memory.md that passes gate-memory", bool(r) and r.returncode == 0,
                    "no memory.md seeded" if r is None else "exit %d %s" % (r.returncode, r.stderr.strip()[-140:])))
    write(mem, "---\ntype: agent-memory\n---\n# memory\n")
    r = subprocess.run([os.path.join(BIN, "gate-memory"), mem], cwd=seed, env=menv, capture_output=True, text=True)
    results.append((2, "a malformed memory.md is rejected with the valid skeleton", r.returncode == 2 and "type: memory" in r.stderr,
                    "exit %d %s" % (r.returncode, r.stderr.strip()[-140:])))
    # A story dir named `S1-01/` (live e2e run 9): the block names the expected `s1-01-<slug>/`
    # (the planner retried 56 times on a bare "no story dir for S1-01").
    st2 = os.path.join(T, "st2", "docs", "agents", "sprint1")
    write(os.path.join(st2, "stories.md"), STORIES)
    for f in ("tasks.md", "validate.md", "plan.md"):
        write(os.path.join(st2, "S1-01", f), "x\n")
    r = sh(os.path.join(BIN, "gate-stage2-complete"), st2, check=False)
    results.append((2, "a misnamed story dir is blocked with the expected name", r.returncode == 2
                    and "s1-01-<slug>" in r.stderr and "S1-01" in r.stderr.split("found")[-1],
                    "exit %d %s" % (r.returncode, r.stderr.strip()[-160:])))
else:
    results.append((2, "md-db available for the schema gate (needs cargo)", False, "no md-db and no cargo"))

# Items whose behaviour lives in a harness tool rather than a worker gate: each is held by the
# focused test that drives that tool, and the suite runs it here so "covered" is a verdict,
# not a pointer. (Until v0.12.0 this list said "does not exist yet" for tools that had shipped.)
HELD_BY = {
    1: ("an intent is admitted and normalized (Intent Contract, clarification policy, gate-intake)", ["intent-contract.test.py"]),
    10: ("transcript and event refs exist (aladdin.event/1 envelope, refs not copies)", ["events.test.py"]),
    11: ("the lineage is complete (task lineage; `events trace SHA` back to the intent)", ["events.test.py", "commit-trace.test.py"]),
    12: ("usage is exact or names what the host does not report", ["budget-meter.test.py"]),
    13: ("hard time and token limits stop the worker", ["budget-hard-stop.test.py", "budget-worktree.test.py"]),
    14: ("admission closes deterministically (sprint envelope, carried tasks)", ["sprint-admission.test.py"]),
    16: ("the handoff is produced and context rotated", ["handoff-resume.test.py", "context-boundary.test.py"]),
    17: ("compaction is used only when chosen and advertised", ["context-boundary.test.py"]),
    18: ("the model route is observable", ["model-routes.test.py"]),
    19: ("memory injection is equivalent across hosts (SessionStart on Codex, OpenCode and dsh)",
         ["codex-hooks.test.py", "opencode-plugin.test.mjs", "dsh-plugin.test.mjs"]),
    20: ("semantic capability gaps are explicit (Unsupported, LSP exit 4, capability matrix)", ["host-adapter.test.py", "lsp-adapter.test.py", "context-boundary.test.py"]),
}
TESTS = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "test")
_env = {k: v for k, v in os.environ.items() if k not in ("AGENTIC_HOST", "SKIP_HOOKS", "STORY_DIR", "AGENT_ROLE", "REPO_DIR", "BASE_REF", "SCOPE_GLOBS")}
for item, (title, files) in sorted(HELD_BY.items()):
    bad = []
    for f in files:
        cmd = ["node", os.path.join(TESTS, f)] if f.endswith(".mjs") else [sys.executable, os.path.join(TESTS, f)]
        r = subprocess.run(cmd, env=_env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            bad.append("%s exit %d %s" % (f, r.returncode, (r.stderr or r.stdout).strip()[-160:]))
    results.append((item, "%s [%s]" % (title, ", ".join(files)), not bad, "; ".join(bad)))

NOT_COVERED = {
    15: "re-decomposition is planner behaviour: measured by live runs (the re-plans in docs/evidence/README.md), not by a fixture",
}

print("ROADMAP §6 conformance — Claude Code wiring, today's behaviour\n")
failed = 0
for item, title, good, detail in results:
    print("%s §6.%-2d %s" % ("ok  " if good else "FAIL", item, title))
    if not good:
        failed += 1
        print("        " + detail)
covered = sorted({i for i, *_ in results})
print("\ncovered: %s · not yet: %s" % (", ".join("§6.%d" % i for i in covered), ", ".join("§6.%d" % i for i in sorted(NOT_COVERED))))
for i in sorted(NOT_COVERED):
    print("  §6.%d — %s" % (i, NOT_COVERED[i]))
print("\n%d/%d checks passed" % (len(results) - failed, len(results)))
shutil.rmtree(T, ignore_errors=True)
sys.exit(1 if failed else 0)
