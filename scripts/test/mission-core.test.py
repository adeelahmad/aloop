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
doc["budget"]["usd"] = 40.0                 # the content approved first: the newest approval covers usd 4000
json.dump(doc, open(path, "w"))
r = run(MI, "check")
assert r.returncode == 2 and "rolled back" in r.stderr, r.stderr
doc["budget"]["usd"] = 4000
json.dump(doc, open(path, "w"))
assert run(MI, "check").returncode == 0
raw = open(path).read()
open(path, "w").write(raw[:40])
r = run(MI, "check")
assert r.returncode == 2 and "not a JSON object" in r.stderr, r.stderr      # corrupt is invalid, not "no mission"
open(path, "w").write(raw)
json.dump(dict(doc, cadence=6), open(path, "w"))
r = run(MI, "check")
assert r.returncode == 2 and "Traceback" not in r.stderr, r.stderr
open(path, "w").write(raw)
ok("a changed mission fails the check (2) or asks (3) until approved; reverting restores the approval; a rollback fails")

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
for e in list(M.APPROVAL_EVENTS) + list(M.LEDGER_EVENTS) + ["mission.set", "mission.tick", "judge.run"]:
    assert e in _events.EVENT_TYPES, e
assert {"ethics.blocked", "ethics.reviewed", "goal.derived", "goal.met"} <= set(M.LEDGER_EVENTS)
ok("approval events, layouts, prefixes and statuses agree; mission events are registered")

# 9) the ledger: committed with the artifacts (a fresh checkout keeps every decision) and tamper-evident
led = M.ledger(T)
assert open(os.path.join(T, "docs", "agents", "mission", ".gitattributes")).read() == "ledger.jsonl merge=union\n"
lines = open(led).read().splitlines()
rows = [json.loads(l) for l in lines]
assert [r["event"] for r in rows] == ["mission.approved"] * 2 and all(r["data"]["by"] == "Adeel" for r in rows), rows
assert rows[0]["prev"] == "" and rows[1]["prev"] == rows[0]["hash"] and rows[1]["role"] == "human"
assert len(M.approvals(T, "mission.approved", "docs/agents/mission/mission.json")) == 2
os.rename(os.path.join(T, ".aladdin", "logs", "events.jsonl"), os.path.join(T, "events.bak"))
r = run(MI, "check")
assert r.returncode == 0 and "approved by Adeel" in r.stderr, r.stderr
os.rename(os.path.join(T, "events.bak"), os.path.join(T, ".aladdin", "logs", "events.jsonl"))


def tampered(text, want):
    open(led, "w").write(text)
    r = run(MI, "check")
    assert r.returncode == 2 and want in r.stderr and "Traceback" not in r.stderr, (want, r.stderr)


tampered("\n".join([lines[0].replace('"by": "Adeel"', '"by": "Eve"'), lines[1]]) + "\n", "ledger line 1 was edited")
tampered(lines[1] + "\n", "ledger line 1 follows a row that is no longer there")
tampered("\n".join(lines + ["<<<<<<< HEAD", "[]"]) + "\n", "ledger line 3 is not JSON")
tampered("\n".join(lines + ["[]"]) + "\n", "ledger line 3 is not a ledger row")
forged = {"event_id": "f" * 32, "ts": "2000-01-01T00:00:00Z", "event": "mission.approved", "role": "human",
          "data": dict(rows[1]["data"], by="Eve"), "prev": rows[1]["hash"]}
forged["hash"] = M.digest(forged, drop=("hash",))
tampered("\n".join(lines + [json.dumps(forged)]) + "\n", "ledger line 3 is dated before the row it follows")
tampered(lines[0] + "\n", "the ledger lost mission.approved")         # the run log still has the removed row
open(led, "w").write("\n".join(lines) + "\n")
assert run(MI, "check").returncode == 0
try:
    M.record_human(T, "mission.set", "x", "Adeel")
    raise AssertionError("a non-human event was recorded as a human decision")
except ValueError:
    pass
ok("the ledger keeps every decision without run data; an edited, removed, backdated or junk row fails the check")

# 10) an area that opts in with NO_ARGS runs bare (`aloop mission due`); others print their help
import shutil  # noqa: E402
P2 = os.path.join(T, "plugin2")
shutil.copytree(BIN, os.path.join(P2, "bin"), ignore=shutil.ignore_patterns("__pycache__"))
os.makedirs(os.path.join(P2, "mission", "schemas"))
for name, body in (("bare", '"""bare: help"""\nNO_ARGS = True\n\ndef main(argv, cwd):\n    print("ran", argv)\n    return 4\n'),
                   ("plain", '"""plain: help"""\n\ndef main(argv, cwd):\n    return 9\n')):
    open(os.path.join(P2, "mission", name + ".py"), "w").write(body)
r = run(os.path.join(P2, "bin", "mission"), "bare")
assert r.returncode == 4 and "ran []" in r.stdout, (r.returncode, r.stdout, r.stderr)
r = run(os.path.join(P2, "bin", "mission"), "plain")
assert r.returncode == 0 and "plain: help" in r.stdout, (r.returncode, r.stdout)
ok("NO_ARGS areas run with no arguments; the rest print their help")

# 11) text is UTF-8 whatever the locale; a lone surrogate is a problem, never a crash
import hashlib  # noqa: E402
T3 = tempfile.mkdtemp(prefix="aloop-mission-")
subprocess.run(["git", "init", "-q"], cwd=T3, check=True)
CENV = dict(ENV, LC_ALL="C", LANG="C", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0")
intent = "the world’s best café app — for everyone"
r = subprocess.run([MI, "init", intent, "--mode", "autonomous", "--budget-usd", "4"], cwd=T3, env=CENV,
                   capture_output=True)
assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
mdoc = json.load(open(M.path_of(T3, "mission"), encoding="utf-8"))
assert mdoc["statement"] == intent and mdoc["sha256"] == M.digest(mdoc), mdoc
r = subprocess.run([MI, "check"], cwd=T3, env=CENV, capture_output=True)
assert r.returncode == 3, (r.returncode, r.stderr.decode("utf-8", "replace"))
r = subprocess.run([MI, "approve", "--by", "Zoë"], cwd=T3, env=CENV, capture_output=True)
assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
assert subprocess.run([MI, "check"], cwd=T3, env=CENV, capture_output=True).returncode == 0
assert M.digest({"a": "é", "b": 1}) == hashlib.sha256(b'{"a":"\xc3\xa9","b":1}').hexdigest()
assert M.digest({"a": "\ud800"}) != M.digest({"a": "\ud801"})
raw = open(M.path_of(T3, "mission"), encoding="utf-8").read().replace("café", "caf\\ud800")
with open(M.path_of(T3, "mission"), "w", encoding="utf-8") as f:
    f.write(raw)
r = subprocess.run([MI, "check"], cwd=T3, env=ENV, capture_output=True, text=True)
assert r.returncode == 2 and "lone surrogate" in r.stderr, (r.returncode, r.stderr)
assert "draft" in M.STATUSES["ethics"] and "recorded" in M.STATUSES["ethics"]
ok("text is UTF-8 in any locale (argv, files); a lone surrogate blocks instead of crashing; ethics has drafts")

# 12) the run-log cross-check is this branch's; a junk run-log line is skipped; nothing records past a broken ledger
T4 = tempfile.mkdtemp(prefix="aloop-mission-")
GENV = dict(ENV, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")


def at4(*args):
    return subprocess.run(args, cwd=T4, env=GENV, capture_output=True, text=True)


at4("git", "init", "-q", "-b", "main")
assert at4(MI, "init", "a mission on two branches", "--mode", "autonomous", "--budget-usd", "4").returncode == 0
assert at4(MI, "approve", "--by", "Adeel").returncode == 0
at4("git", "add", "-A")
at4("git", "commit", "-qm", "approved")
at4("git", "checkout", "-qb", "experiment")
assert at4(MI, "approve", "--by", "Adeel", "--reason", "on a branch").returncode == 0
at4("git", "commit", "-qam", "approved again on a branch")
at4("git", "checkout", "-q", "main")
at4("git", "branch", "-qD", "experiment")
r = at4(MI, "check")
assert r.returncode == 0 and "lost" not in r.stderr, r.stderr
led4 = M.ledger(T4)
kept = open(led4).read()
assert at4(MI, "approve", "--by", "Adeel", "--reason", "on main").returncode == 0
open(led4, "w").write(kept)                    # the newest row removed on the branch that recorded it
r = at4(MI, "check")
assert r.returncode == 2 and "the ledger lost mission.approved" in r.stderr, r.stderr
with open(os.path.join(T4, ".aladdin", "logs", "events.jsonl"), "a") as f:
    f.write('123\nnull\n"x"\n[]\n')
r = at4(MI, "check")
assert r.returncode == 2 and "Traceback" not in r.stderr, r.stderr
try:
    M.record(T4, "baseline.measured", "docs/agents/mission/baseline/x.json", "state-assessor", sha256="0" * 64)
    raise AssertionError("a row was chained past a broken ledger")
except M.LedgerBroken as e:
    assert "the ledger lost mission.approved" in str(e), e
assert open(led4).read() == kept
ok("the lost-row cross-check is scoped to the branch that recorded it; junk run-log lines are skipped; no row past a broken ledger")

print("mission-core: %d checks passed" % PASS[0])
