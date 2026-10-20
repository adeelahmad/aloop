#!/usr/bin/env python3
"""ROADMAP §4.12 (M9) T0: the mission layer's core. The mission is a sealed, hashed file
whose content counts only with a human approval event for exactly that content; a budget
cap is required; ids are harness-issued; the schema subset validator and the snapshot that
`mission tick` decides from; area commands are discovered, so parallel tracks add a file
instead of editing a shared list.
    python3 scripts/test/mission-core.test.py
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="aloop-mission-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"))
ENV.pop("AGENTIC_HOST", None)
PASS = [0]
sys.path.insert(0, BIN)
import _mission as M  # noqa: E402


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def run(*args):
    return subprocess.run(args, cwd=T, env=ENV, capture_output=True, text=True)


def events():
    p = os.path.join(T, ".aladdin", "logs", "events.jsonl")
    return [json.loads(l) for l in open(p)] if os.path.exists(p) else []


subprocess.run(["git", "init", "-q"], cwd=T, check=True)
MI = os.path.join(BIN, "mission")

# 1) init needs a statement, a mode and a budget cap; it writes a sealed draft with a harness id
r = run(MI, "init", "the world's best to-do app", "--mode", "autonomous")
assert r.returncode == 64 and "a budget cap is required" in r.stderr, r.stderr
assert run(MI, "init", "x", "--mode", "yolo", "--budget-usd", "5").returncode == 64
assert "--cadence must look like" in run(MI, "init", "x", "--mode", "autonomous", "--budget-usd", "5",
                                         "--cadence", "often").stderr
r = run(MI, "init", "the world's best to-do app with the best user experience", "--mode", "autonomous",
        "--budget-usd", "40", "--deadline", "2026-12-31", "--constraint", "no paid SaaS", "--by", "Adeel")
assert r.returncode == 0, r.stderr
path = r.stdout.strip()
assert path.endswith(os.path.join("docs", "agents", "mission", "mission.json")), path
doc = json.load(open(path))
assert doc["mission_id"].startswith("msn_") and M.ID.match(doc["mission_id"]), doc["mission_id"]
assert doc["status"] == "draft" and doc["cadence"] == "6h" and doc["budget"] == {"usd": 40.0, "deadline": "2026-12-31"}
assert doc["sha256"] == M.digest(doc) and len(doc["sha256"]) == 64
assert run(MI, "init", "another", "--mode", "autonomous", "--budget-usd", "1").returncode == 2
assert [e["event"] for e in events()] == ["mission.set"]
ok("mission init: statement, mode and a budget cap required; a sealed draft with a harness-issued id")

# 2) a draft needs the human (exit 3); an approval covers exactly this content
r = run(MI, "check")
assert r.returncode == 3 and "ASK[mission]" in r.stderr and "a draft" in r.stderr, r.stderr
assert run(MI, "approve").returncode == 64          # who approved is required
r = run(MI, "approve", "--by", "Adeel", "--reason", "this is the direction")
assert r.returncode == 0, r.stderr
r = run(MI, "check")
assert r.returncode == 0 and "approved by Adeel" in r.stderr, r.stderr
ev = [e for e in events() if e["event"] == "mission.approved"]
assert len(ev) == 1 and ev[0]["data"]["sha256"] == M.digest(json.load(open(path))) and ev[0]["data"]["by"] == "Adeel"
assert ev[0]["data"]["subject"] == "docs/agents/mission/mission.json", ev[0]["data"]
ok("a draft asks the human; mission approve records mission.approved with the content's sha256")

# 3) the fixed point: changing the approved mission fails until a human approves the new content
doc = json.load(open(path))
doc["budget"]["usd"] = 4000
json.dump(doc, open(path, "w"))
r = run(MI, "check")
assert r.returncode == 2 and "has no human approval" in r.stderr, r.stderr
doc["status"] = "draft"
json.dump(doc, open(path, "w"))
r = run(MI, "check")
assert r.returncode == 3 and "changed since it was sealed" in r.stderr, r.stderr
assert run(MI, "approve", "--by", "Adeel").returncode == 0 and run(MI, "check").returncode == 0
doc = json.load(open(path))
doc["status"] = "approved"
doc["statement"] = "something else"
json.dump(doc, open(path, "w"))
assert run(MI, "check").returncode == 2
doc["statement"] = "the world's best to-do app with the best user experience"
json.dump(doc, open(path, "w"))
assert run(MI, "check").returncode == 0     # back to the approved content: approved again
ok("a changed mission fails the check (2) or asks (3) until approved; reverting restores the approval")

# 4) a hand-made id and unknown fields are refused by the schema + core contract
bad = dict(doc, mission_id="msn_madeup", extra=1, mode="sometimes")
p = M.problems(bad, "mission", path)
assert any("was not issued by the harness" in x for x in p), p
assert any("extra is not a known field" in x for x in p) and any("$.mode must be one of" in x for x in p), p
ok("the schema refuses unknown fields and enum values; the id must be harness-issued")

# 5) the schema subset validator
S = {"type": "object", "required": ["a", "b"], "properties": {
    "a": {"type": "array", "minItems": 1, "items": {"type": "object", "required": ["src"],
                                                     "properties": {"src": {"type": "string", "minLength": 1}}}},
    "b": {"type": "integer", "minimum": 2}, "c": {"type": ["string", "null"], "pattern": "^v[0-9]+$"}}}
assert M.validate({"a": [{"src": "x"}], "b": 2, "c": None}, S) == []
p = M.validate({"a": [{"src": " "}, {}], "b": True, "c": "x1"}, S)
assert "$.a[0].src is empty" in p and "$.a[1].src is missing" in p and "$.b must be integer" in p, p
assert "$.c does not match ^v[0-9]+$" in p, p
assert sorted(M.validate({"a": []}, S)) == ["$.a needs at least 1 item(s)", "$.b is missing"]
ok("validate(): type, required, items, minItems, minLength, minimum, pattern, nullable types")

# 6) the snapshot reads only contract fields; status and show work without any area module
r = run(MI, "status", "--json")
assert r.returncode == 0, r.stderr
snap = json.loads(r.stdout)
assert snap["mission"]["count"] == 1 and snap["mission"]["approved_ok"] is True
assert snap["baseline"]["count"] == 0 and snap["goal"]["latest"] is None
os.makedirs(M.mdir(T, "goals"))
gid = M.new_id("goal")
M.save(M.path_of(T, "goal", gid), {"goal_id": gid, "status": "active", "created_at": M.now()})
snap = M.snapshot(T)
assert snap["goal"]["by_status"] == {"active": 1} and snap["goal"]["latest"]["goal_id"] == gid
assert "Mission: the world's best to-do app" in run(MI, "show").stdout
ok("mission status: per-kind counts by status and the latest id, from the contract fields only")

# 7) area commands are discovered from plugin/mission/<area>.py (no shared list to edit)
assert run(MI, "nope").returncode == 64
listed = run(MI, "--list").stdout.split()
assert listed[:5] == ["init", "check", "approve", "show", "status"], listed
areas = sorted(f[:-3] for f in os.listdir(os.path.join(ROOT, "mission")) if f.endswith(".py") and not f.startswith("_"))
assert listed[5:] == areas, (listed, areas)
r = run(os.path.join(BIN, "aloop"), "mission", "--help")
assert r.returncode == 0 and "the mission layer on top of Aladdin" in r.stdout, r.stderr
ok("mission --list = the built-ins + every plugin/mission/<area>.py; `aloop mission` reaches it")

# 8) every approval event names a subject kind; every kind has a status vocabulary and a layout
assert set(M.APPROVAL_EVENTS.values()) <= set(M.LAYOUT) and set(M.PREFIX) == set(M.LAYOUT) == set(M.STATUSES)
sys.path.insert(0, BIN)
import _events  # noqa: E402
for e in list(M.APPROVAL_EVENTS) + ["mission.set", "mission.tick", "goal.derived", "ethics.reviewed", "judge.run"]:
    assert e in _events.EVENT_TYPES, e
ok("approval events, layouts, prefixes and statuses agree; mission events are registered")

print("mission-core: %d checks passed" % PASS[0])
