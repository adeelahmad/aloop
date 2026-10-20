#!/usr/bin/env python3
"""ROADMAP §4.12 (M9) M9.8: the mission loop. `mission tick` picks one action from harness
files (first match wins), `mission due` says whether a tick is worth a model call (zero
tokens: it reads files only), `mission cron` renders the crontab line and runs it (`--fire`,
under the loop lock), and the budget caps are read from the token ledger. Human decisions and
the baseline come from the committed ledger, the way `mission baseline` decides them, so the
baselines here are real: proposed, measured twice and confirmed through `aloop mission`.
    python3 scripts/test/mission-tick.test.py
"""
import contextlib
import fcntl
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
MI = os.path.join(BIN, "mission")
PASS = [0]
sys.path.insert(0, BIN)
sys.path.insert(0, os.path.join(ROOT, "mission"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "fixtures"))
import _mission as M  # noqa: E402
import mission_fixture as F  # noqa: E402
import tick as TICK  # noqa: E402

H = 3600 * 1000
SEQ = [0]
FAILING = {"seconds_to_first_task": 9.0, "working_steps": ["open", "add"], "tasks_shown": 0}
PASSING = {"seconds_to_first_task": 3.0, "working_steps": ["open", "add", "see"], "tasks_shown": 1}


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def iso(ms):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000))


class Project:
    """A throwaway git repo with a mission and the fixture's to-do "app" (a pinned journey runner
    reads app.json), plus helpers that write artifacts, events and ledger records at chosen times
    (ids carry the time, like the harness's)."""

    def __init__(self, mode="autonomous", budget=("--budget-usd", "40"), cadence="6h", veto=None):
        self.dir = tempfile.mkdtemp(prefix="aloop-tick-")
        self.env = dict(os.environ, HOME=os.path.join(self.dir, "home"))
        for k in ("AGENTIC_HOST", "ALOOP_MISSION_LOOP"):
            self.env.pop(k, None)
        subprocess.run(["git", "init", "-q"], cwd=self.dir, check=True)
        os.makedirs(os.path.join(self.dir, "tools"))
        with open(os.path.join(self.dir, "tools", "journey.py"), "w") as f:
            f.write(F.RUNNER)
        self.app(**FAILING)
        self.mode, self.budget, self.cadence, self.veto = mode, budget, cadence, veto
        self.t0 = int(time.time() * 1000)
        self.rid = None

    def run(self, *args, env=None):
        return subprocess.run(args, cwd=self.dir, env=env or self.env, capture_output=True, text=True)

    def mi(self, *args):
        """`aloop mission …` as a role runs it, with a human who types every grant it asks for (M9.17)."""
        return F.as_human(lambda: self.run(MI, *args), self.dir, self.env)

    def app(self, **state):
        with open(os.path.join(self.dir, "app.json"), "w") as f:
            json.dump(state, f)

    def init(self, approve=True):
        extra = ["--veto-window-h", str(self.veto)] if self.veto is not None else []
        r = self.mi("init", "the world's best to-do app", "--mode", self.mode, *self.budget, "--cadence", self.cadence,
                    "--by", "Adeel", *extra)
        assert r.returncode == 0, r.stderr
        if approve:
            assert self.mi("approve", "--by", "Adeel").returncode == 0
        self.t0 = int(self.mission_doc()["mission_id"][4:16], 16)
        return self

    def at(self, hours):
        return self.t0 + int(hours * H)

    def art(self, kind, status, hours, **fields):
        SEQ[0] += 1
        aid = "%s_%012x%08x" % (M.PREFIX[kind], self.at(hours), SEQ[0])
        doc = dict({kind + "_id": aid, "status": status, "created_at": iso(self.at(hours))}, **fields)
        M.save(M.path_of(self.dir, kind, aid), doc)
        return aid, doc

    def ref(self, kind, aid=""):
        return M.rel(self.dir, M.path_of(self.dir, kind, aid))

    def load(self, kind, aid=""):
        return json.load(open(M.path_of(self.dir, kind, aid)))

    def event(self, name, hours, role=None, **data):
        p = os.path.join(self.dir, ".aladdin", "logs", "events.jsonl")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        SEQ[0] += 1
        with open(p, "a") as f:
            f.write(json.dumps({"schema": "aladdin.event/1", "event_id": "e%d" % SEQ[0], "ts": iso(self.at(hours)),
                                "event": name, "ids": {}, "role": role, "refs": {}, "data": data}) + "\n")

    def review(self, subject, doc, tier, hours, **data):
        self.event("ethics.reviewed", hours, subject=subject, sha256=M.digest(doc), tier=tier, **data)

    @contextlib.contextmanager
    def clock(self, hours):
        real = M.now
        M.now = lambda: iso(self.at(hours))
        try:
            yield
        finally:
            M.now = real

    def approve(self, event, subject, doc, hours, **data):
        """A human approval as `mission … --by` records it: the human's typed line (M9.17), then a ledger
        row (role human), at that time."""
        F.say(self.dir, self.env, "%s %s" % (M.grant_verb(event, data), M.code(M.digest(doc))))
        with self.clock(hours):
            return M.approve(self.dir, event, subject, M.digest(doc), "Adeel", **data)

    def mission_ref(self):
        return self.ref("mission")

    def mission_doc(self):
        return self.load("mission")

    def research(self):
        self.rid = F.Project.research(self, ["c1", "c2"])
        return self.rid

    def journey(self, **over):
        p = os.path.join(self.dir, "draft-%d.json" % SEQ[0])
        SEQ[0] += 1
        json.dump(F.Project.draft(self, self.rid, **over), open(p, "w"))
        r = self.mi("journeys", "new", "--from", p)
        assert r.returncode == 0, r.stderr
        return json.load(open(r.stdout.strip()))["journey_id"]

    def baseline(self, *extra, measure=True):
        """A proposed baseline (measured twice when `measure`); a replacement names the current one."""
        cur = TICK.B.current_id(self.dir)
        sup = ["--supersedes", cur, "--evidence", self.rid + "#c2"] if cur else []
        r = self.mi("baseline", "new", "--definition", "a first task in under 5 seconds, and it shows up", *sup,
                    *extra)
        assert r.returncode == 0, r.stderr
        bid = json.load(open(r.stdout.strip()))["baseline_id"]
        if measure:
            r = self.mi("baseline", "measure", bid)
            assert r.returncode == 0, r.stderr
        return bid

    def confirmed(self):
        """Research, a journey and a baseline the human confirmed, all through `aloop mission`."""
        self.research()
        self.journey()
        bid = self.baseline()
        r = self.mi("baseline", "confirm", bid, "--by", "Adeel")
        assert r.returncode == 0, r.stderr
        return bid

    def sprint_closed(self, n, hours):
        p = os.path.join(self.dir, ".aladdin", "state", "sprints.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        try:
            st = json.load(open(p))
        except (OSError, ValueError):
            st = {"closed": []}
        st["closed"].append({"sprint": n, "at": iso(self.at(hours)), "dir": "docs/agents/sprint%d" % n})
        json.dump(st, open(p, "w"))

    def ledger(self, rows):
        d = os.path.join(self.dir, ".aladdin", "ledger")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "s1.jsonl"), "a") as f:
            for hours, model, i, cr, cw, o in rows:
                f.write(json.dumps({"ts": iso(self.at(hours))[:-1] + ".123Z", "agent": "main", "agent_type": "supervisor",
                                    "model": model, "in": i, "cache_read": cr, "cache_write": cw, "out": o,
                                    "session": "s1"}) + "\n")

    def tick(self, hours, *extra, env=None):
        r = self.run(MI, "tick", "--json", "--now", iso(self.at(hours)), *extra, env=env)
        try:
            d = json.loads(r.stdout)
        except ValueError:
            raise AssertionError("tick printed no JSON: %r %r" % (r.stdout, r.stderr))
        d["rc"] = r.returncode
        return d

    def due(self, hours):
        r = self.mi("due", "--json", "--now", iso(self.at(hours)))
        d = json.loads(r.stdout)
        d["rc"] = r.returncode
        return d

    def events(self):
        p = os.path.join(self.dir, ".aladdin", "logs", "events.jsonl")
        return [json.loads(l) for l in open(p)] if os.path.exists(p) else []

    def ticks(self):
        return [e for e in self.events() if e["event"] == "mission.tick"]


def expect(d, action, rc=None, why=None):
    assert d["action"] == action, "expected %s, got %s — %s" % (action, d["action"], d.get("reason"))
    if rc is not None:
        assert d["rc"] == rc, "%s: exit %s, expected %s" % (action, d["rc"], rc)
    if why:
        assert why in d["reason"], "%s: %r not in %r" % (action, why, d["reason"])


def cleared(p, hours=0.1):
    """A project through the mission's ethics review, research, a journey and a confirmed baseline."""
    p.review(p.mission_ref(), p.mission_doc(), "clear", hours)
    return p.confirmed()


def active_goal(p, hours, status="active"):
    gid, gdoc = p.art("goal", status, hours)
    p.review(p.ref("goal", gid), gdoc, "clear", hours)
    return gid, gdoc


# 1) the lifecycle, autonomous, through the real areas: mission → ethics → research → journeys →
#    baseline (proposed, measured twice, confirmed) → assess → evaluate → goal → ethics → intents →
#    sprint → wait → sprint closed → assess again
p = Project()
d = p.tick(0)
expect(d, "SET_MISSION", 3)
assert d["who"] == "human"
p.init(approve=False)
expect(p.tick(0), "CONFIRM_MISSION", 3, "draft")
assert p.mi("approve", "--by", "Adeel").returncode == 0
d = p.tick(0.01)
expect(d, "ETHICS_REVIEW", 0)
assert d["roles"] == ["ethics-reviewer"] and d["subject"] == "docs/agents/mission/mission.json", d
p.review(p.mission_ref(), p.mission_doc(), "clear", 0.02)
d = p.tick(0.03)
expect(d, "RESEARCH", 0)
assert d["roles"] == ["market-researcher"] and d["who"] == "role"
p.research()
expect(p.tick(0.05), "DEFINE_JOURNEYS", 0)
p.journey()
d = p.tick(0.07)
expect(d, "BASELINE", 0, "no baseline yet")
assert d["roles"] == ["state-assessor", "product-tester"], d["roles"]
bid = p.baseline(measure=False)
expect(p.tick(0.08), "BASELINE", 0, "measure it")
assert p.mi("baseline", "measure", bid).returncode == 0
d = p.tick(0.09)
expect(d, "CONFIRM_BASELINE", 3, "is measured")
assert d["subject"] == p.ref("baseline", bid)
bdoc = p.load("baseline", bid)
bdoc["status"] = "confirmed"                                               # a status is not a confirmation
M.save(M.path_of(p.dir, "baseline", bid), bdoc)
d = p.tick(0.10)
assert d["action"] == "BASELINE" and "only a measured version" in d["reason"], d
bdoc["status"] = "measured"
M.save(M.path_of(p.dir, "baseline", bid), bdoc)
assert p.mi("baseline", "confirm", bid, "--by", "Adeel").returncode == 0
expect(p.tick(0.11), "ASSESS", 0, "no state report")
assert p.mi("assess").returncode == 0
d = p.tick(0.12)
expect(d, "EVALUATE", 0, "no eval")
assert d["roles"] == ["evaluator"]
assert p.mi("evaluate").returncode == 0
d = p.tick(0.13)
expect(d, "DERIVE_GOAL", 0, "gaps")
assert d["roles"] == ["mission-planner"]
gid, gdoc = p.art("goal", "derived", 0.18)
d = p.tick(0.19)
expect(d, "ETHICS_REVIEW", 0)
assert d["subject"] == p.ref("goal", gid), d["subject"]
p.review(p.ref("goal", gid), gdoc, "clear", 0.20)
expect(p.tick(0.21), "PLAN_INTENTS", 0)
gdoc["status"] = "active"
M.save(M.path_of(p.dir, "goal", gid), gdoc)
d = p.tick(0.22)
expect(d, "SPRINT", 0)
assert d["who"] == "supervisor", d
p.event("plan.created", 0.5, sprint="S1")
expect(p.tick(0.6), "WAIT", 4, "sprint 1")
p.sprint_closed(1, 2)
expect(p.tick(2.1), "ASSESS", 0, "sprint 1 closed")
p.art("state", "recorded", 2.2, baseline_id=bid)
expect(p.tick(2.3), "EVALUATE", 0, "state report")
p.art("eval", "recorded", 2.4, goal_met=False, baseline_id=bid)
expect(p.tick(2.5), "DERIVE_GOAL", 0)
ok("lifecycle through the real areas: mission → ethics → research → journeys → baseline (measured twice, "
   "confirmed) → assess → evaluate → goal → intents → sprint → wait → re-assess")

# 2) the tick records every decision (mission.tick) and --peek records nothing
ticks = p.ticks()
assert len(ticks) == 19 and {"action", "key", "facts", "reason", "at"} <= set(ticks[-1]["data"]), len(ticks)
assert ticks[-1]["data"]["action"] == "DERIVE_GOAL"
r = p.run(MI, "tick", "--peek", "--now", iso(p.at(2.5)))
assert r.returncode == 0 and r.stdout.startswith("DERIVE_GOAL"), r.stdout
assert len(p.ticks()) == len(ticks)
ok("every tick records mission.tick (action, key, facts fingerprint, reason); --peek records nothing")

# 3) a fresh checkout decides the same: human decisions and the baseline live in the committed ledger;
#    an approval only the run log has (no ledger row) is not one
p = Project().init()
cleared(p)
shutil.rmtree(os.path.join(p.dir, ".aladdin"))                             # run data is git-ignored
p.review(p.mission_ref(), p.mission_doc(), "clear", 0.2)                   # ethics reviews are run data (M9.4)
expect(p.tick(0.3), "ASSESS", 0, "no state report")                        # not CONFIRM_*: the ledger has them
q = Project().init(approve=False)
q.event("mission.approved", 0.1, role="human", subject=q.mission_ref(), sha256=M.digest(q.mission_doc()), by="Adeel")
expect(q.tick(0.2), "CONFIRM_MISSION", 3)
ok("a fresh checkout keeps every human decision and the baseline (the ledger); a run-log-only approval is none")

# 4) ethics on the mission: hard line blocks for good, gray asks once per content, a change re-reviews
p = Project().init()
p.review(p.mission_ref(), p.mission_doc(), "hard", 0.1)
expect(p.tick(0.2), "BLOCKED", 2, "hard line")
p.review(p.mission_ref(), p.mission_doc(), "clear", 0.3)                   # shopping for a better verdict
expect(p.tick(0.4), "BLOCKED", 2, "hard line")
p = Project().init()
p.review(p.mission_ref(), p.mission_doc(), "gray", 0.1, ethics_id="eth_a", review_sha256="1" * 64)
d = p.tick(0.2)
expect(d, "ASK_CONSENT", 3)
assert d["subject"] == p.mission_ref()
p.approve("ethics.consent", p.mission_ref(), p.mission_doc(), 0.3, review="eth_a", review_sha256="2" * 64)
expect(p.tick(0.35), "ASK_CONSENT", 3)                                     # it names another version of the review
p.approve("ethics.consent", p.mission_ref(), p.mission_doc(), 0.36, review="eth_a", review_sha256="1" * 64)
expect(p.tick(0.4), "RESEARCH", 0)
p.review(p.mission_ref(), p.mission_doc(), "gray", 0.41, ethics_id="eth_b", review_sha256="3" * 64)
expect(p.tick(0.42), "ASK_CONSENT", 3)                                     # every gray review needs its consent
p.approve("ethics.consent", p.mission_ref(), p.mission_doc(), 0.43, review="eth_b", review_sha256="3" * 64)
expect(p.tick(0.44), "RESEARCH", 0)
doc = p.mission_doc()
doc["constraints"] = ["no dark patterns"]
doc["status"] = "draft"
M.save(M.path_of(p.dir, "mission"), doc)
expect(p.tick(0.5), "CONFIRM_MISSION", 3)
assert p.mi("approve", "--by", "Adeel").returncode == 0
expect(p.tick(0.6), "ETHICS_REVIEW", 0)                                    # new content, new review
ok("mission ethics: a hard line is never unlocked; gray waits for a consent naming every gray review of that "
   "content; a changed mission is re-reviewed")

# 5) ethics on goals: gray consent does not carry over; hard lines re-derive, 3 failed goals then BLOCKED
p = Project().init()
cleared(p)
p.art("state", "recorded", 1)
p.art("eval", "recorded", 1.1, goal_met=False)
gid, gdoc = p.art("goal", "derived", 1.2)
p.review(p.ref("goal", gid), gdoc, "gray", 1.3)
d = p.tick(1.4)
expect(d, "ASK_CONSENT", 3)
assert d["subject"] == p.ref("goal", gid)
p.approve("ethics.consent", p.ref("goal", gid), gdoc, 1.5)
expect(p.tick(1.6), "PLAN_INTENTS", 0)
gdoc["status"] = "blocked"
M.save(M.path_of(p.dir, "goal", gid), gdoc)
expect(p.tick(1.7), "DERIVE_GOAL", 0, "blocked")
g2, g2doc = p.art("goal", "derived", 1.8)
p.review(p.ref("goal", g2), g2doc, "gray", 1.9)
expect(p.tick(2.0), "ASK_CONSENT", 3)                                      # the consent stayed with the first goal
p.event("ethics.blocked", 2.1, subject=p.ref("goal", g2), sha256=M.digest(g2doc))
expect(p.tick(2.2), "DERIVE_GOAL", 0, "hard line")
g3, g3doc = p.art("goal", "derived", 2.3)
p.review(p.ref("goal", g3), g3doc, "hard", 2.4)
d = p.tick(2.5)
expect(d, "BLOCKED", 2, "3 goals")
assert p.mi("tick", "--force").returncode == 64                            # lifting a block names the human
r = p.mi("tick", "--force", "--by", "Adeel", "--now", iso(p.at(2.6)))
assert r.returncode == 0 and r.stdout.startswith("FORCE"), (r.stdout, r.stderr)
assert p.ticks()[-1]["role"] == "human" and p.ticks()[-1]["data"]["by"] == "Adeel"
expect(p.tick(2.7), "DERIVE_GOAL", 0)                                      # the count starts again
ok("goal ethics: consent is bound to one goal's content; a hard line re-derives; 3 failed goals block until "
   "the human lifts it (--force --by, recorded)")

# 6) spoon-fed: the goal's options and the sprint plan wait for the human; so does every baseline version
p = Project(mode="spoon-fed").init()
bid = cleared(p)
p.art("state", "recorded", 1)
p.art("eval", "recorded", 1.1, goal_met=False)
gid, gdoc = active_goal(p, 1.2, "derived")
d = p.tick(1.4)
expect(d, "OFFER_OPTIONS", 3)
assert d["roles"] == ["mission-planner"] and d["who"] == "human", d
gdoc["status"] = "approved"
M.save(M.path_of(p.dir, "goal", gid), gdoc)
expect(p.tick(1.5), "OFFER_OPTIONS", 3)                                    # a status is not a pick
p.event("goal.approved", 1.55, role="human", subject=p.ref("goal", gid), sha256=M.digest(gdoc), by="Adeel")
expect(p.tick(1.56), "OFFER_OPTIONS", 3)                                   # nor is a run-log event
p.approve("goal.approved", p.ref("goal", gid), gdoc, 1.6)
expect(p.tick(1.7), "PLAN_INTENTS", 0)
gdoc["status"] = "active"
M.save(M.path_of(p.dir, "goal", gid), gdoc)
expect(p.tick(1.8), "SPRINT", 0)
p.event("plan.created", 1.9, sprint="S1")
expect(p.tick(2.0), "APPROVE_PLAN", 3, "sprint 1")
p.event("plan.approved", 2.1, sprint="S1")
expect(p.tick(2.2), "WAIT", 4)
b2 = p.baseline()
expect(p.tick(2.3), "CONFIRM_BASELINE", 3, "spoon-fed")
ok("spoon-fed: OFFER_OPTIONS until a ledger goal.approved covers the goal's content; APPROVE_PLAN before the "
   "sprint runs; a replacement baseline waits for the human")

# 7) autonomous replacement baseline: the cycle goes on in the veto window, then it is applied;
#    a veto binds the content; a looser tolerance is the human's
p = Project().init()
bid = cleared(p)
b2 = p.baseline()
expect(p.tick(2), "ASSESS", 0)
d = p.tick(25.5)
expect(d, "BASELINE", 0, "veto window")
assert "baseline apply %s" % b2 in d["reason"] and d["subject"] == p.ref("baseline", b2), d
assert p.mi("baseline", "apply", b2).returncode == 3                       # the real clock: the window is open
assert p.mi("baseline", "veto", b2, "--by", "Adeel", "--reason", "not yet").returncode == 0
expect(p.tick(26), "ASSESS", 0)
p = Project(veto=0).init()
bid = cleared(p)
assert p.mi("assess").returncode == 0
b2 = p.baseline()
expect(p.tick(0.2), "BASELINE", 0, "apply it")
assert p.mi("baseline", "apply", b2).returncode == 0
expect(p.tick(0.3), "ASSESS", 0, "another baseline")                       # the state measured the old version
b3 = p.baseline("--tolerance", "0.2")
expect(p.tick(0.4), "CONFIRM_BASELINE", 3, "more drift")
ok("autonomous: a replacement applies after the mission's veto window unless vetoed; then the product is "
   "re-assessed against it; a looser tolerance waits for the human")

# 8) the current baseline needs a new version (the mission changed): propose one; in its veto window the
#    loop waits (assess would refuse the old one), then it applies
p = Project().init()
bid = cleared(p)
doc = p.mission_doc()
doc["constraints"] = ["works offline"]
doc["status"] = "draft"
M.save(M.path_of(p.dir, "mission"), doc)
assert p.mi("approve", "--by", "Adeel").returncode == 0
p.review(p.mission_ref(), p.mission_doc(), "clear", 0.5)
d = p.tick(1)
expect(d, "BASELINE", 0, "needs a new version")
assert d["subject"] == p.ref("baseline", bid)
b2 = p.baseline()
d = p.tick(2)
expect(d, "WAIT", 4, "veto window")
assert d["next_check"] and d["next_check"] > iso(p.at(23)), d
expect(p.tick(25), "BASELINE", 0, "apply it")
ok("a mission change makes the baseline propose a new version; the loop waits out its veto window, then applies it")

# 9) a measurement that fails is not progress: flaky runs re-measure, nothing to earn raises the bar,
#    and the repeat guard sees the same work although each failed measurement rewrites the file
p = Project().init()
p.review(p.mission_ref(), p.mission_doc(), "clear", 0.1)
p.research()
p.journey()
bid = p.baseline(measure=False)
open(os.path.join(p.dir, "flaky"), "w").close()
assert p.mi("baseline", "measure", bid).returncode == 2
expect(p.tick(0.2), "BASELINE", 0, "disagreed")
os.remove(os.path.join(p.dir, "flaky"))
p.app(**PASSING)
keys = set()
for i in range(3):
    assert p.mi("baseline", "measure", bid).returncode == 2
    d = p.tick(0.3 + i * 0.01)
    expect(d, "DEFINE_JOURNEYS", 0, "nothing to earn")
    keys.add(d["key"])
assert p.mi("baseline", "measure", bid).returncode == 2
expect(p.tick(0.4), "BLOCKED", 2, "3 times")
assert len(keys) == 1, keys
ok("a flaky measurement is measured again; nothing to earn sends the researchers to raise the bar; the repeat "
   "guard binds a version's content, not its rewritten file")

# 10) broken records stop the loop: an edited ledger row, a confirmed baseline edited after confirmation
p = Project().init()
bid = cleared(p)
bdoc = p.load("baseline", bid)
bdoc["definition_of_best"] = "something easier"
M.save(M.path_of(p.dir, "baseline", bid), bdoc)
expect(p.tick(1), "BLOCKED", 2, "changed after it was confirmed")
lp = M.ledger(p.dir)
rows = open(lp).read().splitlines()
row = json.loads(rows[0])
row["data"]["by"] = "Mallory"
open(lp, "w").write("\n".join([json.dumps(row, sort_keys=True)] + rows[1:]) + "\n")
expect(p.tick(1.1), "BLOCKED", 2, "ledger fails its check")
ok("an edited ledger row or a confirmed baseline changed after confirmation blocks the loop (no grant lifts it)")

# 11) budget caps from the token ledger: tokens (uncached input, cache writes, output), USD (priced per
#     model), deadline. On Claude Code nearly all new input arrives as cache writes, so they count.
p = Project(budget=("--budget-tokens", "1000")).init()
cleared(p)
p.ledger([(-1, "claude-opus-5-5", 5000, 0, 0, 5000)])                     # before the mission: not charged
p.ledger([(0.5, "claude-sonnet-5-5", 400, 90000, 0, 500)])
expect(p.tick(1), "ASSESS", 0)                                             # 900 < 1000 (cache reads are not)
p.ledger([(0.6, "claude-sonnet-5-5", 50, 0, 40, 20)])
d = p.tick(1)
expect(d, "STOP_BUDGET", 3, "1010")
assert d["budget"]["tokens"] == 1010, d["budget"]
p.tick(1.1)
assert len([e for e in p.events() if e["event"] == "mission.budget_exhausted"]) == 1
doc = p.mission_doc()
doc["budget"]["tokens"] = 5000
doc["status"] = "draft"
M.save(M.path_of(p.dir, "mission"), doc)
assert p.mi("approve", "--by", "Adeel", "--reason", "raise the cap").returncode == 0
p.review(p.mission_ref(), p.mission_doc(), "clear", 1.2)
d = p.tick(1.3)
assert d["action"] != "STOP_BUDGET", d                                     # the raised cap resumes the loop
ok("token cap: uncached input + cache writes + output since the mission was set; one budget_exhausted event; "
   "a raised cap resumes")

usd, unpriced = TICK.price([{"model": "claude-opus-5-5", "in": 1_000_000, "cache_read": 1_000_000, "cache_write": 1_000_000,
                             "out": 1_000_000},
                            {"model": "anthropic/claude-sonnet-5-5", "in": 1_000_000, "cache_read": 0, "cache_write": 0, "out": 0},
                            {"model": "gpt-9-turbo", "in": 1_000_000, "cache_read": 0, "cache_write": 0, "out": 0},
                            {"model": "<synthetic>", "in": 0, "cache_read": 0, "cache_write": 0, "out": 0}])
assert abs(usd - (4 + 0.20 + 8 + 20 + 2 + 15)) < 1e-9, usd                 # unknown: the highest known rate ($15)
assert unpriced == ["gpt-9-turbo"], unpriced
for model, rate in (("openrouter/anthropic/claude-opus-4.5", "claude-opus-4-5"), ("claude-opus-5.5", "claude-opus-5-5"),
                    ("github-copilot/claude-haiku-4.5", "claude-haiku-4-5"),
                    ("global.anthropic.claude-sonnet-4-5-20250929-v1:0", "claude-sonnet-4"),
                    ("us-gov.anthropic.claude-sonnet-4-5-20250929-v1:0", "claude-sonnet-4"),
                    ("apac.anthropic.claude-sonnet-4-5-20250929-v1:0", "claude-sonnet-4"),
                    ("eu.anthropic.claude-opus-4-5-20251101-v1:0", "claude-opus-4-5"),
                    ("claude-opus-4-5@20251101", "claude-opus-4-5")):
    assert TICK._rate(model) == TICK.PRICES[rate], (model, TICK._rate(model))
p = Project(budget=("--budget-usd", "5")).init()
cleared(p)
p.ledger([(0.5, "claude-opus-5-5", 1_000_000, 0, 0, 40_000)])
expect(p.tick(1), "ASSESS", 0)                                             # $4.80
p.ledger([(0.6, "claude-opus-5-5", 0, 0, 0, 20_000)])
expect(p.tick(1), "STOP_BUDGET", 3, "$5.20")
p = Project(budget=("--budget-usd", "50", "--deadline", "2000-01-01")).init()
expect(p.tick(0), "STOP_BUDGET", 3, "deadline")
ok("USD cap priced per model (dotted, routed and Bedrock ids too; unknown at the highest rate); a passed deadline "
   "stops the loop")

# 12) cadence: a running sprint waits, re-assesses on the cadence; a met goal raises the bar once per cadence
p = Project(cadence="6h").init()
cleared(p)
p.art("state", "recorded", 1)
p.art("eval", "recorded", 1.1, goal_met=False)
gid, gdoc = active_goal(p, 1.2)
expect(p.tick(1.3), "SPRINT", 0)
p.event("plan.created", 1.5, sprint="S1")
d = p.tick(3)
expect(d, "WAIT", 4)
assert d["next_check"] == iso(p.at(7)), d
expect(p.tick(7.1), "ASSESS", 0, "cadence")
p.art("state", "recorded", 7.2)
expect(p.tick(7.3), "EVALUATE", 0)
p.art("eval", "recorded", 7.4, goal_met=True)
expect(p.tick(7.5), "WAIT", 4)                                             # mid-sprint: the sprint finishes first
p.sprint_closed(1, 8)
expect(p.tick(8.1), "ASSESS", 0)
p.art("state", "recorded", 8.2)
expect(p.tick(8.3), "EVALUATE", 0)
p.art("eval", "recorded", 8.4, goal_met=True)
expect(p.tick(8.5), "RESEARCH", 0, "raise the bar")
p.art("research", "recorded", 9)
expect(p.tick(9.1), "EVALUATE", 0, "research")
p.art("eval", "recorded", 9.2, goal_met=True)
expect(p.tick(9.3), "WAIT", 4, "still met")
expect(p.tick(15.5), "ASSESS", 0, "cadence")
p.art("state", "recorded", 15.6)
p.art("eval", "recorded", 15.7, goal_met=True)
expect(p.tick(15.8), "RESEARCH", 0, "raise the bar")
p.art("eval", "recorded", 15.9, goal_met=False)                            # gaps found: derive
expect(p.tick(16), "DERIVE_GOAL", 0)
ok("cadence: re-assess mid-sprint, act at the close; a met goal raises the bar at most once per cadence")

# 13) an active goal's sprints: its intents are sprinted one by one (intent.received goal_id →
#     plan.created intent_id); a fresh sprint lock is planning under way; a silent sprint has stalled
p = Project().init()
cleared(p)
p.art("state", "recorded", 1)
p.art("eval", "recorded", 1.1, goal_met=False)
gid, gdoc = active_goal(p, 1.2)
p.event("plan.created", 0.5, sprint="S1")                                  # an older sprint is not this goal's
expect(p.tick(1.3), "SPRINT", 0, "no sprint was planned")
lock = os.path.join(p.dir, ".aladdin", "state", "sprint.lock")
os.makedirs(os.path.dirname(lock), exist_ok=True)
open(lock, "w").write("s\n")
os.utime(lock, (p.at(1.4) / 1000, p.at(1.4) / 1000))
expect(p.tick(1.5), "WAIT", 4, "being planned")
os.remove(lock)
p.event("intent.received", 1.6, goal_id=gid, intent_id="I1")
p.event("intent.received", 1.6, goal_id=gid, intent_id="I2")
p.event("plan.created", 1.7, sprint="S2", intent_id="I1")
expect(p.tick(1.8), "WAIT", 4, "sprint 2")
expect(p.tick(14), "BLOCKED", 2, "no activity")                            # 12h without a sign of life
p.sprint_closed(2, 14.1)
expect(p.tick(14.2), "SPRINT", 0, "intent I2")
p.event("plan.created", 14.3, sprint="S3", intent_id="I2")
p.sprint_closed(3, 15)
expect(p.tick(15.1), "ASSESS", 0, "sprint 3 closed")
ok("an active goal's intents are sprinted one by one; a fresh sprint lock waits; 12h without activity is a "
   "stalled sprint for the human")

# 14) the repeat guard: the same action on the same facts is handed out 3 times, then BLOCKED; the
#     human's --force --by hands it out again (recorded, so any session sees it)
p = Project().init()
p.review(p.mission_ref(), p.mission_doc(), "clear", 0.1)
for i in range(3):
    expect(p.tick(0.2 + i * 0.01), "RESEARCH", 0)
d = p.tick(0.3)
expect(d, "BLOCKED", 2, "3 times")
assert p.mi("tick", "--force", "--by", "", "--now", iso(p.at(0.31))).returncode == 64
assert p.mi("tick", "--force", "--by", "Adeel", "--now", iso(p.at(0.31))).returncode == 0
expect(p.tick(0.32), "RESEARCH", 0)
p.research()
expect(p.tick(0.34), "DEFINE_JOURNEYS", 0)
r = p.mi("tick", "--force", "--by", "Adeel", "--now", iso(p.at(0.35)))
assert r.returncode == 0 and "nothing is blocked" in r.stderr, (r.returncode, r.stderr)
q = Project().init()
q.review(q.mission_ref(), q.mission_doc(), "hard", 0.1)
r = q.mi("tick", "--force", "--by", "Adeel", "--now", iso(q.at(0.2)))
assert r.returncode == 2 and "not lifted by --force" in r.stderr, r.stderr  # a hard line is not a repeat
ok("an action handed out 3 times on the same facts blocks; --force --by (recorded) hands it out again; a hard "
   "line is not lifted that way")

# 15) due: zero tokens, read-only; a handout from an interactive session is leased 2h, one from a
#     finished cron session is due again at once; a human action exits 3 once, then 1; a cron session
#     holding the loop lock makes nothing due
p = Project().init()
p.review(p.mission_ref(), p.mission_doc(), "clear", 0.1)
n = len(p.events())
d = p.due(0.2)
assert d["rc"] == 0 and d["due"] is True and d["action"] == "RESEARCH", d
assert len(p.events()) == n                                                # due records nothing
p.tick(0.3)
d = p.due(0.5)
assert d["rc"] == 1 and "handed out" in d["reason"], d
assert p.due(2.4)["rc"] == 0                                               # the 2h lease lapsed: retry
p.tick(2.5, env=dict(p.env, ALOOP_MISSION_LOOP="4242"))
assert p.due(2.6)["rc"] == 0                                               # that cron session has ended
os.makedirs(os.path.join(p.dir, ".aladdin", "state"), exist_ok=True)
fd = os.open(os.path.join(p.dir, ".aladdin", "state", "mission-loop.lock"), os.O_RDWR | os.O_CREAT)
fcntl.flock(fd, fcntl.LOCK_EX)
os.write(fd, json.dumps({"pid": 4242, "host": "claude", "action": "RESEARCH", "at": "now"}).encode())
d = p.due(2.7)
assert d["rc"] == 1 and "session is running (pid 4242" in d["reason"], d
os.close(fd)
assert p.due(2.8)["rc"] == 0
p = Project().init(approve=False)
r = p.mi("due", "--now", iso(p.at(0)))
assert r.returncode == 3 and r.stdout.startswith("[mission] the human is needed: CONFIRM_MISSION"), r.stdout
assert p.ticks()[-1]["data"]["action"] == "CONFIRM_MISSION"               # due recorded the tick itself
d = p.due(0.2)
assert d["rc"] == 1 and "already told" in d["reason"], d
p = Project().init()
cleared(p)
p.art("state", "recorded", 1)
p.art("eval", "recorded", 1.1, goal_met=False)
active_goal(p, 1.2)
p.tick(1.3)
p.event("plan.created", 1.5, sprint="S1")
assert p.due(3)["rc"] == 1 and p.due(7.5)["rc"] == 0                        # waiting, then the cadence is due
r = p.mi("due", "-q", "--now", iso(p.at(3)))
assert r.returncode == 1 and r.stdout == "" and r.stderr == ""
p = Project(budget=("--budget-tokens", "100")).init()                      # at a cap no session starts:
p.ledger([(0.5, "claude-opus-5-5", 50, 0, 0, 60)])                         # due records the stop itself, once
r = p.mi("due", "-q", "--now", iso(p.at(1)))
assert r.returncode == 3 and "STOP_BUDGET" in r.stdout and "110" in r.stdout, (r.returncode, r.stdout)
assert [e["data"]["action"] for e in p.ticks()] == ["STOP_BUDGET"]
assert len([e for e in p.events() if e["event"] == "mission.budget_exhausted"]) == 1
r = p.mi("due", "-q", "--now", iso(p.at(1.5)))
assert r.returncode == 1 and r.stdout == "" and len(p.ticks()) == 1
DUE = __import__("due")
real = DUE.T.decide
DUE.T.decide = lambda *a, **k: 1 / 0                                         # any crash: exit 2 with a BLOCK line
err = io.StringIO()
try:
    with contextlib.redirect_stderr(err):
        assert DUE.main(["-q"], p.dir) == 2
finally:
    DUE.T.decide = real
assert err.getvalue().startswith("BLOCK[mission-due]: ") and "ZeroDivisionError" in err.getvalue(), err.getvalue()
ok("due: reads files only; leases an interactive handout 2h, a finished cron session's not at all; a held loop "
   "lock is a running session; a human action (a cap too) exits 3 once; a crash exits 2, not 'not due'")

# 16) cron: the crontab line runs `mission cron --fire`, which decides like due under the loop lock and
#     starts the host session only when a tick is due; install is idempotent, remove undoes it
p = Project(cadence="6h").init()
r = p.mi("cron")
assert r.returncode == 0, r.stderr
line = r.stdout.strip().splitlines()[0]
aloop = os.path.join(BIN, "aloop")
assert line.startswith("*/30 * * * * cd ") and ("exec %s mission cron --fire --host claude" % aloop) in line, line
assert line.rstrip().endswith("# aloop-mission %s" % os.path.realpath(p.dir)), line
assert p.mi("cron", "--every", "7m").returncode == 64
assert p.mi("cron", "--every", "12h").returncode == 64                     # longer than the 6h cadence
assert p.mi("cron", "--host", "vim").returncode == 64
spec = json.loads(p.mi("cron", "--json").stdout)
assert spec["schedule"] == "*/30 * * * *" and spec["routine"]["schedule"] == "0 */6 * * *", spec
assert "aloop mission due" in spec["routine"]["prompt"] and "Exit 3" in spec["routine"]["prompt"], spec["routine"]
cronbin = os.path.join(p.dir, "fakebin")
os.makedirs(cronbin)
tab = os.path.join(p.dir, "crontab.txt")
open(tab, "w").write("0 3 * * * backup\n")
with open(os.path.join(cronbin, "crontab"), "w") as f:
    f.write('#!/bin/sh\nif [ "$1" = "-l" ]; then cat "%s"; else cat > "%s"; fi\n' % (tab, tab))
os.chmod(os.path.join(cronbin, "crontab"), stat.S_IRWXU)
seen = os.path.join(p.dir, "host-ran.txt")
for host in ("claude", "codex", "opencode"):                               # a host session that says what it got
    with open(os.path.join(cronbin, host), "w") as f:
        f.write('#!/bin/sh\necho "$ALOOP_MISSION_LOOP $*" >> "%s"\n' % seen)
    os.chmod(os.path.join(cronbin, host), stat.S_IRWXU)
env = dict(p.env, PATH=cronbin + os.pathsep + p.env["PATH"])
assert p.run(MI, "cron", "--install", env=env).returncode == 0
assert p.run(MI, "cron", "--install", env=env).returncode == 0
lines = open(tab).read().splitlines()
assert lines[0] == "0 3 * * * backup" and len(lines) == 2 and "aloop-mission" in lines[1], lines
assert p.run(MI, "cron", "--remove", env=env).returncode == 0
assert open(tab).read().splitlines() == ["0 3 * * * backup"]
r = p.run(MI, "cron", "--fire", env=env)                                   # due (the mission's ethics review)
assert r.returncode == 0, r.stderr
got = open(seen).read().split()
assert got[0].isdigit() and got[1] == "-p" and "/aloop:mission" in got, got
r = p.run(MI, "cron", "--fire", "--host", "codex", env=env)
assert "network_access=true" in open(seen).read().splitlines()[-1], open(seen).read()
log = open(os.path.join(p.dir, ".aladdin", "logs", "mission-cron.log")).read()
assert "due: ETHICS_REVIEW" in log and "exited 0" in log, log
fd = os.open(os.path.join(p.dir, ".aladdin", "state", "mission-loop.lock"), os.O_RDWR)
fcntl.flock(fd, fcntl.LOCK_EX)
before = open(seen).read()
assert p.run(MI, "cron", "--fire", env=env).returncode == 1                # a session holds the lock
assert open(seen).read() == before
os.close(fd)
q = Project().init(approve=False)
env_q = dict(q.env, PATH=cronbin + os.pathsep + q.env["PATH"])
assert q.run(MI, "cron", "--fire", env=env_q).returncode == 3
assert q.run(MI, "cron", "--fire", env=env_q).returncode == 1              # told once
qlog = open(os.path.join(q.dir, ".aladdin", "logs", "mission-cron.log")).read()
assert qlog.count("the human is needed: CONFIRM_MISSION") == 1 and open(seen).read() == before, qlog
pct = tempfile.mkdtemp(prefix="aloop-50%-")
shutil.copytree(p.dir, os.path.join(pct, "app"), symlinks=True)
r = subprocess.run([MI, "cron"], cwd=os.path.join(pct, "app"), env=p.env, capture_output=True, text=True)
assert "aloop-50\\%-" in r.stdout and "aloop-50%-" not in r.stdout, r.stdout   # cron reads a bare % as a newline
ok("cron: the */30 line runs `cron --fire` (log, lock, host session with ALOOP_MISSION_LOOP; codex gets the "
   "network); a human action is logged once and starts nothing; % is escaped; install is idempotent")

# 17) files a human or a host may have mangled never crash the loop (a crash would read as "not due")
p = Project().init()
cleared(p)
p.art("state", "recorded", 1)
os.makedirs(os.path.join(p.dir, ".aladdin", "state"), exist_ok=True)
sprints = os.path.join(p.dir, ".aladdin", "state", "sprints.json")
for bad in ('{"closed": null}', '{"closed": [3, "x", {"sprint": "S?"}]}', "[]"):
    open(sprints, "w").write(bad)
    expect(p.tick(1.1, "--peek"), "EVALUATE", 0)
open(sprints, "w").write('{"closed": []}')
with open(os.path.join(p.dir, ".aladdin", "logs", "events.jsonl"), "a") as f:
    f.write('[]\n"text"\n{"event": "mission.tick", "ts": "2026-01-01T00:00:00Z", "data": null}\n'
            '{"event": "ethics.reviewed", "data": {"subject": ["x"], "sha256": {"a": 1}, "tier": "hard"}}\n')
expect(p.tick(1.2), "EVALUATE", 0)
assert p.due(1.3)["rc"] in (0, 1)
p.ledger([(0.5, "claude-opus-5-5", "1200", None, True, 5)])               # a string count, a null, a bool
assert p.tick(1.4)["budget"]["tokens"] == 1205
for field, value in (("budget", {"tokens": "5000"}), ("budget", {"usd": None}), ("cadence", 6)):
    doc = p.mission_doc()
    doc[field] = value
    M.save(M.path_of(p.dir, "mission"), doc)
    d = p.tick(1.5)
    expect(d, "SET_MISSION", 3, "invalid")
    assert p.due(1.5)["rc"] == 1 and "already told" in p.due(1.5)["reason"]
open(M.path_of(p.dir, "mission"), "w").write('{"mission_id": "msn_')
expect(p.tick(1.6), "SET_MISSION", 3, "unreadable")
ok("mangled sprints.json, events, ledger rows, caps, cadence or mission.json: SET_MISSION or the next step, never "
   "a crash")

# 18) a stamp kept to the second (events, sprint closes) ties toward "after": the sprint planned in the
#     second the loop handed out SPRINT is that goal's, a state report in the second of a close is re-assessed
p = Project().init()
cleared(p)
p.art("state", "recorded", 1)
p.art("eval", "recorded", 1.1, goal_met=False)
active_goal(p, 1.2)
expect(p.tick(1.2), "SPRINT", 0)
p.event("plan.created", 1.2, sprint="S1")                                  # same second, ms lost
expect(p.tick(1.3), "WAIT", 4, "sprint 1")
p.art("state", "recorded", 2.0)
p.sprint_closed(1, 2.0)                                                    # closes later in that second
expect(p.tick(2.1), "ASSESS", 0, "sprint 1 closed")
ok("second-resolution stamps tie toward after: a sprint planned in the second of its handout is the goal's; a "
   "same-second close re-assesses")

# 19) discovery and help: the three areas are found by name
listed = p.mi("--list").stdout.split()
assert {"tick", "due", "cron"} <= set(listed), listed
for a in ("tick", "due", "cron"):
    r = p.mi(a, "--help")
    assert r.returncode == 0 and "ROADMAP §4.12" in r.stdout, (a, r.stdout)
assert p.mi("tick", "--bogus").returncode == 64
r = p.mi("tick")                                                           # bare: it runs (NO_ARGS), not --help
assert r.stdout.split()[0] in TICK.ROLES, r.stdout
assert p.mi("due").stdout.startswith(("due:", "not due:"))
assert p.run(os.path.join(BIN, "aloop"), "mission", "tick", "--peek").stdout.split()[0] in TICK.ROLES
ok("tick, due and cron are area commands with --help; bare `aloop mission tick` runs")

print("mission-tick: %d checks passed" % PASS[0])
