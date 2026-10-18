#!/usr/bin/env python3
"""ROADMAP §4.6: the semantic plan is the execution source. With "tasks" in targets.json,
`targets render` writes tasks.md / validate.md / plan.md as deterministic views over it, the
shapes the gates already check (gate-plan-shape, ids freeze, gate-stage2-complete), and
`targets check` (run by the gate) refuses a hand-edited view, so the md files cannot fork
from the plan.
    python3 scripts/test/plan-render.test.py
"""
import json
import os
import subprocess
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-render-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"), PATH="/usr/bin:/bin")
for k in ("AGENTIC_HOST", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA"):
    ENV.pop(k, None)
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


def read(name):
    return open(os.path.join(SD, name)).read()


subprocess.run(["git", "init", "-q"], cwd=T, check=True)
write("src/report.py", "def render(rows):\n    return [str(r) for r in rows]\n")
write("docs/agents/sprint1/stories.md", "---\ntype: stories\nsprint: 1\n---\n- S1-01 export\n")
PLAN = {
    "story": "S1-01",
    "tasks": [
        {"id": "T1", "title": "csv writer", "summary": "A stdlib-only CSV writer for the report rows.",
         "budget": "tokens_hard=800000 time_hard=90",
         "tests": [{"path": "tests/test_csv.py", "fn": "test_writes_rows", "assert": "two rows → two CSV lines"},
                   {"path": "tests/test_csv.py", "fn": "test_quotes_commas", "assert": "a cell with a comma is quoted"}],
         "validate": [{"run": "python3 -m pytest tests/test_csv.py -q", "expect": "2 passed"}]},
        {"id": "T2", "title": "wire it",
         "tests": [{"path": "tests/test_report.py", "fn": "test_render_offers_csv", "assert": "render(rows, csv=True) returns CSV"}]},
    ],
    "targets": [
        {"change_ref": "C1", "task": "T1", "operation": "create", "path": "src/export/csv_writer.py",
         "planned_symbol": "write_csv", "reason": "CSV export needs a writer", "evidence_refs": [], "constraints": []},
        {"change_ref": "C2", "task": "T2", "operation": "modify", "path": "src/report.py", "symbol": "render",
         "reason": "render offers the export", "evidence_refs": [], "constraints": []},
    ],
}
write("docs/agents/sprint1/s1-01-export/targets.json", json.dumps(PLAN))

# 1) render writes the three views in the gate-checked shapes; the gates accept them
r = run(TG, "render", SD)
assert r.returncode == 0 and r.stdout.count("rendered ") == 3, r
tasks, plan, val = read("tasks.md"), read("plan.md"), read("validate.md")
assert tasks.startswith("---\ntype: tasks\nstory: S1-01\n---\n") and "## T1 — csv writer" in tasks and "## T2 — wire it" in tasks
assert "budget: tokens_hard=800000 time_hard=90" in tasks and "- C1 create `src/export/csv_writer.py` `write_csv` — CSV export needs a writer" in tasks, tasks
assert 'scope: "tests only"' in plan and "- [ ] `tests/test_csv.py::test_quotes_commas` — a cell with a comma is quoted" in plan, plan
assert "## Pre-flight" in val and "## Final sign-off" in val and "- PASS: run `python3 -m pytest tests/test_csv.py -q` → `2 passed`" in val, val
assert "never this file" in tasks and "never this file" in plan and "never this file" in val
assert run(os.path.join(BIN, "gate-plan-shape"), os.path.join(SD, "plan.md")).returncode == 0
r = run(os.path.join(BIN, "gate-stage2-complete"), SP)
assert r.returncode == 0, r.stderr
assert not os.path.exists(os.path.join(T, "src", "export")) and not os.path.exists(os.path.join(T, "tests"))
ok("render: tasks.md (headings, budget, Changes), tests-only plan.md, validate.md rubric; gates accept them; no stubs")

# 2) deterministic and idempotent: a second render changes nothing
r = run(TG, "render", SD)
assert r.returncode == 0 and r.stdout == "", r
assert run(TG, "render", SD, "--check").returncode == 0
assert (tasks, plan, val) == (read("tasks.md"), read("plan.md"), read("validate.md"))
ok("render is deterministic and idempotent")

# 3) a hand-edited view is refused by check (the gate runs it); re-rendering heals it
write("docs/agents/sprint1/s1-01-export/tasks.md", tasks + "## T3 — sneaked in\n")
r = run(TG, "check", SD)
assert r.returncode == 2 and "tasks.md differs from its render of targets.json" in r.stderr, r.stderr
assert run(os.path.join(BIN, "gate-stage2-complete"), SP).returncode == 2
r = run(TG, "render", SD, "--check")
assert r.returncode == 2 and "tasks.md" in r.stderr and "plan.md" not in r.stderr, r.stderr
assert run(TG, "render", SD).returncode == 0 and read("tasks.md") == tasks
assert run(TG, "check", SD).returncode == 0
ok("a hand-edited tasks.md is refused by targets check / gate-stage2-complete; render heals it")

# 4) the plan itself is checked: a target naming an unknown task, a task without tests, bad ids
bad = json.loads(json.dumps(PLAN))
bad["targets"][1]["task"] = "T9"
bad["tasks"].append({"id": "T3", "title": "no tests", "tests": []})
bad["tasks"].append({"id": "task-4", "title": "bad id", "tests": [{"path": "tests/t.py", "fn": "test_x", "assert": "x"}]})
bad["tasks"][0]["tests"][0]["fn"] = "not a fn"
write("docs/agents/sprint1/s1-01-export/targets.json", json.dumps(bad))
r = run(TG, "check", SD)
assert r.returncode == 2, r
for want in ("task 'T9' is not a ## heading", "task T3 (#2): needs at least one test", "task task-4 (#3): id must look like T1",
             "task T1 (#0): test #0 needs a repo-relative path and a function name"):
    assert want in r.stderr, (want, r.stderr)
assert run(TG, "render", SD).returncode == 2 and read("tasks.md") == tasks     # nothing rendered from a bad plan
ok("a bad semantic plan is refused before anything is rendered")

# 5) without "tasks", targets.json keeps today's behaviour: tasks.md is the planner's and check reads it
old = {"targets": PLAN["targets"]}
write("docs/agents/sprint1/s1-01-export/targets.json", json.dumps(old))
assert run(TG, "check", SD).returncode == 0
r = run(TG, "render", SD)
assert r.returncode == 2 and 'has no "tasks"' in r.stderr, r
ok("a targets-only targets.json stays valid; render says what it needs")

print("\n%d checks passed" % PASS[0])
