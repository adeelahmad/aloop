"""mission goal — goals from the gap list, and the Aladdin handoff (ROADMAP §4.12, M9.11).

The Evaluator's top gaps become the next goal: in an autonomous mission the top N, in a
spoon-fed one the gaps the human picks (the pick is the goal's approval). A goal is bound to
the mission content it serves, the eval it came from, the standing principles for the
mission's mode, and the Aladdin autonomy that mode maps to (autonomous → autonomous,
spoon-fed → reviewed). It passes the ethics gate before any intent exists; then each gap
becomes an Intent Contract (source `mission`), and `events trace` walks a commit back
through intent → goal → mission → the human who approved it.

Usage:
  mission goal options [--eval EVAL_ID] [--json]
        the eval's gaps in the Evaluator's order (regressions, then the open gaps closest to
        target, then unscored): what a goal can take; spoon-fed offers these.
        A gap a hard line fixed (its summary, ethics) is held: listed apart, never taken
  mission goal derive [--eval EVAL_ID] [--top N]                       (autonomous; N default 3)
  mission goal derive [--eval EVAL_ID] --pick GAP_ID[,…] --by NAME      (spoon-fed)
        write goals/<goal_id>.json from the latest recorded eval of the whole mission (no active goal)
        whose goal is not met, once the mission is approved and its ethics check passes; the goal
        names its targets [{journey_id, criterion_id}], which `mission evaluate` narrows to while
        it is active; records goal.derived in the ledger (and goal.approved on a pick)
  mission goal check GOAL_ID [--json]
        the gate before intents: seal, the content goal.derived recorded, the mission unchanged and
        approved, the eval unchanged, the principles stamp well formed (an older block is stale, kept as provenance), mission and goal ethics, and in spoon-fed mode a human
        goal.approved for this content. Exit 0 · 2 block · 3 needs the human
  mission goal approve GOAL_ID --by NAME [--reason TEXT]
        a human approves the goal's current content (goal.approved)
  mission goal intents GOAL_ID
        the handoff: one Intent Contract per gap (intent new --source mission --goal --gap), filled
        from the gap, the mission and the principles; only after `check` passes; idempotent
  mission goal next GOAL_ID
        the next intent without a plan, made the newest intent file (gate-intake re-checks the newest)
        and normalized in the goal's autonomy (intent check --mode), so the sprint's plan.created
        links it; exit 1 when every intent has a plan
  mission goal close GOAL_ID --eval EVAL_ID
        an active goal (sealed, as derived) and a recorded evaluation of it, newer than the goal,
        that says goal_met: status met, a goal.met ledger record (a goal already met: exit 0, no new record). Exit 2
        while a target (or a regression) is still open, or for a goal that is not active
  mission goal show GOAL_ID [--json]
        the goal, its check, and the principles block it carries
"""
import glob
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.realpath(os.path.join(HERE, "..", "bin"))
sys.path.insert(0, BIN)
sys.path.insert(0, HERE)
import _events  # noqa: E402
import _mission as M  # noqa: E402
import _paths  # noqa: E402
import ethics as E  # noqa: E402
import evaluate as EV  # noqa: E402
import journeys as J  # noqa: E402
import principles as P  # noqa: E402

AUTONOMY = {"autonomous": "autonomous", "spoon-fed": "reviewed"}   # mission mode → Aladdin approval mode (§4.1)
DEFAULT_TOP = 3   # Ship Small: a few gaps per goal, one intent (one sprint) each
GOAL_ID = re.compile(r"^gol_[0-9a-f]{20}$")
EVAL_ID = re.compile(r"^evl_[0-9a-f]{20}$")


# ── evals (M9.10's gap list: plugin/mission/evaluate.py) ────────────────────────────
def first(cwd, event, key, value):
    """The first event of a kind whose data[key] is value (gate-owned events: the one its command wrote)."""
    for e in _events.read(cwd):
        if e.get("event") == event and (e.get("data") or {}).get(key) == value:
            return e
    return None


def eval_record(cwd, eid):
    """(doc, eval.completed event, problems) of one gap list: M9.10's own check (it follows from its
    state, which a state.assessed ledger record covers, its baseline and goal) and the eval.completed
    that recorded this content (written only by `mission evaluate`)."""
    if not isinstance(eid, str) or not EVAL_ID.match(eid):
        return None, None, ["%r is not an eval id (evl_…)" % (eid,)]
    path = M.path_of(cwd, "eval", eid)
    doc, err = M.load(path)
    if not isinstance(doc, dict):
        return None, None, ["no evaluation %s (%s)" % (eid, M.rel(cwd, path))]
    try:
        bad = ["eval %s: %s" % (eid, b) for b in EV.problems(cwd, path, doc)]
        sha = M.digest(doc)
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        return doc, None, ["eval %s is malformed (%s)" % (eid, e)]
    if doc.get("eval_id") != eid:
        bad.append("eval %s: eval_id does not match the file name" % eid)
    ev = first(cwd, "eval.completed", "eval_id", eid)
    if not ev:
        bad.append("eval %s is not recorded (no eval.completed event names it)" % eid)
    elif ev["data"].get("sha256") != sha:
        bad.append("eval %s is not the content its eval.completed recorded" % eid)
    return doc, ev, bad


def latest_eval(cwd):
    """The id of the latest recorded eval (stream order), or None."""
    ids = [(e.get("data") or {}).get("eval_id") for e in _events.read(cwd) if e.get("event") == "eval.completed"]
    for eid in reversed(ids):
        if eid and os.path.exists(M.path_of(cwd, "eval", eid)):
            return eid
    return None


def gap_of(cwd, g):
    """What a goal keeps of an eval gap, with a one-line summary in the journey's own words (stable
    across evals, so a hard line on it holds; the measurement `now` is kept apart)."""
    jdoc, _ = J.load(cwd, str(g.get("journey_id")))
    title = jdoc.get("title") if isinstance(jdoc, dict) and isinstance(jdoc.get("title"), str) else g["journey_id"]
    return {"gap_id": str(g["gap_id"]), "journey_id": str(g["journey_id"]), "criterion_id": str(g["criterion_id"]),
            "kind": str(g.get("kind")), "target": str(g.get("target") or ""), "now": str(g.get("now") or ""),
            "summary": "%s (%s): %s" % (title.strip(), g["criterion_id"], g.get("target") or "passes")}


def eval_gaps(cwd, edoc):
    """The eval's gaps as a goal keeps them, in the Evaluator's priority order (regressions, then the
    open gaps closest to target, then unscored): a goal takes them as given."""
    return [gap_of(cwd, g) for g in edoc.get("gaps") or [] if isinstance(g, dict) and g.get("gap_id")]


def split_held(cwd, gaps):
    """(open, held): a gap whose summary a hard line fixed is held for good (ethics.hard_words), so a
    goal derived again never takes it and the loop moves on to the next gap."""
    hard, ids = E.hard_words(cwd), E.held_gaps(cwd)
    held = [g for g in gaps if E.word_key(g["summary"]) in hard or g["gap_id"] in ids]
    return [g for g in gaps if g not in held], held


def stream_index(cwd, pred):
    """The position of the last event matching pred in the stream, or -1."""
    hit = -1
    for i, e in enumerate(_events.read(cwd)):
        if pred(e):
            hit = i
    return hit


# ── the goal's gate ──────────────────────────────────────────────────────────────────
def mission_of(cwd):
    path = M.path_of(cwd, "mission")
    doc, _ = M.load(path)
    return (path, doc) if isinstance(doc, dict) else (path, None)


def derived(cwd, gid):
    """The goal.derived ledger record `derive` wrote for this goal, or None."""
    ref = M.rel(cwd, M.path_of(cwd, "goal", gid))
    return next((r for r in M.records(cwd, "goal.derived", ref) if r["data"].get("goal_id") == gid), None)


def derived_problems(cwd, g):
    """A goal is the content `derive` recorded in its goal.derived ledger row, so every derive rule
    (the latest eval's open, unheld gaps; the real principles stamp) holds at the gate as well."""
    gid = g["goal_id"]
    d = derived(cwd, gid)
    if not d:
        return ["no goal.derived records %s: only `aloop mission goal derive` makes a goal" % gid]
    data = d.get("data") or {}
    if data.get("sha256") != M.digest(g):
        return ["%s is not the content goal.derived recorded (sha256 %s): derive a new goal"
                % (gid, str(data.get("sha256"))[:12])]
    want = {"eval_id": g["eval"]["eval_id"], "mission_sha256": g["mission"]["sha256"],
            "principles_sha256": g["principles"]["sha256"], "gaps": [x["gap_id"] for x in g["gaps"]]}
    return ["%s: %s does not match its goal.derived record" % (gid, k) for k, v in want.items() if data.get(k) != v]


def check(cwd, gid):
    """(exit, blocks, asks, facts) for one goal."""
    gpath = M.path_of(cwd, "goal", gid)
    g, err = M.load(gpath)
    if not isinstance(g, dict):
        return 2, ["no goal %s (%s)" % (gid, M.rel(cwd, gpath))], [], {}
    try:
        blocks, asks = list(M.problems(g, "goal", gpath)), []
        sha = M.digest(g)
    except ValueError as e:     # text that is not valid Unicode (a lone surrogate) has no digest
        return 2, ["goal %s cannot be hashed (%s)" % (gid, e)], [], {}
    facts = {"goal_id": gid, "path": M.rel(cwd, gpath), "status": g.get("status"), "autonomy": g.get("autonomy"),
             "sha256": sha, "ethics": None}
    blocks += derived_problems(cwd, g) if not blocks else []
    if blocks:
        return 2, blocks, asks, facts
    mpath, m = mission_of(cwd)
    msha = M.digest(m) if m else ""
    if not m or m.get("mission_id") != g["mission"]["mission_id"] or msha != g["mission"]["sha256"]:
        blocks.append("the mission changed since %s was derived (or is gone): derive a new goal from the "
                      "approved mission" % gid)
    elif not M.approved(cwd, "mission.approved", M.rel(cwd, mpath), msha):
        asks.append("the mission's content has no human approval (aloop mission approve --by NAME)")
    if m and g["mode"] != m.get("mode"):
        blocks.append("goal mode %s is not the mission's mode %s" % (g["mode"], m.get("mode")))
    if g["autonomy"] != AUTONOMY[g["mode"]]:
        blocks.append("autonomy must be %s for a %s mission" % (AUTONOMY[g["mode"]], g["mode"]))
    if g["principles"]["mode"] != g["mode"]:
        blocks.append("the principles stamp is for %s, the goal is %s" % (g["principles"]["mode"], g["mode"]))
    blocks += P.verify(g["principles"], stale_ok=True)   # an older block is provenance, not a defect
    facts["principles_stale"] = P.stale(g["principles"])
    edoc, _, ebad = eval_record(cwd, g["eval"]["eval_id"])
    if ebad or edoc.get("sha256") != g["eval"]["sha256"]:
        blocks.append("the eval %s this goal came from changed or is gone" % g["eval"]["eval_id"])
    for kind, sid in (("mission", ""), ("goal", gid)):
        rc, bad, ef = E.verdict(cwd, kind, sid)
        if kind == "goal":
            facts["ethics"] = {"tier": ef.get("tier"), "state": ef.get("state"), "review": ef.get("review"),
                               "consent_event": ef.get("consent_event")}
        (blocks if rc == 2 else asks).extend("ethics: %s" % b for b in bad)
    if g["autonomy"] == "reviewed" and not M.approved(cwd, "goal.approved", facts["path"], facts["sha256"]):
        asks.append("spoon-fed: the human approves this goal's content (aloop mission goal approve %s --by NAME)" % gid)
    return (2 if blocks else 3 if asks else 0), blocks, asks, facts


def report(blocks, asks):
    for b in blocks:
        print("BLOCK[goal]: %s" % b, file=sys.stderr)
    for a in asks:
        print("ASK[goal]: %s" % a, file=sys.stderr)


# ── commands ─────────────────────────────────────────────────────────────────────────
def pick_eval(cwd, o):
    eid = o.get("eval") or latest_eval(cwd)
    if not eid:
        return None, None, ["no recorded evaluation yet: the Evaluator runs first (mission tick: ASSESS → EVALUATE)"]
    doc, ev, bad = eval_record(cwd, eid)
    return doc, ev, bad


def cmd_options(cwd, o):
    doc, _, bad = pick_eval(cwd, o)
    if bad:
        report(bad, [])
        return 2
    gaps, held = split_held(cwd, eval_gaps(cwd, doc))
    opts = [dict(g, rank=i) for i, g in enumerate(gaps, 1)]
    if o.get("json"):
        print(json.dumps({"eval_id": doc["eval_id"], "goal_met": doc["goal_met"], "goal_id": doc.get("goal_id"),
                          "options": opts, "held": held}, indent=1))
    else:
        for x in opts:
            print("%d. %s (%s): %s; now %s" % (x["rank"], x["gap_id"], x["kind"], x["summary"], x["now"] or "-"))
        for x in held:
            print("-  %s held by a hard line (never a goal): %s" % (x["gap_id"], x["summary"]))
    return 0


def cmd_derive(cwd, o):
    if M.ledger_block(cwd, "goal"):
        return 2
    mpath, m = mission_of(cwd)
    if not m or m.get("mode") not in AUTONOMY:
        report(["no mission yet (aloop mission init)"], [])
        return 2
    mode = m["mode"]
    if mode == "autonomous" and o.get("pick"):
        print("mission goal derive: --pick is for spoon-fed missions (the human picks); autonomous takes --top N",
              file=sys.stderr)
        return 64
    if mode == "spoon-fed" and (not o.get("pick") or o.get("top")):
        print("mission goal derive: in a spoon-fed mission the human picks the gaps: aloop mission goal options, "
              "then derive --pick GAP_ID[,…] --by NAME", file=sys.stderr)
        return 64
    if mode == "spoon-fed" and not (o.get("by") or "").strip():
        print("mission goal derive: --by NAME (who picked the gaps; the pick is the goal's approval)", file=sys.stderr)
        return 64
    top = DEFAULT_TOP
    if o.get("top"):
        try:
            top = int(o["top"])
        except ValueError:
            top = 0
        if top < 1:
            print("mission goal derive: --top must be a whole number >= 1", file=sys.stderr)
            return 64
    edoc, eev, bad = pick_eval(cwd, o)
    if bad:
        report(bad, [])
        return 2
    latest = latest_eval(cwd)
    if edoc["eval_id"] != latest:
        report(["eval %s is not the latest recorded evaluation (%s): its gaps may be closed since; derive from the "
                "latest" % (edoc["eval_id"], latest)], [])
        return 2
    msha = M.digest(m)
    if not M.approved(cwd, "mission.approved", M.rel(cwd, mpath), msha):
        report([], ["the mission's content has no human approval (aloop mission approve --by NAME)"])
        return 3
    rc, ebad, _ = E.verdict(cwd, "mission", "")
    if rc:
        E.report(rc, ebad)
        return rc
    if edoc.get("goal_id"):
        report(["eval %s is narrowed to goal %s's targets (it was active): a new goal comes from an eval of the "
                "whole mission (close goal %s, then mission evaluate)" % (edoc["eval_id"], edoc["goal_id"], edoc["goal_id"])],
               [])
        return 2
    if edoc["goal_met"]:
        report(["the evaluation %s says the goal is met: go back to research and raise the bar (new journeys "
                "and a new eval), not a goal from this one" % edoc["eval_id"]], [])
        return 2
    gaps, held = split_held(cwd, eval_gaps(cwd, edoc))
    if mode == "spoon-fed":
        want = [x.strip() for x in o["pick"].split(",") if x.strip()]
        by_id = {g["gap_id"]: g for g in gaps}
        stuck = [x for x in want if x in {g["gap_id"] for g in held}]
        if stuck:
            report(["%s held by a hard line: no goal takes it (aloop mission ethics check)" % ", ".join(stuck)], [])
            return 2
        missing = [x for x in want if x not in by_id]
        if missing or not want:
            report(["not an open gap of eval %s: %s (aloop mission goal options)" % (edoc["eval_id"], ", ".join(missing))],
                   [])
            return 2
        chosen = [by_id[x] for x in dict.fromkeys(want)]
    else:
        chosen = gaps[:top]
    if not chosen:
        report(["the evaluation %s lists no open gaps%s" % (edoc["eval_id"], " (%d held by a hard line)" % len(held)
                                                             if held else "")], [])
        return 2
    gid = M.new_id("goal")
    doc = {"goal_id": gid, "mission": {"mission_id": m["mission_id"], "sha256": msha},
           "eval": {"eval_id": edoc["eval_id"], "sha256": edoc["sha256"]},
           "statement": "Close %d gap%s toward the mission: %s" % (len(chosen), "" if len(chosen) == 1 else "s",
                                                                  "; ".join(g["summary"] for g in chosen)),
           "targets": [{"journey_id": g["journey_id"], "criterion_id": g["criterion_id"]} for g in chosen],
           "gaps": chosen, "mode": mode, "autonomy": AUTONOMY[mode], "principles": P.stamp(mode),
           "status": "derived", "created_at": M.now()}
    M.seal(doc)
    path = M.path_of(cwd, "goal", gid)
    M.save(path, doc)
    M.record(cwd, "goal.derived", M.rel(cwd, path), "mission-planner", goal_id=gid, sha256=doc["sha256"],
             mission_id=m["mission_id"], mission_sha256=msha, eval_id=edoc["eval_id"], eval_event=(eev or {}).get("event_id"),
             gaps=[g["gap_id"] for g in chosen], mode=mode, autonomy=doc["autonomy"],
             principles_sha256=doc["principles"]["sha256"])
    if mode == "spoon-fed":
        rc = approve(cwd, gid, o["by"], "picked from eval %s" % edoc["eval_id"])
        if rc:
            return rc
    print(path)
    return 0


def approve(cwd, gid, by, reason):
    path = M.path_of(cwd, "goal", gid)
    doc, err = M.load(path)
    if not isinstance(doc, dict):
        report(["no goal %s" % gid], [])
        return 2
    sha = M.digest(doc)
    try:
        eid = M.approve(cwd, "goal.approved", M.rel(cwd, path), sha, by, reason, goal_id=gid)
    except ValueError as e:
        print("mission goal approve: %s" % e, file=sys.stderr)
        return 64
    appr = doc.get("approval") if isinstance(doc.get("approval"), dict) else {}
    appr["goal"] = {"event_id": eid, "by": by.strip(), "at": M.now()}
    doc["approval"] = appr
    if doc.get("status") == "derived":
        doc["status"] = "approved"
    M.save(path, doc)
    print("[goal] %s approved by %s (sha256 %s)" % (gid, by.strip(), sha[:12]), file=sys.stderr)
    return 0


def intent_tool():
    """plugin/bin/intent as a module (its problems() checks a contract without emitting)."""
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("aloop_intent_tool", os.path.join(BIN, "intent"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def goal_intents(cwd, gid):
    """{gap_id: (path, doc)} of the intents already handed off for this goal: the ones whose own
    intent.received (written by `intent new --source mission`) names the goal and the gap."""
    out, seen = {}, set()
    for e in _events.read(cwd):
        d = e.get("data") or {}
        if e.get("event") != "intent.received" or d.get("intent_id") in seen:
            continue
        seen.add(d.get("intent_id"))      # an intent's first record is the one `intent new` wrote
        if d.get("goal_id") != gid:
            continue
        p = os.path.join(_paths.docs(cwd), "intents", "%s.json" % d.get("intent_id"))
        doc, _ = M.load(p)
        if isinstance(doc, dict) and d.get("gap_id") and doc.get("intent_id") == d.get("intent_id"):
            out.setdefault(d["gap_id"], (p, doc))
    return out


def fill(it, g, gap, m, gref, jref):
    st = g["principles"]
    it.update(
        actor="the app's end users (as the mission's research defines them)",
        want=gap["summary"],
        why="Closes gap %s (%s; target %s; now %s) of goal %s toward the mission: %s"
            % (gap["gap_id"], gap["kind"], gap["target"], gap["now"] or "-", g["goal_id"], m["statement"]),
        constraints=list(m.get("constraints") or []) + [
            "Standing principles (%s mode, sha256 %s): `aloop mission principles show --mode %s`"
            % (st["mode"], st["sha256"][:12], st["mode"]),
            "No principle relaxes an invariant, a gate or a budget cap (ROADMAP §7)"],
        success_scenarios=["journey %s meets criterion %s (%s) in its pinned run, so the goal's next evaluation "
                           "no longer lists gap %s" % (gap["journey_id"], gap["criterion_id"], gap["target"], gap["gap_id"])],
        failure_scenarios=["journey %s still misses criterion %s in its pinned run: gap %s stays open and the goal "
                           "is not met" % (gap["journey_id"], gap["criterion_id"], gap["gap_id"])],
        connections=[gref, jref],
        done_looks_like="The goal's next recorded evaluation no longer lists gap %s" % gap["gap_id"],
        assumptions=["Derived without a model from the Evaluator's gap %s; the Mission Planner may sharpen the "
                     "want and scenarios, never the goal or gap it serves" % gap["gap_id"]])
    it["provenance"]["gap_id"] = gap["gap_id"]
    it["provenance"]["mission_id"] = g["mission"]["mission_id"]
    return it


def cmd_intents(cwd, gid):
    rc, blocks, asks, facts = check(cwd, gid)
    if rc:
        report(blocks, asks)
        return rc
    gpath = M.path_of(cwd, "goal", gid)
    g, _ = M.load(gpath)
    _, m = mission_of(cwd)
    have = goal_intents(cwd, gid)
    tool = intent_tool()
    paths = []
    for gap in g["gaps"]:
        if gap["gap_id"] in have:
            paths.append(have[gap["gap_id"]][0])
            continue
        r = subprocess.run([os.path.join(BIN, "intent"), "new", "--source", "mission", "--goal", gid, "--gap",
                            gap["gap_id"], "--raw", gap["summary"], "--raw-ref", facts["path"]], cwd=cwd,
                           capture_output=True, text=True)
        if r.returncode:
            sys.stderr.write(r.stderr)
            return 2
        p = r.stdout.strip()
        it, _ = M.load(p)
        fill(it, g, gap, m, facts["path"], M.rel(cwd, M.path_of(cwd, "journey", gap["journey_id"])))
        bad = tool.problems(it, p)
        if bad:
            report(["intent %s: %s" % (it.get("intent_id"), b) for b in bad], [])
            return 2
        with open(p, "w") as f:
            json.dump(it, f, indent=1)
        paths.append(p)
    if g.get("status") in ("derived", "approved"):
        g["status"] = "active"
        M.save(gpath, g)
    print("\n".join(paths))
    print("[goal] %s handed off: %d intent(s), %s mode (aloop mission goal next %s)"
          % (gid, len(paths), g["autonomy"], gid), file=sys.stderr)
    return 0


def cmd_next(cwd, gid):
    rc, blocks, asks, facts = check(cwd, gid)
    if rc:
        report(blocks, asks)
        return rc
    if facts["status"] not in ("active",):
        report(["%s is %s: hand it off first (aloop mission goal intents %s)" % (gid, facts["status"], gid)], [])
        return 2
    g, _ = M.load(M.path_of(cwd, "goal", gid))
    have = goal_intents(cwd, gid)
    planned = {(e.get("data") or {}).get("intent_id") for e in _events.read(cwd)
               if e.get("event") in ("plan.created", "plan.revised")}
    for gap in g["gaps"]:
        if gap["gap_id"] not in have:
            continue
        p, it = have[gap["gap_id"]]
        if it.get("intent_id") in planned:
            continue
        others = [os.path.getmtime(x) for x in glob.glob(os.path.join(os.path.dirname(p), "int_*.json")) if x != p]
        t = max(others + [time.time()]) + 0.01
        os.utime(p, (t, t))     # gate-intake re-checks the newest intent file: the one handed off
        r = subprocess.run([os.path.join(BIN, "intent"), "check", p, "--mode", facts["autonomy"]], cwd=cwd,
                           capture_output=True, text=True)
        sys.stderr.write(r.stderr)
        if r.returncode:
            return r.returncode
        print(p)
        return 0
    print("[goal] every intent of %s has a plan; when its sprints close, evaluate and `mission goal close`" % gid,
          file=sys.stderr)
    return 1


def cmd_close(cwd, gid, eid):
    gpath = M.path_of(cwd, "goal", gid)
    g, _ = M.load(gpath)
    if not isinstance(g, dict):
        report(["no goal %s" % gid], [])
        return 2
    try:
        bad = list(M.problems(g, "goal", gpath))
    except ValueError as e:
        bad = ["goal %s cannot be hashed (%s)" % (gid, e)]
    bad = bad or derived_problems(cwd, g)
    if bad:
        report(bad, [])
        return 2
    if M.ledger_block(cwd, "goal"):
        return 2
    if M.records(cwd, "goal.met", M.rel(cwd, gpath)):     # the record decides, never the status field
        print("[goal] %s is already met" % gid, file=sys.stderr)
        return 0
    if g["status"] != "active":
        report(["goal %s is %s: only an active goal closes (handed off with `mission goal intents`)"
                % (gid, g["status"])], [])
        return 2
    doc, ev, bad = eval_record(cwd, eid)
    if bad:
        report(bad, [])
        return 2
    if doc.get("goal_id") != gid:
        report(["eval %s is not an evaluation of goal %s (it evaluates %s): evaluate while the goal is active"
                % (eid, gid, doc.get("goal_id") or "the whole mission")], [])
        return 2
    born = stream_index(cwd, lambda e: e.get("event_id") == derived(cwd, gid)["event_id"])
    seen = stream_index(cwd, lambda e: e.get("event_id") == ev.get("event_id"))
    if born < 0 or seen <= born:
        report(["eval %s was recorded before goal %s was derived: it says nothing about the goal's work" % (eid, gid)], [])
        return 2
    if not doc["goal_met"]:
        still = [x.get("gap_id") for x in doc["gaps"] if isinstance(x, dict)]
        report(["goal %s is not met in eval %s; still open: %s" % (gid, eid, ", ".join(map(str, still)))], [])
        return 2
    g["status"] = "met"
    M.save(gpath, g)
    M.record(cwd, "goal.met", M.rel(cwd, gpath), "evaluator", goal_id=gid, eval_id=eid, eval_event=ev.get("event_id"),
             sha256=M.digest(g))
    print("[goal] %s met (eval %s)" % (gid, eid), file=sys.stderr)
    return 0


def cmd_show(cwd, gid, as_json):
    g, _ = M.load(M.path_of(cwd, "goal", gid))
    if not isinstance(g, dict):
        report(["no goal %s" % gid], [])
        return 2
    rc, blocks, asks, facts = check(cwd, gid)
    block = ""
    try:
        block = P.render(g.get("mode"))
    except (ValueError, TypeError):
        pass
    if as_json:
        print(json.dumps({"goal": g, "check": dict(facts, exit=rc, blocks=blocks, asks=asks), "principles": block},
                         indent=1))
        return 0
    print("Goal %s (%s, %s): %s" % (gid, g.get("status"), g.get("autonomy"), g.get("statement")))
    for gap in g.get("gaps") if isinstance(g.get("gaps"), list) else []:
        if isinstance(gap, dict):
            print("- %s (%s, now %s): %s" % (gap.get("gap_id"), gap.get("kind"), gap.get("now") or "-",
                                                 gap.get("summary")))
    print("Check: %s" % ("ok" if rc == 0 else "; ".join(blocks + asks)))
    sys.stdout.write(block)
    return 0


def parse_args(argv):
    opts, pos, k = {}, [], None
    for a in argv:
        if k:
            opts[k], k = a, None
        elif a == "--json":
            opts["json"] = True
        elif a.startswith("--"):
            k = a[2:].replace("-", "_")
        else:
            pos.append(a)
    if k:
        opts[k] = ""
    return opts, pos


def main(argv, cwd):
    cmd, (o, pos) = argv[0], parse_args(argv[1:])
    if cmd == "options":
        return cmd_options(cwd, o)
    if cmd == "derive":
        return cmd_derive(cwd, o)
    if cmd not in ("check", "approve", "intents", "next", "close", "show"):
        print("mission goal: unknown command %r (options, derive, check, approve, intents, next, close, show)" % cmd,
              file=sys.stderr)
        return 64
    if not pos or not GOAL_ID.match(pos[0]):
        print("mission goal %s GOAL_ID (gol_…)" % cmd, file=sys.stderr)
        return 64
    gid = pos[0]
    if cmd == "check":
        rc, blocks, asks, facts = check(cwd, gid)
        if o.get("json"):
            print(json.dumps(dict(facts, exit=rc, blocks=blocks, asks=asks), indent=1))
        report(blocks, asks)
        if rc == 0 and not o.get("json"):
            print("[goal] %s ok (%s)" % (gid, facts["autonomy"]), file=sys.stderr)
        return rc
    if cmd == "approve":
        if not (o.get("by") or "").strip():
            print("mission goal approve: --by NAME is required", file=sys.stderr)
            return 64
        return approve(cwd, gid, o["by"], o.get("reason", ""))
    if cmd == "intents":
        return cmd_intents(cwd, gid)
    if cmd == "next":
        return cmd_next(cwd, gid)
    if cmd == "close":
        if not o.get("eval"):
            print("mission goal close GOAL_ID --eval EVAL_ID", file=sys.stderr)
            return 64
        return cmd_close(cwd, gid, o["eval"])
    return cmd_show(cwd, gid, o.get("json"))
