#!/usr/bin/env python3
"""ROADMAP §4.2: a sprint token envelope with admission control. Soft → one rollover
warning; hard → admission closes, refused tasks are carried over in rollover.md (not a
failure), gate-final accepts unticked boxes only for carried tasks, and NEXT.md hands the
carried tasks to the next planning cycle.
    python3 scripts/test/sprint-admission.test.py
"""
import json
import os
import subprocess
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-admit-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"))
ENV.pop("AGENTIC_HOST", None)
PASS = [0]


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def run(*args):
    return subprocess.run(args, cwd=T, env=ENV, capture_output=True, text=True)


def sh(*args):
    r = run(*args)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout.strip()


def events():
    p = os.path.join(T, ".aladdin", "logs", "events.jsonl")
    return [json.loads(l)["event"] for l in open(p)] if os.path.exists(p) else []


def attempt(agent, story, tokens):
    """A finished worker attempt in the sprint, as the hook leaves it."""
    d = os.path.join(T, ".aladdin", "budget")
    os.makedirs(d, exist_ok=True)
    json.dump({"agent_id": agent, "role": "green-worker", "task": "S1-01-T1", "attempt": "1", "story_dir": story,
               "start": 0, "budget": {"TOKENS_HARD": 250000, "TIME_HARD_MIN": 30}, "tokens": tokens, "done": 1,
               "hard": False}, open(os.path.join(d, agent + ".json"), "w"))


sh("git", "init", "-q"); sh("git", "config", "user.email", "a@a"); sh("git", "config", "user.name", "A")
sh("git", "commit", "-q", "--allow-empty", "-m", "init")
sprint = os.path.join(T, "docs", "agents", "sprint1")
story = os.path.join(sprint, "s1-01-x")
os.makedirs(story)
open(os.path.join(T, "docs", "agents", "defaults.md"), "w").write(
    "AGENTIC_SPRINT_TOKENS_SOFT=100000\nAGENTIC_SPRINT_TOKENS_HARD=200000\n")
admit = lambda t: run(os.path.join(BIN, "budget"), "admit", t)  # noqa: E731

# 1) under the envelope: admitted, silently
attempt("a1", story, 50000)
r = admit("S1-01-T2")
assert r.returncode == 0 and "ADMIT S1-01-T2" in r.stdout and "WARNING" not in r.stdout, r.stdout
ok("under the envelope: admitted")

# 2) soft: one rollover warning, still admitted
attempt("a2", story, 60000)
r = admit("S1-01-T3")
assert r.returncode == 0 and "ROLLOVER WARNING" in r.stdout, r.stdout
assert "ROLLOVER WARNING" not in admit("S1-01-T3").stdout and events() == ["sprint.token_soft_limit"]
ok("soft envelope: one rollover warning (sprint.token_soft_limit), still admitted")

# 3) hard: admission closes; refused tasks are carried over, the reason recorded
attempt("a3", story, 100000)
r = admit("S1-01-T4")
assert r.returncode == 3 and "ADMISSION CLOSED" in r.stdout, r.stdout
assert admit("S1-01-T5").returncode == 3 and admit("S1-01-T5").returncode == 3
ro = open(os.path.join(sprint, "rollover.md")).read()
assert "type: rollover" in ro and "not a failure" in ro and ro.count("- S1-01-T4") == 1 and ro.count("- S1-01-T5") == 1, ro
assert events() == ["sprint.token_soft_limit", "sprint.admission_closed", "sprint.rolled_over", "sprint.rolled_over"], events()
ok("hard envelope: admission closed, carried tasks listed once each in rollover.md")

# 4) gate-final's rollover check: only carried tasks may stay unticked
open(os.path.join(story, "plan-ready.md"), "w").write(
    "---\ntype: plan-ready\nstory: S1-01\nfrom_red_at: 2026-10-17T00:00:00Z\n---\n"
    "## T1\n- [x] tests/a.rs::one\n## T4\n- [ ] tests/a.rs::four\n")
assert run(os.path.join(BIN, "budget"), "rollover-check", sprint).returncode == 0
open(os.path.join(story, "plan-ready.md"), "a").write("## T2\n- [ ] tests/a.rs::two\n")
r = run(os.path.join(BIN, "budget"), "rollover-check", sprint)
assert r.returncode == 2 and "tests/a.rs::two" in r.stderr, r.stderr
ok("rollover-check: unticked carried task passes; an unticked task that was admitted blocks")

# 5) the next planning cycle sees the rollover
open(os.path.join(story, "plan-ready.md"), "w").write("## T4\n- [ ] tests/a.rs::four\n")
sh(os.path.join(BIN, "stats"), "sprint-close", "1")
nxt = open(os.path.join(T, "docs", "agents", "NEXT.md")).read()
stats = open(os.path.join(sprint, "stats.md")).read()
assert "## Rolled over: admission closed" in nxt and "- S1-01-T4" in nxt and "- S1-01-T5" in nxt, nxt
assert "| Admission closed (sprint envelope) | yes, 2 tasks carried over |" in stats, stats
ok("NEXT.md lists the carried tasks; stats.md records the rollover")

print("\n%d checks passed" % PASS[0])
