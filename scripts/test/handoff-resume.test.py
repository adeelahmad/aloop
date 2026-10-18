#!/usr/bin/env python3
"""ROADMAP §4.4: the canonical sprint handoff and the resume rule. sprint-close writes
sprintN/handoff.json from ACCEPTED state (gate evidence, merged worktrees, open and
carried work, nudge, refs) and NEXT.md renders it; a task shown done without a passing
gate verdict is named for a re-run, at close and again at SessionStart.
    python3 scripts/test/handoff-resume.test.py
"""
import json
import os
import subprocess
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-handoff-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"))
ENV.pop("AGENTIC_HOST", None)
PASS = [0]


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def sh(*args, inp=None):
    r = subprocess.run(args, cwd=T, env=ENV, capture_output=True, text=True, input=inp)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout.strip()


sh("git", "init", "-q"); sh("git", "config", "user.email", "a@a"); sh("git", "config", "user.name", "A")
sh("git", "commit", "-q", "--allow-empty", "-m", "init")
sprint = os.path.join(T, "docs", "agents", "sprint1")
story = os.path.join(sprint, "s1-01-x")
os.makedirs(story)
open(os.path.join(story, "plan-ready.md"), "w").write(
    "## T1\n- [x] tests/a.rs::one\n## T2\n- [x] tests/a.rs::two\n## T3\n- [ ] tests/a.rs::three\n")
logs = os.path.join(T, ".agentic", "logs")
os.makedirs(logs)
gate = lambda task, rc, self=False: json.dumps({"ts": "2026-10-17T01:00:00Z", "gate": "gate-green-verify",  # noqa: E731
                                                "role": "green-worker", "task": task, "attempt": "1",
                                                "story_dir": story, "exit": rc, "selfcheck": self})
open(os.path.join(logs, "gates.jsonl"), "w").write("\n".join([
    gate("S1-01-T1", 0),             # T1: real evidence
    gate("S1-01-T2", 2),             # T2: ticked, but only a FAILED verdict ...
    gate("S1-01-T2", 0, self=True),  # ... and a self-check, which is not evidence
]) + "\n")
sh(os.path.join(BIN, "events"), "emit", "worktree.merged", "--task", "S1-01-T1")

# 1) resume-check names the task shown done without gate evidence
out = sh(os.path.join(BIN, "stats"), "resume-check")
assert out.count("RE-RUN") == 1 and "RE-RUN S1-01-T2" in out, out
ok("resume-check: done-without-evidence task named; self-checks and failures are not evidence")

# 2) the canonical handoff, built from accepted state
sh(os.path.join(BIN, "stats"), "sprint-close")
ho = json.load(open(os.path.join(sprint, "handoff.json")))
assert ho["schema"] == "aladdin.handoff/1" and ho["sprint"] == 1
done = {c["task"]: c for c in ho["completed"]}
assert set(done) == {"S1-01-T1", "S1-01-T2"} and done["S1-01-T1"]["gate"].startswith("gate-green-verify pass")
assert done["S1-01-T1"]["merged_event"] and done["S1-01-T2"]["gate"] is None
assert ho["unverified"] == ["S1-01-T2"] and ho["open"] == ["S1-01-T3"] and ho["carried"] == []
assert ho["refs"]["events"] == ".agentic/logs/events.jsonl" and "transcript" not in json.dumps(ho)
ok("handoff.json: completed (with gate + merge evidence), unverified, open, carried, refs — no transcripts")

# 3) NEXT.md renders it, and SessionStart re-checks against the gate log NOW
nxt = open(os.path.join(T, "docs", "agents", "NEXT.md")).read()
assert "canonical handoff: `docs/agents/sprint1/handoff.json`" in nxt
assert "## Resume rule: re-run these gates first" in nxt and "- S1-01-T2" in nxt
ctx = json.loads(sh(os.path.join(BIN, "session-start"), inp=json.dumps({"cwd": T})))
ctx = ctx["hookSpecificOutput"]["additionalContext"]
assert "Resume check (from the gate log, now):" in ctx and "RE-RUN S1-01-T2" in ctx
ok("NEXT.md renders the handoff; SessionStart adds the live resume check")

# 4) once the gate passes, the re-run line disappears
with open(os.path.join(logs, "gates.jsonl"), "a") as f:
    f.write(gate("S1-01-T2", 0) + "\n")
assert sh(os.path.join(BIN, "stats"), "resume-check") == ""
ctx = json.loads(sh(os.path.join(BIN, "session-start"), inp=json.dumps({"cwd": T})))["hookSpecificOutput"]["additionalContext"]
assert "Resume check" not in ctx
ok("after a passing verdict: nothing to re-run")

print("\n%d checks passed" % PASS[0])
