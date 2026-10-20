#!/usr/bin/env python3
"""ROADMAP §4.6 (first slice): structured change targets, a shadow workspace and the
narrow-context CLI. A worker starts from `target` / `context`, including for a file that
does not exist yet; no stub is created; a missing structural backend is surfaced, never
guessed; gate-stage2-complete validates the targets.
    python3 scripts/test/semantic-targets.test.py
"""
import json
import os
import subprocess
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-targets-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"), PATH="/usr/bin:/bin")  # no md-db / ctx-symbols: regex backend
ENV.pop("AGENTIC_HOST", None)
ENV.pop("CLAUDE_PLUGIN_ROOT", None)
ENV.pop("CLAUDE_PLUGIN_DATA", None)
PASS = [0]
TG = os.path.join(BIN, "targets")
SP = os.path.join(T, "docs", "agents", "sprint1")
SD = os.path.join(SP, "s1-01-export")


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def run(*args):
    return subprocess.run(args, cwd=T, env=ENV, capture_output=True, text=True)


def write(rel, text):
    p = os.path.join(T, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)


def targets(*ts):
    write(os.path.relpath(os.path.join(SD, "targets.json"), T), json.dumps({"targets": list(ts)}))


subprocess.run(["git", "init", "-q"], cwd=T, check=True)
write("src/report.py", "import csv\n\n\ndef render(rows):\n    out = []\n    for r in rows:\n        out.append(str(r))\n    return out\n\n\ndef footer():\n    return 'end'\n")
write("docs/agents/sprint1/stories.md", "---\ntype: stories\nsprint: 1\n---\n- S1-01 export\n")
write("docs/agents/sprint1/s1-01-export/tasks.md", "---\ntype: tasks\nstory: S1-01\n---\n## T1 — csv writer\n## T2 — wire it\n")
write("docs/agents/sprint1/s1-01-export/validate.md", "---\ntype: validate\nstory: S1-01\n---\n## T1\n## T2\n")
write("docs/agents/sprint1/s1-01-export/plan.md", "---\ntype: plan\nstory: S1-01\nscope: tests only\n---\n")
C1 = {"change_ref": "C1", "task": "T1", "operation": "create", "path": "src/export/csv_writer.py",
      "planned_symbol": "write_csv", "reason": "CSV export needs a writer", "evidence_refs": ["intake.md#want"],
      "constraints": ["stdlib csv only"]}
C2 = {"change_ref": "C2", "task": "T2", "operation": "modify", "path": "src/report.py", "symbol": "render",
      "reason": "render offers the export", "evidence_refs": [], "constraints": []}
targets(C1, C2)

# 1) valid targets pass; target resolves a real symbol to file:line and a planned one as shadow
r = run(TG, "check", SD)
assert r.returncode == 0, r.stderr
out = run(TG, "target", "T2", SD).stdout
assert "src/report.py:4 render" in out and "weaker" in out, out
out = run(TG, "target", "T1", SD).stdout
assert "src/export/csv_writer.py write_csv (planned, shadow)" in out, out
assert not os.path.exists(os.path.join(T, "src", "export")), "no physical stub may be created"
served = [json.loads(x) for x in open(os.path.join(T, ".aladdin", "logs", "events.jsonl"))]
served = [e for e in served if e["event"] == "semantic_target.resolved"]
assert [e["ids"]["task"] for e in served] == ["S1-01-T2", "S1-01-T1"] and served[0]["data"]["change_ref"] == "C2", served
ok("check passes; target gives file:line for an existing symbol and a shadow entry for a planned file")

# 2) the shadow workspace lists planned files/symbols, queryable before the code exists
assert run(TG, "shadow", SD).stdout.strip() == "src/export/csv_writer.py\twrite_csv\tplanned-file\tC1"
ok("shadow workspace lists the planned file and symbol")

# 3) context: the symbol's body only, budget-capped; for a missing file, its would-be neighbours
out = run(TG, "context", "C2", SD).stdout
assert "def render(rows):" in out and "return out" in out and "def footer" not in out, out
assert "truncated at --budget 20" in run(TG, "context", "C2", SD, "--budget", "20").stdout
out = run(TG, "context", "C1", SD).stdout
assert "does not exist yet" in out and "constraint: stdlib csv only" in out, out
ok("context returns just the target's definition (capped by --budget); a planned file gets its constraints")

# 4) refs/callers/callees: no structural backend → said so, exit 4, nothing guessed
r = run(TG, "callers", "C2", SD)
assert r.returncode == 4 and "Not guessing" in r.stderr and not r.stdout, r
ok("callers without an LSP backend is surfaced (exit 4), never guessed")

# 5) bad targets are blocked, and gate-stage2-complete runs the check
cases = [
    (dict(C2, symbol="nope"), "not defined"),
    (dict(C2, task="T9"), "not a ## heading"),
    (dict(C1, path="src/report.py"), "already exists"),
    (dict(C2, path="src/ghost.py"), "neither in the tree nor planned"),
    (dict(C2, operation="rewrite"), "operation must be"),
    (dict(C1, path="../etc/x.py"), "repo-relative"),
]
for bad, why in cases:
    targets(C1, bad) if bad["change_ref"] == "C2" else targets(bad, C2)
    r = run(TG, "check", SD)
    assert r.returncode == 2 and why in r.stderr, (why, r.stderr)
targets(C1, dict(C2, change_ref="C1"))
assert "duplicate change_ref" in run(TG, "check", SD).stderr
r = run(os.path.join(BIN, "gate-stage2-complete"), SP)
assert r.returncode == 2 and "targets.json" in r.stderr, r.stderr
targets(C1, C2, {"change_ref": "C3", "task": "T2", "operation": "extend", "path": "src/export/csv_writer.py",
                 "planned_symbol": "write_header", "reason": "header row", "evidence_refs": [], "constraints": []})
r = run(os.path.join(BIN, "gate-stage2-complete"), SP)
assert r.returncode == 0, r.stderr
ok("unknown symbol/task, create-over-existing, ghost path, bad op, escaping path, duplicate ref → blocked; gate runs it")

# 6) a mid-sprint re-plan re-checks changes that already shipped (live e2e run 19: T1's
#    `create tests/parse.rs` merged, then the T2 re-plan's planner was blocked 9 times with
#    "already exists" for a change it was told not to touch). A create whose file now exists
#    AND defines its planned_symbol is realized, not wrong; one whose file exists without the
#    symbol is still a plan defect.
targets(C1, C2)
write("src/export/csv_writer.py", "import csv\n\n\ndef write_csv(rows):\n    return rows\n")
r = run(TG, "check", SD)
assert r.returncode == 0, r.stderr
assert "src/export/csv_writer.py:4 write_csv" in run(TG, "target", "T1", SD).stdout
write("src/export/csv_writer.py", "import csv\n\n\ndef other():\n    return 1\n")
r = run(TG, "check", SD)
assert r.returncode == 2 and "already exists" in r.stderr, r.stderr
os.remove(os.path.join(T, "src", "export", "csv_writer.py"))
ok("a create whose file already defines its planned_symbol is realized (passes); without the symbol it is still blocked")

# 7) a create of a test file takes its planned_symbol from the task's own `tests` (live runs 36–41:
#    8 planner blocks "create needs planned_symbol" on a new test file, whose symbols are the
#    tests the plan already lists); with no test for that path it is still required
C3 = {"change_ref": "C3", "task": "T1", "operation": "create", "path": "tests/test_csv_writer.py",
      "reason": "the T1 tests", "evidence_refs": [], "constraints": []}
plan = {"story": "S1-01", "tasks": [
    {"id": "T1", "title": "csv writer", "summary": "s", "tests": [{"path": "tests/test_csv_writer.py", "fn": "test_writes_rows", "assert": "rows"}]},
    {"id": "T2", "title": "wire it", "summary": "s", "tests": [{"path": "tests/test_report.py", "fn": "test_offers_export", "assert": "export"}]}]}
write(os.path.relpath(os.path.join(SD, "targets.json"), T), json.dumps(dict(plan, targets=[C1, C2, C3])))
assert run(TG, "render", SD).returncode == 0
r = run(TG, "check", SD)
assert r.returncode == 0, r.stderr
assert "test_writes_rows" in run(TG, "target", "T1", SD).stdout
plan["tasks"][0]["tests"][0]["path"] = "tests/test_other.py"
write(os.path.relpath(os.path.join(SD, "targets.json"), T), json.dumps(dict(plan, targets=[C1, C2, C3])))
assert run(TG, "render", SD).returncode == 0
r = run(TG, "check", SD)
assert r.returncode == 2 and "C3 (#2): create needs planned_symbol" in r.stderr, r.stderr
ok("a create of a test file the task's tests name gets its planned_symbol from them; with none it is still required")

# 8) a later task may modify a symbol an earlier task plans in an existing file (live run 46: T2
#    extended src/main.rs with parse_args and run, T3 modified them, and the planner was blocked
#    4 times with "not defined"); a symbol no target plans is still not defined
C4 = {"change_ref": "C4", "task": "T1", "operation": "extend", "path": "src/report.py",
      "planned_symbol": "export_rows", "reason": "rows for the export", "evidence_refs": [], "constraints": []}
C5 = dict(C2, change_ref="C5", symbol="export_rows")
targets(C1, C2, C4, C5)
r = run(TG, "check", SD)
assert r.returncode == 0, r.stderr
assert "src/report.py export_rows (planned, shadow)" in run(TG, "target", "T2", SD).stdout
targets(C1, C2, C5)
r = run(TG, "check", SD)
assert r.returncode == 2 and "C5 (#2): symbol export_rows is not defined in src/report.py" in r.stderr, r.stderr
ok("a modify of a symbol another task plans in an existing file passes; an unplanned one is still not defined")

print("\n%d checks passed" % PASS[0])
