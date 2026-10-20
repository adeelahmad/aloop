"""tick — the mission loop: one action, decided from facts (ROADMAP §4.12, M9.8).

  mission tick [--json] [--now ISO] [--peek] [--force]
        print the loop's next action:  ACTION  roles — reason
        Exit 0 a role or the supervisor acts now · 2 blocked · 3 needs the human · 4 wait.
        Each call records a mission.tick event (--peek records nothing). The same action on
        unchanged facts is handed out at most 3 times, then BLOCKED; --force hands it out once more.

The loop is IBM's autonomic MAPE-K over docs/agents/mission/: monitor (ASSESS), analyze
(EVALUATE), plan (DERIVE_GOAL, PLAN_INTENTS), execute (SPRINT). Actions, first match wins:

  SET_MISSION       human               no mission, or mission.json is invalid
  CONFIRM_MISSION   human               the mission's current content has no human approval
  STOP_BUDGET       human               spend since the mission was set reached a cap, or the deadline passed
  ETHICS_REVIEW     ethics-reviewer     the mission, or the goal in flight, has no review for its content
  BLOCKED           human               a hard line on the mission; 3 failed goals from one eval; or an
                                        action handed out 3 times on the same facts
  ASK_CONSENT       human               a gray review with no consent for that exact content
  RESEARCH          market-researcher   no research yet; or the goal is met: raise the bar
  DEFINE_JOURNEYS   market-researcher   no user journey yet
  BASELINE          state-assessor, product-tester
                                        no measured baseline; or a replacement's 24h veto window lapsed
  CONFIRM_BASELINE  human               a measured baseline (spoon-fed: or a replacement) awaits confirmation
  OFFER_OPTIONS     mission-planner, then the human
                                        spoon-fed: the goal's ranked options await the human's pick
  PLAN_INTENTS      mission-planner     the goal is cleared: turn its top gaps into Aladdin intents
  SPRINT            supervisor          the goal is active and no sprint was planned for it: /aladdin
  ASSESS            state-assessor      no state report since the baseline, the last sprint, or the cadence
  EVALUATE          evaluator           the latest eval is older than the state, research, journeys or baseline
  DERIVE_GOAL       mission-planner     the latest eval shows gaps and no goal is in flight
  WAIT              —                   a sprint is running; or the raised bar is still met

Facts, never a model's word: `mission check`; artifact status, harness ids (their time) and
sha256; approval events (mission.approved, baseline.confirmed, goal.approved, ethics.consent)
for exactly that content; ethics.reviewed / ethics.blocked events {subject, sha256, tier} (the
most severe verdict for a content wins, so a hard line is never re-reviewed away); the eval's
`goal_met`; Aladdin's plan.created events and .aladdin/state/sprints.json; the sprint lock; and
the token ledger. A sprint planned after a goal was derived is that goal's sprint.

Budget: spend counts every ledger row since the mission was set (the whole project, so it errs
toward stopping). Tokens are uncached input, cache writes and output (on Claude Code nearly all
new input is a cache write; cache reads are not counted); USD prices every class per model
(PRICES), a cache write at the 1-hour rate, an unknown model at the highest known rate. The
ledger has no fast-mode or server-tool charges yet (ROADMAP M9.21).
"""
import calendar
import glob
import hashlib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
sys.path.insert(0, BIN)
import _events  # noqa: E402
import _mission as M  # noqa: E402
import _paths  # noqa: E402

BARE = True               # `mission tick` with no arguments runs (plugin/bin/mission run_area)
ROLES = {
    "SET_MISSION": ("human", []), "CONFIRM_MISSION": ("human", []), "STOP_BUDGET": ("human", []),
    "ETHICS_REVIEW": ("role", ["ethics-reviewer"]), "BLOCKED": ("human", []), "ASK_CONSENT": ("human", []),
    "RESEARCH": ("role", ["market-researcher"]), "DEFINE_JOURNEYS": ("role", ["market-researcher"]),
    "BASELINE": ("role", ["state-assessor", "product-tester"]), "CONFIRM_BASELINE": ("human", []),
    "OFFER_OPTIONS": ("human", ["mission-planner"]), "PLAN_INTENTS": ("role", ["mission-planner"]),
    "SPRINT": ("supervisor", []), "ASSESS": ("role", ["state-assessor"]), "EVALUATE": ("role", ["evaluator"]),
    "DERIVE_GOAL": ("role", ["mission-planner"]), "WAIT": ("none", []),
}
EXIT = {"role": 0, "supervisor": 0, "human": 3, "none": 4}
OPEN = ("derived", "approved", "active")
SEVERITY = {"clear": 0, "gray": 1, "hard": 2}
MAX_HANDOUTS = 3          # the same action on the same facts, then BLOCKED (a retry is not progress)
MAX_GOALS = 3             # goals derived from one eval that were blocked or crossed a hard line
VETO_WINDOW = 24 * 3600 * 1000   # ROADMAP §4.12 call: a replacement baseline applies after 24h unless vetoed
H = 3600 * 1000

# USD per million tokens (input, output, cache read), Anthropic first-party rates of 2026-10-06; a
# cache write is priced at 2x input, the 1-hour rate (the ledger does not record the TTL, so the
# upper bound). Longest prefix wins; an unknown model is priced at the highest rate of each class.
PRICES = {
    "claude-fable-5-1": (10, 50, 0.25), "claude-mythos-5-1": (10, 50, 0.25),
    "claude-fable-5": (10, 50, 1.0), "claude-mythos-5": (10, 50, 1.0),
    "claude-opus-5-5": (4, 20, 0.20), "claude-opus-5": (5, 25, 0.50),
    "claude-opus-4-8": (5, 25, 0.50), "claude-opus-4-7": (5, 25, 0.50), "claude-opus-4-6": (5, 25, 0.50),
    "claude-opus-4-5": (5, 25, 0.50), "claude-opus-4": (15, 75, 1.50),
    "claude-sonnet-5-5": (2, 10, 0.20), "claude-sonnet-5": (2, 10, 0.20), "claude-sonnet-4": (3, 15, 0.30),
    "claude-haiku-5-5": (0.50, 2.50, 0.05),   # the >100K-prompt tier, the upper bound
    "claude-haiku-4-5": (1, 5, 0.10), "claude-haiku-3-5": (0.80, 4, 0.08),
}
TOP = tuple(max(p[i] for p in PRICES.values()) for i in range(3))


# ── time ──────────────────────────────────────────────────────────────────────────────
def ms_ts(s):
    """Epoch ms of an ISO-8601 UTC stamp (2026-10-20T03:00:00Z, optional .fff), or None."""
    s = str(s or "")
    try:
        base = calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")) * 1000
    except ValueError:
        return None
    m = re.match(r"\.(\d{1,3})", s[19:])
    return base + (int(m.group(1).ljust(3, "0")) if m else 0)


def ms_end(s):
    """Like ms_ts, but a stamp kept to the second reads as the last ms of that second: event and
    close stamps lose their ms while ids keep them, so a tie counts as "after" (re-assess, attribute)."""
    t = ms_ts(s)
    return None if t is None else t + (0 if re.match(r"\.\d", str(s)[19:]) else 999)


def num(v):
    """A count from a file a human or host may have written: an int or float (never a bool), a
    numeric string, else 0."""
    if isinstance(v, bool):
        return 0
    if isinstance(v, (int, float)):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0


def cap(v):
    """A budget cap as a number, or None when it is missing or mistyped (`mission check` reports it)."""
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def iso(ms):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000.0))


def ms_doc(doc, kind):
    """An artifact's time: the ms its harness id was issued (finer than created_at)."""
    aid = str(doc.get(kind + "_id", ""))
    if M.ID.match(aid):
        return int(aid.split("_", 1)[1][:12], 16)
    return ms_ts(doc.get("created_at")) or 0


def hours(ms):
    return "%gh" % round(ms / float(H), 1)


# ── spend ─────────────────────────────────────────────────────────────────────────────
def _rate(model):
    m = str(model or "").lower().split("/")[-1]                     # anthropic/…, openrouter/anthropic/…
    m = re.sub(r"^(?:[a-z][a-z-]*\.)?anthropic\.", "", m)          # Bedrock: us., eu., global., us-gov. …
    m = re.sub(r"(?<=\d)\.(?=\d)", "-", m)                          # claude-opus-4.5 → claude-opus-4-5
    for k in sorted(PRICES, key=len, reverse=True):
        if m.startswith(k):
            return PRICES[k]
    return None


def price(rows):
    """(USD, unpriced models) of ledger rows; a model with no known rate is charged TOP."""
    usd, unpriced = 0.0, set()
    for r in rows:
        n = [num(r.get(k)) for k in ("in", "out", "cache_read", "cache_write")]
        if not any(n):
            continue
        rate = _rate(r.get("model"))
        if rate is None:
            unpriced.add(r.get("model") or "?")
            rate = TOP
        usd += (n[0] * rate[0] + n[1] * rate[1] + n[2] * rate[2] + n[3] * rate[0] * 2) / 1e6
    return usd, sorted(unpriced)


def ledger(cwd, since):
    out = []
    for p in sorted(glob.glob(os.path.join(_paths.home(cwd, "ledger", make=False), "*.jsonl"))):
        try:
            lines = open(p).read().splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            t = ms_ts(r.get("ts")) if isinstance(r, dict) else None
            if t is not None and t >= since:
                out.append(r)
    return out


def budget(cwd, mdoc, since, now):
    caps = mdoc.get("budget") if isinstance(mdoc.get("budget"), dict) else {}
    rows = ledger(cwd, since)
    tokens = int(sum(num(r.get("in")) + num(r.get("cache_write")) + num(r.get("out")) for r in rows))
    usd, unpriced = price(rows)
    why = ""
    if cap(caps.get("tokens")) is not None and tokens >= caps["tokens"]:
        why = "spent %d tokens (uncached input, cache writes and output) of the %d-token cap since the mission was " \
              "set" % (tokens, caps["tokens"])
    elif cap(caps.get("usd")) is not None and usd >= caps["usd"]:
        why = "spent $%.2f of the $%.2f cap since the mission was set%s" % (
            usd, caps["usd"], " (%s priced at the highest known rate)" % ", ".join(unpriced) if unpriced else "")
    elif caps.get("deadline"):
        end = ms_ts(str(caps["deadline"])[:10] + "T00:00:00Z")
        if end is not None and now >= end + 24 * H:
            why = "the deadline %s passed" % caps["deadline"]
    return {"caps": caps, "tokens": tokens, "usd": round(usd, 4), "unpriced": unpriced, "since": iso(since),
            "exhausted": why}


# ── facts ─────────────────────────────────────────────────────────────────────────────
def cli():
    """plugin/bin/mission as a module (its `evaluate` is the mission's deterministic check)."""
    mod = sys.modules.get("mission_cli")
    if mod is None:
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("mission_cli", os.path.join(BIN, "mission"))
        spec = importlib.util.spec_from_loader("mission_cli", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        sys.modules["mission_cli"] = mod
    return mod


def lock_ms(root, now):
    """The sprint lock's mtime (ms) when it is live (refreshed within SPRINT_LOCK_TTL), else None."""
    ttl = int(_paths.env("SPRINT_LOCK_TTL", "14400") or 14400) * 1000
    for h in (_paths.NEW_HOME, _paths.OLD_HOME):
        try:
            t = int(os.path.getmtime(os.path.join(root, h, "state", "sprint.lock")) * 1000)
        except OSError:
            continue
        return t if now - t < ttl else None
    return None


def facts(cwd, now):
    root = os.path.realpath(_paths.project(cwd))

    def rel(p):
        return os.path.relpath(os.path.realpath(p), root)

    f = {"now": now, "root": root, "kinds": {}}
    for kind in M.LAYOUT:
        f["kinds"][kind] = sorted(
            ({"ref": rel(p), "id": d.get(kind + "_id"), "status": d.get("status"), "ms": ms_doc(d, kind),
              "sha": M.digest(d), "doc": d} for p, d in M.artifacts(cwd, kind)),
            key=lambda a: (a["ms"], a["ref"]))
    evs = [e for e in _events.read(cwd) if isinstance(e, dict)]
    for e in evs:
        if not isinstance(e.get("data"), dict):
            e["data"] = {}
    f["events"] = evs
    f["approvals"], f["ethics"], f["vetoed"], f["confirmed_at"] = set(), {}, set(), {}
    planned, closed = {}, {}
    for e in evs:
        name, d = e.get("event"), e["data"]
        sub = tuple(x if isinstance(x, str) else None for x in (d.get("subject"), d.get("sha256")))
        if name in M.APPROVAL_EVENTS:
            f["approvals"].add((name,) + sub)
            if name == "baseline.confirmed":
                f["confirmed_at"][sub] = ms_end(e.get("ts")) or 0
        if name in ("ethics.reviewed", "ethics.blocked"):
            t = "hard" if name == "ethics.blocked" else (d.get("tier") if d.get("tier") in SEVERITY else "gray")
            if sub not in f["ethics"] or SEVERITY[t] > SEVERITY[f["ethics"][sub]]:
                f["ethics"][sub] = t
        if name == "baseline.vetoed" and sub[0]:
            f["vetoed"].add(sub[0])
        if name == "plan.created":
            m = re.match(r"^S(\d+)$", str(d.get("sprint") or ""))
            t = ms_end(e.get("ts"))
            if m and t is not None:
                planned[int(m.group(1))] = min(t, planned.get(int(m.group(1)), t))
    st, _ = M.load(os.path.join(_paths.home(cwd, "state", make=False), "sprints.json"))
    cl = st.get("closed") if isinstance(st, dict) else None
    for c in cl if isinstance(cl, list) else []:
        try:
            closed[int(c.get("sprint"))] = ms_end(c.get("at")) or 0
        except (AttributeError, TypeError, ValueError):
            continue
    f["planned"], f["closed"], f["lock"] = planned, closed, lock_ms(root, now)
    mpath = M.path_of(cwd, "mission")
    f["mission_exists"] = os.path.exists(mpath)
    f["check"] = cli().evaluate(cwd)
    mdoc = f["kinds"]["mission"][0]["doc"] if f["kinds"]["mission"] else {}
    f["mission"] = mdoc
    f["mission_ref"] = rel(mpath)
    c = mdoc.get("cadence")
    f["cadence"] = (cli().cadence_seconds(c if isinstance(c, str) else None) or 6 * 3600) * 1000
    f["since"] = ms_doc(mdoc, "mission") if mdoc else now
    f["budget"] = budget(cwd, mdoc, f["since"], now) if mdoc else None
    # what counts as progress: the artifacts (id, status, content) and the decision events
    progress = {k: [(a["id"], a["status"], a["sha"]) for a in v] for k, v in f["kinds"].items()}
    progress["planned"], progress["closed"] = sorted(planned), sorted(closed)
    progress["events"] = sum(1 for e in evs if e.get("event") not in ("mission.tick", "mission.budget_exhausted")
                             and str(e.get("event", "")).split(".")[0] in ("mission", "research", "baseline", "journey",
                                                                           "state", "eval", "goal", "ethics", "plan"))
    f["fingerprint"] = hashlib.sha256(json.dumps(progress, sort_keys=True).encode()).hexdigest()[:16]
    return f


def tier(f, a):
    return f["ethics"].get((a["ref"], a["sha"]))


def has(f, event, a):
    return (event, a["ref"], a["sha"]) in f["approvals"]


def goal_met(f, e):
    if "goal_met" in e["doc"]:
        return e["doc"]["goal_met"] is True
    for ev in f["events"]:
        d = ev.get("data") or {}
        if ev.get("event") == "eval.completed" and d.get("eval_id") == e["id"]:
            return d.get("goal_met") is True
    return False


def latest(f, kind):
    return f["kinds"][kind][-1] if f["kinds"][kind] else None


# ── the decision ──────────────────────────────────────────────────────────────────────
def act(action, reason, subject="", next_check=None):
    return {"action": action, "reason": reason, "subject": subject, "next_check": next_check}


def rules(f):
    now, mode, cad = f["now"], f["mission"].get("mode"), f["cadence"]
    rc, probs, _ = f["check"]
    if not f["mission_exists"]:
        return act("SET_MISSION", 'no mission yet: the human sets one (aloop mission init "<mission>" --mode … '
                                  "--budget-usd N)")
    if not f["mission"]:
        return act("SET_MISSION", "mission.json is unreadable (%s): the human fixes it or sets the mission again "
                                  "(aloop mission init --force)" % ("; ".join(probs) or "not a JSON object"),
                   f["mission_ref"])
    if rc == 2 and not (probs and probs[0].startswith("mission.json says approved")):
        return act("SET_MISSION", "mission.json is invalid: %s" % "; ".join(probs), f["mission_ref"])
    if rc != 0:
        return act("CONFIRM_MISSION", probs[0] if probs else "the mission needs the human's approval", f["mission_ref"])
    if f["budget"]["exhausted"]:
        return act("STOP_BUDGET", "%s: the loop stops; the human raises the cap (edit mission.json, "
                                  "`aloop mission approve`)" % f["budget"]["exhausted"], f["mission_ref"])
    m = {"ref": f["mission_ref"], "sha": f["check"][2].get("sha256")}
    t = tier(f, m)
    if t is None:
        return act("ETHICS_REVIEW", "the mission has no ethics review for its current content", m["ref"])
    if t == "hard":
        return act("BLOCKED", "the mission crosses a hard line (illegal, deceptive or harmful): no consent unlocks "
                              "it; the human changes the mission", m["ref"])
    if t == "gray" and not has(f, "ethics.consent", m):
        return act("ASK_CONSENT", "the mission is in an ethical gray area: the human consents to this content, "
                                  "with a reason, or changes it", m["ref"])
    if not f["kinds"]["research"]:
        return act("RESEARCH", "no research yet: the market and product landscape comes first (sourced claims)")
    if not [j for j in f["kinds"]["journey"] if j["status"] in ("proposed", "active")]:
        return act("DEFINE_JOURNEYS", "no user journey yet: derive the journeys, steps and pass criteria from the research")
    b = baseline_rule(f, mode, now)
    if b:
        return b
    g = latest(f, "goal")
    if g and g["status"] in OPEN:
        r = goal_rule(f, g, mode, now, cad)
        if r:
            return r
    return cycle(f, now, cad)


def baseline_rule(f, mode, now):
    bs = f["kinds"]["baseline"]
    ok = [b for b in bs if b["status"] == "confirmed" and has(f, "baseline.confirmed", b)]
    conf = ok[-1] if ok else None
    f["baseline_at"] = f["confirmed_at"].get((conf["ref"], conf["sha"]), conf["ms"]) if conf else None
    claimed = [b for b in bs if b["status"] == "confirmed" and b not in ok and (not conf or b["ms"] > conf["ms"])]
    if claimed:
        return act("CONFIRM_BASELINE", "baseline %s says confirmed but no human approval covers its content"
                   % claimed[-1]["id"], claimed[-1]["ref"])
    live = [b for b in bs if b["status"] in ("proposed", "measured") and (not conf or b["ms"] > conf["ms"])]
    if not conf:
        if not live:
            return act("BASELINE", "no baseline yet: propose one (a script + pinned versions, ours and the rivals') "
                                   "and measure it twice")
        b = live[-1]
        if b["status"] == "proposed":
            return act("BASELINE", "baseline %s is proposed: measure it (twice, within tolerance)" % b["id"], b["ref"])
        return act("CONFIRM_BASELINE", "baseline %s is measured: the human confirms it as the starting point and "
                                       "the definition of best" % b["id"], b["ref"])
    repl = [b for b in live if b["status"] == "measured" and b["ref"] not in f["vetoed"]]
    if repl:
        b = repl[-1]
        if mode == "spoon-fed":
            return act("CONFIRM_BASELINE", "a replacement baseline %s is measured: the human confirms it (the "
                                           "confirmed %s stays until then)" % (b["id"], conf["id"]), b["ref"])
        if now - b["ms"] >= VETO_WINDOW:
            return act("BASELINE", "the replacement baseline %s passed its 24h veto window without a veto: apply it"
                       % b["id"], b["ref"])
    return None


def goal_sprint(f, g):
    after = sorted((t, n) for n, t in f["planned"].items() if t > g["ms"])
    return after[0][1] if after else None


def goal_rule(f, g, mode, now, cad):
    t = tier(f, g)
    if t is None:
        return act("ETHICS_REVIEW", "goal %s has no ethics review for its content" % g["id"], g["ref"])
    if t == "hard":
        return None                              # the cycle re-derives (and counts it)
    if t == "gray" and not has(f, "ethics.consent", g):
        return act("ASK_CONSENT", "goal %s is in an ethical gray area: the human consents for this goal only, or the "
                                  "planner derives another" % g["id"], g["ref"])
    if g["status"] in ("derived", "approved"):
        if mode == "spoon-fed" and not has(f, "goal.approved", g):
            return act("OFFER_OPTIONS", "spoon-fed: show the human goal %s's ranked options; their pick is "
                                        "goal.approved" % g["id"], g["ref"])
        return act("PLAN_INTENTS", "goal %s is cleared: turn its top gaps into Aladdin intents" % g["id"], g["ref"])
    n = goal_sprint(f, g)
    if n is None:
        if f["lock"] and f["lock"] > g["ms"]:
            return act("WAIT", "sprint planning for goal %s is under way (the sprint lock is fresh)" % g["id"], g["ref"],
                       iso(f["lock"] + 15 * 60 * 1000))
        return act("SPRINT", "goal %s is active and no sprint was planned for it: run /aladdin on its intents" % g["id"],
                   g["ref"])
    if n in f["closed"]:
        return None                              # the cycle: assess and evaluate after the close
    s, e = latest(f, "state"), latest(f, "eval")
    if not s or now - s["ms"] >= cad:
        return act("ASSESS", "the cadence (%s) is due while sprint %d runs: measure the product" % (hours(cad), n))
    if not e or e["ms"] < s["ms"]:
        return act("EVALUATE", "the mid-sprint state report %s is not evaluated yet" % s["id"], s["ref"])
    return act("WAIT", "sprint %d for goal %s is running; the next re-assessment is due at %s"
               % (n, g["id"], iso(s["ms"] + cad)), g["ref"], iso(s["ms"] + cad))


def cycle(f, now, cad):
    s, e, r, j = latest(f, "state"), latest(f, "eval"), latest(f, "research"), latest(f, "journey")
    closes = sorted((t, n) for n, t in f["closed"].items())
    if not s:
        return act("ASSESS", "no state report yet: measure the product with the pinned scripts")
    if f["baseline_at"] and s["ms"] < f["baseline_at"]:
        return act("ASSESS", "the confirmed baseline is newer than the last state report")
    if closes and s["ms"] < closes[-1][0]:
        return act("ASSESS", "sprint %d closed after the last state report" % closes[-1][1])
    if now - s["ms"] >= cad:
        return act("ASSESS", "the cadence (%s) is due: the last state report is from %s" % (hours(cad), iso(s["ms"])))
    if not e:
        return act("EVALUATE", "no eval yet: compare the state with the baseline and the rivals")
    stale = [n for n, t in (("state report", s["ms"]), ("research", r and r["ms"]), ("journeys", j and j["ms"]),
                            ("baseline", f["baseline_at"])) if t and t > e["ms"]]
    if stale:
        return act("EVALUATE", "the latest eval %s is older than the %s" % (e["id"], ", ".join(stale)), e["ref"])
    if not goal_met(f, e):
        failed = []
        for g in f["kinds"]["goal"]:
            if g["ms"] > e["ms"] and (g["status"] in ("blocked", "retired") or tier(f, g) == "hard"):
                failed.append((g, "crosses a hard line" if tier(f, g) == "hard" else "was %s" % g["status"]))
        if len(failed) >= MAX_GOALS:
            return act("BLOCKED", "%d goals derived from eval %s were blocked or crossed a hard line: the human looks"
                       % (len(failed), e["id"]), e["ref"])
        if failed:
            g, why = failed[-1]
            return act("DERIVE_GOAL", "goal %s %s: derive another from eval %s's gap list" % (g["id"], why, e["id"]),
                       e["ref"])
        return act("DERIVE_GOAL", "eval %s shows gaps: derive the next goal from its gap list" % e["id"], e["ref"])
    evals = f["kinds"]["eval"]
    prev = evals[-2] if len(evals) > 1 else None
    if r and prev and goal_met(f, prev) and r["ms"] > prev["ms"] and now - r["ms"] < cad:
        return act("WAIT", "the bar was raised at %s and the goal is still met; the next raise is due at %s"
                   % (iso(r["ms"]), iso(r["ms"] + cad)), e["ref"], iso(min(r["ms"], s["ms"]) + cad))
    return act("RESEARCH", "eval %s: the goal is met, so raise the bar (what is best that the user is not even "
                           "thinking of?)" % e["id"], e["ref"])


def handouts(f, key):
    """How many times `key` was handed out since the facts last changed."""
    n = 0
    for e in reversed([e for e in f["events"] if e.get("event") == "mission.tick"]):
        d = e.get("data") or {}
        if d.get("facts") != f["fingerprint"]:
            break
        if d.get("key") == key:
            n += 1
    return n


def decide(cwd, now=None, force=False):
    now = int(time.time() * 1000) if now is None else now
    f = facts(cwd, now)
    d = rules(f)
    who, roles = ROLES[d["action"]]
    sha = ""
    for a in [x for v in f["kinds"].values() for x in v]:
        if a["ref"] == d["subject"]:
            sha = a["sha"][:12]
    d["key"] = " ".join(x for x in (d["action"], d["subject"] + ("@" + sha if sha else "")) if x)
    d.update(who=who, roles=roles, facts=f["fingerprint"], budget=f["budget"], mission_id=f["mission"].get("mission_id"))
    d["handout"] = handouts(f, d["key"]) + 1 if who in ("role", "supervisor") else 0
    if d["handout"] > MAX_HANDOUTS and not force:
        d.update(action="BLOCKED", who="human", roles=[], key="BLOCKED " + d["key"], next_check=None,
                 reason="%s was handed out %d times on the same facts without a result (%s); the human looks, and "
                        "`aloop mission tick --force` hands it out once more" % (d["action"], d["handout"] - 1, d["reason"]))
        d["handout"] = 0
    d["exit"] = 2 if d["action"] == "BLOCKED" else EXIT[d["who"]]
    d["forced"] = bool(force and d["handout"] > MAX_HANDOUTS)
    d["_facts"] = f
    return d


def record(cwd, d):
    f = d["_facts"]
    M.emit(cwd, "mission.tick", role="mission-loop", refs={"subject": d["subject"]}, action=d["action"], key=d["key"],
           facts=d["facts"], who=d["who"], roles=d["roles"], reason=d["reason"], handout=d["handout"], forced=d["forced"],
           mission_id=d["mission_id"], at=iso(f["now"]))
    if d["action"] == "STOP_BUDGET":
        sha = f["check"][2].get("sha256")
        if not any(e.get("event") == "mission.budget_exhausted" and (e.get("data") or {}).get("sha256") == sha
                   for e in f["events"]):
            M.emit(cwd, "mission.budget_exhausted", role="mission-loop", refs={"mission": f["mission_ref"]},
                   mission_id=d["mission_id"], sha256=sha, why=f["budget"]["exhausted"], tokens=f["budget"]["tokens"],
                   usd=f["budget"]["usd"])


def public(d):
    return {k: v for k, v in d.items() if not k.startswith("_")}


def line(d):
    who = ", ".join(d["roles"]) or {"human": "human", "supervisor": "supervisor"}.get(d["who"], "—")
    if d["action"] == "OFFER_OPTIONS":
        who = "mission-planner, then human"
    return "%-16s %s — %s" % (d["action"], who, d["reason"])


def parse(argv, flags, values):
    opts = {}
    it = iter(argv)
    for a in it:
        if a in flags:
            opts[a.lstrip("-")] = True
        elif a in values:
            opts[a.lstrip("-")] = next(it, "")
        else:
            raise ValueError("unknown argument %r" % a)
    return opts


def now_of(opts):
    if not opts.get("now"):
        return None
    t = ms_ts(opts["now"])
    if t is None:
        raise ValueError("--now must look like 2026-10-20T03:00:00Z")
    return t


def main(argv, cwd):
    try:
        o = parse(argv, ("--json", "--peek", "--force"), ("--now",))
        now = now_of(o)
    except ValueError as e:
        print("mission tick: %s (mission tick --help)" % e, file=sys.stderr)
        return 64
    d = decide(cwd, now, force=o.get("force"))
    if not o.get("peek"):
        record(cwd, d)
    print(json.dumps(public(d), indent=1, sort_keys=True) if o.get("json") else line(d))
    return d["exit"]
