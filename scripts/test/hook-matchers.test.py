#!/usr/bin/env python3
"""Every role gate must fire for the agent_type the host actually sends.

Regression (found by the live e2e run, confirmed with a probe plugin): installed as a
plugin, Claude Code names sub-agents `<plugin>:<agent>` (here `aloop:red-worker`), and a
matcher made only of letters, digits, `_`, `-`, `:` and `|` is an EXACT list. So the
`red-worker` matcher never matched `aloop:red-worker`: no SubagentStop gate ran, and only
the workers' voluntary self-checks stood between their work and the merge. Codex/OpenCode
send the bare name, so both forms must match.

    python3 scripts/test/hook-matchers.test.py
"""
import json
import os
import re

HOOKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugin", "hooks", "hooks.json")
ROLES = ["intake", "standards", "planner", "archivist", "red-worker", "scaffolder", "green-worker",
         "structural-reviewer", "final-gate"]
n = 0


def matches(matcher, name):
    """Claude Code's rule: '' or '*' matches all; a plain name list is exact; else a regex search."""
    if matcher in ("", "*"):
        return True
    if re.fullmatch(r"[A-Za-z0-9_|:-]+", matcher):
        return name in matcher.split("|")
    return re.search(matcher, name) is not None


def ok(msg):
    global n
    n += 1
    print("ok - " + msg)


hooks = json.load(open(HOOKS))["hooks"]
for event in ("SubagentStart", "SubagentStop"):
    for group in hooks[event]:
        m = group.get("matcher", "")
        if m in ("", "*"):
            continue
        named = [r for r in ROLES if matches(m, r) or matches(m, "aloop:" + r)]
        assert named, (event, m)
        for r in named:
            assert matches(m, r) and matches(m, "aloop:" + r), \
                "%s matcher %r must match both %r and %r" % (event, m, r, "aloop:" + r)
        ok("%s %s fires for bare and aloop: names (%s)" % (event, m[:40], ", ".join(named)))

stop_gates = {r: [h["command"] for g in hooks["SubagentStop"] if matches(g.get("matcher", ""), "aloop:" + r)
                  and g.get("matcher") not in ("", "*") for h in g["hooks"]] for r in ROLES}
for r in ROLES:
    assert any("gate-" in c for c in stop_gates[r]), (r, stop_gates[r])
ok("every role has a SubagentStop gate when dispatched as aloop:<role>")

print("\n%d checks passed" % n)
