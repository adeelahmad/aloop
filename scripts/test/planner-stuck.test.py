#!/usr/bin/env python3
"""The planner's stop gate (gate-stage2-complete on SubagentStop) logs its verdicts to
gates.jsonl like every other gate, and the stuck release covers it: the same block three
times in a row releases the planner's stop UNVERIFIED with a stuck marker the supervisor
acts on. Live e2e run 19: a re-planning planner was blocked 9 times on one unchanged reason,
none of them counted, and only a second dispatch ended it.

    python3 scripts/test/planner-stuck.test.py
"""
import json
import os
import subprocess
import tempfile

BIN = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin", "bin"))
GATE = os.path.join(BIN, "gate-stage2-complete")
T = tempfile.mkdtemp(prefix="aloop-planner-stuck-")
ENV = {k: v for k, v in os.environ.items() if k not in ("SKIP_HOOKS", "AGENTIC_SELFCHECK", "AGENTIC_STUCK_BLOCKS")}
ENV["HOME"] = os.path.join(T, "home")
ENV["PATH"] = "/usr/bin:/bin"        # no md-db: the structural step falls back (fixture is minimal)
ENV.pop("CLAUDE_PLUGIN_DATA", None)
SP = os.path.join(T, "docs", "agents", "sprint1")
LOG = os.path.join(T, ".aladdin", "logs", "gates.jsonl")
EVENTS = os.path.join(T, ".aladdin", "logs", "events.jsonl")
n = 0


def ok(msg):
    global n
    n += 1
    print("ok - " + msg)


def write(rel, text):
    p = os.path.join(T, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)


def hook(agent_id="a1", **env):
    p = {"session_id": "s1", "cwd": T, "agent_id": agent_id, "agent_type": "aloop:planner", "hook_event_name": "SubagentStop"}
    return subprocess.run([GATE, SP], input=json.dumps(p), cwd=T, env=dict(ENV, **env), capture_output=True, text=True)


def rows():
    return [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []


subprocess.run(["git", "init", "-q"], cwd=T, check=True)
# a plan defect the planner keeps not fixing: stories.md names S1-01 but there is no story dir
write("docs/agents/sprint1/stories.md", "---\ntype: stories\nsprint: 1\n---\n- S1-01 export\n")

rcs = [hook().returncode for _ in range(2)]
assert rcs == [2, 2], rcs
r = hook()
assert r.returncode == 0 and "STUCK" in r.stderr and "NOT verified" in r.stderr, r
marker_dir = os.path.join(T, ".aladdin", "state", "stuck")
markers = os.listdir(marker_dir)
assert len(markers) == 1 and markers[0].endswith(".a1"), markers
assert "no story dir" in open(os.path.join(marker_dir, markers[0])).read()
ok("the third identical planner block releases its stop unverified, with a stuck marker")

got = [x for x in rows() if x["gate"] == "gate-stage2-complete"]
assert [x["exit"] for x in got] == [2, 2, 0] and all(x["role"] == "planner" for x in got), got
assert got[2].get("released") == "stuck" and got[0]["reason"] == got[1]["reason"] != "", got
ev = [json.loads(l) for l in open(EVENTS)]
assert [e["event"] for e in ev if e["event"].startswith("gate.")] == ["gate.failed"] * 3, [e["event"] for e in ev]
ok("every planner verdict is in gates.jsonl (role planner, same reason hash) and the event stream")

r = hook(agent_id="a2")
assert r.returncode == 2 and "STUCK" not in r.stderr, r
ok("a re-dispatched planner (new agent id) starts the count over")

# the supervisor's self-check (bin/selfcheck) and a direct call never release, and a direct
# call is logged as a self-check, not a verdict
for _ in range(3):
    r = hook(agent_id="a3", AGENTIC_SELFCHECK="1")
    assert r.returncode == 2 and "STUCK" not in r.stderr, r
r = subprocess.run([GATE, SP], cwd=T, env=ENV, capture_output=True, text=True, stdin=subprocess.DEVNULL)
assert r.returncode == 2 and "STUCK" not in r.stderr, r
last = rows()[-1]
assert last["selfcheck"] is True and last["role"] == "", last
assert len([e for e in [json.loads(l) for l in open(EVENTS)] if e["event"].startswith("gate.")]) == 4, "a self-check is not an event"
ok("a self-check never releases and is logged as such; a direct call emits no gate event")

# a sound plan passes and is logged as a pass
write("docs/agents/sprint1/s1-01-export/tasks.md", "---\ntype: tasks\nstory: S1-01\n---\n## T1 — a\n")
write("docs/agents/sprint1/s1-01-export/validate.md", "---\ntype: validate\nstory: S1-01\n---\n## T1\n")
write("docs/agents/sprint1/s1-01-export/plan.md", "---\ntype: plan\nstory: S1-01\nscope: tests only\n---\n")
r = hook(agent_id="a4")
assert r.returncode == 0 and "Stage-2 complete" in r.stderr, r
assert rows()[-1]["exit"] == 0 and rows()[-1]["selfcheck"] is False
ok("a complete plan passes and is logged as a pass")

print("\n%d checks passed" % n)
