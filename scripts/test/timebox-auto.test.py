#!/usr/bin/env python3
"""ROADMAP §4.2: the time budget's `auto` mode. The planner proposes one stable task
duration per project (with min/max), the supervisor records it, `budget` uses it, and it
is revised only with evidence that exists. Task-level overrides still win.
    python3 scripts/test/timebox-auto.test.py
"""
import json
import os
import subprocess
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-timebox-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"))
ENV.pop("AGENTIC_HOST", None)
PASS = [0]
TB = os.path.join(BIN, "timebox")


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def run(*args):
    return subprocess.run(args, cwd=T, env=ENV, capture_output=True, text=True)


def resolved(*extra):
    out = run(os.path.join(BIN, "budget"), "resolve", *extra).stdout
    return dict(l.split("=", 1) for l in out.splitlines() if "=" in l)


subprocess.run(["git", "init", "-q"], cwd=T, check=True)
os.makedirs(os.path.join(T, "docs", "agents"))
DEF = os.path.join(T, "docs", "agents", "defaults.md")

# 1) proposals are validated and recorded once; fixed mode ignores the record
assert run(TB, "propose", "--minutes", "50", "--min", "15", "--max", "45", "--reason", "x").returncode == 2
assert run(TB, "propose", "--minutes", "24", "--min", "15", "--max", "45").returncode == 2  # no reason
r = run(TB, "propose", "--minutes", "24", "--min", "15", "--max", "45", "--reason", "median GREEN attempt 16 min")
assert r.returncode == 0, r.stderr
assert run(TB, "propose", "--minutes", "30", "--min", "15", "--max", "45", "--reason", "again").returncode == 2
assert resolved()["BUDGET_TIME_HARD_MIN"] == "30" and "NOT in force" in run(TB, "show").stdout
ok("propose: N within [min,max] + a reason, recorded once; ignored while AGENTIC_TIME_MODE=fixed")

# 2) auto mode: budget uses it (soft = 2/3); a tasks.md budget line still wins
open(DEF, "w").write("AGENTIC_TIME_MODE=auto\n")
b = resolved()
assert b["BUDGET_TIME_HARD_MIN"] == "24" and b["BUDGET_TIME_SOFT_MIN"] == "16", b
tasks = os.path.join(T, "tasks.md")
open(tasks, "w").write("## T1 — big\nbudget: time_hard=40m\n")
assert resolved("--tasks", tasks, "--task", "T1")["BUDGET_TIME_HARD_MIN"] == "40"
ok("AGENTIC_TIME_MODE=auto: every attempt gets the timebox (24/16 min); a task override still wins")

# 3) revisions need existing evidence and stay in range; history and decisions are kept
assert run(TB, "revise", "--minutes", "30", "--reason", "slower suite").returncode == 2
r = run(TB, "revise", "--minutes", "30", "--reason", "slower suite", "--evidence", "docs/agents/sprint1/stats.md")
assert r.returncode == 2 and "evidence not found" in r.stderr, r.stderr
os.makedirs(os.path.join(T, "docs", "agents", "sprint1"))
open(os.path.join(T, "docs", "agents", "sprint1", "stats.md"), "w").write("p90 28 min\n")
assert run(TB, "revise", "--minutes", "60", "--reason", "x", "--evidence", "docs/agents/sprint1/stats.md").returncode == 2
r = run(TB, "revise", "--minutes", "30", "--reason", "p90 attempt 28 min", "--evidence", "docs/agents/sprint1/stats.md")
assert r.returncode == 0, r.stderr
assert resolved()["BUDGET_TIME_HARD_MIN"] == "30"
rec = json.load(open(os.path.join(T, ".aladdin", "state", "timebox.json")))
assert [h["minutes"] for h in rec["history"]] == [24, 30] and rec["history"][1]["evidence"] == ["docs/agents/sprint1/stats.md"]
dec = [json.loads(x) for x in open(os.path.join(T, ".aladdin", "logs", "events.jsonl")) if '"decision.recorded"' in x]
assert len(dec) == 2 and "timebox revised 30m" in dec[1]["decision"]["reason"], dec
ok("revise: needs existing evidence and stays in range; history + decision.recorded kept")

print("\n%d checks passed" % PASS[0])
