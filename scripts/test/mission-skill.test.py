#!/usr/bin/env python3
"""ROADMAP §4.12 (M9) M9.12: the /aloop:mission skill is the supervisor's playbook for the tick
loop. It must cover every action `mission tick` can hand out and name the roles tick
dispatches, keep tick's exit-code contract, name only mission commands that exist or that §4.12
assigns to a task, keep the hard rules (the supervisor never records an approval for the human,
never writes a mission artifact, never acts without a tick), ship a trigger/eval suite, and
stay small (it is re-read on every supervisor call).
    python3 scripts/test/mission-skill.test.py
"""
import json
import os
import re
import sys

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
PLUGIN = os.path.join(ROOT, "plugin")
SDIR = os.path.join(PLUGIN, "skills", "mission")
SKILL = open(os.path.join(SDIR, "SKILL.md")).read()
sys.path.insert(0, os.path.join(PLUGIN, "bin"))
sys.path.insert(0, os.path.join(PLUGIN, "mission"))
import tick as T  # noqa: E402

BUDGET = 16000
PASS = [0]


def ok(name):
    PASS[0] += 1
    print("ok -", name)


# 1) frontmatter: the skill is `mission` (/aloop:mission), and says when to use it
m = re.match(r"^---\n(.*?)\n---\n", SKILL, re.S)
assert m, "SKILL.md needs YAML frontmatter"
fm = m.group(1)
assert re.search(r"(?m)^name: mission$", fm), fm
desc = re.search(r"(?ms)^description: >-\n(.*?)(?=^\S)", fm + "\nx")
assert desc and "mission" in desc.group(1) and "/aloop:mission" in desc.group(1), fm
ok("frontmatter: name mission, a description naming /aloop:mission")

# 2) every action tick can hand out has a row in the action table, and every role it dispatches is named
rows = dict(re.findall(r"(?m)^\| `([A-Z_]+)` \|(.*)\|$", SKILL))
missing = sorted(set(T.ROLES) - set(rows))
assert not missing, "actions with no row in the skill's table: %s" % missing
assert not set(rows) - set(T.ROLES), "rows for actions tick never hands out: %s" % sorted(set(rows) - set(T.ROLES))
for action, (who, roles) in T.ROLES.items():
    for r in roles:
        assert "`%s`" % r in rows[action], "%s's row does not dispatch `%s`" % (action, r)
ok("the action table has exactly tick's %d actions, each naming the roles tick dispatches" % len(T.ROLES))

# 3) the loop keeps tick's exit-code contract
loop = SKILL[SKILL.index("## The loop"):]
loop = loop[:loop.index("\n## ", 4)]
for code, word in ((0, "act"), (2, "block"), (3, "human"), (4, "wait")):
    assert re.search(r"\b%d\b[^\n]*\b%s" % (code, word), loop, re.I), "the loop does not say exit %d means %s" % (code, word)
assert set(T.EXIT.values()) | {2} == {0, 2, 3, 4}
assert "aloop mission tick" in loop and "aloop mission due" in SKILL
ok("the loop: one `aloop mission tick` per action; exit 0 act · 2 blocked · 3 human · 4 wait")

# 4) every `aloop mission <cmd>` it names is a core command, an existing area, or one §4.12 assigns
roadmap = open(os.path.join(ROOT, "docs", "ROADMAP.md")).read()
sec = roadmap[roadmap.index("### 4.12"):]
planned = set()
for names, ext in re.findall(r"plugin/mission/(\{[^}]+\}|[a-z_]+)\.(\{[^}]+\}|py)", sec):
    if "py" in ext:
        planned |= set(names.strip("{}").split(","))
core = {"init", "check", "approve", "show", "status"}
existing = {f[:-3] for f in os.listdir(os.path.join(PLUGIN, "mission")) if f.endswith(".py") and not f.startswith("_")}
named = set(re.findall(r"aloop mission ([a-z][a-z_-]*)", SKILL))
unknown = sorted(named - core - existing - planned)
assert not unknown, "the skill names mission commands nobody builds: %s (planned: %s)" % (unknown, sorted(planned))
assert {"tick", "due", "cron"} <= existing and {"ethics", "goal", "baseline"} <= named
ok("every `aloop mission <cmd>` is core, an area, or assigned in §4.12 (%d named)" % len(named))

# 5) the hard rules
rules = SKILL[SKILL.index("## Hard rules"):]
for phrase in ("never write", "docs/agents/mission/", "explicit yes", "--by", "hard line", "never weaken",
               "never gates", "every action comes from", "STOP_BUDGET"):
    assert phrase.lower() in rules.lower(), "the hard rules do not say %r" % phrase
# M9.17: a grant is the human's typed line, so the supervisor relays the line the command asks for, verbatim
for phrase in ("they reply", "exact line", "typed", 'the command the ASK names after "then"', "goal approve",
               "grants nothing"):
    assert phrase in rules, "the hard rules do not say how a grant is made (M9.17): %r" % phrase
for src, forms in (("bin/_mission.py", ("they reply  ", "then run this again")),          # what the CLI prints
                   ("mission/goal.py", ("then aloop mission goal approve",))):
    text = open(os.path.join(PLUGIN, src)).read()
    assert all(f in text for f in forms), (src, forms)
ok("hard rules: no mission artifact by hand, approvals only in the human's own typed line, hard lines final, "
   "telemetry never gates")

# 6) setting a mission asks the human only for what requirement 1 gives them
setup = SKILL[SKILL.index("## Setting a mission"):]
setup = setup[:setup.index("\n## ", 4)]
for want in ("mission", "mode", "budget", "cadence"):
    assert want in setup, "setting a mission does not ask for the %s" % want
assert re.search(r"never (?:ask|write)[^\n]*(?:metric|journey)", setup), "setup must say the human never defines metrics/journeys"
assert "aloop mission cron" in setup and "--install" in setup
ok("setting a mission: statement, mode, budget, cadence; no metrics; the cron is offered")

# 7) the trigger and eval suites live with the skill
ev = json.load(open(os.path.join(SDIR, "evals", "evals.json")))
assert ev["skill_name"] == "mission" and len(ev["evals"]) >= 3
q = json.load(open(os.path.join(SDIR, "evals", "eval_queries.json")))
assert {x["should_trigger"] for x in q} == {True, False} and len(q) >= 8
ok("evals/: %d evals and %d trigger queries (both outcomes)" % (len(ev["evals"]), len(q)))

# 8) small: it is re-read on every supervisor call
n = len(SKILL.encode())
assert n <= BUDGET, "SKILL.md is %d bytes (budget %d)" % (n, BUDGET)
ok("SKILL.md is within its %d-byte budget (%d)" % (BUDGET, n))

print("mission-skill: %d checks passed" % PASS[0])
