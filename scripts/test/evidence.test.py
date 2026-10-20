#!/usr/bin/env python3
"""ROADMAP §4.11 (M8): every public claim links to a reproducible measured run.
`run-summary <run-dir>` turns a live run's transcript and gate log into summary.json plus a
ledger row; docs/evidence/README.md is the ledger of public runs, each with its numbers and the
command that reproduces it, its cost exactly as run-summary prints a measured one for its host (the
row run-summary prints passes); every time/cost figure in the README demo table and on the site is
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

# 2) the ledger: every row has a run, intent, version, checks, wall, cost and a reproduce command.
#    The cost cell is a MEASURED cost exactly as run-summary prints one for the row's host (the HOST= of its
#    reproduce command; none is Claude Code): Claude's result line to the cent, dsh's priced usage to four
#    places (<$0.0001 below). Codex and OpenCode measure none (their row reads —), so they have no format:
#    an unmeasured cost, as — or shown as a price, is never a ledger figure.
COST = {"claude": r"\$\d+\.\d\d", "dsh": r"\$\d+\.\d{4}|<\$0\.0001"}


def cells_of(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def ledger_row(c):
    if len(c) < 8 or not c[2] or "make e2e-live YES=1" not in c[-1]:
        return False
    host = (re.search(r"\bHOST=(\w+)", c[-1]) or [None, "claude"])[1]
    return bool(re.fullmatch(r"\d+/\d+", c[3]) and re.fullmatch(r"\d+m\d+s", c[4]) and host in COST and re.fullmatch(COST[host], c[5]))


ledger = open(os.path.join(ROOT, "docs", "evidence", "README.md")).read()
rows = [l for l in ledger.splitlines() if re.match(r"^\| (run )?\d+", l)]
assert len(rows) >= 6, rows
cells = [cells_of(l) for l in rows]
for c in cells:
    assert ledger_row(c), c
ok("docs/evidence/README.md: every run row carries intent, version, checks, wall, a measured cost and its reproduce command")

# 2b) the row run-summary prints is the row the ledger takes, on each host that measures a cost (pasted with
#     its first cell renamed `run N`); a host that measures none, or a cell in another host's format, is refused
def as_run(line):
    return ["run 99"] + cells_of(line)[1:]


assert ledger_row(as_run(row)), row
dsh_rows = {}
for name, usage in (("dsh-cents", (120000, 900000, 4000)), ("dsh-micro", (1, 0, 0))):
    rd = os.path.join(T, name); hd = os.path.join(rd, "repo", ".aladdin", "host", "dsh")
    os.makedirs(hd)
    subprocess.run(["git", "init", "-q", os.path.join(rd, "repo")], check=True)
    open(os.path.join(rd, "out.jsonl"), "w").close()
    with open(os.path.join(hd, "root.jsonl"), "w") as f:      # one deepseek-flash call, Sat 12:00 UTC (off-peak)
        f.write(json.dumps({"timestamp": "2026-10-24T12:00:00.000Z", "message": {"id": "a1", "model": "deepseek-flash", "usage": {
            "input_tokens": usage[0], "cache_read_input_tokens": usage[1], "output_tokens": usage[2], "cache_creation_input_tokens": 0}}}) + "\n")
    r = subprocess.run([os.path.join(BIN, "run-summary"), rd, "--intent", "semver", "--host", "dsh", "--checks", "12/12", "--secs", "550",
                        "--plugin", plug], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    dsh_rows[name] = as_run([l for l in r.stdout.splitlines() if l.startswith("| ")][-1])
assert [c[5] for c in dsh_rows.values()] == ["$0.0231", "<$0.0001"], dsh_rows
for c in dsh_rows.values():
    assert ledger_row(c), c
for host, cost in (("dsh", "$0.02"), ("dsh", "—"), ("claude", "$0.0231"), ("claude", "<$0.0001"), ("claude", "—"),
                   ("codex", "—"), ("codex", "$0.00"), ("opencode", "$0.65")):
    c = list(dsh_rows["dsh-cents"])
    c[5], c[-1] = cost, "`make e2e-live YES=1 INTENT=semver%s`" % ("" if host == "claude" else " HOST=" + host)
    assert not ledger_row(c), c
ok("the evidence row run-summary prints passes the ledger check on Claude Code and dsh (four places, <$0.0001); "
   "an unmeasured cost (— or a price on Codex/OpenCode) or another host's format does not")

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
