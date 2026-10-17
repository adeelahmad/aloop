#!/usr/bin/env python3
"""Conformance fixtures (ROADMAP §6) around TODAY's behaviour, on the Claude Code wiring.

Each case builds a tiny Rust crate in a throw-away repo, gives a worker a real linked
worktree + .agentic/task.env + story comms (init.md/output.md), plants good or bad work,
and runs the REAL gate script the SubagentStop hook runs. A case passes when the gate's
verdict (0 = accept, 2 = block) is the expected one. No network, no tokens; needs cargo.

    python3 scripts/conformance/claude-code.test.py

Items this suite does not cover yet are listed at the end with the reason, so the gap is
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
    write(os.path.join(wt, ".agentic", "task.env"),
          "TASK_ID=S1-01-T1\nATTEMPT=1\nAGENT_ROLE=%s\nSTORY_DIR=%s\nBASE_REF=main\nSCOPE_GLOBS='%s'\n" % (role, STORY, scope))
    return wt


def gate(name, cwd, extra=None, agent_type=None, transcript=None):
    env = dict(ENV, REPO_DIR=cwd, STANDARDS_FILE=os.path.join(MAIN, "standards.md"), **(extra or {}))
    payload = {"hook_event_name": "SubagentStop", "session_id": "s1", "agent_id": "a1", "agent_type": agent_type}
    if transcript:
        payload["agent_transcript_path"] = transcript
    payload = json.dumps(payload) if agent_type else ""
    r = subprocess.run([os.path.join(BIN, name)], cwd=cwd, env=env, capture_output=True, text=True, input=payload)
    return r.returncode, (r.stderr or r.stdout).strip().splitlines()


def case(item, title, name, cwd, expect, must_say=None, extra=None, agent_type=None, transcript=None):
    rc, lines = gate(name, cwd, extra, agent_type, transcript)
    blocks = [l for l in lines if l.startswith("BLOCK[")]
    good = rc == expect and (must_say is None or any(must_say in l for l in blocks))
    detail = blocks[0] if blocks else (lines[-1] if lines else "")
    results.append((item, title, good, "exit %d (want %d) %s" % (rc, expect, detail[:140])))


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

# A worker's `selfcheck <role>` with no AGENT_ROLE in its env must run the gate, not crash it
# (live e2e run: `AGENT_ROLE: unbound variable` made the self-check fail closed on good work).
os.remove(os.path.join(green_ok_wt, ".agentic", "task.env"))
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
wt = green()
os.remove(os.path.join(STORY, "output.md"))
case(6, "a worker that leaves no output.md report is blocked", "gate-green-verify", wt, 2, "no output.md")
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
    with open(os.path.join(wt, ".agentic", "task.env"), "a") as f:
        f.write("".join(l + "\n" for l in lines))
    return wt


wt = tamper(["exit 0"])
case(6, "a worker that appends `exit 0` to its task.env is still blocked", "gate-green-verify", wt, 2, "matrix command failed")
wt = tamper(["GATE_RUN_MATRIX=0"])
case(6, "a worker that sets GATE_RUN_MATRIX=0 in its task.env is still blocked", "gate-green-verify", wt, 2, "matrix command failed")
wt = tamper(["SCOPE_GLOBS='*'", "true; exit 0"])
case(6, "task.env is data, never executed (`true; exit 0`)", "gate-green-verify", wt, 2, "matrix command failed")

# §6.7 Worktrees stay isolated
write(os.path.join(MAIN, ".agentic", "task.env"),
      "TASK_ID=S1-01-T1\nATTEMPT=1\nAGENT_ROLE=red-worker\nSTORY_DIR=%s\n" % STORY)
report("red-worker", [("tests/sub.rs::sub_works", "FAIL")])
case(7, "a RED worker running in the shared main tree is blocked", "gate-red-verify", MAIN, 2, "worktree isolation dropped")
with open(os.path.join(MAIN, ".agentic", "task.env"), "a") as f:
    f.write("SKIP_HOOKS=1\n")
case(7, "SKIP_HOOKS=1 written into task.env does not lift isolation", "gate-red-verify", MAIN, 2, "worktree isolation dropped")
os.remove(os.path.join(MAIN, ".agentic", "task.env"))
report("green-worker", [("tests/sub.rs::sub_works", "PASS")])
case(7, "a GREEN worker running in the shared main tree is blocked", "gate-green-verify", MAIN, 2, "worktree isolation dropped",
     extra={"AGENT_ROLE": "green-worker", "STORY_DIR": STORY})

# the role comes from the host, not from the worker-editable task.env
write(os.path.join(MAIN, ".agentic", "task.env"),
      "TASK_ID=S1-01-T1\nATTEMPT=1\nAGENT_ROLE=planner\nSTORY_DIR=%s\n" % STORY)
report("red-worker", [("tests/sub.rs::sub_works", "FAIL")])
case(7, "a RED worker that relabels itself AGENT_ROLE=planner is still caught", "gate-red-verify", MAIN, 2,
     "the host dispatched a 'red-worker'", agent_type="aloop:red-worker")
os.remove(os.path.join(MAIN, ".agentic", "task.env"))
case(7, "with no task.env role, the host's role still enforces isolation", "gate-red-verify", MAIN, 2,
     "worktree isolation dropped", extra={"STORY_DIR": STORY}, agent_type="aloop:red-worker")

# §6.6/§6.7 (M0.7) The contract is the supervisor's: it lives in the MAIN tree, keyed by the
# TASK_ID the host's dispatch prompt names. On Claude Code `isolation: "worktree"` creates
# the worktree (.claude/worktrees/agent-<id>) at dispatch, so nobody can pre-write its task.env;
# a worktree copy is only a hint, and one that disagrees with the contract is tampering.
CONTRACT = os.path.join(MAIN, ".agentic", "state", "tasks", "S1-01-T1.env")
TRANSCRIPT = os.path.join(T, "agent-a1.jsonl")
write(TRANSCRIPT, json.dumps({"type": "user", "message": {"role": "user", "content": "TASK_ID: S1-01-T1\nMake the RED test pass."}}) + "\n")
write(CONTRACT, "TASK_ID=S1-01-T1\nATTEMPT=1\nAGENT_ROLE=green-worker\nSTORY_DIR=%s\nBASE_REF=main\nSCOPE_GLOBS='src/*.rs'\n" % STORY)
wt = green()
os.remove(os.path.join(wt, ".agentic", "task.env"))
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
with open(os.path.join(wt, ".agentic", "task.env"), "a") as f:
    f.write("TASK_ID=S1-01-T9\n")
case(6, "a worker that points its task.env at another task is blocked", "gate-green-verify", wt, 2,
     "TASK_ID", agent_type="aloop:green-worker", transcript=TRANSCRIPT)
os.remove(CONTRACT)

# §6.8 An accepted GREEN is mergeable
r = sh("git", "merge", "--no-ff", "-qm", "accept", "agentic/case%d" % int(os.path.basename(green_ok_wt)[2:]), check=False)
t = sh("cargo", "test", "--workspace", "-q", check=False) if r.returncode == 0 else r
results.append((8, "the accepted GREEN branch merges into main and main's tests pass", r.returncode == 0 and t.returncode == 0,
                "merge exit %d, cargo test exit %d" % (r.returncode, t.returncode)))

# §6.9 Hard-breached work is discarded (the gate releases the stop UNVERIFIED, never as a pass)
wt = green(lib=LIB + "\npub fn sub(a: i32, b: i32) -> i32 {\n    a + b\n}\n")
write(os.path.join(wt, ".agentic", "budget-exceeded"), "role=green-worker attempt=1 kind=time_hard\n")
rc, lines = gate("gate-green-verify", wt)
said = any("NOT verified" in l for l in lines) and any("do not merge" in l for l in lines)
results.append((9, "a hard-breached worker is released as 'not a pass, do not merge'", rc == 0 and said,
                "exit %d %s" % (rc, (lines[0] if lines else "")[:140])))

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
    bad = (("stories.md", plan, STORIES.split("## Sprint demo")[0], "stories.md missing required sections is rejected"),
           ("tasks.md", STORY, "---\ntype: tasks\nstory: X1\n---\n# Tasks\n", "tasks.md with a malformed story id is rejected"))
    for name, d, text, title in bad:
        f = os.path.join(d, name)
        write(f, text)
        r = sh(os.path.join(BIN, "gate-validate-artifact"), f, check=False)
        results.append((2, title, r.returncode == 2 and "BLOCK[gate-validate-artifact]" in r.stderr,
                        "exit %d %s" % (r.returncode, r.stderr.strip()[-140:])))
else:
    results.append((2, "md-db available for the schema gate (needs cargo)", False, "no md-db and no cargo"))

NOT_COVERED = {
    1: "Intent Contract does not exist yet (M4)",
    10: "event stream does not exist yet (M6); transcripts are covered by scripts/test/*",
    11: "lineage query does not exist yet (M6)",
    12: "usage exactness is covered by scripts/test/codex-hooks + opencode-plugin; no 'estimated' marker yet (M2)",
    13: "covered by scripts/test/budget-worktree.test.py and the host tests",
    14: "sprint admission control does not exist yet (M2)",
    15: "re-decomposition is planner behaviour; needs a model run (measured, M2)",
    16: "handoff is covered by scripts/test/codex-hooks (SessionStart); rotation needs M3",
    17: "compaction detection does not exist yet (M3)",
    18: "model route is written into generated agents (host tests); abstract routes are M7",
    19: "memory injection equivalence needs M7's capability matrix",
    20: "semantic capability reporting does not exist yet (M5)",
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
