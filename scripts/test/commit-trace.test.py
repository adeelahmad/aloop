#!/usr/bin/env python3
"""ROADMAP §4.7 / milestone 7: a commit traces back to its intent without transcripts.
intent.received → intent.normalized → plan.created (gate-stage2-complete, caused by the
intent) → plan.approved (events authority) → worktree.merged → commit.created, and
`events trace SHA` walks that chain from the event stream alone.
    python3 scripts/test/commit-trace.test.py
"""
import json
import os
import subprocess
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-trace-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"), PATH="/usr/bin:/bin",
           GIT_AUTHOR_NAME="A", GIT_AUTHOR_EMAIL="a@a", GIT_COMMITTER_NAME="A", GIT_COMMITTER_EMAIL="a@a")
for k in ("AGENTIC_HOST", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA"):
    ENV.pop(k, None)
PASS = [0]
SP = os.path.join(T, "docs", "agents", "sprint1")


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def sh(*args, cwd=T, check=True):
    r = subprocess.run(args, cwd=cwd, env=ENV, capture_output=True, text=True)
    assert not check or r.returncode == 0, (args, r.stderr)
    return r


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(text)


def events():
    return [json.loads(x) for x in open(os.path.join(T, ".agentic", "logs", "events.jsonl"))]


sh("git", "init", "-q", "-b", "main")
sh("git", "commit", "-q", "--allow-empty", "-m", "init")

# intent → normalized
path = sh(os.path.join(BIN, "intent"), "new", "--source", "prompt", "--raw", "add CSV export").stdout.strip()
doc = json.load(open(path))
doc.update(actor="analyst", want="export CSV", why="finance", success_scenarios=["csv downloads"],
           failure_scenarios=["empty table → header-only csv"], done_looks_like="Export button works")
json.dump(doc, open(path, "w"))
sh(os.path.join(BIN, "intent"), "check", path)

# plan: stage-2 gate freezes ids → plan.created caused by the intent
write(os.path.join(SP, "stories.md"), "---\ntype: stories\nsprint: 1\n---\n- S1-01 export\n")
for f, extra in (("tasks.md", "## T1 — writer\n"), ("validate.md", "# Pre-flight\n## T1\n# Final sign-off\n"), ("plan.md", "")):
    write(os.path.join(SP, "s1-01-export", f), "---\ntype: %s\nstory: S1-01\n---\n%s" % (f[:-3], extra))
sh(os.path.join(BIN, "gate-stage2-complete"), SP)
plan = [e for e in events() if e["event"] == "plan.created"]
intent_ev = [e for e in events() if e["event"] == "intent.normalized"][0]
assert len(plan) == 1 and plan[0]["caused_by"] == intent_ev["event_id"] and plan[0]["data"]["sprint"] == "S1", plan
sh(os.path.join(BIN, "gate-stage2-complete"), SP)
assert len([e for e in events() if e["event"] in ("plan.created", "plan.revised")]) == 1
ok("gate-stage2-complete emits plan.created once, caused by intent.normalized (unchanged re-run is silent)")

# approval → plan.approved for that plan
sh(os.path.join(BIN, "events"), "authority", "--initiated-by", "adeel", "--mode", "reviewed", "--scope", "sprint 1 as approved")
appr = [e for e in events() if e["event"] == "plan.approved"]
assert len(appr) == 1 and appr[0]["caused_by"] == plan[0]["event_id"] and appr[0]["authority"]["initiated_by"] == "adeel"
ok("events authority emits plan.approved linked to the latest plan")

# a worker's commit merged back → commit.created per accepted commit
wt = sh(os.path.join(BIN, "task-worktree"), "add", "S1-01-T1").stdout.strip()
write(os.path.join(wt, "csv.py"), "def write_csv():\n    return ''\n")
sh("git", "add", "csv.py", cwd=wt)
sh("git", "commit", "-q", "-m", "feat: csv writer (S1-01-T1)", cwd=wt)
sha = sh("git", "rev-parse", "HEAD", cwd=wt).stdout.strip()
sh("git", "merge", "-q", "--no-ff", "-m", "merge S1-01-T1", "agentic/S1-01-T1")
sh(os.path.join(BIN, "events"), "emit", "gate.passed", "--task", "S1-01-T1", "--data", "gate=gate-green-verify")
sh(os.path.join(BIN, "task-worktree"), "remove", "S1-01-T1")
cc = [e for e in events() if e["event"] == "commit.created"]
merged = [e for e in events() if e["event"] == "worktree.merged"][0]
assert len(cc) == 1 and cc[0]["data"]["sha"] == sha and cc[0]["parent_event_id"] == merged["event_id"], cc
ok("task-worktree remove emits commit.created for each merged commit, parented to worktree.merged")

# events trace: commit → task → gates → plan → intent → approver
out = sh(os.path.join(BIN, "events"), "trace", sha[:10]).stdout
for want in ("commit   %s feat: csv writer" % sha[:12], "task     S1-01-T1", "gate.passed", "worktree.merged",
             "plan     plan.created S1", "intent   %s" % doc["intent_id"], "approved adeel (reviewed)"):
    assert want in out, (want, out)
assert sh(os.path.join(BIN, "events"), "trace", "deadbeef", check=False).returncode == 1
ok("events trace walks commit → task → gates → plan → intent → approver with no transcript")

# commit message from the task title + recorded decisions + the intent (no model prose)
sh(os.path.join(BIN, "events"), "decide", "implementation", "--task", "S1-01-T1",
   "--reason", "stdlib csv keeps the no-new-deps constraint", "--evidence", "intake.md#constraints")
msg = sh(os.path.join(BIN, "events"), "commit-msg", "S1-01-T1").stdout
assert msg.splitlines()[0] == "S1-01-T1: writer", msg
assert "- implementation: stdlib csv keeps the no-new-deps constraint" in msg and "Intent: %s" % doc["intent_id"] in msg, msg
assert sh(os.path.join(BIN, "events"), "commit-msg", "S1-01-T9", check=False).returncode == 1
ok("events commit-msg builds the message from the task title, decisions and intent")

# graph export: rebuildable from the stream; commit → event → task edges, intent nodes
g = json.loads(sh(os.path.join(BIN, "events"), "graph").stdout)
ids = {n["id"] for n in g["nodes"]}
assert "commit:" + sha in ids and "task:S1-01-T1" in ids and "intent:" + doc["intent_id"] in ids, ids
assert {"from": "commit:" + sha, "to": cc[0]["event_id"], "rel": "recorded_by"} in g["edges"]
assert {"from": cc[0]["event_id"], "to": merged["event_id"], "rel": "parent"} in g["edges"]
assert {"from": plan[0]["event_id"], "to": intent_ev["event_id"], "rel": "caused_by"} in g["edges"]
assert sh(os.path.join(BIN, "events"), "graph", "--format", "dot").stdout.startswith("digraph aladdin {")
ok("events graph exports nodes and edges (json/dot) derived only from the stream")

print("\n%d checks passed" % PASS[0])
