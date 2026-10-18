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
r = subprocess.run([os.path.join(BIN, "run-summary"), T, "--intent", "wordfreq", "--checks", "13/13", "--secs", "924",
                    "--plugin", plug], capture_output=True, text=True)
assert r.returncode == 0, r.stderr
s = json.load(open(os.path.join(T, "summary.json")))
assert s["cost_usd"] == 1.3 and s["wakes"] == 2 and s["supervisor_turns"] == 44 and s["wall"] == "15m24s", s
assert s["plugin_reads"] == 1 and s["gate_verdicts"] == 4 and s["gate_blocks"] == 1 and s["checks"] == "13/13", s
assert s["intent"] == "wordfreq" and s["plugin_version"] == json.load(open(os.path.join(plug, ".claude-plugin", "plugin.json")))["version"]
assert re.fullmatch(r"[0-9a-f]{40}", s["run_repo_head"]) and s["reproduce"].startswith("make e2e-live YES=1 INTENT=wordfreq"), s
row = [l for l in r.stdout.splitlines() if l.startswith("| ")][-1]
assert "| wordfreq |" in row and "| 13/13 |" in row and "| 15m24s |" in row and "| $1.30 |" in row and "| 2 |" in row, row
assert os.path.isfile(os.path.join(T, "summary.md"))
ok("run-summary: last result line wins, wakes counted, plugin reads exclude --schema, gates tallied; summary.json/.md + ledger row")

# 2) the ledger: every row has a run, intent, version, checks, wall, cost and a reproduce command
ledger = open(os.path.join(ROOT, "docs", "evidence", "README.md")).read()
rows = [l for l in ledger.splitlines() if re.match(r"^\| (run )?\d+", l)]
assert len(rows) >= 6, rows
cells = [[c.strip() for c in l.strip("|").split("|")] for l in rows]
for c in cells:
    assert len(c) >= 8 and re.fullmatch(r"\d+/\d+", c[3]) and re.fullmatch(r"\d+m\d+s", c[4]) and re.fullmatch(r"\$\d+\.\d\d", c[5]), c
    assert c[2] and "make e2e-live YES=1" in c[-1], c
ok("docs/evidence/README.md: every run row carries intent, version, checks, wall, cost and its reproduce command")

# 3) every public time/cost figure is a ledger row; the demo rows link to the ledger
readme = open(os.path.join(ROOT, "README.md")).read()
site = open(os.path.join(ROOT, "docs", "index.html")).read()
figures = set(re.findall(r"^\| .+ \| .+ \| (\d+m\d+s) \| (\$\d+\.\d+) \|$", readme, re.M)) | set(re.findall(r"(\d+m\d+s) for (\$\d+\.\d+)", site))
assert figures
pairs = {(c[4], c[5]) for c in cells}
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

print("\n%d checks passed" % PASS[0])
