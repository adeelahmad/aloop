"""mission evaluate — the Evaluator's gap list: the product now against its baseline (ROADMAP §4.12, M9.10).

The gap list is a pure function of a state report, the baseline it measured against and the
active goal's targets, so any model re-running it gets the same answer (and `evaluate check`
proves it). The Evaluator never builds; it adds rival comparisons as sourced claims.

  regression  a criterion that passed at baseline and fails now (always a gap, goal or not)
  open        a target criterion that fails now
  unscored    a target criterion on a judged metric no judge has scored yet
  earned      a criterion that failed at baseline and passes now

Gaps are ordered regressions first, then open gaps closest to their target first (ship small),
then unscored. The goal is met when no target criterion is a gap and nothing regressed. With
no active goal, every criterion the baseline froze is a target (the definition of best).

Usage:
  mission evaluate [STATE_ID] [--rival REF]… [--json]
        evaluate a state report (default: the latest); writes docs/agents/mission/eval/<id>.json
        (sealed) and emits eval.completed. Exit 0 · 2 block (the state is not intact or its runs disagree)
  mission evaluate check [EVAL_ID] [--json]
        the gap list is intact and follows from its state, baseline and goal. Exit 0 · 2
"""
import json
import os
import sys

import _mission as M
import _runner
import assess as A
import baseline as B
import journeys as J

KIND = "eval"


def active_goal(cwd):
    """The goal being worked, from the records: the newest goal whose current content a
    goal.derived (or the human's goal.approved) record covers and no later goal.met record closed.
    A status field activates nothing. A goal (M9.11) names its targets as [{"journey_id", "criterion_id"}]."""
    best = None
    for path, d in M.artifacts(cwd, "goal"):
        ref, sha = M.rel(cwd, path), M.digest(d)
        starts = [r["ts"] for ev in ("goal.derived", "goal.approved") for r in M.records(cwd, ev, ref)
                  if r["data"].get("sha256") == sha]
        if starts and not any(r["ts"] >= max(starts) for r in M.records(cwd, "goal.met", ref)):
            if best is None or max(starts) > best[0]:
                best = (max(starts), d)
    return best[1] if best else None


def goal_problems(cwd, goal, bdoc):
    """A goal's targets are criteria the baseline froze: a target that names nothing would drop
    every open gap and read as GOAL MET with nothing measured."""
    t = goal.get("targets")
    if not isinstance(t, list) or not t or not all(isinstance(x, dict) and isinstance(x.get("journey_id"), str)
                                                   and isinstance(x.get("criterion_id"), str) for x in t):
        return ["goal %s: targets must be a non-empty list of {journey_id, criterion_id}" % goal.get("goal_id")]
    have = set()
    for j in bdoc.get("journeys") or []:
        jd, _ = J.load(cwd, j["journey_id"])
        have |= {(j["journey_id"], c.get("id")) for c in (jd or {}).get("pass_criteria") or [] if isinstance(c, dict)}
    return ["goal %s targets %s#%s, which baseline %s does not freeze" % (goal.get("goal_id"), x["journey_id"],
            x["criterion_id"], bdoc.get("baseline_id")) for x in t if (x["journey_id"], x["criterion_id"]) not in have]


def target_of(c):
    return "every step passes" if c.get("steps") == "all" else "%s %s %s" % (c.get("metric"), c.get("op"), c.get("value"))


def distance(c, now_metrics):
    """How far a failing metric criterion is from its target, relative to the target (0..)."""
    if c.get("steps") == "all":
        return 1.0
    v = now_metrics.get(c.get("metric"))
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not isinstance(c.get("value"), (int, float)):
        return 1.0
    return round(abs(v - c["value"]) / max(abs(c["value"]), 1e-9), 6)


def input_problems(cwd, bdoc, goal):
    """Why the gap list cannot be computed from these inputs: the baseline must be content a
    confirmation covers, each journey it lists the content it froze (never whatever the file says
    now), and the goal's targets criteria it froze."""
    if M.malformed(M.problems(bdoc, B.KIND)):
        return ["baseline %s is malformed" % bdoc.get("baseline_id")]
    sha = M.digest(bdoc)
    if not any(r["data"].get("sha256") == sha for r in M.records(cwd, B.CONFIRM, M.rel(cwd, B.bpath(cwd, bdoc["baseline_id"])))):
        return ["baseline %s is not the content its confirmation covers (edited since?)" % bdoc["baseline_id"]]
    out = []
    for j in bdoc.get("journeys") or []:
        jd, _ = J.load(cwd, j["journey_id"])
        if not isinstance(jd, dict):
            out.append("journey %s is gone" % j["journey_id"])
        elif M.digest(J.unretired(jd)) != j["sha256"]:
            out.append("journey %s is not the content baseline %s froze" % (j["journey_id"], bdoc["baseline_id"]))
    return out or (goal_problems(cwd, goal, bdoc) if goal else [])


def compute(cwd, state, bdoc, goal):
    """{gaps, earned, regressions, metrics, goal_met} from a state, its baseline and the goal."""
    base_runs = (bdoc.get("measurements") or {}).get("runs") or [{"journeys": {}}]
    base, now = base_runs[0]["journeys"], state["runs"][0]["journeys"]
    targets = {(t["journey_id"], t["criterion_id"]) for t in (goal or {}).get("targets") or []}
    gaps, earned, metrics = [], [], []
    for j in bdoc["journeys"]:
        jid = j["journey_id"]
        jd, _ = J.load(cwd, jid)
        if not isinstance(jd, dict):       # retired since or not, the frozen content is what was measured
            continue
        b_crit = {c["id"]: c for c in (base.get(jid) or {}).get("criteria", [])}
        n_rec = now.get(jid) or {"criteria": [], "metrics": {}}
        n_crit = {c["id"]: c for c in n_rec.get("criteria", [])}
        tol = {m["id"]: max(bdoc.get("tolerance", 0), m.get("tolerance", 0)) for m in jd["metrics"]}
        for c in jd["pass_criteria"]:
            cid = c["id"]
            targeted = not targets or (jid, cid) in targets
            was, now_c = (b_crit.get(cid) or {}).get("passed"), n_crit.get(cid) or {"passed": False, "why": "not measured"}
            row = {"gap_id": "%s#%s" % (jid, cid), "journey_id": jid, "criterion_id": cid,
                   "now": now_c.get("why", ""), "baseline": (b_crit.get(cid) or {}).get("why", ""), "target": target_of(c)}
            if was is True and now_c["passed"] is not True:
                gaps.append(dict(row, kind="regression", distance=distance(c, n_rec.get("metrics", {}))))
            elif now_c["passed"] is True:
                if was is not True:
                    earned.append(row)
            elif targeted:
                kind = "unscored" if now_c["passed"] is None else "open"
                gaps.append(dict(row, kind=kind, distance=distance(c, n_rec.get("metrics", {}))))
        for m in jd["metrics"]:
            if m["query"].get("kind") == "ga4":
                continue
            bv = ((base.get(jid) or {}).get("metrics") or {}).get(m["id"])
            nv = (n_rec.get("metrics") or {}).get(m["id"])
            d = _runner.drift(bv, nv) if bv is not None and nv is not None else None
            if d is None:
                trend = "unmeasured"
            elif d <= tol.get(m["id"], 0):
                trend = "same"
            else:   # booleans compare too: True > False
                trend = "better" if ((nv < bv) if m["direction"] == "lower" else (nv > bv)) else "worse"
            metrics.append({"journey_id": jid, "metric": m["id"], "baseline": bv, "now": nv, "trend": trend})
    order = {"regression": 0, "open": 1, "unscored": 2}
    gaps.sort(key=lambda g: (order[g["kind"]], g["distance"], g["gap_id"]))
    regressions = sum(1 for g in gaps if g["kind"] == "regression")
    return {"gaps": gaps, "earned": earned, "regressions": regressions, "metrics": metrics, "goal_met": not gaps}


def load_state(cwd, sid):
    path, doc = (M.path_of(cwd, "state", sid), M.load(M.path_of(cwd, "state", sid))[0]) if sid else A.latest(cwd)
    return path, doc


def cmd_evaluate(cwd, sid, o):
    spath, state = load_state(cwd, sid)
    if not isinstance(state, dict):
        print("BLOCK[evaluate]: no state report%s" % (" " + sid if sid else " yet (mission assess)"), file=sys.stderr)
        return 2
    bad = A.problems(cwd, spath, state)
    if not bad and not state.get("agreed"):
        bad.append("its runs disagree (%s): a flaky measurement is fixed before it is evaluated"
                   % "; ".join(state.get("disagreements") or []))
    bdoc, _ = M.load(B.bpath(cwd, state.get("baseline_id", "")))
    goal = active_goal(cwd)
    bad += input_problems(cwd, bdoc, goal) if isinstance(bdoc, dict) and not bad else []
    bad += J.ref_problems(cwd, o.get("rival", []), "rival")
    if bad:
        for b in bad:
            print("BLOCK[evaluate]: %s" % b, file=sys.stderr)
        return 2
    res = compute(cwd, state, bdoc, goal)
    eid = M.new_id(KIND)
    doc = dict(res, eval_id=eid, state_id=state["state_id"], baseline_id=bdoc["baseline_id"],
               goal_id=(goal or {}).get("goal_id"), rival_refs=o.get("rival", []), status="recorded", created_at=M.now())
    M.seal(doc)
    path = M.save(M.path_of(cwd, KIND, eid), doc)
    M.emit(cwd, "eval.completed", role="evaluator", refs={"eval": M.rel(cwd, path), "state": M.rel(cwd, spath)},
           eval_id=eid, sha256=doc["sha256"], state_id=state["state_id"], goal_id=doc["goal_id"],
           goal_met=res["goal_met"], gaps=len(res["gaps"]), regressions=res["regressions"])
    if o.get("json"):
        print(json.dumps(doc, indent=1))
    else:
        print(path)
        print("%s: %d gap(s), %d regression(s), %d earned" % (
            "GOAL MET" if res["goal_met"] else "goal not met", len(res["gaps"]), res["regressions"], len(res["earned"])),
            file=sys.stderr)
    return 0


def latest(cwd):
    rows = M.artifacts(cwd, KIND)
    return max(rows, key=lambda pd: (pd[1].get("created_at", ""), pd[0])) if rows else (None, None)


def problems(cwd, path, doc):
    out = M.problems(doc, KIND, path)
    if out:
        return out
    if "sha256" not in doc:
        out.append("a gap list is sealed when it is written")
    spath = M.path_of(cwd, "state", doc["state_id"])
    state, _ = M.load(spath)
    if not isinstance(state, dict):
        return out + ["state %s does not exist" % doc["state_id"]]
    out += ["state %s: %s" % (doc["state_id"], p) for p in A.problems(cwd, spath, state)]
    if not out and not state.get("agreed"):
        out.append("state %s: its runs disagree: a flaky measurement is never evaluated" % doc["state_id"])
    bdoc, _ = M.load(B.bpath(cwd, doc["baseline_id"]))
    if not isinstance(bdoc, dict) or bdoc.get("baseline_id") != state.get("baseline_id"):
        return out + ["baseline %s is not the one state %s measured against" % (doc["baseline_id"], doc["state_id"])]
    goal = None
    if doc.get("goal_id"):
        goal, _ = M.load(M.path_of(cwd, "goal", doc["goal_id"]))
        if not isinstance(goal, dict):
            return out + ["goal %s does not exist" % doc["goal_id"]]
    out += input_problems(cwd, bdoc, goal)
    if out:
        return out
    again = compute(cwd, state, bdoc, goal)
    for k in ("gaps", "earned", "regressions", "metrics", "goal_met"):
        if again[k] != doc.get(k):
            out.append("%s does not follow from state %s (recomputed: %s)" % (k, doc["state_id"], json.dumps(again[k])[:160]))
    out += J.ref_problems(cwd, doc.get("rival_refs"), "rival")
    return out


def cmd_check(cwd, eid, as_json):
    path, doc = (M.path_of(cwd, KIND, eid), M.load(M.path_of(cwd, KIND, eid))[0]) if eid else latest(cwd)
    if not isinstance(doc, dict):
        print("BLOCK[evaluate]: no gap list%s" % (" " + eid if eid else " yet (mission evaluate)"), file=sys.stderr)
        return 2
    bad = problems(cwd, path, doc)
    for b in bad:
        print("BLOCK[evaluate]: %s: %s" % (doc.get("eval_id"), b), file=sys.stderr)
    if as_json:
        print(json.dumps({"eval_id": doc.get("eval_id"), "problems": bad}, indent=1))
    return 2 if bad else 0


def parse(argv):
    return J.parse(argv, multi=("rival",), known=("rival",))


NO_ARGS = True


def main(argv, cwd):
    check = bool(argv) and argv[0] == "check"
    o, pos = parse(argv[1:] if check else argv)
    if J.unknown_flags(o, "evaluate"):
        return 64
    if check:
        return cmd_check(cwd, pos[0] if pos else "", o.get("json"))
    return cmd_evaluate(cwd, pos[0] if pos else "", o)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], os.getcwd()))
