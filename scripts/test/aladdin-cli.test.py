#!/usr/bin/env python3
"""ROADMAP §4.11 (first slice): the `aladdin` product entry point and the /aladdin skill.
`aladdin "<intent>"` records the intent (contract + raw text) and says how to start the run;
status / resume / report read the harness files; the semantic queries and every aloop tool
are reachable under the one name; installs link and copy the new names beside the old.
    python3 scripts/test/aladdin-cli.test.py
"""
import json
import os
import subprocess
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-aladdin-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"), AGENTIC_NO_AUTOINSTALL="1", PATH="/usr/bin:/bin",
           GIT_AUTHOR_NAME="A", GIT_AUTHOR_EMAIL="a@a", GIT_COMMITTER_NAME="A", GIT_COMMITTER_EMAIL="a@a")
for k in ("AGENTIC_HOST", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA"):
    ENV.pop(k, None)
PASS = [0]
A = os.path.join(BIN, "aladdin")
SP = "docs/agents/sprint1"


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def run(*args, **env):
    return subprocess.run(args, cwd=T, env=dict(ENV, **env), capture_output=True, text=True)


def write(rel, text):
    p = os.path.join(T, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)


subprocess.run(["git", "init", "-q", "-b", "main"], cwd=T, check=True)

# 1) the intent: contract issued by the harness, raw words kept, how to start printed; nothing launched
r = run(A, "add a csv export")
assert r.returncode == 0 and "intent recorded: " in r.stdout and "/aladdin add a csv export" in r.stdout, r
contract = r.stdout.split("intent recorded: ")[1].split(" (raw")[0]
doc = json.load(open(contract))
assert doc["intent_id"].startswith("int_") and doc["source"] == "prompt" and doc["provenance"]["raw_ref"].startswith("docs/agents/intents/raw-")
assert open(os.path.join(T, doc["provenance"]["raw_ref"])).read() == "add a csv export\n"
ev = [json.loads(x) for x in open(os.path.join(T, ".aladdin", "logs", "events.jsonl"))]
assert [e["event"] for e in ev] == ["intent.received"], ev
r = run(A, "run")
assert r.returncode == 0 and "intent: " + contract in r.stdout and "/aladdin add a csv export" in r.stdout, r
assert run(A, "run", "fix the footer").stdout.count("intent recorded") == 1
ok('aladdin "<intent>" records the contract and the raw words, says how to start; run repeats the pending one')

# 2) before any sprint: status / resume say so (exit 0); report has nothing (exit 1)
r = run(A, "status")
assert r.returncode == 0 and "no sprint yet" in r.stdout and "Project" in r.stdout, r
r = run(A, "resume")
assert r.returncode == 0 and "nothing to resume" in r.stdout, r
assert run(A, "report").returncode == 1
ok("status / resume / report before a sprint exists")

# 3) with a sprint: status sweeps the tasks and names tasks to re-gate; resume shows NEXT.md; report shows stats.md
write(SP + "/stories.md", "---\ntype: stories\nsprint: 1\n---\n- S1-01 export\n")
write(SP + "/s1-01-export/tasks.md", "---\ntype: tasks\nstory: S1-01\n---\n## T1 — writer\n## T2 — wire\n")
write(SP + "/s1-01-export/plan-ready.md", "---\ntype: plan-ready\nstory: S1-01\nfrom_red_at: 2026-10-18T00:00:00Z\n---\n"
      "## T1\n- [x] `tests/t.py::test_a` — a\n## T2\n- [ ] `tests/t.py::test_b` — b\n")
r = run(A, "status")
assert r.returncode == 0 and "sprint: " in r.stdout and "RE-RUN S1-01-T1" in r.stdout, r
assert any(ln.split()[0] in ("SPAWN", "WAIT", "ACCEPT", "RETRY", "DONE", "REPLAN", "ESCALATE", "ROLLOVER") for ln in r.stdout.splitlines() if "S1-01-T2" in ln), r.stdout
r = run(A, "resume")
assert "mid-flight" in r.stdout and "RE-RUN S1-01-T1" in r.stdout and "fresh claude session" in r.stdout, r
write("docs/agents/NEXT.md", "# NEXT\nResume S1-01-T2.\n")
assert "Resume S1-01-T2." in run(A, "resume").stdout
r = run(A, "report")
assert r.returncode == 0 and "has not closed yet" in r.stdout and "sprint: " in r.stdout, r
write(SP + "/stats.md", "# Sprint 1 stats\n| Context peak | 1 |\n")
assert run(A, "report").stdout.startswith("# Sprint 1 stats")
assert "fresh opencode session" in run(A, "resume", AGENTIC_HOST="opencode").stdout
ok("status sweeps the sprint and applies the resume rule; resume shows the handoff; report shows stats.md")

# 4) the semantic queries and every aloop tool answer under the one name
write(SP + "/s1-01-export/targets.json", json.dumps({"targets": [
    {"change_ref": "C1", "task": "T1", "operation": "create", "path": "src/w.py", "planned_symbol": "w", "reason": "r"}]}))
r = run(A, "target", "T1", os.path.join(T, SP, "s1-01-export"))
assert r.returncode == 0 and "src/w.py w (planned, shadow)" in r.stdout, r
assert run(A, "--list").stdout == run(os.path.join(BIN, "aloop"), "--list").stdout
assert "aladdin" not in run(A, "--list").stdout.split() and "aloop-init.orig" not in run(A, "--list").stdout
assert run(A, "stats", "show").returncode == 0 and run(A, "init", "--show").returncode == 0
assert run(A, "--bogus").returncode == 64 and run(A, "a/b").returncode == 64
ok("target/context/... and every aloop tool answer under aladdin; aliases hidden; bad names refused")

# 5) the /aladdin skill exists beside /aloop and defers to it; installs carry the new names
sk = open(os.path.join(ROOT, "skills", "aladdin", "SKILL.md")).read()
assert sk.startswith("---\nname: aladdin\n") and "skills/aloop/SKILL.md" in sk
r = run(os.path.join(BIN, "aloop"), "install", "opencode")
assert r.returncode == 0, r
assert os.path.isfile(os.path.join(T, ".opencode", "skills", "aladdin", "SKILL.md"))
lb = os.path.join(ENV["HOME"], ".local", "bin")
assert os.path.realpath(os.path.join(lb, "aladdin")) == os.path.realpath(A) and os.path.islink(os.path.join(lb, "aloop"))
ok("/aladdin skill beside /aloop; opencode install copies it; ~/.local/bin/aladdin links to the plugin")

print("\n%d checks passed" % PASS[0])
