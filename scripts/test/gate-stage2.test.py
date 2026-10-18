#!/usr/bin/env python3
"""Regression: gate-stage2-complete must gate the sprint it is given, and must not
validate the framework docs (init/output/intake/standards/retro) against the planning
schema. Before the fix it picked `find … | sort | tail -1` (sprint2 while sprint1 was
being gated) and md-db rejected every init.md/output.md/intake.md/standards.md/retro.md
with F002 "unknown document type".   python3 scripts/test/gate-stage2.test.py

Needs a real md-db: $MD_DB, else plugin/tools/md-db/target/release/md-db (make test builds it).
"""
import os
import subprocess
import sys
import tempfile

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
GATE = os.path.join(REPO, "plugin", "bin", "gate-stage2-complete")
SELFCHECK = os.path.join(REPO, "plugin", "bin", "selfcheck")
MD_DB = os.environ.get("MD_DB") or os.path.join(REPO, "plugin", "tools", "md-db", "target", "release", "md-db")
if not os.access(MD_DB, os.X_OK):
    sys.exit("FAIL: md-db binary not found at %s (run `make build` or set MD_DB)" % MD_DB)

T = tempfile.mkdtemp(prefix="agentic-stage2-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"), CLAUDE_PLUGIN_DATA=os.path.join(T, "data"),
           PATH=os.path.dirname(MD_DB) + os.pathsep + os.environ["PATH"], AGENTIC_NO_AUTOINSTALL="1")
for k in ("SPRINT_DIR", "STORY_DIR", "AGENT_ROLE", "CLAUDE_PLUGIN_ROOT"):
    ENV.pop(k, None)
PASS = [0]


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def write(rel, text):
    p = os.path.join(T, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(text)


def run(*args, env=None):
    return subprocess.run(args, cwd=T, env=env or ENV, capture_output=True, text=True)


def fm(**kv):
    return "---\n" + "".join("%s: %s\n" % (k, v) for k, v in kv.items()) + "---\n"


def sprint(n, tbw=False):
    s, sid = "docs/agents/sprint%d" % n, "S%d-01" % n
    d = "%s/s%d-01-thing" % (s, n)
    write(s + "/stories.md", fm(type="stories", sprint=n) + "".join(
        "\n## %s\nx\n" % h for h in ("Sprint goal", "Sprint demo", "Definition of Done", "Out of scope",
                                     "User stories", "Story dependency graph")) + "\n%s: thing\n" % sid)
    write(s + "/plan.md", fm(type="sprint-plan", sprint=n, stage='"2"') + "".join(
        "\n## %s\nx\n" % h for h in ("Sprint goal", "Critical path", "Cross-cutting gates")))
    write(d + "/tasks.md", fm(type="tasks", story=sid) + "\n## T1 — do it\n%s\n" % ("TBW" if tbw else "x"))
    write(d + "/validate.md", fm(type="validate", story=sid) + "\n## Pre-flight\nx\n\n## Final sign-off\nx\n")
    write(d + "/plan.md", fm(type="plan", story=sid, scope='"tests only"') + "\nx\n")
    write(d + "/plan-ready.md", fm(type="plan-ready", story=sid, from_red_at='"2026-10-18T10:00:00Z"') + "\n- [x] ok\n")
    # Framework docs that live in every sprint dir — not planning artifacts.
    write(s + "/intake.md", fm(type="intake", sprint=n) + "\n# intake\n")
    write(s + "/standards.md", fm(type="standards", sprint=n) + "\n# standards\n")
    write(s + "/retro-2026-10-19.md", fm(type="retro", sprint=n) + "\n# retro\n")
    write(d + "/init.md", fm(type="init", story=sid) + "\n# init\n")
    write(d + "/output.md", fm(type="output", story=sid) + "\n# output\n")
    write(s + "/replan-1/init.md", fm(type="init", story=sid) + "\n# replan\n")
    return s


subprocess.run(["git", "init", "-q"], cwd=T, check=True)
s1 = sprint(1)
s2 = sprint(2, tbw=True)          # the later sprint is mid-planning; it must not be what gets gated

r = run(GATE, s1)
assert r.returncode == 0, "explicit sprint1 must pass, got rc=%d\n%s" % (r.returncode, r.stderr + r.stdout)
assert "sprint2" not in r.stderr, r.stderr
ok("explicit sprint dir: gates only sprint1; framework docs are not schema-validated")

r = run(GATE, env=dict(ENV, SPRINT_DIR=s1))
assert r.returncode == 0, "SPRINT_DIR=sprint1 must pass, got rc=%d\n%s" % (r.returncode, r.stderr + r.stdout)
ok("SPRINT_DIR selects the sprint")

r = run(SELFCHECK, "planner", s1)
assert r.returncode == 0, "selfcheck planner <sprint1> must pass, got rc=%d\n%s" % (r.returncode, r.stderr + r.stdout)
ok("selfcheck forwards the sprint dir to the gate")

r = run(GATE, s2)
assert r.returncode == 2 and "TBW" in r.stderr, r.stderr
ok("explicit sprint2 still blocks on its own TBW")

# No dir passed (SubagentStop hook): the sprint whose planning artifacts the planner
# touched last — never the lexicographically last sprint dir.
os.utime(os.path.join(T, s2, "stories.md"), (1_700_000_000, 1_700_000_000))
os.utime(os.path.join(T, s1, "stories.md"), None)
r = run(GATE)
assert r.returncode == 0 and "sprint1" in r.stderr and "sprint2" not in r.stderr, r.stderr + r.stdout
ok("no dir passed: picks the most recently planned sprint, not `sort | tail -1`")

# Real planning artifacts are still fully validated: a broken sprint-plan / tasks fails.
write(s1 + "/plan.md", fm(type="sprint-plan", sprint=1, stage='"2"') + "\n## Sprint goal\nx\n")
r = run(GATE, s1)
assert r.returncode == 2 and "md-db rejected" in r.stderr, r.stderr + r.stdout
write(s1 + "/plan.md", fm(type="sprint-plan", sprint=1, stage='"2"') + "".join(
    "\n## %s\nx\n" % h for h in ("Sprint goal", "Critical path", "Cross-cutting gates")))
write(s1 + "/s1-01-thing/tasks.md", fm(type="tasks", story="bogus") + "\n## T1 — x\nx\n")
r = run(GATE, s1)
assert r.returncode == 2 and "md-db rejected" in r.stderr, r.stderr + r.stdout
write(s1 + "/s1-01-thing/tasks.md", fm(type="tasks", story="S1-01") + "\n## T1 — x\nx\n")
ok("planning artifacts keep full schema validation")

# Only the listed framework types are excluded: an unknown type in a story dir still fails.
write(s1 + "/s1-01-thing/notes.md", fm(type="scratch") + "\nx\n")
r = run(GATE, s1)
assert r.returncode == 2 and "md-db rejected" in r.stderr, r.stderr + r.stdout
os.remove(os.path.join(T, s1, "s1-01-thing", "notes.md"))
ok("unlisted doc types are still rejected (explicit list, not a broad skip)")

r = run(GATE, s1)
assert r.returncode == 0, r.stderr + r.stdout
ok("sprint1 passes again once fixed")

print("1..%d  all passed" % PASS[0])
