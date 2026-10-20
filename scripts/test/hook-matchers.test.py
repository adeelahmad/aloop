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
# every role the plugin ships (Aladdin's nine and the mission layer's six), from plugin/agents/
ROLES = sorted(f[:-3] for f in os.listdir(os.path.join(os.path.dirname(HOOKS), "..", "agents")) if f.endswith(".md"))
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

# the door rule on every host and role (M9.31): the door gate guards every tool call, and every dispatched
# role starts with the core principles (the main session gets them from session-start)
DOORS = '"${CLAUDE_PLUGIN_ROOT}/bin/mission" doors hook'
PRINCIPLES = '"${CLAUDE_PLUGIN_ROOT}/bin/mission" principles hook'
pre_all = [h for g in hooks["PreToolUse"] if g.get("matcher") == "*" for h in g["hooks"]]
cmds = [h["command"] for h in pre_all]
assert DOORS in cmds, cmds
# listed last, after budget hook pre: the order OpenCode and dsh run it in, since it records door.crossed (uses
# up a once-grant) and must only do so once every other gate admitted the call
assert cmds.index(DOORS) == len(cmds) - 1 and cmds.index('"${CLAUDE_PLUGIN_ROOT}/bin/budget" hook pre') < cmds.index(DOORS), cmds
assert pre_all[cmds.index(DOORS)].get("timeout") == 15, pre_all
ok("PreToolUse *: the door gate (mission doors hook) guards every tool call, listed last")

start_all = [h for g in hooks["SubagentStart"] if g.get("matcher") == "*" for h in g["hooks"]]
cmds = [h["command"] for h in start_all]
assert PRINCIPLES in cmds and cmds.index(PRINCIPLES) == 2, cmds   # the third command of the SubagentStart * group
assert start_all[2].get("timeout") == 10, start_all[2]
for r in ROLES:
    got = [h["command"] for g in hooks["SubagentStart"] if matches(g.get("matcher", ""), "aloop:" + r) for h in g["hooks"]]
    assert got.count(PRINCIPLES) == 1, (r, got)              # once: Claude Code joins every hook's context
ok("SubagentStart *: every role (%d) gets the core principles once (mission principles hook)" % len(ROLES))

# parity: the hosts that do not read hooks.json run the door gate from their plugin (expected red until
# M9.31's OpenCode and dsh parts land)
DOOR_CALL = re.compile(r'"mission"\s*,\s*\[\s*"doors"\s*,\s*"hook"\s*\]')
PLUGINS = os.path.join(os.path.dirname(HOOKS), "..", "hosts")
for host, f in (("opencode", "aloop.js"), ("dsh", "aloop.mjs")):
    src = open(os.path.join(PLUGINS, host, f)).read()
    assert DOOR_CALL.search(src), "%s/%s runs no [\"mission\", [\"doors\", \"hook\"]] check" % (host, f)
ok("OpenCode and dsh plugins run the door gate too (mission doors hook)")

print("\n%d checks passed" % n)
