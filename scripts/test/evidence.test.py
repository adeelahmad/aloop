#!/usr/bin/env python3
"""ROADMAP §4.11 (M8): every public claim links to a reproducible measured run.
`run-summary <run-dir>` turns a live run's transcript and gate log into summary.json plus a
ledger row; docs/evidence/README.md is the ledger of public runs, each with its numbers and the
command that reproduces it; every time/cost figure in the README demo table and on the site is
a ledger row, and the demo rows link to the ledger.
    python3 scripts/test/evidence.test.py
"""
import json
import os
import re
import subprocess
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
BIN = os.path.join(ROOT, "plugin", "bin")
PASS = [0]


def ok(name):
    PASS[0] += 1
    print("ok -", name)


# 1) run-summary on a fixture run dir
T = tempfile.mkdtemp(prefix="agentic-evidence-")
repo = os.path.join(T, "repo"); os.makedirs(os.path.join(repo, ".aladdin", "logs"))
subprocess.run(["git", "init", "-q", repo], check=True)
subprocess.run(["git", "-C", repo, "commit", "-q", "--allow-empty", "-m", "init"], check=True,
               env=dict(os.environ, GIT_AUTHOR_NAME="a", GIT_AUTHOR_EMAIL="a@a", GIT_COMMITTER_NAME="a", GIT_COMMITTER_EMAIL="a@a"))
plug = os.path.join(ROOT, "plugin")
lines = [
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "input": {"command": "cat %s/skills/aloop/SKILL.md" % plug}}]}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "input": {"command": "md-db validate --schema %s/schemas/x.kdl a.md" % plug}}]}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "input": {"command": "SCH=%s/schemas/x.kdl; md-db describe --schema \"$SCH\" --type stories" % plug}}]}},
    {"type": "assistant", "message": {"content": "plain string content is fine"}},
    {"type": "result", "total_cost_usd": 1.2345, "num_turns": 41, "duration_ms": 925000},
    {"type": "result", "total_cost_usd": 1.3, "num_turns": 44, "duration_ms": 940000},
]
with open(os.path.join(T, "out.jsonl"), "w") as f:
    f.write("not json\n")
    for l in lines:
        f.write(json.dumps(l) + "\n")
with open(os.path.join(repo, ".aladdin", "logs", "gates.jsonl"), "w") as f:
    for g, x in (("gate-red-verify", 2), ("gate-red-verify", 0), ("gate-green-verify", 0), ("gate-final", 0)):
        f.write(json.dumps({"gate": g, "exit": x, "selfcheck": False}) + "\n")
    f.write(json.dumps({"gate": "gate-scaffold-verify", "exit": 2, "selfcheck": True}) + "\n")   # caught by the worker's own self-check
r = subprocess.run([os.path.join(BIN, "run-summary"), T, "--intent", "wordfreq", "--checks", "13/13", "--secs", "924",
                    "--plugin", plug], capture_output=True, text=True)
assert r.returncode == 0, r.stderr
s = json.load(open(os.path.join(T, "summary.json")))
assert s["cost_usd"] == 1.3 and s["wakes"] == 2 and s["supervisor_turns"] == 44 and s["wall"] == "15m24s", s
assert s["plugin_reads"] == 1 and s["gate_verdicts"] == 5 and s["gate_blocks"] == 2 and s["checks"] == "13/13", s
assert s["intent"] == "wordfreq" and s["plugin_version"] == json.load(open(os.path.join(plug, ".claude-plugin", "plugin.json")))["version"]
assert re.fullmatch(r"[0-9a-f]{40}", s["run_repo_head"]) and s["reproduce"].startswith("make e2e-live YES=1 INTENT=wordfreq"), s
row = [l for l in r.stdout.splitlines() if l.startswith("| ")][-1]
assert "| wordfreq |" in row and "| 13/13 |" in row and "| 15m24s |" in row and "| $1.30 |" in row and "| 2 |" in row, row
assert s["selfcheck_blocks"] == 1 and "2 blocks, 1 caught by the worker's own self-check" in r.stdout, (s, r.stdout)
assert os.path.isfile(os.path.join(T, "summary.md"))
ok("run-summary: last result line wins, wakes counted, plugin reads exclude schema uses (also via a shell variable), gates tallied (self-check catches apart); summary.json/.md + ledger row")

# 2) the ledger: every row has a run, intent, version, checks, wall, cost and a reproduce command
ledger = open(os.path.join(ROOT, "docs", "evidence", "README.md")).read()
rows = [l for l in ledger.splitlines() if re.match(r"^\| (run )?\d+", l)]
assert len(rows) >= 6, rows
cells = [[c.strip() for c in l.strip("|").split("|")] for l in rows]
for c in cells:
    assert len(c) >= 8 and re.fullmatch(r"\d+/\d+", c[3]) and re.fullmatch(r"\d+m\d+s", c[4]) and re.fullmatch(r"\$\d+\.\d\d", c[5]), c
    assert c[2] and "make e2e-live YES=1" in c[-1], c
ok("docs/evidence/README.md: every run row carries intent, version, checks, wall, cost and its reproduce command")


# 2b) the mission runs (scripts/smoke/e2e-mission.sh) have their own table: every `| mission N |` row carries
#     its fixture, plugin, checks, sessions / grants, sprints, wall, cost, tick's own estimate, the outcome and
#     its reproduce command
MISSION_HEADER = "| run | fixture | plugin | checks | sessions / grants | sprints | wall | cost | tick estimate | outcome | reproduce |"


def mission_row_problems(line):
    """What is wrong with one `| mission N |` ledger row ([] when nothing is)."""
    c = [x.strip() for x in line.strip().strip("|").split("|")]
    if len(c) != 11:
        return ["%d cells, not 11 (%s)" % (len(c), MISSION_HEADER)]
    want = ((0, r"mission \d+", "run"), (3, r"\d+/\d+", "checks N/M"), (4, r"\d+ / \d+", "sessions / grants"),
            (5, r"\d+", "sprints"), (6, r"\d+m\d\ds", "wall"), (7, r"(?:≥)?\$\d+\.\d\d", "cost $x.xx"),
            (8, r"\$\d+\.\d\d|—", "tick estimate $x.xx or —"))
    bad = ["%s is %r" % (what, c[i]) for i, rx, what in want if not re.fullmatch(rx, c[i])]
    bad += ["%s is empty" % what for i, what in ((1, "fixture"), (2, "plugin"), (9, "outcome")) if not c[i]]
    if "make e2e-mission YES=1" not in c[10]:
        bad.append("no reproduce command (make e2e-mission YES=1)")
    return bad


assert "## Mission runs" in ledger, "the ledger has a table for the mission runs"
assert MISSION_HEADER in ledger[ledger.index("## Mission runs"):], "the mission table has its header"
intro = ledger[:ledger.index("| run | intent |")]
assert "scripts/smoke/e2e-live.sh" in intro and "scripts/smoke/e2e-mission.sh" in intro, "the header names both run scripts"
mrows = [l for l in ledger.splitlines() if re.match(r"^\| mission \d+ \|", l)]
for l in mrows:
    assert not mission_row_problems(l), (l, mission_row_problems(l))
mcells = [[c.strip() for c in l.strip("|").split("|")] for l in mrows]
placeholder = "| mission <N> | todo (claude) | v<version> | <N>/<M> | <s> / <g> | <n> | <wall> | <cost> | <tick> | <outcome> | `make e2e-mission YES=1` |"
assert len(mission_row_problems(placeholder)) == 7, mission_row_problems(placeholder)
assert mission_row_problems(placeholder.replace("make e2e-mission YES=1", "make e2e-live YES=1"))[-1].startswith("no reproduce")
assert mission_row_problems("| mission 1 | todo | v1 |") == ["3 cells, not 11 (%s)" % MISSION_HEADER]
MT = tempfile.mkdtemp(prefix="agentic-evidence-mission-")
os.makedirs(os.path.join(MT, "sessions"))
os.makedirs(os.path.join(MT, "repo"))
subprocess.run(["git", "init", "-q", os.path.join(MT, "repo")], check=True)
for n, (usd, turns) in enumerate(((1.10, 30), (0.25, 12)), 1):
    with open(os.path.join(MT, "sessions", "%02d.jsonl" % n), "w") as f:
        f.write(json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": usd,
                            "num_turns": turns}) + "\n")
r = subprocess.run(["python3", os.path.join(ROOT, "scripts", "smoke", "mission_summary.py"), MT, "--sessions",
                    os.path.join(MT, "sessions"), "--kind", "mission", "--checks", "18/19", "--secs", "4000", "--run", "1",
                    "--plugin", plug], capture_output=True, text=True)
assert r.returncode == 0, r.stderr
mrow = [l for l in r.stdout.splitlines() if l.startswith("| mission ")][-1]
assert not mission_row_problems(mrow) and "| $1.35 |" in mrow and "| 66m40s |" in mrow and "| 18/19 |" in mrow, mrow
ms = json.load(open(os.path.join(MT, "summary.json")))
assert ms["kind"] == "mission" and ms["cost_usd"] == 1.35 and ms["turns"] == 42, ms
ok("the mission table: every `| mission N |` row has checks, sessions / grants, sprints, wall, cost, tick estimate, "
   "outcome and `make e2e-mission YES=1`; a placeholder row is rejected; mission_summary.py writes a row that passes")

# 3) every public time/cost figure is a ledger row (a run or a mission run); the demo rows link to the ledger
readme = open(os.path.join(ROOT, "README.md")).read()
site = open(os.path.join(ROOT, "docs", "index.html")).read()
figures = set(re.findall(r"^\| .+ \| .+ \| (\d+m\d+s) \| (\$\d+\.\d+) \|$", readme, re.M)) | set(re.findall(r"(\d+m\d+s) for (\$\d+\.\d+)", site))
assert figures
pairs = {(c[4], c[5]) for c in cells} | {(c[6], c[7]) for c in mcells if c[7].startswith("$")}
for f in figures:
    assert f in pairs, "public figure %r is not a ledger row" % (f,)
demo = readme[readme.index("## Demo"):readme.index("## Test-driven agents")]
assert "docs/evidence/README.md" in demo and "docs/evidence/README.md" in site, "the demo and the site link to the ledger"
assert "docs/evidence/" in readme[readme.index("## Evidence"):readme.index("## Audit")]
ok("every public time/cost figure is a ledger row; README demo, README evidence and the site link to the ledger")

# 4) the live run writes its own evidence
e2e = open(os.path.join(ROOT, "scripts", "smoke", "e2e-live.sh")).read()
assert "run-summary" in e2e, "e2e-live.sh must call run-summary so each run leaves summary.json and a ledger row"
ok("e2e-live.sh ends with run-summary")
mission_sh = open(os.path.join(ROOT, "scripts", "smoke", "e2e-mission.sh")).read()
assert "mission_summary.py" in mission_sh, "e2e-mission.sh must write the mission run's summary.json and ledger row"
ok("e2e-mission.sh ends with the mission summary")

print("\n%d checks passed" % PASS[0])
