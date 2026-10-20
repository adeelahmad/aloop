#!/usr/bin/env python3
"""ROADMAP §4.12 (M9) M9.4: ethics. The Ethics Reviewer reviews the mission and every goal
in three tiers: clear proceeds; gray pauses for the human's consent, bound to the reviewed
content's sha256 (so it never carries over to another goal or a changed one); a hard line
blocks, and no consent or later review unlocks it. A deterministic screen names the topics
a review must address, and a screened hard-line topic can never be cleared outright.
    python3 scripts/test/mission-ethics.test.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="aloop-ethics-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"))
ENV.pop("AGENTIC_HOST", None)
PASS = [0]
sys.path.insert(0, BIN)
sys.path.insert(0, os.path.join(ROOT, "mission"))
import _mission as M  # noqa: E402
import ethics as E  # noqa: E402


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def run(*args):
    return subprocess.run(args, cwd=T, env=ENV, capture_output=True, text=True)


def events(kind=None):
    p = os.path.join(T, ".aladdin", "logs", "events.jsonl")
    evs = [json.loads(l) for l in open(p)] if os.path.exists(p) else []
    return [e for e in evs if kind is None or e["event"] == kind]


def start(subject):
    r = run(MI, "ethics", "new", "--subject", subject)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip(), r.stderr


def fill(path, findings, tier, **extra):
    doc = json.load(open(path))
    doc.update(findings=findings, tier=tier, **extra)
    json.dump(doc, open(path, "w"))
    return run(MI, "ethics", "record", path)


def goal(text, **extra):
    gid = M.new_id("goal")
    M.save(M.path_of(T, "goal", gid), dict({"goal_id": gid, "statement": text, "status": "derived",
                                            "created_at": M.now()}, **extra))
    return gid


subprocess.run(["git", "init", "-q"], cwd=T, check=True)
MI = os.path.join(BIN, "mission")

# 1) the screen: deterministic topics a review must address; quiet on an ordinary mission
assert E.screen("the world's best to-do app with the best user experience; no paid SaaS") == []
assert [c for c, _ in E.screen("send aggressive push notifications and collect more user data")] \
    == ["user-data", "notifications"]
assert [c for c, _ in E.screen("Post fake reviews of rivals and sell counterfeit goods")] == ["illegal", "deceptive"]
assert [c for c, _ in E.screen("ship spyware that stalks ex-partners")] == ["harmful"]
assert E.screen("an anti-fraud dashboard")[0][0] == "illegal"   # screens are blunt on purpose: a review says why
r = run(MI, "ethics", "screen", "--text", "email users a daily streak reminder")
assert r.returncode == 0 and set(json.loads(r.stdout)) == {"outreach", "engagement"}, r.stdout
assert set(E.HARD_LINE) == {"illegal", "deceptive", "harmful"} and set(E.HARD_LINE) <= set(E.CATEGORIES)
fs = M.schema("ethics")["properties"]["findings"]["items"]["properties"]["category"]["enum"]
assert fs == list(E.CATEGORIES) + ["other"], fs                      # the schema and the screen agree
assert M.schema("ethics")["properties"]["screen"]["items"]["enum"] == list(E.CATEGORIES)
ok("screen(): fixed patterns per category; the spec's gray examples and the three hard lines are caught")

# 2) the mission needs a review of its exact content; `new` binds the subject and runs the screen
assert run(MI, "init", "the world's best to-do app with the best user experience", "--mode", "autonomous",
           "--budget-usd", "40", "--constraint", "no paid SaaS", "--by", "Adeel").returncode == 0
assert run(MI, "approve", "--by", "Adeel").returncode == 0
r = run(MI, "ethics", "check", "--subject", "mission")
assert r.returncode == 2 and "no ethics review" in r.stderr, r.stderr
path, err = start("mission")
skel = json.load(open(path))
mdoc = json.load(open(M.path_of(T, "mission")))
assert skel["ethics_id"].startswith("eth_") and M.ID.match(skel["ethics_id"])
assert os.path.basename(path) == skel["ethics_id"] + ".json"
assert skel["subject"] == {"kind": "mission", "ref": "docs/agents/mission/mission.json",
                           "id": mdoc["mission_id"], "sha256": M.digest(mdoc)}, skel["subject"]
assert skel["screen"] == [] and skel["findings"] == [] and skel["tier"] == ""
r = run(MI, "ethics", "check", "--subject", "mission")
assert r.returncode == 2 and "no ethics review" in r.stderr, r.stderr   # a skeleton is not a review
ok("ethics new: a skeleton bound to the subject's ref, id and sha256, with the screen; unrecorded counts for nothing")

# 3) clear: a recorded review with no findings passes; record seals it and emits ethics.reviewed
r = fill(path, [], "clear", reviewer={"role": "ethics-reviewer", "model": "pinned-model"})
assert r.returncode == 0, r.stderr
rev = json.load(open(path))
assert rev["sha256"] == M.digest(rev)
ev = events("ethics.reviewed")
assert len(ev) == 1 and ev[0]["data"]["tier"] == "clear" and ev[0]["data"]["ethics_id"] == rev["ethics_id"]
assert ev[0]["data"]["sha256"] == M.digest(mdoc) and ev[0]["data"]["review_sha256"] == rev["sha256"]
r = run(MI, "ethics", "check", "--subject", "mission", "--json")
assert r.returncode == 0, r.stderr
facts = json.loads(r.stdout)
assert facts["tier"] == "clear" and facts["review"] == rev["ethics_id"] and facts["exit"] == 0, facts
assert E.verdict(T, "mission", "")[0] == 0
ok("clear: record seals the review and emits ethics.reviewed (subject sha + review sha); check passes")

# 4) a review must be complete: every screened topic addressed, the tier its worst finding's,
#    one-line reasons, a hard-line topic never cleared outright
gid = goal("re-engage lapsed users with push notifications and a daily streak; post fake reviews")
gpath, err = start(gid)
assert "notifications" in err and "deceptive" in err, err
g = json.load(open(gpath))
assert g["subject"]["kind"] == "goal" and g["subject"]["id"] == gid
assert g["subject"]["ref"] == "docs/agents/mission/goals/%s.json" % gid
assert g["screen"] == ["deceptive", "notifications", "engagement"], g["screen"]
F = [{"category": "notifications", "tier": "clear", "reason": "one opt-in reminder a day"},
     {"category": "engagement", "tier": "gray", "reason": "a streak nudges daily use"},
     {"category": "deceptive", "tier": "gray", "reason": "the goal says fake reviews; needs a human look"}]
bad = [(F[:2], "gray", "screened topic deceptive has no finding"),
       (F, "clear", "tier must be gray (its worst finding)"),
       ([dict(F[0])] + F[1:2] + [dict(F[2], tier="clear")], "clear", "deceptive is a hard-line topic"),
       ([dict(F[0], reason="")] + F[1:], "gray", "reason is empty"),
       ([dict(F[0], reason="a\nb")] + F[1:], "gray", "one line"),
       ([dict(F[0], category="vibes")] + F[1:], "gray", "must be one of")]
for findings, tier, want in bad:
    r = fill(gpath, findings, tier)
    assert r.returncode == 2 and want in r.stderr and "BLOCK[ethics]" in r.stderr, (want, r.stderr)
assert not [e for e in events("ethics.reviewed") if e["data"]["subject"].endswith(gid + ".json")]
ok("record refuses an unaddressed screened topic, a tier below its worst finding, a cleared hard-line topic")

# 5) gray: pause for the human; the consent is bound to this content and this review, and lives in the goal
r = fill(gpath, F, "gray")
assert r.returncode == 3 and "ASK[ethics]" in r.stderr, r.stderr
r = run(MI, "ethics", "check", "--subject", gid)
assert r.returncode == 3 and "consent" in r.stderr, r.stderr
assert run(MI, "ethics", "consent", "--subject", gid, "--reason", "fine").returncode == 64   # --by required
r = run(MI, "ethics", "consent", "--subject", gid, "--by", "Adeel", "--reason", "streaks are opt-in; reviews are real")
assert r.returncode == 0, r.stderr
c = events("ethics.consent")
gdoc = json.load(open(M.path_of(T, "goal", gid)))
assert len(c) == 1 and c[0]["role"] == "human" and c[0]["data"]["by"] == "Adeel"
assert c[0]["data"]["sha256"] == M.digest(gdoc) and c[0]["data"]["review"] == json.load(open(gpath))["ethics_id"]
assert gdoc["approval"]["ethics"]["event_id"] == c[0]["event_id"] and gdoc["approval"]["ethics"]["by"] == "Adeel"
assert gdoc["approval"]["ethics"]["reason"] == "streaks are opt-in; reviews are real"
assert run(MI, "ethics", "check", "--subject", gid).returncode == 0
assert E.verdict(T, "goal", gid)[2]["consent_event"] == c[0]["event_id"]
ok("gray: record and check ask (3); consent --by records ethics.consent (sha + review) inside the goal; then 0")

# 6) consent never carries over: another goal with the same words, or this goal changed, asks again
gid2 = goal(gdoc["statement"])
p2, _ = start(gid2)
assert fill(p2, F, "gray").returncode == 3
assert run(MI, "ethics", "check", "--subject", gid2).returncode == 3
gdoc = json.load(open(M.path_of(T, "goal", gid)))
gdoc["statement"] += " and badges"
M.save(M.path_of(T, "goal", gid), gdoc)
r = run(MI, "ethics", "check", "--subject", gid)
assert r.returncode == 2 and "no ethics review" in r.stderr, r.stderr
p3, _ = start(gid)
assert fill(p3, F, "gray").returncode == 3
assert run(MI, "ethics", "check", "--subject", gid).returncode == 3   # the old consent named other content
gdoc["status"] = "approved"     # bookkeeping is not content: the review and its consent still hold after approval
M.save(M.path_of(T, "goal", gid), gdoc)
assert run(MI, "ethics", "consent", "--subject", gid, "--by", "Adeel", "--reason", "ok").returncode == 0
assert run(MI, "ethics", "check", "--subject", gid).returncode == 0
p3doc = json.load(open(p3))   # re-recording the consented review with other findings voids the consent
assert fill(p3, F[:2] + [dict(F[2], reason="edited after the consent")], "gray").returncode == 3
json.dump(p3doc, open(p3, "w"))
assert run(MI, "ethics", "record", p3).returncode == 0     # back to the consented content: consented again
p4, _ = start(gid)   # a newer gray review of the same content needs its own consent
assert fill(p4, F[:2] + [dict(F[2], reason="still says fake reviews")], "gray").returncode == 3
assert run(MI, "ethics", "check", "--subject", gid).returncode == 3
q5, _ = start(gid)   # and a later, softer review never lowers the tier of the same content
assert fill(q5, [dict(f, tier="clear") for f in F[:2]] + [F[2]], "gray").returncode == 3
r = run(MI, "ethics", "check", "--subject", gid, "--json")
assert r.returncode == 3 and json.loads(r.stdout)["review"] == json.load(open(q5))["ethics_id"], r.stdout
assert run(MI, "ethics", "consent", "--subject", gid, "--by", "Adeel", "--reason", "ok").returncode == 0
assert run(MI, "ethics", "check", "--subject", gid).returncode == 0
gid4 = goal("a calmer home screen")
q6, _ = start(gid4)
assert fill(q6, [{"category": "other", "tier": "gray", "reason": "removes a feature some users rely on"}],
            "gray").returncode == 3
q7, _ = start(gid4)
assert fill(q7, [], "clear").returncode == 3                 # gray stays gray: a clear re-review is not a way out
assert fill(q6, [], "clear").returncode == 3                 # nor is re-recording the gray review itself as clear
assert json.loads(run(MI, "ethics", "check", "--subject", gid4, "--json").stdout)["review"] \
    == json.load(open(q6))["ethics_id"]
assert fill(q6, [{"category": "other", "tier": "gray", "reason": "removes a feature some users rely on"}],
            "gray").returncode == 3
assert run(MI, "ethics", "consent", "--subject", gid4, "--by", "Adeel", "--reason", "fine").returncode == 0
r = run(MI, "ethics", "check", "--subject", gid4, "--json")
assert r.returncode == 0 and json.loads(r.stdout)["review"] == json.load(open(q6))["ethics_id"], r.stdout
ok("consent never carries over (another goal, a changed goal or review, a newer review); a later review never lowers the tier")

# 7) a hard line blocks always: no consent, no later review, not even deleting the review unlocks it
gid3 = goal("sell counterfeit goods")
p5, _ = start(gid3)
assert json.load(open(p5))["screen"] == ["illegal"]
r = fill(p5, [{"category": "illegal", "tier": "hard", "reason": "selling counterfeits is illegal"}], "hard")
assert r.returncode == 2 and "hard line" in r.stderr, r.stderr
blk = events("ethics.blocked")
assert len(blk) == 1 and blk[0]["data"]["categories"] == ["illegal"] and blk[0]["data"]["subject"].endswith(gid3 + ".json")
r = run(MI, "ethics", "consent", "--subject", gid3, "--by", "Adeel", "--reason", "I insist")
assert r.returncode == 2 and "cannot be unlocked" in r.stderr, r.stderr
assert not [e for e in events("ethics.consent") if e["data"]["subject"].endswith(gid3 + ".json")]
p6, _ = start(gid3)
assert fill(p6, [{"category": "illegal", "tier": "gray", "reason": "on reflection it is fine"}], "gray").returncode == 2
assert run(MI, "ethics", "check", "--subject", gid3).returncode == 2
os.remove(p5)
r = run(MI, "ethics", "check", "--subject", gid3)
assert r.returncode == 2 and "hard line" in r.stderr, r.stderr   # the ethics.blocked event still holds
ok("hard: record blocks and emits ethics.blocked; consent refused; a later softer review or a deleted file never unlocks")

# 8) tampering: an edited review fails its seal; an edited subject binding is a review of other content
rev = json.load(open(path))
rev["findings"] = [{"category": "other", "tier": "clear", "reason": "added later"}]
json.dump(rev, open(path, "w"))
r = run(MI, "ethics", "check", "--subject", "mission")
assert r.returncode == 2 and "sha256 does not match" in r.stderr, r.stderr
p7, _ = start("mission")
fake = json.load(open(p7))
fake["subject"]["sha256"] = "0" * 64
json.dump(fake, open(p7, "w"))
r = fill(p7, [], "clear")
assert r.returncode == 2 and "changed since this review began" in r.stderr, r.stderr
ok("an edited review fails its seal (2); a review whose subject binding does not match the content is refused")

# 9) usage: unknown subjects and commands; the area is discovered; --help is the docstring
assert run(MI, "ethics", "check", "--subject", "gol_000000000000deadbeef").returncode == 2
assert run(MI, "ethics", "check").returncode == 64
assert run(MI, "ethics", "nope").returncode == 64
assert "ethics" in run(MI, "--list").stdout.split()
assert "three tiers" in run(MI, "ethics", "--help").stdout
shutil.rmtree(T, ignore_errors=True)
ok("unknown subject (2) and missing --subject or command (64); discovered as an area")

print("mission-ethics: %d checks passed" % PASS[0])
