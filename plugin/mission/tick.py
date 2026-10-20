"""tick — the mission loop: one action, decided from facts (ROADMAP §4.12, M9.8).

  mission tick [--json] [--now ISO] [--peek]
        print the loop's next action:  ACTION  roles — reason
        Exit 0 a role or the supervisor acts now · 2 blocked · 3 needs the human · 4 wait.
        Each call records a mission.tick event (--peek records nothing). The same action is
        handed out at most 3 times in a row without a result, then BLOCKED.
  mission tick --force --by NAME
        the human lifts a block the loop set (that repeat guard, or 3 failed goals in a row). It
        is their own words (M9.17): it asks (exit 3) for the line they reply, `lift <mission>
        <code>`, then records the lift in the ledger (mission.lifted), so the next tick, from any
        session or a fresh checkout, goes on. A hard line or a stalled sprint is not lifted this way.

The loop is IBM's autonomic MAPE-K over docs/agents/mission/: monitor (ASSESS), analyze
(EVALUATE), plan (DERIVE_GOAL, PLAN_INTENTS), execute (SPRINT). Actions, first match wins:

  SET_MISSION       human               no mission, or mission.json is invalid
  CONFIRM_MISSION   human               the mission's current content has no human approval
  STOP_BUDGET       human               spend since the mission was set reached a cap, or the deadline passed
  ETHICS_REVIEW     ethics-reviewer     the mission, or the goal in flight, has no review for its content
  BLOCKED           human               a hard line on the mission, or on a goal whose sprint runs; 3 failed
                                        goals in a row; a stalled sprint; an action handed out 3 times; the
                                        ledger or the confirmed baseline fails its check
  ASK_CONSENT       human               a gray review with no consent for that exact content (and review)
  RESEARCH          market-researcher   no research yet; or the goal is met: raise the bar
  DEFINE_JOURNEYS   market-researcher   no user journey yet; or a baseline has nothing to earn: raise the bar
  BASELINE          state-assessor, product-tester
                                        no baseline, or a version to measure; the current one needs a new
                                        version; or a replacement passed its veto window: apply it
  CONFIRM_BASELINE  human               a measured first version (spoon-fed, or a looser tolerance: any
                                        version) awaits the human's confirmation
  OFFER_OPTIONS     mission-planner, then the human
                                        spoon-fed: the goal's ranked options await the human's pick
                                        (whatever its status says: the pick is a record)
  PLAN_INTENTS      mission-planner     the goal is cleared: turn its top gaps into Aladdin intents
  SPRINT            supervisor          the goal is active and an intent of it has no sprint: /aladdin
  APPROVE_PLAN      human               spoon-fed: the goal's sprint plan waits for the human's go
  ASSESS            state-assessor      no state report since the baseline, the last sprint, or the cadence
  EVALUATE          evaluator           the latest eval is older than the state, research, journeys or baseline
  DERIVE_GOAL       mission-planner     the latest eval shows gaps and no goal is in flight
  WAIT              —                   a sprint is running; or the raised bar is still met

Facts, never a model's word: `mission check`; artifact status, harness ids (their time) and
sha256; the committed ledger (docs/agents/mission/ledger.jsonl, so a fresh checkout decides the
same): the newest human approval of a subject (mission.approved, goal.approved, ethics.consent),
recorded with role human and `--by`, for exactly that content; the baseline as `mission
baseline` decides it (the current version is the newest baseline.confirmed record, a version
needs a baseline.measured record for its content, a veto binds the content, a replacement
applies after the mission's veto_window_h from its measurement, a version once confirmed is
archived and only the human's restore brings it back, and a proposal is measurable only when
`baseline measure` would accept it); a state report only with its state.assessed record; journeys
as their records say (a retirement is a record); `mission ethics`'s verdict on the mission and
each goal (none → ETHICS_REVIEW, pending → ASK_CONSENT, hard or broken → BLOCKED, or a goal
re-derived; one rule with the gate: a hard line follows its words to any goal, a review edited
after it was recorded is broken), and a committed review of tier hard, so a checkout without the
run log fails closed; the eval's
`goal_met`; a goal only with its goal.derived (or goal.approved) record, closed by goal.met, and an
eval narrowed to it (goal_id) decides its close (`aloop mission goal close`); Aladdin's intent,
plan and sprint events and .aladdin/state/sprints.json; the
sprint lock; and the token ledger (<run data>/ledger/). A goal's sprints are planned from its intents (intent.received
goal_id → plan.created intent_id), else since the loop handed out its SPRINT. A stamp kept to
the second ties toward "after".

Budget: spend counts every token-ledger row since the mission was set (the whole project, so it errs
toward stopping). Tokens are uncached input, cache writes and output (on Claude Code nearly all
new input is a cache write; cache reads are not counted); USD prices every class per model
(PRICES), a cache write at the 1-hour rate, an unknown model at the highest known rate. The
ledger has no fast-mode or server-tool charges yet (ROADMAP M9.22).
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
sys.path.insert(0, HERE)
import _events  # noqa: E402
import _mission as M  # noqa: E402
import _paths  # noqa: E402
import baseline as B  # noqa: E402
import ethics as E  # noqa: E402
import journeys as J  # noqa: E402

NO_ARGS = True            # `mission tick` with no arguments runs (plugin/bin/mission run_area)
ROLES = {
    "SET_MISSION": ("human", []), "CONFIRM_MISSION": ("human", []), "STOP_BUDGET": ("human", []),
    "ETHICS_REVIEW": ("role", ["ethics-reviewer"]), "BLOCKED": ("human", []), "ASK_CONSENT": ("human", []),
    "RESEARCH": ("role", ["market-researcher"]), "DEFINE_JOURNEYS": ("role", ["market-researcher"]),
    "BASELINE": ("role", ["state-assessor", "product-tester"]), "CONFIRM_BASELINE": ("human", []),
    "OFFER_OPTIONS": ("human", ["mission-planner"]), "PLAN_INTENTS": ("role", ["mission-planner"]),
    "SPRINT": ("supervisor", []), "APPROVE_PLAN": ("human", []),
    "ASSESS": ("role", ["state-assessor"]), "EVALUATE": ("role", ["evaluator"]),
    "DERIVE_GOAL": ("role", ["mission-planner"]), "WAIT": ("none", []),
}
EXIT = {"role": 0, "supervisor": 0, "human": 3, "none": 4}
MAX_HANDOUTS = 3          # the same action in a row without a result, then BLOCKED (a retry is not progress)
MAX_GOALS = 3             # goals in a row that were blocked or retired or crossed a hard line
STALL = 12 * 3600 * 1000  # a planned sprint with no event, close or lock refresh for this long has stalled
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
    f["events"], f["cwd"] = evs, cwd
    f["ticks"] = [e for e in evs if e.get("event") == "mission.tick"]
    f["loop_log"] = [e for e in evs if e.get("event") in ("mission.tick", "mission.lifted")]
    f["ethics"], f["hard_refs"], f["verdicts"] = {}, set(), {}
    f["intents"], f["plans"], f["approved_plans"], f["activity"] = {}, [], set(), {}
    planned, closed, resets, newest = {}, {}, [0], {}
    # human decisions come from the committed ledger (a fresh checkout keeps them), and count only as
    # a human's: role human and a name (`--by`), as M.approve records them; the newest one per subject
    # decides, so restoring content an older approval covered is a rollback, not approved
    raw, _ = M.ledger_rows(cwd)
    rows = sorted(raw, key=lambda r: r["ts"])            # the core's order: a union merge interleaves branches
    assessed, born, met = {}, {}, {}
    f["lifted"], f["lifts"], f["stops"] = {}, {}, {}
    for r in rows:
        name, d = r["event"], r["data"]
        if name == "state.assessed":
            assessed[d.get("subject")] = d.get("sha256")
        if name in ("goal.derived", "goal.approved"):
            born.setdefault((d.get("subject"), d.get("sha256")), []).append(r["ts"])
        if name == "goal.met":
            met.setdefault(d.get("subject"), []).append(r["ts"])
        if name == "mission.budget_exhausted":            # the loop stopped at a cap for this content
            f["stops"].setdefault(d.get("sha256"), d)
        if name == "mission.lifted" and r.get("role") == "human" and str(d.get("by") or "").strip() \
                and d.get("sha256") == M.digest(lifted(d)):      # the human's typed line granted exactly this
            f["lifted"][r["event_id"]] = d.get("block")
            f["lifts"][d.get("block")] = f["lifts"].get(d.get("block"), 0) + 1
            if d.get("grant") == "GOALS" and isinstance(d.get("occurrence"), int):
                resets.append(d["occurrence"])          # only lifting that block clears the count, up to its goals
        sub = tuple(x if isinstance(x, str) else None for x in (d.get("subject"), d.get("sha256")))
        if name in M.APPROVAL_EVENTS and r.get("role") == "human" and str(d.get("by") or "").strip():
            newest[(name, sub[0])] = sub[1]
            if name == "mission.approved":
                resets.append(ms_end(r["ts"]) or 0)
    f["approvals"] = {(name, ref, sha) for (name, ref), sha in newest.items()}
    f["ledger_head"] = raw[-1]["hash"] if raw else ""
    # a state report counts only with the state.assessed record of its content (mission assess)
    f["kinds"]["state"] = [a for a in f["kinds"]["state"] if assessed.get(a["ref"]) == a["sha"]]
    # a goal is one `mission goal derive` recorded (goal.derived, or the human's goal.approved, for
    # its content) and it is closed by its goal.met record, as `mission evaluate` reads them
    f["kinds"]["goal"] = [dict(a, met=any(t >= max(born[(a["ref"], a["sha"])]) for t in met.get(a["ref"], [])))
                          for a in f["kinds"]["goal"] if (a["ref"], a["sha"]) in born]
    # fail closed: a committed hard review holds on a checkout without the run log (ROADMAP M9.24);
    # every other ethics fact is `mission ethics`'s verdict (ethics_of)
    for a in f["kinds"].get("ethics", []):
        s = a["doc"].get("subject")
        if a["doc"].get("tier") == "hard" and a["status"] == "recorded" and isinstance(s, dict) \
                and isinstance(s.get("ref"), str):
            f["hard_refs"].add(s["ref"])
            if isinstance(s.get("sha256"), str):
                f["ethics"][(s["ref"], s["sha256"])] = "hard"
    for e in evs:
        name, d = e.get("event"), e["data"]
        ts = ms_end(e.get("ts"))
        if name == "intent.received" and isinstance(d.get("goal_id"), str) and isinstance(d.get("intent_id"), str):
            f["intents"].setdefault(d["goal_id"], []).append(d["intent_id"])
        m = re.match(r"^S(\d+)$", str(d.get("sprint") or (e.get("ids") or {}).get("sprint") or ""))
        if m and ts:
            n = int(m.group(1))
            f["activity"][n] = max(ts, f["activity"].get(n, 0))
            if name == "plan.created" and n not in planned:
                planned[n] = ts
                f["plans"].append((n, ts, d.get("intent_id")))
            if name == "plan.approved":
                f["approved_plans"].add(n)
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
    f["ledger_problems"] = M.ledger_problems(cwd)
    mdoc = f["kinds"]["mission"][0]["doc"] if f["kinds"]["mission"] else {}
    f["mission"] = mdoc
    f["mission_ref"] = rel(mpath)
    c = mdoc.get("cadence")
    f["cadence"] = (cli().cadence_seconds(c if isinstance(c, str) else None) or 6 * 3600) * 1000
    f["since"] = ms_doc(mdoc, "mission") if mdoc else now
    f["reset_at"] = max(resets)
    f["budget"] = budget(cwd, mdoc, f["since"], now) if mdoc else None
    # an audit fingerprint of what the rules read (recorded with each tick)
    progress = {k: [(a["id"], a["status"], a["sha"]) for a in v] for k, v in f["kinds"].items()}
    progress["planned"], progress["closed"] = sorted(planned), sorted(closed)
    progress["decisions"] = sorted(map(str, f["approvals"] | set(f["ethics"].items())))
    progress["ledger"] = f["ledger_head"]
    progress["intents"] = sorted(map(str, f["intents"].items()))
    progress["approved_plans"], progress["lock"] = sorted(f["approved_plans"]), f["lock"]
    progress["reset_at"], progress["budget"] = f["reset_at"], f["budget"]
    f["progress"] = progress      # fingerprint() adds the ethics verdicts the rules asked for
    return f


def fingerprint(f):
    """An audit fingerprint of what the rules read (recorded with each tick)."""
    progress = dict(f["progress"], verdicts=sorted(map(str, f["verdicts"].items())))
    return hashlib.sha256(json.dumps(progress, sort_keys=True).encode()).hexdigest()[:16]


def ethics_of(f, kind, a):
    """(state, problems): `mission ethics`'s verdict on this content (none, broken, pending, hard,
    ok, unresolved), one rule for the loop and the gate. A committed hard review holds whatever
    the run log says (fail closed), for this content, and for a goal for good."""
    key = (a["ref"], a["sha"])
    if key not in f["verdicts"]:
        _, probs, facts = E.verdict(f["cwd"], kind, "" if kind == "mission" else a["id"])
        st = facts.get("state") or "unresolved"
        if f["ethics"].get(key) == "hard" or (kind == "goal" and a["ref"] in f["hard_refs"]):
            st, probs = "hard", probs if st == "hard" else ["a recorded review of %s found a hard line" % a["ref"]]
        f["verdicts"][key] = (st, probs)
    return f["verdicts"][key]


def reply(verb, name, sha, cmd):
    """The line the human types to grant exactly this content (M9.17), and the command that then records
    it: the same code the grant's provenance checks, so relaying the hand-off is enough."""
    return "the human replies `%s %s %s`, then `aloop mission %s`" % (verb, name, M.code(sha), cmd)


def confirm_line(b):
    return reply("confirm", b["id"], b["sha"], "baseline confirm %s --by NAME" % b["id"])


def has(f, event, a):
    return (event, a["ref"], a["sha"]) in f["approvals"]


def goal_met(f, e):
    if "goal_met" in e["doc"]:
        return e["doc"]["goal_met"] is True
    for ev in f["events"]:
        d = ev["data"]
        if ev.get("event") == "eval.completed" and d.get("eval_id") == e["id"]:
            return d.get("goal_met") is True
    return False


def latest(f, kind):
    return f["kinds"][kind][-1] if f["kinds"][kind] else None


def sprint_handout(f, g):
    """When the loop first handed out SPRINT for this goal (any content of it), or None."""
    for e in f["ticks"]:
        d = e["data"]
        if d.get("action") == "SPRINT" and str(d.get("key") or "").split("@")[0] == "SPRINT " + g["ref"]:
            return ms_ts(d.get("at")) or ms_ts(e.get("ts"))
    return None


def goal_sprints(f, g):
    """This goal's sprints, oldest first: planned from one of its intents (intent.received
    goal_id → plan.created intent_id); with no linked intent, planned since its SPRINT handout."""
    ids = set(f["intents"].get(g["id"]) or [])
    if ids:
        return [n for n, t, iid in f["plans"] if iid in ids]
    start = sprint_handout(f, g)
    return [n for n, t, _ in f["plans"] if start is not None and t >= start]


def last_activity(f, n):
    return max(f["planned"].get(n, 0), f["activity"].get(n, 0), f["lock"] or 0)


def stalled(f, n):
    return f["now"] - last_activity(f, n) >= STALL


def failed_goals(f):
    """The goals in a row, newest first, that were blocked or retired or crossed a hard line,
    since the human last acted (approved the mission, or lifted a block with --force)."""
    out = []
    for g in reversed(f["kinds"]["goal"]):
        if g["ms"] <= f["reset_at"]:
            break
        hard = ethics_of(f, "goal", g)[0] == "hard"
        if not hard and g["status"] not in ("blocked", "retired"):
            break
        out.append((g, "crosses a hard line" if hard else "was %s" % g["status"]))
    return out


# ── the decision ──────────────────────────────────────────────────────────────────────
def act(action, reason, subject="", next_check=None, grant=None, pin=None, bind=None):
    """pin: the content the action's key is bound to, when it is not the subject's whole digest (a
    failed measurement rewrites the file, and the repeat guard must still see the same work); bind:
    the key names this instead of the subject (the same content under any id is the same work)."""
    return {"action": action, "reason": reason, "subject": subject, "next_check": next_check, "grant": grant,
            "pin": pin, "bind": bind}


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
    if f["ledger_problems"]:
        lref = M.rel(f["cwd"], M.ledger(f["cwd"]))
        return act("BLOCKED", "the mission ledger fails its check (%s): every gate decides from it, so the loop "
                              "stops until the human restores it (git checkout %s)"
                   % ("; ".join(f["ledger_problems"][:3]), lref), lref,
                   pin=hashlib.sha256("\n".join(f["ledger_problems"]).encode()).hexdigest()[:12])
    if rc == 2 and not (probs and probs[0].startswith(("mission.json says approved",
                                                        "mission.json is content a human approved"))):
        return act("SET_MISSION", "mission.json is invalid: %s" % "; ".join(probs), f["mission_ref"])
    if rc != 0:
        return act("CONFIRM_MISSION", probs[0] if probs else "the mission needs the human's approval", f["mission_ref"])
    stop = f["stops"].get(f["check"][2].get("sha256"))
    why = f["budget"]["exhausted"] or (stop and "%s (the ledger's stop for this content)" % stop.get("why"))
    if why:
        return act("STOP_BUDGET", "%s: the loop stops; the human raises the cap (edit mission.json, "
                                  "`aloop mission approve`)" % why, f["mission_ref"])
    m = {"ref": f["mission_ref"], "sha": f["check"][2].get("sha256")}
    t, why = ethics_of(f, "mission", m)
    if t == "none":
        return act("ETHICS_REVIEW", "the mission has no ethics review for its current content", m["ref"])
    if t == "hard":
        return act("BLOCKED", "the mission crosses a hard line (illegal, deceptive or harmful): no consent unlocks "
                              "it; the human changes the mission", m["ref"])
    if t == "pending":
        return act("ASK_CONSENT", "the mission is in an ethical gray area: the human consents to this content and "
                                  "each of its gray reviews, with a reason (%s), or changes it"
                   % reply("consent", f["mission"].get("mission_id"), m["sha"],
                           "ethics consent --subject mission --by NAME --reason TEXT"), m["ref"])
    if t != "ok":
        return act("BLOCKED", "the mission's ethics review fails its check (%s): the human restores it (git), or "
                              "changes the mission" % (why[0] if why else t), m["ref"])
    if not f["kinds"]["research"]:
        return act("RESEARCH", "no research yet: the market and product landscape comes first (sourced claims)")
    frozen = J.pins(f["cwd"])[0]
    if not [j for j in f["kinds"]["journey"]
            if J.lifecycle(f["cwd"], os.path.join(f["root"], j["ref"]), j["doc"], frozen) != "retired"]:
        return act("DEFINE_JOURNEYS", "no user journey yet: derive the journeys, steps and pass criteria from the research")
    b = baseline_rule(f, mode, now)
    if b:
        return b
    g = latest(f, "goal")
    if g and not g["met"] and g["status"] not in ("blocked", "retired"):
        r = goal_rule(f, g, mode, now, cad)
        if r:
            return r
    return cycle(f, now, cad)


def veto_window(f):
    """The mission's veto window in ms, or None when it is not a number of hours >= 0 (baseline apply refuses)."""
    w = f["mission"].get("veto_window_h", B.DEFAULT_VETO_H)
    if isinstance(w, bool) or not isinstance(w, (int, float)) or w != w or w in (float("inf"), float("-inf")) or w < 0:
        return None
    return int(w * H)


def listed_now(f):
    """{journey_id: sha256} of the journeys `baseline new` would list now (every one not retired)."""
    frozen = J.pins(f["cwd"])[0]
    return {j["doc"].get("journey_id"): j["sha"] for j in f["kinds"]["journey"]
            if J.lifecycle(f["cwd"], os.path.join(f["root"], j["ref"]), j["doc"], frozen) != "retired"}


def unmeasurable(f, a):
    """(journey problems, version problems): why `baseline measure` would refuse this proposed
    version now. A listed journey that no longer runs as pinned (its file or its runner's files
    changed) needs a journey that supersedes it; anything else, a new version."""
    cwd, doc = f["cwd"], a["doc"]
    bad = M.problems(doc, B.KIND, os.path.join(f["root"], a["ref"]))
    if M.malformed(bad):
        return [], bad
    pin, jbad = J.pins(cwd), []
    for j in doc["journeys"]:
        jp = J.jpath(cwd, j["journey_id"])
        jd, _ = M.load(jp)
        if isinstance(jd, dict):
            jbad += [(M.rel(cwd, jp), "journey %s: %s" % (j["journey_id"], x)) for x in J.problems(cwd, jp, jd, pin)]
    return jbad, bad + B.standing_problems(cwd, doc)


def version_rule(f, a, conf):
    """What a baseline version newer than the current one needs, as baseline.py decides it."""
    cwd, bid, stays = f["cwd"], a["id"], " (the confirmed %s stays until then)" % conf["id"] if conf else ""
    doc, _, rec, bad = B.measured_ok(cwd, bid)
    if rec and not bad:
        return "measured", rec
    if rec:
        return act("BASELINE", "baseline %s was measured but can no longer take over (%s): propose a new version%s"
                   % (bid, bad[0], stays), a["ref"]), None
    m = doc.get("measurements") if isinstance(doc, dict) else None
    if a["status"] != "proposed":
        return act("BASELINE", "baseline %s says %s, but no record covers its content (a status proves nothing): "
                               "propose a version and measure it%s" % (bid, a["status"], stays), a["ref"]), None
    pin = B.content_sha(doc)[:12]
    jbad, vbad = unmeasurable(f, a)
    if jbad:
        return act("DEFINE_JOURNEYS", "%s, which baseline %s lists, can no longer be measured: propose a journey "
                                      "that supersedes it, then a new baseline version%s" % (jbad[0][1], bid, stays),
                   jbad[0][0]), None
    if vbad:     # its own key: "measure it" was other work
        return act("BASELINE", "baseline %s can no longer be measured (%s): propose a new version%s"
                   % (bid, vbad[0], stays), a["ref"], pin=pin + "/stale"), None
    if isinstance(m, dict) and m.get("agreed") is False:
        return act("BASELINE", "baseline %s's runs disagreed (not reproducible): make the runner deterministic, then "
                               "measure it again%s" % (bid, stays), a["ref"], pin=pin), None
    if isinstance(m, dict) and m.get("agreed") and not m.get("not_passing"):
        if listed_now(f) != {j["journey_id"]: j["sha256"] for j in doc["journeys"]}:
            return act("BASELINE", "baseline %s had nothing to earn, and the journeys changed since it was measured: "
                                   "propose a new version (it lists them)%s" % (bid, stays), a["ref"],
                       pin=pin + "/stale"), None
        return act("DEFINE_JOURNEYS", "baseline %s has nothing to earn: every journey %s at baseline; raise the bar "
                                      "(new or stricter journeys), then propose a new version%s"
                   % (bid, "already passes" if not m.get("pending_judge") else "passes or waits for a judge", stays),
                   a["ref"], pin=pin), None
    return act("BASELINE", "baseline %s is proposed: measure it (twice, within tolerance)%s" % (bid, stays),
               a["ref"], pin=pin), None


def refused(f, v):
    """The human vetoed this content: say so, keyed on the content, so a re-proposal of it is the same work."""
    r = v["veto"]["data"]
    return act("BASELINE", "baseline %s is content the human vetoed (%s at %s: %s): propose different journeys, "
                           "a different definition or tolerance; only the human can still confirm it"
               % (v["id"], r.get("by"), v["veto"]["ts"], r.get("reason")), v["ref"],
               bind="vetoed@" + B.content_sha(v["doc"])[:12])


def baseline_rule(f, mode, now):
    """The current version is the one the newest baseline.confirmed record names (a human's, or an
    autonomous mission's veto-window application); a newer, unvetoed version never confirmed is a
    replacement (one confirmed before is archived: only the human's `baseline restore` brings it back)."""
    cwd = f["cwd"]
    rows = M.records(cwd, B.CONFIRM)
    cur_path, cur = B.current(cwd)
    cur_ref = rows[-1]["data"].get("subject") if rows else None
    f["baseline_at"] = ms_end(rows[-1]["ts"]) if rows else None
    f["baseline_id"] = B.current_id(cwd)
    conf = next((a for a in f["kinds"]["baseline"] if a["ref"] == cur_ref), None) if cur_ref else None
    once = {r["data"].get("subject") for r in rows}
    cands = [dict(a, veto=B.vetoed(cwd, os.path.join(f["root"], a["ref"]), a["doc"]))
             for a in f["kinds"]["baseline"]
             if a["ref"] not in once and (not conf or a["ms"] > conf["ms"])]
    newer = [a for a in cands if not a["veto"]]
    vetoed = cands[-1] if cands and cands[-1]["veto"] else None     # the newest proposal was refused
    if not cur_path:
        if not newer:
            return refused(f, vetoed) if vetoed else \
                act("BASELINE", "no baseline yet: propose one (the journeys, pinned scripts, the rivals') and "
                                "measure it twice")
        r, _ = version_rule(f, newer[-1], None)
        if r != "measured":
            return r
        return act("CONFIRM_BASELINE", "baseline %s is measured: the human confirms it as the starting point and "
                                       "the definition of best: %s" % (newer[-1]["id"], confirm_line(newer[-1])),
                   newer[-1]["ref"])
    rc, probs, _ = B.evaluate(cwd)
    if rc == 2:
        return act("BLOCKED", "the confirmed baseline %s fails its check (%s): the human restores it (git), or "
                              "confirms a new version" % (f["baseline_id"], "; ".join(probs[:3])), cur_ref)
    if newer:
        b = newer[-1]
        r, rec = version_rule(f, b, conf or {"id": f["baseline_id"]})
        if r != "measured":
            return r
        prev_tol = (cur or {}).get("tolerance", B.DEFAULT_TOLERANCE)
        if mode != "autonomous" or num(b["doc"].get("tolerance")) > num(prev_tol):
            why = ("a spoon-fed mission confirms every version" if mode != "autonomous" else
                   "it tolerates more drift than %s: a looser gate is the human's" % f["baseline_id"])
            return act("CONFIRM_BASELINE", "a replacement baseline %s is measured; %s (the confirmed %s stays until "
                                           "then): %s" % (b["id"], why, f["baseline_id"], confirm_line(b)), b["ref"])
        w = veto_window(f)
        since = ms_end(rec["ts"])
        if w is not None and since is not None and now >= since + w:
            return act("BASELINE", "the replacement baseline %s passed its %s veto window with no veto: apply it "
                                   "(aloop mission baseline apply %s)" % (b["id"], hours(w), b["id"]), b["ref"])
        if rc == 3 and w is not None and since is not None:
            return act("WAIT", "the confirmed baseline needs replacing (%s) and %s is in its veto window: it applies "
                               "at %s unless the human vetoes or confirms it sooner" % (probs[0], b["id"],
                                                                                         iso(since + w)),
                       b["ref"], iso(since + w))
    if rc == 3:
        if vetoed and not newer:
            d = refused(f, vetoed)
            d["reason"] = "the confirmed baseline %s needs a new version (%s), and %s" % (
                f["baseline_id"], probs[0], d["reason"])
            return d
        return act("BASELINE", "the confirmed baseline %s needs a new version (%s): propose one with its evidence"
                   % (f["baseline_id"], probs[0]), cur_ref)
    return None


def goal_rule(f, g, mode, now, cad):
    t, why = ethics_of(f, "goal", g)
    running = [n for n in goal_sprints(f, g) if n not in f["closed"]]
    if t == "none":
        return act("ETHICS_REVIEW", "goal %s has no ethics review for its content" % g["id"], g["ref"])
    if t == "hard":
        if running:
            return act("BLOCKED", "goal %s crossed a hard line while sprint %d runs its intents: the human stops the "
                                  "sprint; no new goal starts until it closes" % (g["id"], running[-1]), g["ref"])
        return None                              # the cycle re-derives (and counts it)
    if t == "pending":
        return act("ASK_CONSENT", "goal %s is in an ethical gray area: the human consents for this goal only (%s), "
                                  "or the planner derives another"
                   % (g["id"], reply("consent", g["id"], g["sha"],
                                     "ethics consent --subject %s --by NAME --reason TEXT" % g["id"])), g["ref"])
    if t != "ok":
        return act("BLOCKED", "goal %s's ethics review fails its check (%s): the human restores it (git), or the "
                              "planner derives another goal" % (g["id"], why[0] if why else t), g["ref"])
    if mode == "spoon-fed" and not has(f, "goal.approved", g):
        if running:
            return act("BLOCKED", "spoon-fed: sprint %d runs goal %s, which the human never picked (no goal.approved "
                                  "for its content): the human picks it or stops the sprint" % (running[-1], g["id"]),
                       g["ref"])
        return act("OFFER_OPTIONS", "spoon-fed: show the human goal %s's ranked options; their pick is "
                                    "goal.approved (aloop mission goal approve %s --by NAME)" % (g["id"], g["id"]),
                   g["ref"])
    if g["status"] in ("derived", "approved"):
        return act("PLAN_INTENTS", "goal %s is cleared: turn its gaps into Aladdin intents (aloop mission goal "
                                   "intents %s)" % (g["id"], g["id"]), g["ref"])
    if running:
        return sprint_rule(f, g, running[-1], mode, now, cad)
    done = goal_sprints(f, g)
    e = latest(f, "eval")
    mine = e if e and e["doc"].get("goal_id") == g["id"] and e["ms"] > g["ms"] else None
    if mine and goal_met(f, mine):
        return act("EVALUATE", "goal %s is met in eval %s: close it (aloop mission goal close %s --eval %s)"
                   % (g["id"], mine["id"], g["id"], mine["id"]), g["ref"])
    sprinted = {iid for _, _, iid in f["plans"]}
    left = [i for i in f["intents"].get(g["id"]) or [] if i not in sprinted]
    if done and not left:
        s = latest(f, "state")
        if mine and mine["ms"] > max(f["closed"][n] for n in done) and (not s or mine["ms"] >= s["ms"]):
            return act("BLOCKED", "goal %s's intents were all sprinted and eval %s still lists %d gap(s) of it: the "
                                  "human decides (a goal that is not met is never closed: ROADMAP M9.50)"
                       % (g["id"], mine["id"], len(mine["doc"].get("gaps") or [])), g["ref"], pin=mine["id"])
        return None                              # every intent sprinted and closed: assess and evaluate
    since = max([f["closed"][n] for n in done] + [sprint_handout(f, g) or 0, g["ms"]])
    if f["lock"] and f["lock"] > since:
        return act("WAIT", "a sprint is being planned (the sprint lock is fresh)", g["ref"],
                   iso(f["lock"] + 15 * 60 * 1000))
    what = "intent %s is not sprinted yet" % left[0] if left else "no sprint was planned for it"
    return act("SPRINT", "goal %s is active and %s: `aloop mission goal next %s` makes it the next intent, then "
                         "/aladdin on it" % (g["id"], what, g["id"]), g["ref"])


def sprint_rule(f, g, n, mode, now, cad):
    """The goal's sprint n is planned and not closed."""
    if mode == "spoon-fed" and n not in f["approved_plans"]:
        return act("APPROVE_PLAN", "spoon-fed: sprint %d's plan for goal %s waits for the human's go (reviewed mode)"
                   % (n, g["id"]), g["ref"])
    if stalled(f, n):
        return act("BLOCKED", "sprint %d for goal %s shows no activity since %s: the human resumes it (/aladdin) or "
                              "closes it (`stats sprint-close`)" % (n, g["id"], iso(last_activity(f, n))), g["ref"],
                   pin="S%d" % n)
    s, e = latest(f, "state"), latest(f, "eval")
    if not s or now - s["ms"] >= cad:
        return act("ASSESS", "the cadence (%s) is due while sprint %d runs: measure the product" % (hours(cad), n))
    if not e or e["ms"] < s["ms"]:
        return act("EVALUATE", "the mid-sprint state report %s is not evaluated yet" % s["id"], s["ref"])
    nxt = min(s["ms"] + cad, last_activity(f, n) + STALL)
    return act("WAIT", "sprint %d for goal %s is running; the next check is due at %s" % (n, g["id"], iso(nxt)),
               g["ref"], iso(nxt))


def measured_other(f, a):
    """A state report or eval made against another baseline than the current one: its baseline_id
    says so; without one (an older file), its time is before the current version took over."""
    bid = a["doc"].get("baseline_id")
    if isinstance(bid, str) and bid:
        return bool(f["baseline_id"]) and bid != f["baseline_id"]
    return bool(f["baseline_at"]) and a["ms"] < f["baseline_at"]


def cycle(f, now, cad):
    s, e, r, j = latest(f, "state"), latest(f, "eval"), latest(f, "research"), latest(f, "journey")
    fails = failed_goals(f)
    if len(fails) >= MAX_GOALS:
        return act("BLOCKED", "%d goals in a row were blocked or crossed a hard line (latest: %s): the human looks; "
                              "`aloop mission tick --force --by NAME` lets the loop derive again"
                   % (len(fails), fails[0][0]["id"]), fails[0][0]["ref"], grant="GOALS")
    closes = sorted((t, n) for n, t in f["closed"].items())
    if not s:
        return act("ASSESS", "no state report yet: measure the product with the pinned scripts")
    if measured_other(f, s):
        return act("ASSESS", "the last state report %s measured another baseline than the current %s"
                   % (s["id"], f["baseline_id"]))
    if closes and s["ms"] < closes[-1][0]:
        return act("ASSESS", "sprint %d closed after the last state report" % closes[-1][1])
    if now - s["ms"] >= cad:
        return act("ASSESS", "the cadence (%s) is due: the last state report is from %s" % (hours(cad), iso(s["ms"])))
    if not e:
        return act("EVALUATE", "no eval yet: compare the state with the baseline and the rivals")
    stale = [n for n, t in (("state report", s["ms"]), ("research", r and r["ms"]), ("journeys", j and j["ms"]))
             if t and t > e["ms"]] + (["baseline"] if measured_other(f, e) else [])
    if stale:
        return act("EVALUATE", "the latest eval %s is older than the %s" % (e["id"], ", ".join(stale)), e["ref"])
    gid = e["doc"].get("goal_id")
    if gid:      # narrowed to a goal's targets while it was open: it says nothing about the whole mission
        g = next((x for x in f["kinds"]["goal"] if x["id"] == gid), None)
        if g and not g["met"] and ethics_of(f, "goal", g)[0] == "hard":
            return act("BLOCKED", "goal %s crossed a hard line, and `mission evaluate` still narrows to it (ROADMAP "
                                  "M9.29): no new goal can be derived until it is set aside; the human looks"
                       % gid, g["ref"])
        if not g or g["met"]:
            return act("EVALUATE", "eval %s was narrowed to goal %s, which is closed: evaluate the whole mission "
                                   "(aloop mission evaluate)" % (e["id"], gid), e["ref"])
    running = sorted(n for n, t in f["planned"].items() if n not in f["closed"] and t >= f["since"])
    if running:
        n = running[-1]
        if stalled(f, n):
            return act("BLOCKED", "sprint %d shows no activity since %s: the human resumes it (/aladdin) or closes it "
                                  "(`stats sprint-close`)" % (n, iso(last_activity(f, n))), pin="S%d" % n)
        nxt = min(s["ms"] + cad, last_activity(f, n) + STALL)
        return act("WAIT", "sprint %d is running; the next goal is derived after it closes" % n, "", iso(nxt))
    if not goal_met(f, e):
        why = "goal %s %s: derive another from" % (fails[0][0]["id"], fails[0][1]) if fails else "eval %s shows " \
            "gaps: derive the next goal from" % e["id"]
        if f["mission"].get("mode") == "spoon-fed":
            return act("OFFER_OPTIONS", "spoon-fed: %s eval %s's gap list; show the human its ranked gaps (aloop "
                                        "mission goal options), and their pick derives it (aloop mission goal derive "
                                        "--pick GAP_ID --by NAME)" % (why, e["id"]), e["ref"])
        return act("DERIVE_GOAL", "%s eval %s's gap list (aloop mission goal derive)" % (why, e["id"]), e["ref"])
    evals = f["kinds"]["eval"]
    prev = evals[-2] if len(evals) > 1 else None
    if r and prev and goal_met(f, prev) and r["ms"] > prev["ms"] and now - r["ms"] < cad:
        return act("WAIT", "the bar was raised at %s and the goal is still met; the next raise is due at %s"
                   % (iso(r["ms"]), iso(r["ms"] + cad)), e["ref"], iso(min(r["ms"], s["ms"]) + cad))
    return act("RESEARCH", "eval %s: the goal is met, so raise the bar (what is best that the user is not even "
                           "thinking of?)" % e["id"], e["ref"])


def handouts(f, key):
    """How many times in a row `key` went to a role or the supervisor: another handout ends the
    run, the human's lift of it starts the count again (the run-log event of its ledger row, the first
    copy only, so a replayed line or a run-log tick lifts nothing), and human or WAIT ticks between do
    not count (a retry after a lapsed lease is still a retry)."""
    n, log = 0, f["loop_log"]
    first = {}
    for i, e in enumerate(log):
        first.setdefault(e.get("event_id"), i)
    for i in range(len(log) - 1, -1, -1):
        e, d = log[i], log[i]["data"]
        if e.get("event") == "mission.lifted":
            if f["lifted"].get(e.get("event_id")) == "BLOCKED " + key and first[e.get("event_id")] == i:
                break
            continue
        if d.get("who") not in ("role", "supervisor"):
            continue
        if d.get("key") != key:
            break
        n += 1
    return n


def decide(cwd, now=None):
    now = int(time.time() * 1000) if now is None else now
    f = facts(cwd, now)
    d = rules(f)
    who, roles = ROLES[d["action"]]
    sha, bind = d.pop("pin", None) or "", d.pop("bind", None)
    for a in [x for v in f["kinds"].values() for x in v] if not sha else []:
        if a["ref"] == d["subject"]:
            sha = a["sha"][:12]
    d["key"] = " ".join(x for x in (d["action"], bind or (d["subject"] + "@" + sha if d["subject"] and sha else
                                                          d["subject"] or sha)) if x)
    d.update(who=who, roles=roles, facts=fingerprint(f), budget=f["budget"], mission_id=f["mission"].get("mission_id"))
    d["handout"] = handouts(f, d["key"]) + 1 if who in ("role", "supervisor") else 0
    if d["handout"] > MAX_HANDOUTS:
        d.update(action="BLOCKED", who="human", roles=[], key="BLOCKED " + d["key"], next_check=None, grant=d["key"],
                 reason="%s was handed out %d times in a row without a result (%s); the human looks, and "
                        "`aloop mission tick --force --by NAME` hands it out again" % (d["action"], d["handout"] - 1,
                                                                                       d["reason"]))
        d["handout"] = 0
    d["exit"] = 2 if d["action"] == "BLOCKED" else EXIT[d["who"]]
    d["_facts"] = f
    return d


def record(cwd, d):
    f = d["_facts"]
    M.emit(cwd, "mission.tick", role="mission-loop", refs={"subject": d["subject"]}, action=d["action"], key=d["key"],
           facts=d["facts"], who=d["who"], roles=d["roles"], reason=d["reason"], handout=d["handout"],
           mission_id=d["mission_id"], at=iso(f["now"]), loop=os.environ.get("ALOOP_MISSION_LOOP") or None)
    if d["action"] == "STOP_BUDGET":
        sha = f["check"][2].get("sha256")
        if sha not in f["stops"]:                         # once per content, in the ledger (a clone stays stopped)
            M.record(cwd, "mission.budget_exhausted", f["mission_ref"], "mission-loop", mission_id=d["mission_id"],
                     sha256=sha, why=f["budget"]["exhausted"], tokens=f["budget"]["tokens"], usd=f["budget"]["usd"])


def lifted(data):
    """What a `lift` line grants (its digest is the code): this occurrence of one block of one mission."""
    return {k: data.get(k) for k in ("mission_id", "block", "lift", "occurrence")}


def lift_of(f, d):
    """This block's lift: its key, how many lifts of that key the ledger has, and the occurrence: for
    3 failed goals the newest one's time (the reset reaches that far, whatever the clock says), for
    a repeat the handout that tripped it. So a line typed for one block never lifts another."""
    if d["grant"] == "GOALS":
        at = max([g["ms"] for g in f["kinds"]["goal"] if g["ref"] == d["subject"]] or [0])
    else:
        at = next((e.get("event_id") for e in reversed(f["ticks"]) if e["data"].get("key") == d["grant"]
                   and e["data"].get("who") in ("role", "supervisor")), "")
    return {"mission_id": d["mission_id"], "block": d["key"], "lift": f["lifts"].get(d["key"], 0), "occurrence": at}


def force(cwd, now, by):
    """The human lifts a block the loop set (the repeat guard, or 3 failed goals in a row): their typed
    `lift` line (M9.17; M.NeedsHuman asks for it), recorded in the ledger, and a grant tick, so the next
    tick, from any session or a fresh checkout, hands the action out again. A hard line or a stalled
    sprint is not lifted this way."""
    d = decide(cwd, now)
    if d["action"] != "BLOCKED":
        print("mission tick --force: nothing is blocked; the next action is %s" % line(d), file=sys.stderr)
        return d["exit"]
    if not d.get("grant"):
        print("mission tick --force: this block is not lifted by --force: %s" % d["reason"], file=sys.stderr)
        return 2
    f = d["_facts"]
    lift = lift_of(f, d)
    sha = M.digest(lift)
    M.approve(cwd, "mission.lifted", f["mission_ref"], sha, by, grant=d["grant"], at=iso(f["now"]), **lift)
    M.emit(cwd, "mission.tick", role="human", refs={"subject": d["subject"]}, action="FORCE", key="FORCE " + d["grant"],
           grant=d["grant"], lift=sha, by=by, who="human", roles=[], handout=0, facts=d["facts"],
           mission_id=d["mission_id"], reason="%s lifted the block: %s" % (by, d["reason"]), at=iso(f["now"]))
    print("FORCE            %s lifted the block; the next tick goes on (%s)" % (by, d["grant"]))
    return 0


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
        o = parse(argv, ("--json", "--peek", "--force"), ("--now", "--by"))
        now = now_of(o)
    except ValueError as e:
        print("mission tick: %s (mission tick --help)" % e, file=sys.stderr)
        return 64
    if o.get("force"):
        if not str(o.get("by") or "").strip():
            print("mission tick: --force needs --by NAME: lifting a block is the human's decision, and it is recorded",
                  file=sys.stderr)
            return 64
        return force(cwd, now, o["by"].strip())
    d = decide(cwd, now)
    if not o.get("peek"):
        record(cwd, d)
    print(json.dumps(public(d), indent=1, sort_keys=True) if o.get("json") else line(d))
    return d["exit"]
