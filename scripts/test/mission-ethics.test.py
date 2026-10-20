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
stored = gdoc["approval"]["ethics"]
assert stored["sha256"] == M.digest(gdoc) and list(stored["consents"]) == [c[0]["data"]["review"]], stored
one = stored["consents"][c[0]["data"]["review"]]
assert one["event_id"] == c[0]["event_id"] and one["by"] == "Adeel" and one["review_sha256"] == c[0]["data"]["review_sha256"]
assert one["reason"] == "streaks are opt-in; reviews are real"
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
r = fill(q6, [], "clear")                                  # nor is re-recording the gray review itself as clear:
assert r.returncode == 2 and "never lowers" in r.stderr, r.stderr   # it blocks until the gray version is back
assert run(MI, "ethics", "consent", "--subject", gid4, "--by", "Adeel", "--reason", "x").returncode == 2
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

# 9) hardening (adversarial review of #72): what a model can do with its normal tools never clears a gate
#    a) re-recording an older, consented review does not hide a newer gray one
gA = goal("a calmer home screen with fewer options")
rA, _ = start(gA)
assert fill(rA, [{"category": "other", "tier": "gray", "reason": "removes options"}], "gray").returncode == 3
assert run(MI, "ethics", "consent", "--subject", gA, "--by", "Adeel", "--reason", "fine").returncode == 0
rB, _ = start(gA)
assert fill(rB, [{"category": "other", "tier": "gray", "reason": "also hides the export button"}], "gray").returncode == 3
keep = json.load(open(rA))
assert fill(rA, [{"category": "other", "tier": "gray", "reason": "removes options!"}], "gray").returncode == 3
json.dump(keep, open(rA, "w"))
assert run(MI, "ethics", "record", rA).returncode == 3          # B still has no consent
r = run(MI, "ethics", "check", "--subject", gA)
assert r.returncode == 3 and json.load(open(rB))["ethics_id"] in r.stderr, r.stderr
#    b) a hard line follows the goal through edits, and its words to any other goal
gH = goal("pay strangers to post five-star ratings under made-up names")
rH, _ = start(gH)
assert fill(rH, [{"category": "other", "tier": "hard", "reason": "fake ratings deceive buyers"}], "hard").returncode == 2
gdoc = json.load(open(M.path_of(T, "goal", gH)))
M.save(M.path_of(T, "goal", gH), dict(gdoc, statement=gdoc["statement"] + " "))
rH2, _ = start(gH)
assert fill(rH2, [], "clear").returncode == 2
M.save(M.path_of(T, "goal", gH), dict(gdoc, statement="pay strangers to post five-star ratings under invented names"))
rH4, _ = start(gH)                                  # a real edit: other words, the same goal: still retired
r = fill(rH4, [], "clear")
assert r.returncode == 2 and "retire" in r.stderr, r.stderr
r = run(MI, "ethics", "check", "--subject", gH)
assert r.returncode == 2 and "hard line" in r.stderr and "retire" in r.stderr, r.stderr
gH2 = goal("pay strangers to post five-star ratings under made-up names")
rH3, _ = start(gH2)
r = fill(rH3, [], "clear")
assert r.returncode == 2 and "hard line" in r.stderr, r.stderr
gR = M.new_id("goal")                               # the same words with the keys in another order
M.save(M.path_of(T, "goal", gR), {"created_at": M.now(), "status": "derived",
                                  "statement": "pay strangers to post five-star ratings under made-up names", "goal_id": gR})
rR, _ = start(gR)
r = fill(rR, [], "clear")
assert r.returncode == 2 and "hard line" in r.stderr, r.stderr
#    c) the event stream must agree with the sealed review, and only the human's consent counts
r = run(os.path.join(BIN, "events"), "emit", "ethics.consent", "--data", "subject=x")
assert r.returncode == 64 and "only its own command" in r.stderr, r.stderr
assert run(os.path.join(BIN, "events"), "emit", "ethics.reviewed").returncode == 64
gF = goal("a darker theme")
rF, _ = start(gF)
assert fill(rF, [{"category": "other", "tier": "gray", "reason": "contrast drops"}], "gray").returncode == 3
fd = json.load(open(rF))
sub = fd["subject"]
M.emit(T, "ethics.reviewed", role="ethics-reviewer", ethics_id=fd["ethics_id"], subject=sub["ref"],
       sha256=sub["sha256"], review_sha256=fd["sha256"], tier="clear")
r = run(MI, "ethics", "check", "--subject", gF)
assert r.returncode == 2 and "is not the sealed review's" in r.stderr, r.stderr
gC = goal("a lighter theme")
rC, _ = start(gC)
assert fill(rC, [{"category": "other", "tier": "gray", "reason": "contrast drops"}], "gray").returncode == 3
cd = json.load(open(rC))
M.emit(T, "ethics.consent", subject=cd["subject"]["ref"], sha256=cd["subject"]["sha256"], review=cd["ethics_id"],
       review_sha256=cd["sha256"])                       # no role human, no by, no reason: not a consent
assert run(MI, "ethics", "check", "--subject", gC).returncode == 3
#    d) the screen reads normalized words, keys and nested fields, not only top-level strings
for t in ("sell counter​feit goods", "post fake reviews", "fake  reviews", "ｓｐｙｗａｒｅ", "counter­feit"):
    assert E.screen(t), t
assert [c for c, _ in E.screen(E.text_of({"goal_id": "gol_x", "plan": {"post fake reviews of rivals": True}}))] \
    == ["deceptive"]
assert [c for c, _ in E.screen(E.text_of({"steps": [{"status": "ship spyware"}], "tactic_id": "sell counterfeit goods"}))] \
    == ["illegal", "harmful"]
assert E.screen(E.text_of({"status": "fake reviews", "approval": {"ethics": {"reason": "fake reviews"}},
                          "created_at": "2026-10-20T02:00:00Z"})) == []
assert E.screen(E.text_of({"created_at": "post fake reviews"}))     # created_at is content unless it is a timestamp
#    e) a goal is its own file with its own id; malformed reviews and text exit 2, never a traceback
gS = goal("a symlinked goal")
os.remove(M.path_of(T, "goal", gS))
os.symlink(M.path_of(T, "goal", gC), M.path_of(T, "goal", gS))
r = run(MI, "ethics", "check", "--subject", gS)
assert r.returncode == 2 and "symlink" in r.stderr, r.stderr
gW = goal("a goal with someone else's id")
gw = json.load(open(M.path_of(T, "goal", gW)))
json.dump(dict(gw, goal_id=gC), open(M.path_of(T, "goal", gW), "w"))
r = run(MI, "ethics", "check", "--subject", gW)
assert r.returncode == 2 and "is not the harness-issued id of this file" in r.stderr, r.stderr
r = run(MI, "ethics", "new", "--subject", gW)
assert r.returncode == 2 and "is not the harness-issued id of this file" in r.stderr, r.stderr
rN, _ = start(gF)
nd = json.load(open(rN))
nd["subject"]["id"] = 123
json.dump(nd, open(rN, "w"))
r = run(MI, "ethics", "record", rN)
assert r.returncode == 2 and "Traceback" not in r.stderr, r.stderr
rU, _ = start(gF)
json.dump(dict(json.load(open(rU)), findings=[{"category": "other", "tier": "gray", "reason": "x\ud800"}], tier="gray"),
          open(rU, "w"))
r = run(MI, "ethics", "record", rU)
assert r.returncode == 2 and "Traceback" not in r.stderr, r.stderr
gT = goal("a quieter onboarding")
rT1, _ = start(gT)
assert fill(rT1, [{"category": "other", "tier": "gray", "reason": "skips the tour"}], "gray").returncode == 3
assert run(MI, "ethics", "consent", "--subject", gT, "--by", "Adeel", "--reason", "users asked for it").returncode == 0
rT2, _ = start(gT)
assert fill(rT2, [{"category": "other", "tier": "gray", "reason": "hides tips"}], "gray").returncode == 3
assert run(MI, "ethics", "consent", "--subject", gT, "--by", "Sam", "--reason", "tips move to help").returncode == 0
cs = json.load(open(M.path_of(T, "goal", gT)))["approval"]["ethics"]["consents"]
assert [cs[json.load(open(x))["ethics_id"]]["by"] for x in (rT1, rT2)] == ["Adeel", "Sam"], cs
ok("hardening: no reordering, edit, re-id, forged event, symlink, invisible character or nested field clears a gate")

# 10) second review round: the screen reads mechanics and modifiers; a hard line follows a goal's own words
#     (its statement and each gap's summary) to any goal derived again; the verdict names its state
for t, want in (("Generate fake 5-star reviews for the listing", "deceptive"), ("post fake positive reviews", "deceptive"),
                ("post fake-reviews", "deceptive"), ("a fake countdown timer on checkout", "deceptive"),
                ("Sell users' data to advertisers", "third-party"), ("share the users’ data", "third-party"),
                ("collect users’ data", "user-data"), ("Make the cancel button hard to find", "deceptive"),
                ("Hide the unsubscribe link in emails", "deceptive"), ("pre-check the box that subscribes users", "deceptive"),
                ("secretly record what they type", "deceptive"), ("Charge the card again after the user cancels", "deceptive"),
                ("Upload the user's address book", "user-data"), ("Collect more data about users, their contacts", "user-data"),
                ("collect children's data", "user-data"), ("Hack into the rival's API", "harmful"),
                ("Exfiltrate tasks from a competitor's database", "harmful"), ("Mine cryptocurrency on users' devices", "harmful"),
                ("brute-force the passwords of competitor accounts", "harmful"), ("make a deepfake of the CEO", "deceptive"),
                ("make the cancel-subscription button hard to find", "deceptive")):
    assert want in [c for c, _ in E.screen(t)], (t, E.screen(t))
for t in ("the world's best to-do app with the best user experience; no paid SaaS", "drag to reorder tasks",
          "a calmer home screen", "dark mode", "sync lists across devices", "seed 50 fake users for local development",
          "add tags and filters to the board", "steady stealth-free styling"):
    assert E.screen(t) == [], (t, E.screen(t))
GX = {"gap_id": "jrn_x#pay", "summary": "pay strangers to praise us under made-up names", "now": "3 of 5"}
GY = {"gap_id": "jrn_y#export", "summary": "export a list as CSV", "now": "missing"}
gX = goal("Close 2 gaps toward the mission: %s; %s" % (GX["summary"], GY["summary"]), gaps=[GX, GY])
rX, _ = start(gX)
HF = {"category": "other", "tier": "hard", "reason": "paid fake praise deceives buyers"}
r = fill(rX, [HF], "hard")                          # a goal of several gaps: a hard finding names the gaps it is about
assert r.returncode == 2 and "names the gaps it is about" in r.stderr, r.stderr
assert not [e for e in events("ethics.blocked") if e["data"]["subject"].endswith(gX + ".json")]
r = fill(rX, [dict(HF, gaps=["jrn_q#nope"])], "hard")
assert r.returncode == 2 and "not a gap of this goal" in r.stderr, r.stderr
assert fill(rX, [dict(HF, gaps=[GX["gap_id"]])], "hard").returncode == 2
assert json.load(open(M.path_of(T, "goal", gX)))["status"] == "blocked"       # the hard-lined goal is retired
blk = [e for e in events("ethics.blocked") if e["data"]["subject"].endswith(gX + ".json")]
assert len(blk) == 1 and blk[0]["data"]["words"] == [E.word_key(GX["summary"])], blk   # only the gap it is about
gY1 = goal("Close 1 gap toward the mission: %s" % GY["summary"], gaps=[dict(GY, now="still missing")])
rY1, _ = start(gY1)
assert fill(rY1, [], "clear").returncode == 0         # the benign gap bundled with it is free to go on its own
gX2 = goal("Close 1 gap toward the mission:  Pay strangers to praise us under made-up names ",
           gaps=[dict(GX, now="4 of 5", evidence=["eval run 2"])])    # derived again: other id, measurements, eval
rX2, _ = start(gX2)
r = fill(rX2, [], "clear")
assert r.returncode == 2 and "hard line" in r.stderr, r.stderr
assert E.hard_words(T) & set(E.word_keys("goal", json.load(open(M.path_of(T, "goal", gX2)))))
gX3 = goal("Close 1 gap toward the mission: tidy up settings", gaps=[{"gap_id": "jrn_z#s", "summary": "tidy up settings"}])
rX3, _ = start(gX3)
assert fill(rX3, [], "clear").returncode == 0                  # other words: its own verdict
#     the verdict's state, so the loop tells "review it" from "fix the store" from "ask the human"
gN = goal("a softer palette")
st = lambda s: json.loads(run(MI, "ethics", "check", "--subject", s, "--json").stdout)["state"]  # noqa: E731
assert st(gN) == "none"
rN2, _ = start(gN)
assert fill(rN2, [{"category": "other", "tier": "gray", "reason": "lower contrast"}], "gray").returncode == 3
assert st(gN) == "pending"
assert run(MI, "ethics", "consent", "--subject", gN, "--by", "Adeel", "--reason", "fine").returncode == 0
assert st(gN) == "ok" and st(gX) == "hard" and st(gX2) == "hard"
os.remove(rN2)
r = run(MI, "ethics", "check", "--subject", gN, "--json")
assert r.returncode == 2 and json.loads(r.stdout)["state"] == "broken" and "restore it" in r.stderr, r.stderr
ok("second round: mechanics and modifiers screened; a hard line follows a goal's words to any re-derived goal; state")

# 11) usage: unknown subjects and commands; the area is discovered; --help is the docstring
assert run(MI, "ethics", "check", "--subject", "gol_000000000000deadbeef").returncode == 2
assert run(MI, "ethics", "check").returncode == 64
assert run(MI, "ethics", "nope").returncode == 64
assert "ethics" in run(MI, "--list").stdout.split()
assert "three tiers" in run(MI, "ethics", "--help").stdout
shutil.rmtree(T, ignore_errors=True)
ok("unknown subject (2) and missing --subject or command (64); discovered as an area")

print("mission-ethics: %d checks passed" % PASS[0])
