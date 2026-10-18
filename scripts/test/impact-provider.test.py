#!/usr/bin/env python3
"""ROADMAP §4.8: the impact provider seam. Results carry source refs and land in planning
provenance; a result without a source is dropped; an unavailable provider is surfaced and
planning continues (exit 0). Aladdin builds no index.
    python3 scripts/test/impact-provider.test.py
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-impact-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"))
for k in ("AGENTIC_HOST", "AGENTIC_IMPACT_PROVIDER"):
    ENV.pop(k, None)
PASS = [0]
IM = os.path.join(BIN, "impact")


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def run(*args, **env):
    return subprocess.run(args, cwd=T, env=dict(ENV, **env), capture_output=True, text=True)


subprocess.run(["git", "init", "-q"], cwd=T, check=True)
prov = os.path.join(T, "provider.py")
open(prov, "w").write("""import json, sys
kind, subject = sys.argv[1], sys.argv[2]
if subject == "boom":
    sys.exit(3)
if subject == "junk":
    print("not json"); sys.exit(0)
print(json.dumps({"schema": "aladdin.impact/1", "results": [
    {"summary": "billing-service reads %s" % subject, "source_ref": "github:org/billing@abc123:src/report.py"},
    {"summary": "a rumour with no source"}]}))
""")
CMD = "%s %s" % (sys.executable, prov)

# 1) no provider: surfaced, planning continues
r = run(IM, "query", "consumers", "report.render")
assert r.returncode == 0 and "provider unavailable: none configured" in r.stderr and not r.stdout, r
assert "none" in run(IM, "status").stdout
ok("no provider configured → WARN, exit 0, nothing invented")

# 2) a provider answers: results with source refs, unsourced dropped, provenance recorded
os.makedirs(os.path.join(T, "docs", "agents"))
open(os.path.join(T, "docs", "agents", "defaults.md"), "w").write("AGENTIC_IMPACT_PROVIDER=%s\n" % CMD)
r = run(IM, "query", "consumers", "report.render", "--task", "S1-01-T1")
assert r.returncode == 0, r.stderr
assert r.stdout.strip() == "- billing-service reads report.render [github:org/billing@abc123:src/report.py]", r.stdout
assert "dropped 1 result(s) without a source_ref" in r.stderr
saved = [f for f in os.listdir(os.path.join(T, "docs", "agents", "impact"))]
doc = json.load(open(os.path.join(T, "docs", "agents", "impact", saved[0])))
assert doc["dropped_without_source"] == 1 and doc["results"][0]["source_ref"].startswith("github:"), doc
ev = [json.loads(x) for x in open(os.path.join(T, ".agentic", "logs", "events.jsonl"))][-1]
assert ev["event"] == "artifact.created" and ev["data"]["kind"] == "impact.consumers" and ev["ids"]["task"] == "S1-01-T1"
assert ev["refs"]["impact"].endswith(saved[0])
ok("answers carry source refs; unsourced claims dropped; saved + recorded as planning provenance")

# 3) a failing, malformed or missing provider is surfaced and never fails planning
for subject, why in (("boom", "exit 3"), ("junk", "not JSON")):
    r = run(IM, "query", "rationale", subject)
    assert r.returncode == 0 and why in r.stderr and not r.stdout, (subject, r.stderr)
r = run(IM, "query", "standards", "x", AGENTIC_IMPACT_PROVIDER="/nonexistent/provider")
assert r.returncode == 0 and "cannot run" in r.stderr, r.stderr
assert run(IM, "query", "gossip", "x").returncode == 64
ok("provider failure, bad JSON or a missing binary → WARN, exit 0; an unknown kind is a usage error")

print("\n%d checks passed" % PASS[0])
