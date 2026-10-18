#!/usr/bin/env python3
"""ROADMAP §4.3: planner lesson candidates carry symptom / cause / change / applies_to /
evidence_refs. They reach memory.md only through the archivist recurrence rule (>= 2
distinct tasks, or a human keep), and gate-memory enforces that. No second memory system.
    python3 scripts/test/lesson-candidates.test.py
"""
import json
import os
import subprocess
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-lessons-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"))
ENV.pop("AGENTIC_HOST", None)
ENV.pop("CLAUDE_PLUGIN_ROOT", None)
PASS = [0]
L = os.path.join(BIN, "lessons")
DOCS = os.path.join(T, "docs", "agents")


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def run(*args):
    return subprocess.run(args, cwd=T, env=ENV, capture_output=True, text=True)


def add(task, change="split a story when it touches more than 3 files", **kw):
    a = dict(symptom="green-worker hit the token hard limit", cause="story spanned 7 files",
             change=change, applies_to="planner", evidence="ev_123,sprint1/s1-01-x/output.md")
    a.update(kw)
    args = [L, "add"] + sum([["--" + k.replace("_", "-"), v] for k, v in a.items()], [])
    if task:
        args += ["--task", task]
    return run(*args)


subprocess.run(["git", "init", "-q"], cwd=T, check=True)

# 1) a complete candidate is stored with its id and evidence, and emits memory.candidate
r = add("S1-01-T1")
assert r.returncode == 0 and r.stdout.strip() == "L-001", r.stderr
c = json.loads(open(os.path.join(DOCS, "lessons.jsonl")).readline())
assert c["evidence_refs"] == ["ev_123", "sprint1/s1-01-x/output.md"] and c["task"] == "S1-01-T1", c
ev = [json.loads(x) for x in open(os.path.join(T, ".aladdin", "logs", "events.jsonl"))]
assert ev[-1]["event"] == "memory.candidate" and ev[-1]["data"]["lesson_id"] == "L-001", ev[-1]
ok("candidate recorded with symptom/cause/change/applies_to/evidence_refs; memory.candidate emitted")

# 2) missing fields, missing evidence and gate-weakening changes are refused
assert add("S1-01-T2", cause="").returncode == 2
assert add("S1-01-T2", evidence="").returncode == 2
r = add("S1-01-T2", change="skip the test when it is flaky")
assert r.returncode == 2 and "weaken" in r.stderr, r.stderr
assert len(open(os.path.join(DOCS, "lessons.jsonl")).readlines()) == 1
ok("empty field, no evidence, or a gate-weakening change → refused, nothing stored")

# 3) a one-off is not recurring; the same change in a second task is (case/punctuation-insensitive)
assert "no recurring" in run(L, "recurring").stdout
assert add("S1-01-T1").returncode == 0  # same task again: still one occurrence
assert "no recurring" in run(L, "recurring").stdout
assert add("S2-03-T1", change="Split a story when it touches more than 3 files.").returncode == 0
out = run(L, "recurring").stdout
assert "| planner |" in out and "L-001 L-002 L-003" in out and "| 2 |" in out, out
ok("recurrence counts distinct tasks; 2 tasks → a memory.md row for the archivist")

# 4) gate-memory blocks a promoted one-off and accepts a recurring group or a human keep
head = "---\ntype: memory\nupdated_at: 2026-10-17T00:00:00Z\n---\n# Entries\n\n| id | applies_to | text | origin | recurrence | added |\n|---|---|---|---|---|---|\n"
assert add("S3-01-T1", change="pin the toolchain version in task.env").returncode == 0  # L-004, one-off
mem = os.path.join(DOCS, "memory.md")
open(mem, "w").write(head + "| M-001 | planner | pin the toolchain | L-004 | 1 | 2026-10-17 |\n")
r = run(os.path.join(BIN, "gate-memory"), mem)
assert r.returncode == 2 and "L-004" in r.stderr, r.stderr
open(mem, "w").write(head + "| M-001 | planner | pin the toolchain | L-004 | 1 keep | 2026-10-17 |\n"
                     "| M-002 | planner | split stories >3 files | L-001 L-003 | 2 | 2026-10-17 |\n")
r = run(os.path.join(BIN, "gate-memory"), mem)
assert r.returncode == 0, r.stderr
ok("gate-memory: a one-off promotion is blocked; recurring or human-kept rows pass")

# 5) once promoted, the group no longer shows as recurring; unknown ids are refused
assert "no recurring" in run(L, "recurring").stdout
open(mem, "w").write(head + "| M-001 | all | x | L-999 | 2 | 2026-10-17 |\n")
assert run(os.path.join(BIN, "gate-memory"), mem).returncode == 2
ok("promoted groups drop out of `recurring`; a row citing an unknown lesson is blocked")

print("\n%d checks passed" % PASS[0])
