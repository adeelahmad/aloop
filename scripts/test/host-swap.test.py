#!/usr/bin/env python3
"""ROADMAP §4.9: host swap. A sprint started on one host resumes on another from portable
state only (sprint dir, .aladdin/config.yaml, .agentic/logs). `host swap --to H` refuses a
swap that would lose something (no config.yaml, no provenance, live worker worktrees), names
the target's capability gaps, records the handoff, and SessionStart on the new host resumes
the sprint with the same resume rule.
    python3 scripts/test/host-swap.test.py
"""
import json
import os
import subprocess
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-swap-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"), AGENTIC_NO_AUTOINSTALL="1",
           GIT_AUTHOR_NAME="A", GIT_AUTHOR_EMAIL="a@a", GIT_COMMITTER_NAME="A", GIT_COMMITTER_EMAIL="a@a")
ENV.pop("AGENTIC_HOST", None)
PASS = [0]
H = os.path.join(BIN, "host")
SP = "docs/agents/sprint1"


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def run(*args, inp=None, **env):
    return subprocess.run(args, cwd=T, env=dict(ENV, **env), capture_output=True, text=True, input=inp)


def write(rel, text):
    p = os.path.join(T, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)


def events(kind):
    try:
        return [e for e in map(json.loads, open(os.path.join(T, ".agentic", "logs", "events.jsonl"))) if e["event"] == kind]
    except OSError:
        return []


subprocess.run(["git", "init", "-q", "-b", "main"], cwd=T, check=True)
write("a.py", "x = 1\n")
subprocess.run(["git", "add", "-A"], cwd=T, check=True)
subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=T, env=ENV, check=True)

# 1) nothing portable yet: every missing piece is named, nothing is written
r = run(H, "swap", "--to", "opencode")
assert r.returncode == 2, r
for want in ("no .aladdin/config.yaml", "no docs/agents/sprintN dir", "no .agentic/logs/events.jsonl"):
    assert want in r.stderr, (want, r.stderr)
assert not os.path.exists(os.path.join(T, ".agentic", "state", "host-swap.json"))
assert run(H, "swap", "--to", "claude").returncode == 2        # already on claude
assert run(H, "swap", "--to", "cursor").returncode == 64
ok("swap refuses when the state is not portable, names each gap, writes nothing")

# the sprint, as the claude host left it: closed with a handoff and NEXT.md, settings in config.yaml
write("docs/agents/defaults.md", "AGENTIC_TOKENS_HARD=300000\n")
assert run(os.path.join(BIN, "config"), "migrate").returncode == 0
write(SP + "/stories.md", "---\ntype: stories\nsprint: 1\n---\n- S1-01 export\n")
write(SP + "/s1-01-export/tasks.md", "---\ntype: tasks\nstory: S1-01\n---\n## T1 — writer\n## T2 — wire\n")
write(SP + "/s1-01-export/plan-ready.md", "---\ntype: plan-ready\nstory: S1-01\nfrom_red_at: 2026-10-18T00:00:00Z\n---\n"
      "## T1\n- [x] `tests/t.py::test_a` — a\n## T2\n- [ ] `tests/t.py::test_b` — b\n")
write(SP + "/handoff.json", json.dumps({"schema": "aladdin.handoff/1", "sprint": 1, "completed": [{"task": "S1-01-T1"}], "open": ["S1-01-T2"]}))
write("docs/agents/NEXT.md", "# NEXT\nResume S1-01-T2.\n")
assert run(os.path.join(BIN, "events"), "emit", "sprint.started", "--role", "supervisor").returncode == 0

# 2) a live worker worktree blocks the swap (it would be orphaned)
assert run(os.path.join(BIN, "task-worktree"), "add", "S1-01-T2").returncode == 0, "fixture"
r = run(H, "swap", "--to", "opencode")
assert r.returncode == 2 and "1 worker worktree(s) still live" in r.stderr and "S1-01-T2" in r.stderr, r.stderr
assert run(os.path.join(BIN, "task-worktree"), "remove", "S1-01-T2").returncode == 0, "fixture"
ok("a live worker worktree blocks the swap until it is finished or removed")

# 3) the swap: recorded from portable state, capability gaps named, resume rule applied, event emitted
r = run(H, "swap", "--to", "opencode")
assert r.returncode == 0, r.stderr
assert "claude → opencode from portable state (docs/agents/sprint1 + handoff.json, .aladdin/config.yaml, .agentic/logs/events.jsonl)" in r.stdout, r.stdout
assert "compaction: advertised → unknown" in r.stdout and "correctness path" in r.stdout, r.stdout
assert "RE-RUN S1-01-T1" in r.stdout, r.stdout          # done in plan-ready.md, no gate verdict: re-gate on the new host
rec = json.load(open(os.path.join(T, ".agentic", "state", "host-swap.json")))
assert rec["schema"] == "aladdin.host-swap/1" and (rec["from"], rec["to"]) == ("claude", "opencode")
assert rec["handoff"] == "docs/agents/sprint1/handoff.json" and rec["next"] == "docs/agents/NEXT.md" and rec["rerun"], rec
ev = events("sprint.context_handoff")
assert len(ev) == 1 and ev[0]["data"]["to"] == "opencode" and ev[0]["host"] == "claude" and ev[0]["refs"]["swap"] == ".agentic/state/host-swap.json", ev
ok("swap records the handoff from portable state, names capability gaps and the tasks to re-gate, emits sprint.context_handoff")

# 4) SessionStart on the OLD host ignores the pending swap; on the NEW host it resumes the sprint and consumes it
start = json.dumps({"hook_event_name": "SessionStart", "cwd": T, "source": "startup"})
r = run(os.path.join(BIN, "session-start"), inp=start)
assert "Resumed on" not in r.stdout and os.path.exists(os.path.join(T, ".agentic", "state", "host-swap.json")), r.stdout
r = run(os.path.join(BIN, "session-start"), inp=start, AGENTIC_HOST="opencode")
ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
assert ctx.startswith("Resumed on opencode after a host swap from claude") and "Resume S1-01-T2." in ctx and "RE-RUN S1-01-T1" in ctx, ctx
assert not os.path.exists(os.path.join(T, ".agentic", "state", "host-swap.json"))
rot = events("context.rotated")
assert rot and rot[-1]["data"]["source"] == "host-swap" and rot[-1]["host"] == "opencode", rot
r = run(os.path.join(BIN, "session-start"), inp=start, AGENTIC_HOST="opencode")
assert "Resumed on" not in json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]   # consumed once
ok("SessionStart on the new host resumes from the same state, records the rotation, consumes the swap once")

# 5) mid-sprint (no NEXT.md, no handoff.json): the new host still gets the resume rule
os.remove(os.path.join(T, "docs", "agents", "NEXT.md"))
os.remove(os.path.join(T, SP, "handoff.json"))
assert run(H, "swap", "--to", "codex").returncode == 0
r = run(os.path.join(BIN, "session-start"), inp=start, AGENTIC_HOST="codex")
ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
assert "swap from claude" in ctx and "mid-flight" in ctx and "RE-RUN S1-01-T1" in ctx, ctx
ok("a mid-sprint swap resumes from plan-ready.md and the gate evidence when there is no NEXT.md yet")

print("\n%d checks passed" % PASS[0])
