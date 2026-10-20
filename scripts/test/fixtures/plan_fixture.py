"""plan_fixture.py — the human's approval of a sprint's plan in a test project (ROADMAP M0.9).

    from plan_fixture import approve_plan
    approve_plan(cwd, env)          # sprint 1: a plan record when none, then the human types the line it asks for

gate-dispatch admits no execution role of a sprint before this, so every test that dispatches one
approves its sprint first, the way the human does: `events authority` asks (exit 3) for
`approve SN <code>`, the human types it (the UserPromptSubmit hook captures it), and it runs again.
"""
import json
import os
import subprocess

from mission_fixture import BIN, as_human

EV = os.path.join(BIN, "events")


def approve_plan(cwd, env, sprint=1, by="tester", mode="reviewed"):
    """Approve sprint N's plan as it is now (mode autonomous: grant it autonomy); returns the grant's event id."""
    os.makedirs(os.path.join(cwd, "docs", "agents", "sprint%d" % sprint), exist_ok=True)
    log = os.path.join(cwd, ".aladdin", "logs", "events.jsonl")
    seen = [json.loads(ln) for ln in open(log)] if os.path.exists(log) else []
    if not any(e.get("event") in ("plan.created", "plan.revised") and (e.get("data") or {}).get("sprint") == "S%d" % sprint
               for e in seen):         # what `ids freeze` records when gate-stage2-complete passes
        r = subprocess.run([EV, "emit", "plan.created", "--role", "planner", "--data", "sprint=S%d" % sprint],
                           cwd=cwd, env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    args = [EV, "authority", "--initiated-by", by, "--mode", mode, "--scope", "sprint %d as approved" % sprint,
            "--sprint", str(sprint)]
    r = as_human(lambda: subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True), cwd, env)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()
