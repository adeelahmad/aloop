#!/usr/bin/env python3
"""ROADMAP §4.5: canonical ids and referential integrity. The harness issues UUIDv7 ids
for every story and task alias, checks the structured references between artifacts,
freezes the map, never reuses a retired alias, and gate-stage2-complete runs it.
Events carry the canonical ids once the map is frozen.
    python3 scripts/test/ids-integrity.test.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-ids-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"), PATH="/usr/bin:/bin")  # no md-db: structural path only
ENV.pop("AGENTIC_HOST", None)
ENV.pop("CLAUDE_PLUGIN_ROOT", None)
PASS = [0]
IDS = os.path.join(BIN, "ids")
SP = os.path.join(T, "docs", "agents", "sprint1")
UUID7 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def run(*args):
    return subprocess.run(args, cwd=T, env=ENV, capture_output=True, text=True)


def write(rel, text):
    p = os.path.join(SP, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)


def story(slug, alias, tasks, validate=None, fm=None):
    write(slug + "/tasks.md", "---\ntype: tasks\nstory: %s\n---\n%s" % (fm or alias, tasks))
    write(slug + "/validate.md", "---\ntype: validate\nstory: %s\n---\n# Pre-flight\n%s# Final sign-off\n"
          % (alias, validate if validate is not None else "".join("## T%s\n" % n for n in re.findall(r"## T(\d+)", tasks))))
    write(slug + "/plan.md", "---\ntype: plan\nstory: %s\nscope: tests only\n---\n" % alias)


def frozen():
    return json.load(open(os.path.join(SP, "ids.json")))["ids"]


subprocess.run(["git", "init", "-q"], cwd=T, check=True)
write("stories.md", "---\ntype: stories\nsprint: 1\n---\n# User stories\n- S1-01 export\n- S1-02 import\n")
story("s1-01-export", "S1-01", "## T1 — csv writer\n## T2 — download button\n")
story("s1-02-import", "S1-02", "## T1 — csv reader\n")

# 1) freeze issues UUIDv7 ids for every story and task alias
r = run(IDS, "freeze", SP)
assert r.returncode == 0, r.stderr
m = frozen()
assert set(m) == {"S1-01", "S1-01-T1", "S1-01-T2", "S1-02", "S1-02-T1"}, m
assert all(UUID7.match(e["id"]) for e in m.values()) and len({e["id"] for e in m.values()}) == 5
assert m["S1-01"]["kind"] == "story" and m["S1-01-T2"]["kind"] == "task"
assert run(IDS, "resolve", "S1-01-T2", SP).stdout.strip() == m["S1-01-T2"]["id"]
ok("freeze: every story/task alias gets a unique UUIDv7; resolve maps alias → id")

# 2) the map is frozen: re-freezing keeps ids; a re-plan split retires the original, never reuses it
story("s1-01-export", "S1-01", "## T1 — csv writer\n## T3 — button markup\nsplit_from: T2\n## T4 — button wiring\nsplit_from: T2\n")
assert run(IDS, "freeze", SP).returncode == 0
m2 = frozen()
assert m2["S1-01-T1"]["id"] == m["S1-01-T1"]["id"] and m2["S1-01-T2"]["retired"] and "S1-01-T3" in m2
story("s1-01-export", "S1-01", "## T1 — csv writer\n## T2 — reuse\n## T3 — x\n## T4 — y\n")
r = run(IDS, "freeze", SP)
assert r.returncode == 2 and "retired" in r.stderr and frozen() == m2, r.stderr
ok("ids stay frozen; a split retires the original; reusing a retired number is blocked, nothing written")

# 3) broken structured references are blocked
story("s1-01-export", "S1-01", "## T1 — csv writer\n## T3 — x\n## T4 — y\n")
story("s1-02-import", "S1-02", "## T1 — csv reader\n", validate="## T1\n## T9\n")
r = run(IDS, "check", SP)
assert r.returncode == 2 and "T9" in r.stderr, r.stderr
story("s1-02-import", "S1-02", "## T1 — csv reader\n", fm="S1-07")
r = run(IDS, "check", SP)
assert r.returncode == 2 and "says story: S1-07" in r.stderr, r.stderr
story("s1-02-import", "S1-02", "## T1 — csv reader\n## T2 — y\nsplit_from: T8\n")
r = run(IDS, "check", SP)
assert r.returncode == 2 and "never existed" in r.stderr, r.stderr
story("s1-02-import", "S1-02", "## T1 — csv reader\n")
story("s1-03-orphan", "S1-03", "## T1 — z\n")
r = run(IDS, "check", SP)
assert r.returncode == 2 and "not listed in stories.md" in r.stderr and "no canonical id" in r.stderr, r.stderr
ok("validate.md task missing from tasks.md, wrong story frontmatter, dangling split_from, unlisted story → blocked")

# 4) gate-stage2-complete runs it at the planning → execution boundary
r = run(os.path.join(BIN, "gate-stage2-complete"), SP)
assert r.returncode == 2 and "S1-03" in r.stderr, r.stderr
write("stories.md", "---\ntype: stories\nsprint: 1\n---\n# User stories\n- S1-01\n- S1-02\n- S1-03\n")
r = run(os.path.join(BIN, "gate-stage2-complete"), SP)
assert r.returncode == 0, r.stderr
assert "S1-03-T1" in frozen() and not run(IDS, "check", SP).returncode
ok("gate-stage2-complete blocks a broken reference graph and freezes a sound one")

# 5) events carry the canonical ids once frozen
sys.path.insert(0, BIN)
import _events  # noqa: E402
_events.emit(T, "task.dispatched", task="S1-01-T3", role="red-worker")
ev = [json.loads(x) for x in open(os.path.join(T, ".agentic", "logs", "events.jsonl"))][-1]
assert ev["ids"]["task_uid"] == frozen()["S1-01-T3"]["id"] and ev["ids"]["story_uid"] == frozen()["S1-01"]["id"], ev["ids"]
ok("events emitted for a frozen task carry task_uid and story_uid")

print("\n%d checks passed" % PASS[0])
