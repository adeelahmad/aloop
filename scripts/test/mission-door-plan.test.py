#!/usr/bin/env python3
"""ROADMAP M11.10: the door-classifier decides one-way vs two-way BEFORE execution. It tags every planned task
(`mission doors tag`); `mission doors plan SN` lists the plan's open one-way doors all together; its stop gate
(gate-door-plan) refuses to finish while a task is untagged; gate-dispatch parks only a task whose door the
human has not authorized, and the rest of the sprint runs. Tags only add: a later two-way tag removes nothing.

    python3 scripts/test/mission-door-plan.test.py
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "fixtures"))
from mission_fixture import Project, M, say  # noqa: E402

BIN = os.path.dirname(M.__file__)
PASS = [0]


def ok(name):
    PASS[0] += 1
    print("ok -", name)


p = Project()
P, ENV = p.dir, p.env


def run(*args, inp=None, env=None):
    return subprocess.run(list(args), cwd=P, env=env or ENV, capture_output=True, text=True, input=inp)


def write(rel, text):
    f = os.path.join(P, rel)
    os.makedirs(os.path.dirname(f), exist_ok=True)
    with open(f, "w") as fh:
        fh.write(text)


def doors(*args):
    return run(os.path.join(BIN, "mission"), "doors", *args)


write("docs/agents/defaults.md", "---\ntype: agentic-defaults\n---\n```conf\nAGENTIC_MODEL_PLANNING=haiku\n"
      "AGENTIC_MODEL_WORKER=haiku\n```\n")
say(P, ENV, "/aladdin add a csv export and publish the package")
r = run(os.path.join(BIN, "intent"), "new", "--source", "prompt", "--raw", "add a csv export and publish the package")
path = r.stdout.strip()
doc = json.load(open(path))
doc.update(actor="u", want="w", why="y", done_looks_like="d", success_scenarios=["s"], failure_scenarios=["f"],
           connections=["c"])
json.dump(doc, open(path, "w"))
assert run(os.path.join(BIN, "intent"), "check", path).returncode == 0
sd = "docs/agents/sprint1"
write(sd + "/stories.md", "---\ntype: stories\nsprint: 1\n---\n# S1\n\n- S1-01 (s1-01-csv)\n- S1-02 (s1-02-pub)\n")
write(sd + "/plan.md", "---\ntype: sprint-plan\nsprint: 1\nstage: \"2\"\n---\n# Waves\n")
for sid, story in (("s1-01-csv", "S1-01"), ("s1-02-pub", "S1-02")):
    for f in ("tasks.md", "validate.md", "plan.md"):
        write("%s/%s/%s" % (sd, sid, f), "---\nstory: %s\n---\n## T1 — the task\n" % story)
assert run(os.path.join(BIN, "ids"), "freeze", sd).returncode == 0
for tid, sid in (("S1-01-T1", "s1-01-csv"), ("S1-02-T1", "s1-02-pub")):
    write(".aladdin/state/tasks/%s.env" % tid, "TASK_ID=%s\nAGENT_ROLE=green-worker\nSTORY_DIR=%s\n"
          % (tid, os.path.join(P, sd, sid)))
r = run(os.path.join(BIN, "events"), "authority", "--initiated-by", "Adeel", "--scope", "intent", "--sprint", "1")
assert r.returncode == 0, r.stderr                                   # the typed intent is the grant (M11.2)


def dispatch(tid):
    pl = {"session_id": "s", "cwd": P, "tool_name": "Agent", "tool_input": {
        "subagent_type": "aloop:green-worker", "prompt": "TASK_ID: %s\nDo it." % tid, "isolation": "worktree",
        "model": "haiku"}}
    return run(os.path.join(BIN, "gate-dispatch"), inp=json.dumps(pl))


def gate():
    return run(os.path.join(BIN, "gate-door-plan"))


# 1) before classification: every task untagged; the classifier's stop is refused until each is tagged
r = doors("plan", "S1", "--json")
assert r.returncode == 0 and {x["state"] for x in json.loads(r.stdout)["rows"]} == {"untagged"}, r
r = gate()
assert r.returncode == 2 and "S1-01-T1" in r.stderr and "S1-02-T1" in r.stderr, r.stderr
ok("an untagged task blocks the door-classifier's stop")

# 2) tags: a reversible task two-way, the publish task one-way; the plan lists the open door once, together
assert doors("tag", "S1-01-T1", "--two-way", "--reason", "local code and tests").returncode == 0
assert doors("tag", "S1-02-T1", "--door", "publish-release", "--reason", "publishes the package").returncode == 0
assert doors("tag", "S1-02-T1", "--door", "no-such-door", "--reason", "x").returncode == 64
assert doors("tag", "S1-02-T1", "--reason", "x").returncode == 64
r = doors("plan", "S1")
assert r.returncode == 3 and "1 one-way door(s) in S1's plan" in r.stderr and "ASK[door] publish-release for S1-02-T1" in r.stderr, r.stderr
assert "aloop mission doors authorize publish-release" in r.stderr and "S1-01-T1" not in r.stderr.split("ASK")[1]
r = gate()
assert r.returncode == 0 and "publish-release" in r.stderr, r.stderr
ok("tags record each task; `doors plan` lists the open one-way doors together; the stop passes once all are tagged")

# 3) gate-dispatch parks only the one-way task; the two-way one runs
r = dispatch("S1-01-T1")
assert r.returncode == 0, r.stderr
r = dispatch("S1-02-T1")
assert r.returncode == 2 and "parked on a one-way door" in r.stderr and "publish-release" in r.stderr, r.stderr
ok("only the task with an open one-way door is parked; the rest of the sprint is dispatched")

# 4) tags only add: a later two-way tag does not remove the door
assert doors("tag", "S1-02-T1", "--two-way", "--reason", "trust me").returncode == 0
assert dispatch("S1-02-T1").returncode == 2
ok("a two-way tag never removes a tagged door")

# 5) the human authorizes in their own terminal: the task runs
env = dict(ENV)
env.pop("CLAUDECODE", None)
r = run(os.path.join(BIN, "mission"), "doors", "authorize", "publish-release", "--by", "Adeel", "--reason", "ship it",
        env=env)
assert r.returncode == 0, r.stderr
assert doors("plan", "S1").returncode == 0
r = dispatch("S1-02-T1")
assert r.returncode == 0, r.stderr
ok("once the human authorizes the door, the parked task is dispatched")

# 6) a planned wall is reported for a re-plan
assert doors("tag", "S1-01-T1", "--door", "bypass-gate", "--reason", "skips hooks").returncode == 0
assert doors("plan", "S1").returncode == 2 and gate().returncode == 2
ok("a planned wall blocks the classifier's stop: re-plan it")

print("\n%d checks passed" % PASS[0])
