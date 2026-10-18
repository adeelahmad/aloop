#!/usr/bin/env python3
"""ROADMAP §4.10: the sprint report's Metrics table, derived from harness files only
(attempt states, the gate log) — percentiles, breaches by role/class, hard-breach waste,
tokens per accepted task, first-pass rates, re-plan rate, cache-hit/churn, context peak, and\nfrom the event stream: commits, tests added, time soft limits, semantic context served.
    python3 scripts/test/metrics.test.py
"""
import json
import os
import subprocess
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-metrics-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"))
ENV.pop("AGENTIC_HOST", None)
PASS = [0]


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def sh(*args):
    r = subprocess.run(args, cwd=T, env=ENV, capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout.strip()


sh("git", "init", "-q"); sh("git", "config", "user.email", "a@a"); sh("git", "config", "user.name", "A")
sh("git", "commit", "-q", "--allow-empty", "-m", "init")
sprint = os.path.join(T, "docs", "agents", "sprint1")
story = os.path.join(sprint, "s1-01-x")
os.makedirs(story)
open(os.path.join(story, "tasks.md"), "w").write("## T1\n## T2\n## T3\n## T4\n")
open(os.path.join(sprint, "execution.log"), "w").write("2026-10-17T01:00:00Z wave-1 replan S1-01 start T3\n")
BD = os.path.join(T, ".agentic", "budget")
os.makedirs(BD)
for i, (task, toks, mins, killed, cls) in enumerate([
        ("S1-01-T1", 40000, 4.0, False, None), ("S1-01-T2", 60000, 8.0, False, None),
        ("S1-01-T4", 20000, 2.0, False, None), ("S1-01-T3", 260000, 12.0, True, "TASK_TOO_LARGE")]):
    s = {"agent_id": "a%d" % i, "role": "green-worker", "task": task, "attempt": "1", "story_dir": story, "start": 1,
         "budget": {"TOKENS_HARD": 250000, "TIME_HARD_MIN": 30}, "tokens": toks, "elapsed_min": mins, "done": 2,
         "hard": 2 if killed else False,
         "usage": {"in": toks // 2, "cache_read": toks * 4, "cache_write": toks // 2, "out": toks // 2, "context": toks + 10000}}
    if killed:
        s.update({"hook_killed": 2, "breach_class": [cls, "x"]})
    json.dump(s, open(os.path.join(BD, s["agent_id"] + ".json"), "w"))
logs = os.path.join(T, ".agentic", "logs")
os.makedirs(logs)
with open(os.path.join(logs, "gates.jsonl"), "w") as f:
    for task, rc in (("S1-01-T1", 0), ("S1-01-T2", 2), ("S1-01-T2", 0), ("S1-01-T4", 0)):
        f.write(json.dumps({"ts": "2026-10-17T01:00:00Z", "gate": "gate-green-verify", "role": "green-worker", "task": task,
                            "attempt": "1", "story_dir": story, "exit": rc, "selfcheck": False}) + "\n")

# a merged commit adding one test file and one source file, plus time/semantic events
os.makedirs(os.path.join(T, "tests"))
open(os.path.join(T, "tests", "test_x.py"), "w").write("def test_x():\n    pass\n")
open(os.path.join(T, "x.py"), "w").write("X = 1\n")
sh("git", "add", "-A", "tests", "x.py"); sh("git", "commit", "-q", "-m", "feat: x")
sha = sh("git", "rev-parse", "HEAD")
with open(os.path.join(logs, "events.jsonl"), "w") as f:
    for ev, data in (("commit.created", {"sha": sha}), ("task.time_soft_limit", {}),
                     ("semantic_target.resolved", {"change_ref": "C1"}), ("semantic_target.resolved", {"change_ref": "C2"})):
        f.write(json.dumps({"event": ev, "ids": {"sprint": "S1", "task": "S1-01-T1"}, "data": data}) + "\n")
    f.write(json.dumps({"event": "commit.created", "ids": {"sprint": "S2"}, "data": {"sha": "0" * 40}}) + "\n")
    f.write(json.dumps({"event": "context.rotated", "ts": "2026-10-17T01:00:00Z", "ids": {}, "data": {}}) + "\n")

sh(os.path.join(BIN, "stats"), "sprint-close")
st = open(os.path.join(sprint, "stats.md")).read()
row = lambda name: [l for l in st.splitlines() if l.startswith("| " + name)][0]  # noqa: E731

assert row("Task attempt duration p50 / p90 / p95 (min)").endswith("| 4.0 / 12.0 / 12.0 |"), row("Task attempt duration")
assert row("Task attempt tokens p50 / p90 / p95").endswith("| 40.0k / 260.0k / 260.0k |"), row("Task attempt tokens")
ok("p50/p90/p95 duration and tokens (nearest rank)")

assert row("Hard breaches by role / class").endswith("| green-worker/TASK_TOO_LARGE 1 |")
assert row("Hard-breach waste").endswith("| 260.0k (68% of worker tokens) |"), row("Hard-breach waste")
assert row("Tokens per accepted task").endswith("| 126.7k |"), row("Tokens per accepted task")
ok("breaches by role/class, hard-breach waste, tokens per accepted task")

assert row("First-pass rate by gate").endswith("| gate-green-verify 2/3 |"), row("First-pass rate")
assert row("Re-plan rate").endswith("| 0.25 |")
ok("first-pass rate per gate and re-plan rate")

assert row("Cache-hit ratio").endswith("| 80% |") and row("Cache churn").endswith("| 0.12 |"), (row("Cache-hit"), row("Cache churn"))
assert row("Context peak").endswith("| 270.0k |")
assert row("Context-window share").endswith("| 135% of 200.0k |"), row("Context-window share")   # the default window; the peak overran it
ok("cache-hit ratio, churn, context peak and context-window share from the usage classes")

assert row("Commits produced / test files added").endswith("| 1 / 1 |"), row("Commits produced")
assert row("Time soft limits reached").endswith("| 1 |") and row("Semantic context served").endswith("| 2 |")
assert row("Context rotations / compactions").endswith("| 1 / 0 |"), row("Context rotations")
assert row("Usage accuracy").endswith("| exact (read from host logs) |"), row("Usage accuracy")
ok("commits produced, test files added, time soft limits and semantic context served, from the event stream")

print("\n%d checks passed" % PASS[0])
