#!/usr/bin/env python3
"""ROADMAP M11.11: a listed one-way door can be made two-way, by the human only. Two halves, both needed:
ALOOP_DOORS_TWO_WAY names the door (an agent may set it), and the human's consent is a ledger row that counts
only in their own typed line `two-way door:DOOR <code>` (M9.17's mechanism, so an agent cannot forge it).
A wall or a question-only door is never reclassified; a revoke closes it again.

    python3 scripts/test/mission-doors-reclassify.test.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "fixtures"))
from mission_fixture import Project, M  # noqa: E402

PASS = [0]


def ok(name):
    PASS[0] += 1
    print("ok -", name)


p = Project()
subprocess.run(["git", "branch", "-M", "main"], cwd=p.dir, check=True)
LISTED = dict(p.env, ALOOP_DOORS_TWO_WAY="send-external-message, push-default-branch")


MISSION = os.path.join(os.path.dirname(M.__file__), "mission")


def classify(env=None, cmd="git push origin main"):
    return subprocess.run([MISSION, "doors", "classify", "--bash", cmd], cwd=p.dir, env=env or p.env,
                          capture_output=True, text=True)


# 1) the variable alone opens nothing
assert classify().returncode == 3
assert classify(LISTED).returncode == 3
ok("ALOOP_DOORS_TWO_WAY alone does not make a door two-way")

# 2) the agent's command alone records nothing: it asks for the human's own line
r = p.raw("doors", "reclassify", "push-default-branch", "--by", "Adeel", "--reason", "solo repo, CI gates main")
assert r.returncode == 3 and "they reply  two-way door:push-default-branch " in r.stderr, r.stderr
assert not M.records(p.dir, "door.reclassified")
ok("reclassify asks for the human's typed `two-way door:<id> <code>` line and records nothing before it")

# 3) the human types it: the consent is a ledger row citing their words; with the variable the door is two-way
r = p.run("doors", "reclassify", "push-default-branch", "--by", "Adeel", "--reason", "solo repo, CI gates main")
assert r.returncode == 0, r.stderr
rows = M.records(p.dir, "door.reclassified", "door:push-default-branch", role="human")
assert len(rows) == 1 and M.cites(rows[0]) and M.ledger_problems(p.dir) == [], rows
assert classify().returncode == 3                                # consent without the variable: still one-way
r = classify(LISTED)
assert r.returncode == 0 and "two-way by consent" in r.stderr, r.stderr
assert classify(LISTED, "git push --force origin main").returncode == 3   # only the door consented to
ok("consent + variable make that door two-way; either alone does not; other doors stay one-way")

# 4) a forged consent (no typed line) is a broken ledger: nothing opens
q = Project()
subprocess.run(["git", "branch", "-M", "main"], cwd=q.dir, check=True)
try:
    M.record(q.dir, "door.reclassified", "door:push-default-branch", "human", by="agent", reason="r",
             sha256="0" * 64)
except Exception:  # noqa: BLE001 — refusing to write is a pass too
    pass
r = subprocess.run([MISSION, "doors", "classify", "--bash",
                    "git push origin main"], cwd=q.dir, env=dict(q.env, ALOOP_DOORS_TWO_WAY="push-default-branch"),
                   capture_output=True, text=True)
assert r.returncode == 3, r.stderr
ok("a consent row with no typed line opens nothing")

# 5) walls and question-only doors are never reclassified; a revoke closes the door again
for did in ("bypass-gate", "forge-human-record", "spend-past-cap"):
    r = p.raw("doors", "reclassify", did, "--by", "Adeel", "--reason", "r")
    assert r.returncode == 2, (did, r.stderr)
assert p.raw("doors", "reclassify", "no-such-door", "--by", "Adeel", "--reason", "r").returncode == 64
env = dict(p.env)
env.pop("CLAUDECODE", None)
r = subprocess.run([MISSION, "doors", "revoke", "push-default-branch",
                    "--by", "Adeel", "--reason", "back to asking"], cwd=p.dir, env=env, capture_output=True, text=True)
assert r.returncode == 0, r.stderr
assert classify(LISTED).returncode == 3
ok("a wall or a question-only door is refused; a revoke closes a reclassified door")

print("\n%d checks passed" % PASS[0])
