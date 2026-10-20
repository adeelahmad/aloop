"""mission assess — the State Assessor's measurement of the product now (ROADMAP §4.12, M9.10).

It runs exactly what the confirmed baseline froze — every journey's pinned runner and every
benchmark, twice — and records the numbers with the commit they measured. It reports and
never interprets: the Evaluator compares (mission evaluate).

Usage:
  mission assess [--runs 2] [--json]
        needs a confirmed, intact baseline (mission baseline check); writes
        docs/agents/mission/state/<id>.json (sealed) and emits state.assessed. Runs that
        disagree beyond the baseline's tolerance are recorded (agreed: false) and block.
        Exit 0 · 2 block · 3 needs the human (no confirmed baseline, or it needs re-confirming)
  mission assess check [ID] [--json]
        a state report is intact: sealed, and it measured the journeys its baseline froze
"""
import json
import os
import subprocess
import sys

import _mission as M
import _paths
import _runner
import baseline as B
import journeys as J

KIND = "state"


def head(cwd):
    top = _paths.project(cwd)

    def git(*a):
        r = subprocess.run(["git", "-C", top] + list(a), capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""
    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain", "--untracked-files=no"))}


def judged(cwd, bdoc):
    """Judge verdicts for judged metrics from the judges area (M9.5): {journey_id: {metric_id:
    {"value", "refs"}}}. A journey's judged metric is judged with the journey id as the subject,
    against the rubric content the journey pinned; a verdict that is not pass or fail (incomplete,
    inconclusive, blocked), or a judge that cannot check the pin, leaves it pending."""
    try:
        import judge
    except ImportError:
        return {}
    verdict = getattr(judge, "verdict", None)
    if verdict is None:
        return {}
    out = {}
    for j in bdoc["journeys"]:
        jd, _ = J.load(cwd, j["journey_id"])
        for m in (jd or {}).get("metrics", []):
            if m["query"].get("kind") == "judge":
                try:
                    v = verdict(cwd, m["query"].get("rubric"), j["journey_id"], rubric_sha256=m["query"].get("rubric_sha256"))
                except TypeError:
                    continue
                if isinstance(v, dict) and v.get("verdict") in ("pass", "fail") and _runner.finite(v.get("score")):
                    out.setdefault(j["journey_id"], {})[m["id"]] = {"value": v["score"], "refs": v.get("run_ids") or []}
    return out


def with_judged(runs, scores, cwd):
    """Fill judged metric values into each run and re-score its criteria."""
    if not scores:
        return runs
    for run in runs:
        for jid, rec in run["journeys"].items():
            if jid in scores:
                rec["metrics"].update({mid: v["value"] for mid, v in scores[jid].items()})
                jd, _ = J.load(cwd, jid)
                rec["criteria"] = J.score(jd, rec)
                rec["passed"] = J.verdict(rec["criteria"])
    return runs


def cmd_assess(cwd, o):
    rc, bad, facts = B.evaluate(cwd)
    if rc:
        for b in bad:
            print("%s[assess]: %s" % ("ASK" if rc == 3 else "BLOCK", b), file=sys.stderr)
        return rc
    _, bdoc = B.current(cwd)
    try:
        n = max(2, int(o.get("runs") or 2))
    except ValueError:
        n = 2
    runs = []
    for i in range(n):
        res, errs = B.measure_once(cwd, bdoc)
        if errs:
            for e in errs:
                print("BLOCK[assess]: run %d: %s" % (i + 1, e), file=sys.stderr)
            return 2
        runs.append(res)
    scores = judged(cwd, bdoc)
    runs = with_judged(runs, scores, cwd)
    diffs, _ = B.disagreements(cwd, bdoc, runs)
    sid = M.new_id(KIND)
    doc = {"state_id": sid, "baseline_id": bdoc["baseline_id"], "baseline_version": bdoc["version"],
           "direction_sha256": bdoc["direction_sha256"], "head": head(cwd), "runs": runs, "agreed": not diffs,
           "disagreements": diffs, "judged": scores, "status": "recorded", "created_at": M.now()}
    M.seal(doc)
    path = M.save(M.path_of(cwd, KIND, sid), doc)
    M.record(cwd, "state.assessed", M.rel(cwd, path), "state-assessor", sha256=doc["sha256"], state_id=sid,
             baseline_id=bdoc["baseline_id"], agreed=not diffs, commit=doc["head"]["commit"])
    if o.get("json"):
        print(json.dumps(doc, indent=1))
    else:
        print(path)
    for d in diffs:
        print("BLOCK[assess]: not reproducible now: %s" % d, file=sys.stderr)
    return 2 if diffs else 0


def latest(cwd):
    """The newest state report `mission assess` recorded (a file with no record is not one)."""
    rows = [(p, d) for p, d in M.artifacts(cwd, KIND)
            if (M.newest(cwd, "state.assessed", M.rel(cwd, p)) or {}).get("data", {}).get("sha256") == M.digest(d)]
    return max(rows, key=lambda pd: (str(pd[1].get("created_at", "")), pd[0])) if rows else (None, None)


def run_shape(run, n):
    """The shape every reader of a run relies on (the schema stops at the run's keys)."""
    js = run.get("journeys")
    if not isinstance(js, dict) or not isinstance(run.get("benchmarks"), dict):
        return ["run %d: journeys and benchmarks must be objects" % n]
    for jid, rec in js.items():
        if not isinstance(rec, dict) or not isinstance(rec.get("metrics"), dict) or rec.get("passed") not in (True, False, None) \
                or not isinstance(rec.get("criteria"), list) or \
                not all(isinstance(c, dict) and isinstance(c.get("id"), str) for c in rec["criteria"]):
            return ["run %d: journey %s is not a scored run record {criteria, metrics, passed, sha256}" % (n, jid)]
    return []


def problems(cwd, path, doc):
    out = M.problems(doc, KIND, path)
    if out:
        return out
    if "sha256" not in doc:
        out.append("a state report is sealed when it is written")
    rec = M.newest(cwd, "state.assessed", M.rel(cwd, path))
    if not rec or rec["data"].get("sha256") != M.digest(doc):
        out.append("state %s has no state.assessed record for this content: only `mission assess` measures" % doc["state_id"])
    out += [x for i, run in enumerate(doc["runs"]) for x in run_shape(run, i + 1)]
    if out:
        return out
    bdoc, _ = M.load(B.bpath(cwd, doc["baseline_id"]))
    if not isinstance(bdoc, dict):
        return out + ["baseline %s does not exist" % doc["baseline_id"]]
    frozen = {j["journey_id"]: j["sha256"] for j in bdoc.get("journeys", [])}
    for i, run in enumerate(doc["runs"]):
        for jid, rec in (run.get("journeys") or {}).items():
            if jid not in frozen:
                out.append("run %d measured journey %s, which baseline %s does not freeze" % (i + 1, jid, bdoc["baseline_id"]))
            elif rec.get("sha256") != frozen[jid]:
                out.append("run %d measured a different version of journey %s than the baseline froze" % (i + 1, jid))
        missing = sorted(set(frozen) - set(run.get("journeys") or {}))
        if missing:
            out.append("run %d skipped journeys %s" % (i + 1, ", ".join(missing)))
    return out


def cmd_check(cwd, sid, as_json):
    path, doc = (M.path_of(cwd, KIND, sid), M.load(M.path_of(cwd, KIND, sid))[0]) if sid else latest(cwd)
    if not isinstance(doc, dict):
        print("BLOCK[assess]: no state report%s" % (" " + sid if sid else " yet (mission assess)"), file=sys.stderr)
        return 2
    bad = problems(cwd, path, doc)
    for b in bad:
        print("BLOCK[assess]: %s: %s" % (doc.get("state_id"), b), file=sys.stderr)
    if as_json:
        print(json.dumps({"state_id": doc.get("state_id"), "problems": bad}, indent=1))
    return 2 if bad else 0


def parse(argv):
    return J.parse(argv, multi=(), known=("runs",))


NO_ARGS = True


def main(argv, cwd):
    check = bool(argv) and argv[0] == "check"
    o, pos = parse(argv[1:] if check else argv)
    if J.unknown_flags(o, "assess"):
        return 64
    if check:
        return cmd_check(cwd, pos[0] if pos else "", o.get("json"))
    if pos:
        print("mission assess: unknown command %r (mission assess --help)" % pos[0], file=sys.stderr)
        return 64
    return cmd_assess(cwd, o)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], os.getcwd()))
