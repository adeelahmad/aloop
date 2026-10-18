#!/usr/bin/env python3
"""ROADMAP §4.3: the planning feedback lifecycle. The planner gets a MINIMAL failure
envelope (facts + references, nothing inlined), a grace rule (0–1 failed or hard-breached
tasks → no nudge; 2+ → one "plan smaller" nudge for the next planning cycle only) and a
calibration verdict derived from the breach classes.
    python3 scripts/test/planner-feedback.test.py
"""
import json
import os
import shutil
import subprocess
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-feedback-")
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
BD = os.path.join(T, ".aladdin", "budget")
os.makedirs(BD)


def attempt(agent, task, tokens, killed=None, mins=5.0):
    s = {"agent_id": agent, "role": "green-worker", "task": task, "attempt": "1", "story_dir": story, "start": 1,
         "budget": {"TOKENS_HARD": 250000, "TIME_HARD_MIN": 30, "TOKEN_METER": "fresh"}, "tokens": tokens,
         "elapsed_min": mins, "done": 2, "hard": 2 if killed else False, "transcript": "/t/%s.jsonl" % agent}
    if killed:
        s.update({"hook_killed": 2, "breach_class": [killed, "evidence for %s" % task]})
    json.dump(s, open(os.path.join(BD, agent + ".json"), "w"))


def close():
    sh(os.path.join(BIN, "stats"), "sprint-close", "1")
    return open(os.path.join(T, "docs", "agents", "NEXT.md")).read()


# 1) grace: one hard-breached task → no nudge
attempt("a1", "S1-01-T1", 260000, killed="TASK_TOO_LARGE")
attempt("a2", "S1-01-T2", 40000)
nxt = close()
assert "Grace rule: 1 failed or hard-breached task(s) — no nudge." in nxt and "Plan smaller" not in nxt, nxt
ok("grace rule: 0–1 troubled tasks → no nudge")

# 2) two or more → one nudge with a calibration verdict, for the next cycle only
attempt("a3", "S1-01-T3", 255000, killed="TASK_TOO_LARGE")
nxt = close()
assert "## Planner feedback (for sprint 2's planning only)" in nxt
assert "Calibration verdict: **keep the budget and plan smaller** (`plan-smaller`)" in nxt, nxt
assert "S1-01-T1, S1-01-T3" in nxt and "budget envelope <task>" in nxt
ok("2+ troubled tasks: one plan-smaller nudge, scoped to the next planning cycle")

# 3) the verdict follows the breach classes
for cls, verdict in (("TEST_SUITE_SLOW", "raise-time"), ("ENVIRONMENT_SLOW", "fix-environment"),
                     ("CONTEXT_PATHOLOGY", "reduce-context")):
    attempt("a1", "S1-01-T1", 60000, killed=cls)
    attempt("a3", "S1-01-T3", 60000, killed=cls)
    assert "(`%s`)" % verdict in close(), cls
attempt("a1", "S1-01-T1", 260000, killed="TASK_TOO_LARGE")
attempt("a3", "S1-01-T3", 260000, killed="TASK_TOO_LARGE")
attempt("a2", "S1-01-T2", 230000)          # the passing attempt ran at 92% of its limit
assert "(`raise-tokens`)" in close()
ok("verdict: slow tests → raise time, slow env → fix env, context → reduce, too-large near the cap → raise tokens")

# 4) a gate-blocked task counts toward the grace rule
shutil.rmtree(BD); os.makedirs(BD)
attempt("a1", "S1-01-T1", 260000, killed="AGENT_LOOPING")
logs = os.path.join(T, ".aladdin", "logs")
os.makedirs(logs, exist_ok=True)
open(os.path.join(logs, "gates.jsonl"), "w").write(json.dumps(
    {"ts": "2026-10-17T00:00:00Z", "gate": "gate-green-verify", "role": "green-worker", "task": "S1-01-T4",
     "attempt": "1", "story_dir": story, "exit": 2, "selfcheck": False}) + "\n")
assert "(`plan-smaller`)" in close()
ok("a task that failed its gate counts toward the grace rule")

# 5) the failure envelope: minimal facts + references, nothing inlined
env = sh(os.path.join(BIN, "budget"), "envelope", "S1-01-T1")
for want in ("task: S1-01-T1", "role: green-worker", "limit: token_hard", "observed: 260.0k tokens (fresh meter) of 250.0k",
             "breach_class: AGENT_LOOPING — evidence for S1-01-T1", "transcript: /t/a1.jsonl", "output: %s/output.md" % story):
    assert want in env, (want, env)
assert len(env.splitlines()) <= 16
ok("budget envelope: task/attempt/role/limit/observed/class/gate/changed + refs, under 16 lines")

print("\n%d checks passed" % PASS[0])
