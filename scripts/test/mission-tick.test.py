#!/usr/bin/env python3
"""ROADMAP §4.12 (M9) M9.8: the mission loop. `mission tick` picks one action from harness
files (first match wins), `mission due` says whether a tick is worth a model call (zero
tokens: it reads files only), `mission cron` renders the host routine / crontab line, and
the budget caps are read from the token ledger. No area module is needed: every fact comes
from the core contract (status, harness ids, sha256, approval events) and Aladdin's own files.
    python3 scripts/test/mission-tick.test.py
"""
import calendar
import contextlib
import io
import json
import os
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
import _mission as M  # noqa: E402
import tick as TICK  # noqa: E402

H = 3600 * 1000
SEQ = [0]


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def iso(ms):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000))


class Project:
    """A throwaway git repo with a mission, and helpers that write artifacts and events at
    chosen times (ids carry the time, like the harness's)."""

    def __init__(self, mode="autonomous", budget=("--budget-usd", "40"), cadence="6h"):
        self.dir = tempfile.mkdtemp(prefix="aloop-tick-")
        self.env = dict(os.environ, HOME=os.path.join(self.dir, "home"))
        self.env.pop("AGENTIC_HOST", None)
        subprocess.run(["git", "init", "-q"], cwd=self.dir, check=True)
        self.mode, self.budget, self.cadence = mode, budget, cadence
        self.t0 = int(time.time() * 1000)

    def run(self, *args, env=None):
        return subprocess.run(args, cwd=self.dir, env=env or self.env, capture_output=True, text=True)

    def init(self, approve=True):
        r = self.run(MI, "init", "the world's best to-do app", "--mode", self.mode, *self.budget,
                     "--cadence", self.cadence, "--by", "Adeel")
        assert r.returncode == 0, r.stderr
        if approve:
            assert self.run(MI, "approve", "--by", "Adeel").returncode == 0
        self.mission = json.load(open(M.path_of(self.dir, "mission")))
        self.t0 = int(self.mission["mission_id"][4:16], 16)
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

    def event(self, name, hours, **data):
        p = os.path.join(self.dir, ".aladdin", "logs", "events.jsonl")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        SEQ[0] += 1
        with open(p, "a") as f:
            f.write(json.dumps({"schema": "aladdin.event/1", "event_id": "e%d" % SEQ[0], "ts": iso(self.at(hours)),
                                "event": name, "ids": {}, "role": None, "refs": {}, "data": data}) + "\n")

    def review(self, subject, doc, tier, hours):
        self.event("ethics.reviewed", hours, subject=subject, sha256=M.digest(doc), tier=tier)

    def approve_event(self, event, subject, doc, hours):
        self.event(event, hours, subject=subject, sha256=M.digest(doc), by="Adeel", reason="")

    def mission_ref(self):
        return self.ref("mission")

    def mission_doc(self):
        return json.load(open(M.path_of(self.dir, "mission")))

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
                f.write(json.dumps({"ts": iso(self.at(hours)) [:-1] + ".123Z", "agent": "main", "agent_type": "supervisor",
                                    "model": model, "in": i, "cache_read": cr, "cache_write": cw, "out": o,
                                    "session": "s1"}) + "\n")

    def tick(self, hours, *extra):
        r = self.run(MI, "tick", "--json", "--now", iso(self.at(hours)), *extra)
        try:
            d = json.loads(r.stdout)
        except ValueError:
            raise AssertionError("tick printed no JSON: %r %r" % (r.stdout, r.stderr))
        d["rc"] = r.returncode
        return d

    def due(self, hours):
        r = self.run(MI, "due", "--json", "--now", iso(self.at(hours)))
        d = json.loads(r.stdout)
        d["rc"] = r.returncode
        return d

    def events(self):
        p = os.path.join(self.dir, ".aladdin", "logs", "events.jsonl")
        return [json.loads(l) for l in open(p)] if os.path.exists(p) else []


def expect(d, action, rc=None, why=None):
    assert d["action"] == action, "expected %s, got %s — %s" % (action, d["action"], d.get("reason"))
    if rc is not None:
        assert d["rc"] == rc, "%s: exit %s, expected %s" % (action, d["rc"], rc)
    if why:
        assert why in d["reason"], "%s: %r not in %r" % (action, why, d["reason"])


def cleared(p, hours=0.1):
    """A project through the mission's ethics review, research, a journey and a confirmed baseline."""
    p.review(p.mission_ref(), p.mission_doc(), "clear", hours)
    p.art("research", "recorded", hours)
    p.art("journey", "proposed", hours)
    bid, bdoc = p.art("baseline", "confirmed", hours)
    p.approve_event("baseline.confirmed", p.ref("baseline", bid), bdoc, hours)
    return bid


# 1) the lifecycle, autonomous: mission → ethics → research → journeys → baseline → assess → evaluate
#    → goal → ethics → intents → sprint → wait → sprint closed → assess again
p = Project()
d = p.tick(0)
expect(d, "SET_MISSION", 3)
assert d["who"] == "human"
p.init(approve=False)
expect(p.tick(0), "CONFIRM_MISSION", 3, "draft")
assert p.run(MI, "approve", "--by", "Adeel").returncode == 0
d = p.tick(0.01)
expect(d, "ETHICS_REVIEW", 0)
assert d["roles"] == ["ethics-reviewer"] and d["subject"] == "docs/agents/mission/mission.json", d
p.review(p.mission_ref(), p.mission_doc(), "clear", 0.02)
d = p.tick(0.03)
expect(d, "RESEARCH", 0)
assert d["roles"] == ["market-researcher"] and d["who"] == "role"
p.art("research", "recorded", 0.04)
expect(p.tick(0.05), "DEFINE_JOURNEYS", 0)
p.art("journey", "proposed", 0.06)
d = p.tick(0.07)
expect(d, "BASELINE", 0, "propose")
assert d["roles"] == ["state-assessor", "product-tester"], d["roles"]
bid, bdoc = p.art("baseline", "proposed", 0.08)
expect(p.tick(0.09), "BASELINE", 0, "measure")
bdoc["status"] = "measured"
M.save(M.path_of(p.dir, "baseline", bid), bdoc)
expect(p.tick(0.10), "CONFIRM_BASELINE", 3)
bdoc["status"] = "confirmed"
M.save(M.path_of(p.dir, "baseline", bid), bdoc)
expect(p.tick(0.11), "CONFIRM_BASELINE", 3, "no human approval")       # a status is not an approval
p.approve_event("baseline.confirmed", p.ref("baseline", bid), bdoc, 0.12)
expect(p.tick(0.13), "ASSESS", 0)
p.art("state", "recorded", 0.14)
d = p.tick(0.15)
expect(d, "EVALUATE", 0)
assert d["roles"] == ["evaluator"]
eid, _ = p.art("eval", "recorded", 0.16, goal_met=False)
d = p.tick(0.17)
expect(d, "DERIVE_GOAL", 0)
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
d = p.tick(0.6)
expect(d, "WAIT", 4, "sprint 1")
p.sprint_closed(1, 2)
expect(p.tick(2.1), "ASSESS", 0, "sprint 1 closed")
p.art("state", "recorded", 2.2)
expect(p.tick(2.3), "EVALUATE", 0)
p.art("eval", "recorded", 2.4, goal_met=False)
expect(p.tick(2.5), "DERIVE_GOAL", 0)
ok("lifecycle: mission → ethics → research → journeys → baseline → assess → evaluate → goal → intents → sprint → wait → re-assess")

# 2) the tick records every decision (mission.tick) and never needs an area module
ticks = [e for e in p.events() if e["event"] == "mission.tick"]
assert len(ticks) == 19 and {"action", "key", "facts", "reason", "at"} <= set(ticks[-1]["data"]), ticks[-1]
assert ticks[-1]["data"]["action"] == "DERIVE_GOAL"
r = p.run(MI, "tick", "--peek", "--now", iso(p.at(2.5)))
assert r.returncode == 0 and r.stdout.startswith("DERIVE_GOAL"), r.stdout
assert len([e for e in p.events() if e["event"] == "mission.tick"]) == len(ticks)   # --peek records nothing
ok("every tick records mission.tick (action, key, facts fingerprint, reason); --peek records nothing")

# 3) ethics on the mission: hard line blocks for good, gray asks once per content, a change re-reviews
p = Project().init()
p.review(p.mission_ref(), p.mission_doc(), "hard", 0.1)
expect(p.tick(0.2), "BLOCKED", 2, "hard line")
p.review(p.mission_ref(), p.mission_doc(), "clear", 0.3)                   # shopping for a better verdict
expect(p.tick(0.4), "BLOCKED", 2, "hard line")
p = Project().init()
p.review(p.mission_ref(), p.mission_doc(), "gray", 0.1)
d = p.tick(0.2)
expect(d, "ASK_CONSENT", 3)
assert d["subject"] == p.mission_ref()
p.approve_event("ethics.consent", p.mission_ref(), p.mission_doc(), 0.3)
expect(p.tick(0.4), "RESEARCH", 0)
doc = p.mission_doc()
doc["constraints"] = ["no dark patterns"]
doc["status"] = "draft"
M.save(M.path_of(p.dir, "mission"), doc)
expect(p.tick(0.5), "CONFIRM_MISSION", 3)
assert p.run(MI, "approve", "--by", "Adeel").returncode == 0
expect(p.tick(0.6), "ETHICS_REVIEW", 0)                                    # new content, new review
ok("mission ethics: a hard line is never unlocked; gray waits for consent on that content; a changed mission is re-reviewed")

# 4) ethics on goals: gray consent does not carry over; hard lines re-derive, 3 per eval then BLOCKED
p = Project().init()
cleared(p)
p.art("state", "recorded", 1)
p.art("eval", "recorded", 1.1, goal_met=False)
gid, gdoc = p.art("goal", "derived", 1.2)
p.review(p.ref("goal", gid), gdoc, "gray", 1.3)
d = p.tick(1.4)
expect(d, "ASK_CONSENT", 3)
assert d["subject"] == p.ref("goal", gid)
p.approve_event("ethics.consent", p.ref("goal", gid), gdoc, 1.5)
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
expect(p.tick(2.5), "BLOCKED", 2, "3 goals")
ok("goal ethics: consent is bound to one goal's content; a hard line re-derives, 3 failed goals per eval block")

# 5) spoon-fed: the goal's options wait for the human's pick (an event, never a status); replacements wait too
p = Project(mode="spoon-fed").init()
bid = cleared(p)
p.art("state", "recorded", 1)
p.art("eval", "recorded", 1.1, goal_met=False)
gid, gdoc = p.art("goal", "derived", 1.2)
p.review(p.ref("goal", gid), gdoc, "clear", 1.3)
d = p.tick(1.4)
expect(d, "OFFER_OPTIONS", 3)
assert d["roles"] == ["mission-planner"] and d["who"] == "human", d
gdoc["status"] = "approved"
M.save(M.path_of(p.dir, "goal", gid), gdoc)
expect(p.tick(1.5), "OFFER_OPTIONS", 3)
p.approve_event("goal.approved", p.ref("goal", gid), gdoc, 1.6)
expect(p.tick(1.7), "PLAN_INTENTS", 0)
p.art("baseline", "measured", 1.8)
expect(p.tick(1.9), "CONFIRM_BASELINE", 3, "replacement")
ok("spoon-fed: OFFER_OPTIONS until a goal.approved event covers the goal's content; a replacement baseline asks")

# 6) autonomous replacement baseline: the cycle goes on during the veto window, then it is applied
p = Project().init()
cleared(p)
rid, _ = p.art("baseline", "measured", 1)
expect(p.tick(2), "ASSESS", 0)
expect(p.tick(25.5), "BASELINE", 0, "veto window")
p.event("baseline.vetoed", 25.6, subject=p.ref("baseline", rid), by="Adeel")
expect(p.tick(26), "ASSESS", 0)
ok("autonomous: a replacement baseline is applied after the 24h veto window unless vetoed")

# 7) budget caps from the token ledger: tokens (uncached input, cache writes, output), USD (priced per
#    model), deadline. On Claude Code nearly all new input arrives as cache writes, so they count.
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
assert p.run(MI, "approve", "--by", "Adeel", "--reason", "raise the cap").returncode == 0
p.review(p.mission_ref(), p.mission_doc(), "clear", 1.2)
expect(p.tick(1.3), "ASSESS", 0)
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
d = p.tick(1)
expect(d, "STOP_BUDGET", 3, "$5.20")
p = Project(budget=("--budget-usd", "50", "--deadline", "2000-01-01")).init()
expect(p.tick(0), "STOP_BUDGET", 3, "deadline")
ok("USD cap priced per model (dotted, routed and Bedrock ids too; unknown at the highest rate); a passed deadline "
   "stops the loop")

# 8) cadence: a running sprint waits, re-assesses on the cadence; a met goal raises the bar once per cadence
p = Project(cadence="6h").init()
cleared(p)
p.art("state", "recorded", 1)
p.art("eval", "recorded", 1.1, goal_met=False)
gid, gdoc = p.art("goal", "active", 1.2)
p.review(p.ref("goal", gid), gdoc, "clear", 1.2)
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

# 9) the sprint for an active goal: planning in progress (a fresh sprint lock) is a wait, not a second /aladdin
p = Project().init()
cleared(p)
p.art("state", "recorded", 1)
p.art("eval", "recorded", 1.1, goal_met=False)
gid, gdoc = p.art("goal", "active", 1.2)
p.review(p.ref("goal", gid), gdoc, "clear", 1.2)
p.event("plan.created", 0.5, sprint="S1")                                  # an older sprint is not this goal's
expect(p.tick(1.3), "SPRINT", 0)
lock = os.path.join(p.dir, ".aladdin", "state", "sprint.lock")
os.makedirs(os.path.dirname(lock), exist_ok=True)
open(lock, "w").write("s\n")
os.utime(lock, (p.at(1.4) / 1000, p.at(1.4) / 1000))
expect(p.tick(1.5), "WAIT", 4, "planning")
ok("an active goal's sprint: SPRINT once, then a fresh sprint lock (planning under way) is a wait")

# 10) the repeat guard: the same action on unchanged facts is handed out 3 times, then BLOCKED; --force retries
p = Project().init()
p.review(p.mission_ref(), p.mission_doc(), "clear", 0.1)
for i in range(3):
    expect(p.tick(0.2 + i * 0.01), "RESEARCH", 0)
d = p.tick(0.3)
expect(d, "BLOCKED", 2, "3 times")
expect(p.tick(0.31, "--force"), "RESEARCH", 0)
expect(p.tick(0.32), "BLOCKED", 2)
p.art("research", "recorded", 0.33)
expect(p.tick(0.34), "DEFINE_JOURNEYS", 0)
ok("an action handed out 3 times on the same facts blocks; --force retries once; new facts clear it")

# 11) due: zero tokens, read-only; a handed-out action is leased; the human is told once
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
p.art("research", "recorded", 0.6)
assert p.due(0.7)["rc"] == 0                                               # new facts: due at once
p = Project().init(approve=False)
assert p.due(0)["rc"] == 0                                                 # tell the human once
p.tick(0.1)
d = p.due(0.2)
assert d["rc"] == 1 and "already" in d["reason"], d
p = Project().init()
cleared(p)
p.art("state", "recorded", 1)
p.art("eval", "recorded", 1.1, goal_met=False)
gid, gdoc = p.art("goal", "active", 1.2)
p.review(p.ref("goal", gid), gdoc, "clear", 1.2)
p.event("plan.created", 1.5, sprint="S1")
assert p.due(3)["rc"] == 1 and p.due(7.5)["rc"] == 0                        # waiting, then the cadence is due
r = p.run(MI, "due", "-q", "--now", iso(p.at(3)))
assert r.returncode == 1 and r.stdout == "" and r.stderr == ""
p = Project(budget=("--budget-tokens", "100")).init()                      # at a cap no session starts:
p.ledger([(0.5, "claude-opus-5-5", 50, 0, 0, 60)])                         # due records the stop itself, once
r = p.run(MI, "due", "-q", "--now", iso(p.at(1)))
assert r.returncode == 1 and "no session starts" in r.stderr and "110" in r.stderr, (r.returncode, r.stderr)
assert [e["data"]["action"] for e in p.events() if e["event"] == "mission.tick"] == ["STOP_BUDGET"]
assert len([e for e in p.events() if e["event"] == "mission.budget_exhausted"]) == 1
r = p.run(MI, "due", "-q", "--now", iso(p.at(1.5)))
assert r.returncode == 1 and r.stderr == "" and len([e for e in p.events() if e["event"] == "mission.tick"]) == 1
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
ok("due: reads files only; leases a handed-out action 2h; tells the human once; WAIT and STOP_BUDGET are never "
   "due; a crash exits 2, not 'not due'")

# 12) cron: the routine and the crontab line, gated by `due`; install is idempotent, remove undoes it
p = Project(cadence="6h").init()
r = p.run(MI, "cron")
assert r.returncode == 0, r.stderr
line = r.stdout.strip().splitlines()[0]
aloop = os.path.join(BIN, "aloop")
assert line.startswith("*/30 * * * * cd ") and ("{ %s mission due -q && " % aloop) in line, line
assert "claude -p" in line and "/aloop:mission" in line and "; } >> " in line and "mission-cron.log" in line, line
assert line.rstrip().endswith("# aloop-mission %s" % os.path.realpath(p.dir)), line
r = p.run(MI, "cron", "--host", "codex", "--every", "1h")
assert r.stdout.startswith("0 * * * * ") and "codex exec --full-auto" in r.stdout, r.stdout
assert "opencode run" in p.run(MI, "cron", "--host", "opencode").stdout
assert p.run(MI, "cron", "--every", "7m").returncode == 64
r = p.run(MI, "cron", "--json")
spec = json.loads(r.stdout)
assert spec["schedule"] == "*/30 * * * *" and "aloop mission due" in spec["routine_prompt"], spec
fake = os.path.join(p.dir, "fakebin")
os.makedirs(fake)
tab = os.path.join(p.dir, "crontab.txt")
open(tab, "w").write("0 3 * * * backup\n")
with open(os.path.join(fake, "crontab"), "w") as f:
    f.write('#!/bin/sh\nif [ "$1" = "-l" ]; then cat "%s"; else cat > "%s"; fi\n' % (tab, tab))
os.chmod(os.path.join(fake, "crontab"), stat.S_IRWXU)
env = dict(p.env, PATH=fake + os.pathsep + p.env["PATH"])
assert p.run(MI, "cron", "--install", env=env).returncode == 0
assert p.run(MI, "cron", "--install", env=env).returncode == 0
lines = open(tab).read().splitlines()
assert lines[0] == "0 3 * * * backup" and len(lines) == 2 and "aloop-mission" in lines[1], lines
assert p.run(MI, "cron", "--remove", env=env).returncode == 0
assert open(tab).read().splitlines() == ["0 3 * * * backup"]
ok("cron: */30 crontab line gated by `aloop mission due` (both logged), per host; --install idempotent, "
   "--remove undoes it")

# 13) files a human or a host may have mangled never crash the loop (a crash would read as "not due")
p = Project().init()
cleared(p)
p.art("state", "recorded", 1)
os.makedirs(os.path.join(p.dir, ".aladdin", "state"), exist_ok=True)
sprints = os.path.join(p.dir, ".aladdin", "state", "sprints.json")
for bad in ('{"closed": null}', '{"closed": [3, "x", {"sprint": "S?"}]}', "[]"):
    open(sprints, "w").write(bad)
    expect(p.tick(1.1), "EVALUATE", 0)
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

# 14) a stamp kept to the second (events, sprint closes) ties toward "after": the sprint planned in the
#     same second as its goal is that goal's, a state report in the second of a close is re-assessed
p = Project().init()
cleared(p)
p.art("state", "recorded", 1)
p.art("eval", "recorded", 1.1, goal_met=False)
gid, gdoc = p.art("goal", "active", 1.2)
p.review(p.ref("goal", gid), gdoc, "clear", 1.2)
p.event("plan.created", 1.2, sprint="S1")                                  # same second, ms lost
expect(p.tick(1.3), "WAIT", 4, "sprint 1")
p.art("state", "recorded", 2.0)
p.sprint_closed(1, 2.0)                                                    # closes later in that second
expect(p.tick(2.1), "ASSESS", 0, "sprint 1 closed")
ok("second-resolution stamps tie toward after: same-second sprints belong to the goal; a same-second close re-assesses")

# 15) discovery and help: the three areas are found by name
listed = p.run(MI, "--list").stdout.split()
assert {"tick", "due", "cron"} <= set(listed), listed
for a in ("tick", "due", "cron"):
    r = p.run(MI, a, "--help")
    assert r.returncode == 0 and "ROADMAP §4.12" in r.stdout, (a, r.stdout)
assert p.run(MI, "tick", "--bogus").returncode == 64
r = p.run(MI, "tick")                                                      # bare: it runs (BARE), not --help
assert r.stdout.split()[0] in TICK.ROLES, r.stdout
assert p.run(MI, "due").stdout.startswith(("due:", "not due:"))
assert p.run(os.path.join(BIN, "aloop"), "mission", "tick", "--peek").stdout.split()[0] in TICK.ROLES
ok("tick, due and cron are area commands with --help; bare `aloop mission tick` runs")

print("mission-tick: %d checks passed" % PASS[0])
