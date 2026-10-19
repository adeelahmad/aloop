#!/usr/bin/env python3
"""The supervisor's playbook draws the layout the gates enforce, and stays small.
1) SKILL.md's data-plane tree must put `sprintN/` under `docs/agents/`, never under the
   run-data dir: the tree drew it below `<project>/.aladdin/`, and live run 24's haiku
   supervisor put the whole sprint in `.aladdin/sprint1/` (the gates now refuse it, #41).
2) SKILL.md is re-read on every supervisor call; #43 moved rare-path sections to
   reference.md (58k -> 49k chars). A byte budget keeps it from growing back unnoticed.
3) Part B says how one task's RED, SCAFFOLD and GREEN share a worktree on Claude Code: live
   runs 31-33 each spent supervisor turns rediscovering it (task-worktree --help, inspect, add).
    python3 scripts/test/skill-layout.test.py
"""
import os
import re

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
SKILL = open(os.path.join(ROOT, "plugin", "skills", "aloop", "SKILL.md")).read()
BUDGET = 50000
PASS = [0]


def ok(name):
    PASS[0] += 1
    print("ok -", name)


sec = SKILL[SKILL.index("## The data plane"):]
tree = sec[sec.index("```") + 3:]
tree = tree[:tree.index("```")]
lines = tree.splitlines()
parent = None
for l in lines:
    if not l.strip() or l.lstrip().startswith("#"):
        continue
    ind = len(l) - len(l.lstrip())
    if ind == 0:
        parent = l.split()[0]
    elif ind == 2 and l.split()[0].startswith("sprintN"):
        assert parent == "docs/agents/", "sprintN/ is drawn under %r, not docs/agents/" % parent
ok("the data-plane tree draws sprintN/ under docs/agents/ only")
run_data = [l for l in lines if l.startswith("<project>/.aladdin/")]
assert run_data and re.search(r"never a sprint|run data only", run_data[0]), run_data
ok("the run-data dir is drawn as run data only, never a sprint")

part_b = SKILL[SKILL.index("# PART B"):]
assert re.search(r"aloop task-worktree add <TASK_ID> <RED's", part_b), "Part B must name the RED -> SCAFFOLD/GREEN worktree handoff"
assert re.search(r"no `isolation`", part_b) and "aloop bind <path>" in part_b, "SCAFFOLD/GREEN must bind to the task worktree"
ok("Part B gives the chain handoff: task-worktree add on RED's branch, later stages bind to it")

n = len(SKILL.encode())
assert n <= BUDGET, "SKILL.md is %d bytes (budget %d): move rare-path material to reference.md" % (n, BUDGET)
ok("SKILL.md is within its %d-byte budget (%d)" % (BUDGET, n))
print("\n%d checks passed" % PASS[0])
