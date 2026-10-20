#!/usr/bin/env python3
"""ROADMAP §4.12 (M9.2): the baseline. It is bound to the approved mission, measured by
running every journey's pinned runner twice (the runs must agree within tolerance), refused
when nothing is left to earn (the mission's RED), confirmed by a human (which freezes it and
its journeys), and replaced only with evidence: a spoon-fed mission confirms every version,
an autonomous one applies a replacement after its veto window unless the human vetoes.
    python3 scripts/test/mission-baseline.test.py
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "fixtures"))
from mission_fixture import Project, M  # noqa: E402

PASS = [0]


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def new_id(r):
    assert r.returncode == 0, r.stderr
    return json.load(open(r.stdout.strip()))["baseline_id"]


# 1) a baseline needs an approved mission and journeys; it lists them by sha256
draft_only = Project(approve=False)
assert draft_only.run("baseline", "new", "--definition", "x").returncode == 3
p = Project(veto_window_h=0)
rid = p.research(["c1", "c2", "c3"])
r = p.run("baseline", "new", "--definition", "a first task in under 5 seconds, and it shows up")
assert r.returncode == 2 and "no journeys to measure" in r.stderr, r.stderr
j1 = p.journey(p.draft(rid))
r = p.run("baseline", "new", "--definition", "", "--rival", "Todoist")
assert r.returncode == 2 and "definition_of_best is empty" in r.stderr and "must be <research_id>#<claim_id>" in r.stderr, r.stderr
b1 = new_id(p.run("baseline", "new", "--definition", "a first task in under 5 seconds, and it shows up",
                  "--rival", rid + "#c3"))
doc = p.load("baseline", b1)
assert doc["version"] == 1 and doc["status"] == "proposed" and doc["journeys"] == [
    {"journey_id": j1, "sha256": M.digest(p.load("journey", j1))}]
assert doc["mission_sha256"] == M.mission(p.dir)[1]
assert p.run("baseline", "check").returncode == 3          # nothing confirmed yet
ok("baseline new: bound to the approved mission, lists its journeys by sha256, rivals are sourced claims")

# 2) measure: twice, within tolerance; a flaky product or a changed journey is refused
open(os.path.join(p.dir, "flaky"), "w").close()
r = p.run("baseline", "measure", b1)
assert r.returncode == 2 and "not reproducible: journey %s metric secs" % j1 in r.stderr and "drifts 16.7%" in r.stderr, r.stderr
assert p.load("baseline", b1)["status"] == "proposed" and p.load("baseline", b1)["measurements"]["agreed"] is False
os.remove(os.path.join(p.dir, "flaky"))
jd = p.load("journey", j1)
jd["title"] = "edited after listing"
p.save("journey", j1, jd)
r = p.run("baseline", "measure", b1)
assert r.returncode == 2 and "changed since this baseline listed it" in r.stderr, r.stderr
jd["title"] = "Add a task and see it"
p.save("journey", j1, jd)
ok("baseline measure: two runs must agree within tolerance; a journey edited after listing is refused")

# 3) the mission's RED: a baseline whose journeys all pass already has nothing to earn
p.app(seconds_to_first_task=2.0, working_steps=["open", "add", "see"], tasks_shown=1)
r = p.run("baseline", "measure", b1)
assert r.returncode == 2 and "nothing to earn" in r.stderr, r.stderr
p.app(seconds_to_first_task=9.0, working_steps=["open", "add"], tasks_shown=0)
r = p.run("baseline", "measure", b1, "--runs", "3")
assert r.returncode == 0, r.stderr
doc = p.load("baseline", b1)
assert doc["status"] == "measured" and doc["sha256"] == M.digest(doc) and len(doc["measurements"]["runs"]) == 3
assert doc["measurements"]["not_passing"] == [j1] and doc["measurements"]["max_drift"] == 0
assert p.events("baseline.measured")[-1]["data"]["not_passing"] == [j1]
ok("baseline measure: refused when every journey already passes; measured (sealed) when it agrees and fails today")

# 4) confirm: the human's approval of exactly this content freezes it and its journeys
assert p.run("baseline", "confirm", b1).returncode == 64
r = p.run("baseline", "confirm", b1, "--by", "Adeel", "--reason", "yes, that is what best means")
assert r.returncode == 0, r.stderr
assert p.load("baseline", b1)["status"] == "confirmed" and p.load("journey", j1)["status"] == "active"
assert p.run("baseline", "check").returncode == 0 and p.run("journeys", "check").returncode == 0
assert p.run("baseline", "current").stdout.strip() == b1
ev = p.events("baseline.confirmed")
assert len(ev) == 1 and ev[0]["data"]["by"] == "Adeel" and ev[0]["data"]["sha256"] == M.digest(p.load("baseline", b1))
ok("baseline confirm: baseline.confirmed for its sha256; its journeys become active and frozen")

# 5) tampering is caught; a changed mission asks the human to re-confirm the direction
jd = p.load("journey", j1)
jd["pass_criteria"][1]["value"] = 50
p.save("journey", j1, jd)
assert "changed after it was frozen" in p.run("baseline", "check").stderr
assert "changed after it was frozen" in p.run("journeys", "check").stderr
jd["pass_criteria"][1]["value"] = 5
p.save("journey", j1, jd)
bd = p.load("baseline", b1)
bd["tolerance"] = 0.5
p.save("baseline", b1, bd)
r = p.run("baseline", "check")
assert r.returncode == 2 and "sha256 does not match" in r.stderr, r.stderr
bd["tolerance"] = 0.05
p.save("baseline", b1, bd)
assert p.run("baseline", "check").returncode == 0
md = p.load("mission")
md["statement"] += ", for teams"
md["status"] = "draft"
p.save("mission", "", md)
assert p.run("approve", "--by", "Adeel").returncode == 0
r = p.run("baseline", "check")
assert r.returncode == 3 and "earlier mission content" in r.stderr, r.stderr
ok("a frozen journey or baseline edited by hand fails the check; a re-approved mission asks to re-confirm")

# 6) a replacement needs evidence; an autonomous mission applies it after the veto window, unless vetoed
md["statement"] = md["statement"].replace(", for teams", "")
p.save("mission", "", md)
assert p.run("approve", "--by", "Adeel").returncode == 0 and p.run("baseline", "check").returncode == 0
j2 = p.journey(p.draft(rid, title="Add three tasks in a row"))
r = p.run("baseline", "new", "--definition", "faster, and three tasks in a row")
assert r.returncode == 2 and "names it with --supersedes and --evidence" in r.stderr, r.stderr
r = p.run("baseline", "new", "--definition", "faster", "--supersedes", b1)
assert r.returncode == 2 and "needs evidence" in r.stderr, r.stderr
b2 = new_id(p.run("baseline", "new", "--definition", "faster, and three tasks in a row", "--supersedes", b1,
                  "--evidence", rid + "#c2"))
assert p.load("baseline", b2)["version"] == 2 and len(p.load("baseline", b2)["journeys"]) == 2
assert p.run("baseline", "apply", b2).returncode == 2           # not measured yet
assert p.run("baseline", "measure", b2).returncode == 0
r = p.run("baseline", "apply", b2)
assert r.returncode == 0 and "applied after the 0h veto window" in r.stderr, r.stderr
assert p.load("baseline", b1)["status"] == "superseded" and p.load("baseline", b2)["status"] == "confirmed"
assert p.load("journey", j2)["status"] == "active" and p.run("baseline", "check").returncode == 0
last = M.records(p.dir, "baseline.confirmed")[-1]
assert last["role"] == "mission" and last["data"]["applied"] == "veto_window" and not M.approvals(p.dir, "baseline.confirmed", last["data"]["subject"])
b3 = new_id(p.run("baseline", "new", "--definition", "even faster", "--supersedes", b2, "--evidence", rid + "#c1"))
assert p.run("baseline", "measure", b3).returncode == 0
assert p.run("baseline", "veto", b3, "--by", "Adeel").returncode == 64
assert p.run("baseline", "veto", b3, "--by", "Adeel", "--reason", "not what I meant by best").returncode == 0
assert p.run("baseline", "apply", b3).returncode == 2 and p.run("baseline", "current").stdout.strip() == b2
again = new_id(p.run("baseline", "new", "--definition", "even faster", "--supersedes", b2, "--evidence", rid + "#c2"))
assert p.run("baseline", "measure", again).returncode == 0
r = p.run("baseline", "apply", again)
assert r.returncode == 2 and "content the human vetoed" in r.stderr, r.stderr         # the same content under a new id
r = p.run("baseline", "new", "--definition", "only the first journey", "--journey", j1, "--supersedes", b2,
          "--evidence", rid + "#c1")
assert r.returncode == 2 and "drops journey %s: retire it first" % j2 in r.stderr, r.stderr
r = p.run("baseline", "new", "--definition", "noisier", "--supersedes", b2, "--evidence", rid + "#c1", "--tolerance", "0.3")
assert r.returncode == 2 and "tolerance must be <= 0.25" in r.stderr, r.stderr
b4 = new_id(p.run("baseline", "new", "--definition", "same, noisier", "--supersedes", b2, "--evidence", rid + "#c1",
                  "--tolerance", "0.2"))
assert p.run("baseline", "measure", b4).returncode == 0
r = p.run("baseline", "apply", b4)
assert r.returncode == 3 and "tolerates more drift" in r.stderr, r.stderr
assert "(tolerance 0.2)" in p.run("baseline", "show", b4).stdout
r = p.run("baseline", "new", "--definition", "nan", "--supersedes", b2, "--evidence", rid + "#c1", "--tolerance", "nan")
assert r.returncode == 2 and "tolerance must be a finite number" in r.stderr, r.stderr
b5 = new_id(p.run("baseline", "new", "--definition", "faster still", "--supersedes", b2, "--evidence", rid + "#c1"))
assert p.run("baseline", "measure", b5).returncode == 0 and p.run("baseline", "apply", b5).returncode == 0
r = p.run("baseline", "apply", b4)
assert r.returncode == 2 and "but the current confirmed version is %s" % b5 in r.stderr, r.stderr    # stale: no rollback
r = p.run("baseline", "new", "--definition", "x", "--supersedes", b2, "--evidence", rid + "#c1")
assert r.returncode == 2 and "is not the current confirmed version (%s)" % b5 in r.stderr, r.stderr
ok("a replacement needs --supersedes + evidence and keeps every active journey; autonomous: applied after the "
   "veto window unless vetoed; a looser tolerance waits for the human")

# 7) the first version, a spoon-fed mission and an open window all need the human
q = Project(mode="spoon-fed", veto_window_h=0)
qr = q.research(["c1"])
q.journey(q.draft(qr))
c1 = new_id(q.run("baseline", "new", "--definition", "fast first task"))
assert q.run("baseline", "measure", c1).returncode == 0
r = q.run("baseline", "apply", c1)
assert r.returncode == 3 and "first baseline is confirmed by the human" in r.stderr, r.stderr
assert q.run("baseline", "confirm", c1, "--by", "Adeel").returncode == 0
c2 = new_id(q.run("baseline", "new", "--definition", "faster", "--supersedes", c1, "--evidence", qr + "#c1"))
assert q.run("baseline", "measure", c2).returncode == 0
assert "spoon-fed mission confirms every baseline" in q.run("baseline", "apply", c2).stderr
w = Project(veto_window_h=24)
wr = w.research(["c1"])
w.journey(w.draft(wr))
d1 = new_id(w.run("baseline", "new", "--definition", "fast first task"))
assert w.run("baseline", "measure", d1).returncode == 0 and w.run("baseline", "confirm", d1, "--by", "Adeel").returncode == 0
d2 = new_id(w.run("baseline", "new", "--definition", "faster", "--supersedes", d1, "--evidence", wr + "#c1"))
assert w.run("baseline", "measure", d2).returncode == 0
r = w.run("baseline", "apply", d2)
assert r.returncode == 3 and "veto window is open for 24.0 more hour(s)" in r.stderr, r.stderr
ok("the first version and a spoon-fed mission's replacements are confirmed by the human; an open window waits")

# 8) retiring a frozen journey asks for a new baseline version
r = p.run("journeys", "retire", j2, "--evidence", rid + "#c2")
assert r.returncode == 3
assert p.run("journeys", "approve", j2, "--by", "Adeel", "--reason", "three-in-a-row is not a real use").returncode == 0
assert p.run("journeys", "retire", j2, "--evidence", rid + "#c2").returncode == 0
r = p.run("baseline", "check")
assert r.returncode == 3 and "was retired: a new baseline version supersedes this one" in r.stderr, r.stderr
ok("retiring a journey the baseline froze asks for a new baseline version")

# 9) the records decide, not the files: a forged measurement, a hand-set status, a re-sealed journey, NaN
d3 = new_id(w.run("baseline", "new", "--definition", "the same, measured by hand", "--supersedes", d1, "--evidence", wr + "#c1"))
x = w.load("baseline", d3)
x.update(status="measured", measurements=dict(w.load("baseline", d2)["measurements"], at="2000-01-01T00:00:00Z"))
M.seal(x)
w.save("baseline", d3, x)
r = w.run("baseline", "apply", d3)
assert r.returncode == 2 and "has no baseline.measured record for this content" in r.stderr, r.stderr
x = w.load("baseline", d2)
x["measurements"]["at"] = "2000-01-01T00:00:00Z"
M.seal(x)
w.save("baseline", d2, x)
r = w.run("baseline", "apply", d2)
assert r.returncode == 2 and "no baseline.measured record" in r.stderr, r.stderr      # the window starts at the record
jw = M.artifacts(w.dir, "journey")[0][1]
weak = dict(jw, pass_criteria=[{"id": "fast", "metric": "secs", "op": "<=", "value": 500}])
M.seal(weak)
w.save("journey", jw["journey_id"], weak)
r = w.run("baseline", "new", "--definition", "carry the weaker one", "--supersedes", d1, "--evidence", wr + "#c1")
assert r.returncode == 2 and "listed at other content than it was pinned to" in r.stderr, r.stderr
w.save("journey", jw["journey_id"], jw)
assert w.run("baseline", "check").returncode == 0
assert q.run("baseline", "confirm", c2, "--by", "Adeel").returncode == 0 and q.run("baseline", "current").stdout.strip() == c2
for bid_, st in ((c1, "confirmed"), (c2, "superseded")):
    y = q.load("baseline", bid_)
    y["status"] = st
    q.save("baseline", bid_, y)
r = q.run("baseline", "check")
assert r.returncode == 2 and "%s says confirmed, but the records make %s current" % (c1, c2) in r.stderr, r.stderr
assert q.run("baseline", "current").stdout.strip() == c2
for bid_, st in ((c1, "superseded"), (c2, "confirmed")):
    y = q.load("baseline", bid_)
    y["status"] = st
    q.save("baseline", bid_, y)
assert q.run("baseline", "check").returncode == 0
y = q.load("baseline", c2)
q.save("baseline", c2, dict(y, journeys=["jrn_x"]))
r = q.run("baseline", "check")
assert r.returncode == 2 and "must be object" in r.stderr and "Traceback" not in r.stderr, r.stderr
q.save("baseline", c2, y)
nd = tempfile.mkdtemp(prefix="aloop-mission-")
subprocess.run(["git", "init", "-q"], cwd=nd, check=True)
v = subprocess.run([os.path.join(M.PLUGIN, "bin", "mission"), "init", "m", "--mode", "autonomous", "--budget-usd", "1",
                    "--veto-window-h", "nan"], cwd=nd, env=q.env, capture_output=True, text=True)
assert v.returncode != 0 and "veto-window-h" in v.stderr, v.stderr
ok("a forged measurement or window, a hand-set status, a re-sealed frozen journey, a malformed file, NaN: all refused")

# 10) the RED is a demonstrated failure: a journey waiting for a judge does not count
jp = Project(veto_window_h=0)
jr = jp.research(["c1"])
jdraft = jp.draft(jr)
jdraft["metrics"].append({"id": "ux", "name": "delight", "unit": "1-5", "direction": "higher",
                          "query": jp.rubric(), "source_refs": [jr + "#c1"]})
jdraft["pass_criteria"].append({"id": "delight", "metric": "ux", "op": ">=", "value": 4})
jp.journey(jdraft)
jp.app(seconds_to_first_task=2.0, working_steps=["open", "add", "see"], tasks_shown=1)
e1 = new_id(jp.run("baseline", "new", "--definition", "delightful"))
r = jp.run("baseline", "measure", e1)
assert r.returncode == 2 and "no journey fails yet and 1 wait for a judge score" in r.stderr, r.stderr
ok("a journey whose only open criterion waits for a judge is not a failure: the baseline waits")

# 11) a run that leaves a value out is not a measurement; the human can restore an archived version
open(os.path.join(jp.dir, "tools", "quiet.py"), "w").write('print(\'{"steps": {"open": true}, "metrics": {}}\')\n')
jq = jp.journey(dict(jp.draft(jr), runner=dict(jp.draft(jr)["runner"], command=["python3", "tools/quiet.py"])))
r = jp.run("baseline", "new", "--definition", "quiet", "--journey", jq)
r = jp.run("baseline", "measure", new_id(r))
assert r.returncode == 2 and "the runner did not report add, see, secs, tasks_shown" in r.stderr, r.stderr
assert p.run("baseline", "current").stdout.strip() == b5
r = p.run("baseline", "restore", b2)
assert r.returncode == 64, r.stderr                                    # a human names themselves
r = p.run("baseline", "restore", b2, "--by", "Adeel", "--reason", "the faster-still bar was premature")
assert r.returncode == 2 and "retired since" in r.stderr, r.stderr      # j2 was retired after b2 froze it
r = p.run("baseline", "restore", b4, "--by", "Adeel")
assert r.returncode == 2 and "never confirmed" in r.stderr, r.stderr
assert q.run("baseline", "restore", c1, "--by", "Adeel", "--reason", "back to the first bar").returncode == 0
assert q.run("baseline", "current").stdout.strip() == c1 and q.run("baseline", "check").returncode == 0
assert q.load("baseline", c2)["status"] == "superseded"
ok("a run that leaves out a step or metric is refused; restore reinstates only an intact, once-confirmed version")

# 12) the freeze is the records', not the files'; a broken ledger stops every decision; a symlinked version blocks
q = Project(veto_window_h=0)
qr = q.research(["c1", "c2"])
qj = q.journey(q.draft(qr))
qb = new_id(q.run("baseline", "new", "--definition", "a first task in under 5 seconds"))
assert q.run("baseline", "measure", qb).returncode == 0
assert q.run("baseline", "confirm", qb, "--by", "Adeel").returncode == 0
assert M.newest(q.dir, "baseline.confirmed", M.rel(q.dir, q.path("baseline", qb)))["data"]["journeys"] == \
    [{"journey_id": qj, "sha256": M.digest(q.load("journey", qj))}]
bdoc = q.load("baseline", qb)
q.save("baseline", qb, dict(bdoc, definition_of_best="edited, so no confirmation covers this file"))
r = q.run("journeys", "retire", qj, "--evidence", qr + "#c2")
assert r.returncode == 3 and not q.load("journey", qj).get("retirement"), (r.returncode, r.stderr)
q.save("baseline", qb, bdoc)
led = M.ledger(q.dir)
good = open(led).read()
open(led, "w").write(good.replace('"by": "Adeel"', '"by": "Eve"', 1))
for args in (["baseline", "new", "--definition", "x"], ["baseline", "measure", qb], ["baseline", "apply", qb],
             ["baseline", "confirm", qb, "--by", "Adeel"], ["baseline", "veto", qb, "--by", "Adeel", "--reason", "no"],
             ["baseline", "restore", qb, "--by", "Adeel"], ["journeys", "approve", qj, "--by", "Adeel", "--reason", "ok"],
             ["journeys", "retire", qj, "--evidence", qr + "#c2"], ["assess"]):
    r = q.run(*args)
    assert r.returncode == 2 and "was edited" in r.stderr and "Traceback" not in r.stderr, (args, r.returncode, r.stderr)
assert open(led).read() == good.replace('"by": "Adeel"', '"by": "Eve"', 1)
open(led, "w").write(good)
assert q.run("baseline", "check").returncode == 0
bp = q.path("baseline", qb)
os.rename(bp, bp + ".orig")
os.symlink(bp + ".orig", bp)
r = q.run("baseline", "check")
assert r.returncode == 0 and "Traceback" not in r.stderr, (r.returncode, r.stderr)     # the same content, through a link
M.save(bp + ".orig", dict(bdoc, definition_of_best="another content behind the link"))
r = q.run("baseline", "check")
assert r.returncode == 2 and "changed after it was confirmed" in r.stderr, (r.returncode, r.stderr)
M.save(bp + ".orig", bdoc)
os.remove(bp)
os.rename(bp + ".orig", bp)
assert q.run("baseline", "check").returncode == 0
ok("a confirmation records what it froze (an edited baseline file unfreezes nothing); a broken ledger blocks every decision; a link is read, never a crash")

# 13) the current version must be intact to decide from; a veto binds content in any order; restore sticks
z = Project(veto_window_h=0)
zr = z.research(["c1", "c2"])
z1 = z.journey(z.draft(zr))
zb1 = new_id(z.run("baseline", "new", "--definition", "a first task in under 5 seconds"))
assert z.run("baseline", "measure", zb1).returncode == 0 and z.run("baseline", "confirm", zb1, "--by", "Adeel").returncode == 0
z2 = z.journey(z.draft(zr, title="A second journey"))
zp = z.path("baseline", zb1)
zdoc = z.load("baseline", zb1)
os.rename(zp, zp + ".aside")
r = z.run("baseline", "new", "--journey", z2, "--definition", "d", "--supersedes", zb1, "--evidence", zr + "#c1")
assert r.returncode == 2 and "the current version %s is gone" % zb1 in r.stderr, r.stderr
os.rename(zp + ".aside", zp)


def sup(*extra):
    return z.run("baseline", "new", "--supersedes", zb1, "--evidence", zr + "#c1", *extra)


zb2 = new_id(sup("--definition", "looser", "--tolerance", "0.25"))
assert z.run("baseline", "measure", zb2).returncode == 0
z.save("baseline", zb1, dict(zdoc, tolerance=0.25))
r = z.run("baseline", "apply", zb2)
assert r.returncode == 2 and "the current version %s changed after it was confirmed" % zb1 in r.stderr, r.stderr
z.save("baseline", zb1, zdoc)
r = z.run("baseline", "apply", zb2)
assert r.returncode == 3 and "tolerates more drift" in r.stderr, r.stderr
r = sup("--definition", "dup", "--journey", z1, "--journey", z1)
assert r.returncode == 2 and "lists journey %s twice" % z1 in r.stderr, r.stderr
zb3 = new_id(sup("--definition", "two journeys", "--journey", z1, "--journey", z2))
assert z.run("baseline", "measure", zb3).returncode == 0
assert z.run("baseline", "veto", zb3, "--by", "Adeel", "--reason", "not this").returncode == 0
zb4 = new_id(sup("--definition", "two journeys", "--journey", z2, "--journey", z1))
assert z.run("baseline", "measure", zb4).returncode == 0
r = z.run("baseline", "apply", zb4)
assert r.returncode == 2 and "content the human vetoed" in r.stderr, r.stderr
zb5 = new_id(sup("--definition", "a fifth"))
assert z.run("baseline", "measure", zb5).returncode == 0
z.save("baseline", zb5, dict(z.load("baseline", zb5), status="vetoed"))
assert z.run("baseline", "veto", zb5, "--by", "Adeel", "--reason", "really").returncode == 0
assert M.newest(z.dir, "baseline.vetoed", M.rel(z.dir, z.path("baseline", zb5)))["data"]["by"] == "Adeel"
zb6 = new_id(sup("--definition", "a sixth"))
assert z.run("baseline", "measure", zb6).returncode == 0 and z.run("baseline", "apply", zb6).returncode == 0
assert z.run("baseline", "restore", zb1, "--by", "Adeel", "--reason", "too early").returncode == 0
z.save("baseline", zb6, dict(z.load("baseline", zb6), status="measured"))
r = z.run("baseline", "apply", zb6)
assert r.returncode == 2 and "was confirmed before" in r.stderr, r.stderr
r = z.run("baseline", "veto", zb6, "--by", "Adeel", "--reason", "x")
assert r.returncode == 2 and "was confirmed" in r.stderr, r.stderr
assert z.run("baseline", "current").stdout.strip() == zb1
zdoc = z.load("baseline", zb1)
for bad_doc in (dict(zdoc, measurements=dict(zdoc["measurements"], runs=[{"journeys": "x"}, {"journeys": "x"}])),
                dict(zdoc, journeys=["jrn_x"]), dict(zdoc, tolerance="0.05"), {k: v for k, v in zdoc.items() if k != "version"}):
    z.save("baseline", zb1, bad_doc)
    for args in (["baseline", "check"], ["assess"], ["baseline", "new", "--definition", "d", "--supersedes", zb1,
                 "--evidence", zr + "#c1"], ["baseline", "apply", zb2], ["baseline", "show"], ["baseline", "current"]):
        r = z.run(*args)
        assert "Traceback" not in r.stderr and r.returncode in (0, 2, 3), (args, r.returncode, r.stderr[-300:])
z.save("baseline", zb1, zdoc)
assert z.run("baseline", "check").returncode == 0
ok("an edited, moved or malformed current version decides nothing; a veto binds content in any order; a restore sticks")

# 14) the human's old words never decide again (M9.17): a restore line is spent once another version is current
y = Project(veto_window_h=0)
yr = y.research(["c1"])
y.journey(y.draft(yr))
yb1 = new_id(y.run("baseline", "new", "--definition", "a first task in under 5 seconds"))
assert y.run("baseline", "measure", yb1).returncode == 0
r = y.raw("baseline", "confirm", yb1, "--by", "Adeel")
assert r.returncode == 3 and "they reply  confirm %s " % yb1 in r.stderr, r.stderr    # --by alone confirms nothing
assert y.run("baseline", "confirm", yb1, "--by", "Adeel").returncode == 0


def then(definition):
    b = new_id(y.run("baseline", "new", "--definition", definition, "--supersedes", y.run("baseline", "current").stdout.strip(),
                     "--evidence", yr + "#c1"))
    assert y.run("baseline", "measure", b).returncode == 0 and y.run("baseline", "apply", b).returncode == 0, b
    return b


then("faster")
assert y.run("baseline", "restore", yb1, "--by", "Adeel", "--reason", "too early").returncode == 0
said = M.newest(y.dir, "baseline.confirmed", M.rel(y.dir, y.path("baseline", yb1)))["data"]["prompt"]["line"]
assert said.startswith("restore %s " % yb1), said
yb3 = then("faster again")
r = y.raw("baseline", "restore", yb1, "--by", "Adeel", "--reason", "the same words again")
assert r.returncode == 3 and "they reply  %s  " % said in r.stderr, r.stderr          # the same line, but said before yb3
assert y.run("baseline", "current").stdout.strip() == yb3
y.say(said)                                                                            # said again, now
assert y.raw("baseline", "restore", yb1, "--by", "Adeel", "--reason", "meant it").returncode == 0
assert y.run("baseline", "current").stdout.strip() == yb1
ok("a grant is the human's own words: --by alone confirms nothing, and an old restore line is spent once another version applied")

print("mission-baseline: %d checks passed" % PASS[0])
