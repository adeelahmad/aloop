"""mission baseline — where the product stands today, measured the same way every time (ROADMAP §4.12, M9.2).

A baseline lists the journeys that define "best" (each by its sha256), optional benchmark
scripts and the Product Tester's rival results, and is measured by running every journey's
pinned runner (and every benchmark) twice: the runs must agree within the tolerance. The
human confirms it in plain language; that confirmation freezes the version and its
journeys. It is the mission's RED test: a baseline whose journeys all pass already has
nothing to earn and is refused. A replacement needs evidence that the old one no longer
holds; in an autonomous mission it applies itself after the mission's veto window.

Which version is current, what was measured and what was vetoed are read from the ledger
(M.records), never from a status field: status is outside the digest, so it proves nothing.

Usage:
  mission baseline new --definition "what best looks like, in plain words" [--journey ID]…
        [--benchmark FILE.json] [--rival REF]… [--tolerance 0.05] [--supersedes BLN --evidence REF…]
        list the journeys (default: every proposed or active one) and write a proposed version
        bound to the approved mission; emits baseline.proposed
  mission baseline measure ID [--runs 2] [--json]
        run every journey runner and benchmark --runs times; agreed within tolerance and at
        least one journey failing → measured (sealed); records baseline.measured. The veto
        window starts at that record. Exit 0 · 2 not reproducible / nothing to earn / a
        listed journey changed
  mission baseline confirm ID --by NAME [--reason TEXT]
        the human confirms a measured version: records baseline.confirmed for its sha256,
        freezes its journeys (active) and supersedes the previous confirmed version
  mission baseline apply ID
        an autonomous mission's replacement applies itself once the veto window has passed
        with no veto. Exit 0 · 2 not eligible · 3 needs the human (window open, or spoon-fed)
  mission baseline veto ID --by NAME --reason TEXT
        the human refuses a proposed or measured version; the same content never applies itself again
  mission baseline restore ID --by NAME [--reason TEXT]
        the human reinstates an archived version, unchanged since it was confirmed, over the current one
  mission baseline check [--json]
        the current confirmed version is intact: sealed, confirmed for exactly this content,
        bound to the mission as approved now, its journeys unchanged. Exit 0 · 2 · 3
  mission baseline show [ID] [--json] · mission baseline current
"""
import calendar
import json
import math
import os
import sys
import time

import _mission as M
import _paths
import _runner
import journeys as J

KIND = "baseline"
DEFAULT_TOLERANCE = 0.05
DEFAULT_VETO_H = 24
CONFIRM = "baseline.confirmed"
# what a veto refuses: the content, under whatever id, version or evidence it comes back
NOT_CONTENT = M.UNHASHED + ("baseline_id", "version", "created_at", "supersedes", "evidence_refs", "measurements")


def bpath(cwd, bid):
    return M.path_of(cwd, KIND, bid)


def versions(cwd):
    return M.artifacts(cwd, KIND)


def current(cwd):
    """(path, doc) of the version the newest confirmation record names (a human's, or an
    autonomous mission's veto-window application), or (None, None). doc is None when that file
    is gone or unreadable: the path still says a confirmed version exists."""
    rows = M.records(cwd, CONFIRM)
    if not rows:
        return None, None
    path = os.path.join(_paths.project(cwd), rows[-1]["data"].get("subject", ""))
    doc, _ = M.load(path)
    return path, doc if isinstance(doc, dict) else None


def current_id(cwd):
    path, _ = current(cwd)
    return os.path.basename(path)[:-len(".json")] if path else None


def epoch(ts):
    try:
        return calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return None


def say(level, msg):
    print("%s[baseline]: %s" % (level, msg), file=sys.stderr)


def content_sha(doc):
    return M.digest(doc, drop=NOT_CONTENT)


def listing_problems(cwd, listed):
    """What a listing of journeys loses from the current confirmed version: every pinned journey
    is listed at its pinned content, and a frozen one is listed, or retired (with a human when
    that weakens the evaluation) with its replacement listed as the retirement recorded it."""
    frozen, pinned = J.pins(cwd)
    have = {j.get("journey_id"): j.get("sha256") for j in listed if isinstance(j, dict)}
    out = []
    for jid, sha in sorted(have.items()):
        if jid in pinned and sha != pinned[jid]:
            out.append("journey %s is listed at other content than it was pinned to: a changed journey is a new "
                       "journey that supersedes it" % jid)
        jd, _ = J.load(cwd, jid)
        if isinstance(jd, dict) and J.lifecycle(cwd, J.jpath(cwd, jid), jd, frozen) == "retired":
            out.append("journey %s is retired" % jid)
    _, cur = current(cwd)
    for j in (cur or {}).get("journeys") or []:
        x, seen = j.get("journey_id"), set()
        while x not in have and x not in seen:
            seen.add(x)
            jd, _ = J.load(cwd, x)
            if not isinstance(jd, dict) or J.lifecycle(cwd, J.jpath(cwd, x), jd, frozen) != "retired":
                out.append("the new version drops journey %s: retire it first (mission journeys retire)" % x)
                break
            x = jd["retirement"].get("replaced_by")
            if not x:
                break       # retired with no replacement: a human approved that while it was pinned
    return out


def standing_problems(cwd, doc):
    """Why this version could not take over from the current one now (empty: it could)."""
    out = []
    for j in doc["journeys"]:
        jd, _ = J.load(cwd, j["journey_id"])
        if not isinstance(jd, dict):
            out.append("journey %s no longer exists" % j["journey_id"])
        elif M.digest(jd) != j["sha256"]:
            out.append("journey %s changed since this baseline listed it: propose a new baseline" % j["journey_id"])
    out += listing_problems(cwd, doc["journeys"])
    _, msha, mev = M.mission(cwd)
    if not mev or msha != doc.get("mission_sha256"):
        out.append("the mission changed since this baseline was proposed (or is not approved): propose a new one")
    cur = current_id(cwd)
    if doc.get("supersedes") != cur:
        out.append("%s supersedes %s, but the current confirmed version is %s: propose a new version against it"
                   % (doc["baseline_id"], doc.get("supersedes") or "nothing", cur or "none"))
    if doc.get("supersedes"):
        out += J.evidence_problems(cwd, doc.get("evidence_refs"), "evidence") if doc.get("evidence_refs") else \
            ["a replacement baseline needs evidence that the old one no longer holds (evidence_refs)"]
    return out


def cmd_new(cwd, o):
    mdoc, msha, mev = M.mission(cwd)
    if not mev:
        say("ASK", "the mission is not approved yet (mission check): a baseline is measured for an approved mission")
        return 3
    pin = J.pins(cwd)
    ids = o.get("journey") or [d["journey_id"] for p, d in J.all_journeys(cwd) if isinstance(d.get("journey_id"), str)
                               and J.lifecycle(cwd, p, d, pin[0]) != "retired"]
    bad, listed = [], []
    for jid in ids:
        p = J.jpath(cwd, jid)
        d, _ = M.load(p)
        if not isinstance(d, dict):
            bad.append("journey %s does not exist" % jid)
            continue
        bad += ["journey %s: %s" % (jid, x) for x in J.problems(cwd, p, d, pin)]
        listed.append({"journey_id": jid, "sha256": M.digest(d)})
    if not ids:
        bad.append("no journeys to measure: the researchers propose them first (mission journeys new)")
    benches = []
    if o.get("benchmark"):
        benches, err = M.load(o["benchmark"])
        if not isinstance(benches, list):
            bad.append("--benchmark must be a JSON list of {id, name, command, pinned, source_refs}: %s" % (err or "not a list"))
            benches = []
        for b in benches:
            if isinstance(b, dict):
                b["files"], fbad = J.runner_files(cwd, b)
                bad += ["benchmark %s: %s" % (b.get("id"), x) for x in fbad]
                if not b.get("pinned"):
                    bad.append("benchmark %s: pinned names at least one tool, its version and a probe" % b.get("id"))
    bad += listing_problems(cwd, listed)
    cur_path, cur = current(cwd)
    if o.get("supersedes"):
        if o["supersedes"] != current_id(cwd):
            bad.append("supersedes %s, which is not the current confirmed version (%s)" % (o["supersedes"], current_id(cwd) or "none"))
        bad += J.evidence_problems(cwd, o.get("evidence"), "evidence") if o.get("evidence") else \
            ["a replacement baseline needs evidence that the old one no longer holds (--evidence REF)"]
    elif cur_path:
        bad.append("a confirmed baseline exists (%s): a new version names it with --supersedes and --evidence" % current_id(cwd))
    try:
        tol = float(o.get("tolerance") or DEFAULT_TOLERANCE)
    except ValueError:
        tol = -1
    bid = M.new_id(KIND)
    doc = {"baseline_id": bid, "version": (cur or {}).get("version", 0) + 1 if o.get("supersedes") else 1,
           "mission_sha256": msha, "definition_of_best": (o.get("definition") or "").strip(), "journeys": listed,
           "benchmarks": benches, "rivals": o.get("rival", []), "tolerance": tol,
           "supersedes": o.get("supersedes"), "evidence_refs": o.get("evidence", []), "measurements": None,
           "status": "proposed", "created_at": M.now()}
    bad += [x for x in M.problems(doc, KIND) if not x.startswith("$.journeys needs")]
    bad += J.ref_problems(cwd, doc["rivals"], "rival")
    for b in benches:
        if isinstance(b, dict):
            bad += J.ref_problems(cwd, b.get("source_refs"), "benchmark %s" % b.get("id"))
    if bad:
        for b in bad:
            say("BLOCK", b)
        return 2
    path = M.save(bpath(cwd, bid), doc)
    M.emit(cwd, "baseline.proposed", role="mission", refs={"baseline": M.rel(cwd, path)}, baseline_id=bid,
           version=doc["version"], journeys=[j["journey_id"] for j in listed])
    print(path)
    return 0


def measure_once(cwd, doc):
    """One run of every listed journey and benchmark: ({"journeys": {id: rec}, "benchmarks": {id: res}}, problems)."""
    out, bad = {"journeys": {}, "benchmarks": {}}, []
    for j in doc["journeys"]:
        jd, _ = J.load(cwd, j["journey_id"])
        rec, err = J.run_journey(cwd, jd)
        if rec is None:
            bad.append("journey %s: %s" % (j["journey_id"], err))
        elif rec["unreported"]:   # a criterion on a value nobody measured is not a measured failure
            bad.append("journey %s: the runner did not report %s" % (j["journey_id"], ", ".join(rec["unreported"])))
        else:
            out["journeys"][j["journey_id"]] = rec
    for b in doc["benchmarks"]:
        res, err = _runner.run(b, _paths.project(cwd), extra_env={"ALADDIN_BENCHMARK": b["id"]})
        if res is None:
            bad.append("benchmark %s: %s" % (b["id"], err))
        else:
            out["benchmarks"][b["id"]] = res["metrics"]
    return out, bad


def disagreements(cwd, doc, runs):
    """Every measurement that differs between runs by more than its tolerance."""
    out, worst = [], 0.0
    first = runs[0]
    for other in runs[1:]:
        for jid, rec in first["journeys"].items():
            o = other["journeys"].get(jid, {})
            jd, _ = J.load(cwd, jid)
            tols = {m["id"]: max(doc["tolerance"], m.get("tolerance", 0)) for m in jd.get("metrics", [])}
            if rec["steps"] != o.get("steps"):
                out.append("journey %s: step results differ between runs (%s vs %s)" % (jid, rec["steps"], o.get("steps")))
            if rec["passed"] != o.get("passed"):
                out.append("journey %s: passed %s in one run and %s in another" % (jid, rec["passed"], o.get("passed")))
            for mid, v in rec["metrics"].items():
                d = _runner.drift(v, o.get("metrics", {}).get(mid))
                worst = max(worst, d)
                if not d <= tols.get(mid, doc["tolerance"]):
                    out.append("journey %s metric %s: %s vs %s drifts %.1f%% (> %.1f%%)" % (
                        jid, mid, v, o.get("metrics", {}).get(mid), 100 * d, 100 * tols.get(mid, doc["tolerance"])))
        for bid, ms in first["benchmarks"].items():
            for mid, v in ms.items():
                d = _runner.drift(v, other["benchmarks"].get(bid, {}).get(mid))
                worst = max(worst, d)
                if not d <= doc["tolerance"]:
                    out.append("benchmark %s metric %s drifts %.1f%% (> %.1f%%)" % (bid, mid, 100 * d, 100 * doc["tolerance"]))
    return out, worst


def cmd_measure(cwd, bid, o):
    path = bpath(cwd, bid)
    doc, err = M.load(path)
    if not isinstance(doc, dict):
        say("BLOCK", "no baseline %s (%s)" % (bid, err))
        return 2
    bad = M.problems(doc, KIND, path)
    if not M.malformed(bad):
        if doc.get("status") != "proposed":
            bad.append("%s is %s: only a proposed version is measured (a new version supersedes it)" % (bid, doc.get("status")))
        bad += standing_problems(cwd, doc)
    if bad:
        for b in bad:
            say("BLOCK", b)
        return 2
    try:
        n = max(2, int(o.get("runs") or 2))
    except ValueError:
        n = 2
    runs = []
    for i in range(n):
        res, errs = measure_once(cwd, doc)
        if errs:
            for e in errs:
                say("BLOCK", "run %d: %s" % (i + 1, e))
            return 2
        runs.append(res)
    diffs, worst = disagreements(cwd, doc, runs)
    failing = [jid for jid, rec in runs[0]["journeys"].items() if rec["passed"] is False]
    pending = [jid for jid, rec in runs[0]["journeys"].items() if rec["passed"] is None]
    doc["measurements"] = {"at": M.now(), "runs": runs, "agreed": not diffs,
                           "max_drift": round(worst, 6) if math.isfinite(worst) else None,
                           "not_passing": failing, "pending_judge": pending}
    if diffs:
        M.save(path, doc)
        for d in diffs:
            say("BLOCK", "not reproducible: %s" % d)
        return 2
    if not failing:
        M.save(path, doc)
        if pending:
            say("BLOCK", "no journey fails yet and %d wait for a judge score (mission judge): a baseline shows what is "
                         "left to earn only once a journey demonstrably fails" % len(pending))
        else:
            say("BLOCK", "nothing to earn: every journey already passes at baseline. The definition of best is the "
                         "mission's RED test; the researchers raise the bar (new or stricter journeys) first")
        return 2
    doc["status"] = "measured"
    M.seal(doc)
    M.save(path, doc)
    M.record(cwd, "baseline.measured", M.rel(cwd, path), "state-assessor", sha256=doc["sha256"], baseline_id=bid,
             runs=n, max_drift=doc["measurements"]["max_drift"], not_passing=failing)
    if o.get("json"):
        print(json.dumps(doc["measurements"], indent=1))
    print("[baseline] %s measured: %d runs agree (max drift %.1f%%); %d of %d journeys failing now" % (
        bid, n, 100 * worst, len(failing), len(doc["journeys"])), file=sys.stderr)
    return 0


def measurement_problems(doc):
    """The measurement rules, re-checked from the recorded runs (not from a flag a file carries)."""
    m = doc.get("measurements") or {}
    runs = m.get("runs") if isinstance(m.get("runs"), list) else []
    out = []
    if len(runs) < 2:
        out.append("it was measured %d time(s): a baseline is measured at least twice" % len(runs))
    elif not m.get("agreed"):
        out.append("its measurements did not agree")
    first = (runs[0] if runs and isinstance(runs[0], dict) else {}).get("journeys") or {}
    if runs and not any(isinstance(r, dict) and r.get("passed") is False for r in first.values()):
        out.append("no journey failed when it was measured: a baseline with nothing to earn is refused")
    return out


def freeze(cwd, doc, path, by="", reason="", window=None):
    """Record the confirmation (a human's, or an autonomous mission's veto window passing), freeze
    the journeys and mark the previous version superseded."""
    prev_path, prev = current(cwd)
    data = {"baseline_id": doc["baseline_id"], "version": doc["version"]}
    if window is None:
        eid = M.approve(cwd, CONFIRM, M.rel(cwd, path), M.digest(doc), by, reason, **data)
    else:     # not a human decision: recorded as the mission's, so no reader mistakes it for one
        by = "veto window (%gh, autonomous mission)" % window
        eid = M.record(cwd, CONFIRM, M.rel(cwd, path), "mission", sha256=M.digest(doc), applied="veto_window",
                       window_h=window, by=by, **data)["event_id"]
    if prev and prev_path != path:
        prev["status"] = "superseded"
        M.save(prev_path, prev)
        M.emit(cwd, "baseline.superseded", role="mission", refs={"baseline": M.rel(cwd, prev_path)},
               baseline_id=prev["baseline_id"], superseded_by=doc["baseline_id"])
    for j in doc["journeys"]:
        jp = J.jpath(cwd, j["journey_id"])
        jd, _ = M.load(jp)
        if jd.get("status") != "active":
            jd["status"] = "active"
            M.seal(jd)
            M.save(jp, jd)
    doc["status"], doc["approval"] = "confirmed", {"event_id": eid, "by": by, "at": M.now()}
    M.save(path, doc)
    return eid


def measured_ok(cwd, bid):
    """(doc, path, its baseline.measured record, problems) of a version about to be confirmed or applied."""
    path = bpath(cwd, bid)
    doc, err = M.load(path)
    if not isinstance(doc, dict):
        return None, path, None, ["no baseline %s (%s)" % (bid, err)]
    bad = M.problems(doc, KIND, path)
    if M.malformed(bad):
        return doc, path, None, bad
    rec = M.newest(cwd, "baseline.measured", M.rel(cwd, path))
    if not rec or rec["data"].get("sha256") != M.digest(doc):
        return doc, path, None, bad + ["%s has no baseline.measured record for this content: only a script's "
                                       "measurement counts (mission baseline measure %s)" % (bid, bid)]
    if doc.get("status") != "measured":
        bad.append("%s is %s: only a measured version is confirmed or applied" % (bid, doc.get("status")))
    bad += measurement_problems(doc) + standing_problems(cwd, doc)
    return doc, path, rec, bad


def cmd_confirm(cwd, bid, o):
    doc, path, _, bad = measured_ok(cwd, bid)
    if bad:
        for b in bad:
            say("BLOCK", b)
        return 2
    try:
        freeze(cwd, doc, path, o.get("by", ""), o.get("reason", ""))
    except ValueError as e:
        print("baseline confirm: %s" % e, file=sys.stderr)
        return 64
    print("[baseline] %s (v%d) confirmed by %s; %d journeys frozen" % (bid, doc["version"], o["by"], len(doc["journeys"])),
          file=sys.stderr)
    return 0


def vetoed(cwd, path, doc):
    """The human's veto of this version, or of the same content under any other id, or None."""
    c = content_sha(doc)
    for r in M.records(cwd, "baseline.vetoed", role="human"):
        if r["data"].get("subject") == M.rel(cwd, path) or r["data"].get("content_sha256") == c:
            return r
    return None


def cmd_apply(cwd, bid):
    doc, path, rec, bad = measured_ok(cwd, bid)
    if bad:
        for b in bad:
            say("BLOCK", b)
        return 2
    mdoc, _, _ = M.mission(cwd)
    if not doc.get("supersedes"):
        say("ASK", "the first baseline is confirmed by the human (mission baseline confirm %s --by NAME)" % bid)
        return 3
    if mdoc.get("mode") != "autonomous":
        say("ASK", "a spoon-fed mission confirms every baseline: mission baseline confirm %s --by NAME" % bid)
        return 3
    v = vetoed(cwd, path, doc)
    if v:
        say("BLOCK", "%s is content the human vetoed (%s, %s): only the human confirms it now" % (bid, v["data"].get("by"), v["ts"]))
        return 2
    _, cur = current(cwd)
    prev_tol = (cur or {}).get("tolerance", DEFAULT_TOLERANCE)
    if doc["tolerance"] > prev_tol:
        say("ASK", "%s tolerates more drift than %s (%g > %g): the human confirms a looser gate"
            % (bid, doc["supersedes"], doc["tolerance"], prev_tol))
        return 3
    window = mdoc.get("veto_window_h", DEFAULT_VETO_H)
    if isinstance(window, bool) or not isinstance(window, (int, float)) or not math.isfinite(window) or window < 0:
        say("BLOCK", "the mission's veto_window_h %r is not a number of hours >= 0" % (window,))
        return 2
    since = epoch(rec["ts"])
    left = (since or 0) + window * 3600 - time.time()
    if since is None or left > 0:
        say("ASK", "the veto window is open for %.1f more hour(s); the human may veto (mission baseline veto %s)"
            % (max(left, 0) / 3600.0, bid))
        return 3
    freeze(cwd, doc, path, window=window)
    print("[baseline] %s (v%d) applied after the %gh veto window" % (bid, doc["version"], window), file=sys.stderr)
    return 0


def cmd_restore(cwd, bid, o):
    """The human reinstates an archived version (one a confirmation once covered, unchanged since):
    an autonomous replacement is reversible."""
    if not (o.get("by") or "").strip():
        print("baseline restore: a human restores a version (--by NAME)", file=sys.stderr)
        return 64
    path = bpath(cwd, bid)
    doc, err = M.load(path)
    if not isinstance(doc, dict):
        say("BLOCK", "no baseline %s (%s)" % (bid, err))
        return 2
    if bid == current_id(cwd):
        say("BLOCK", "%s is the current version already" % bid)
        return 2
    sha = M.digest(doc)
    was = [r for r in M.records(cwd, CONFIRM, M.rel(cwd, path)) if r["data"].get("sha256") == sha]
    bad = M.problems(doc, KIND, path)
    if not was:
        bad.append("%s was never confirmed with this content: only an archived version is restored" % bid)
    if not M.malformed(bad):
        for j in doc["journeys"]:
            jd, _ = J.load(cwd, j["journey_id"])
            if not isinstance(jd, dict) or M.digest(J.unretired(jd)) != j["sha256"]:
                bad.append("journey %s changed or is gone since %s froze it: propose a new version" % (j["journey_id"], bid))
            elif J.lifecycle(cwd, J.jpath(cwd, j["journey_id"]), jd) == "retired":
                bad.append("journey %s was retired since: propose a new version without it" % j["journey_id"])
        _, msha, mev = M.mission(cwd)
        if not mev or msha != doc.get("mission_sha256"):
            bad.append("%s was confirmed for an earlier mission content: propose a new version" % bid)
    if bad:
        for b in bad:
            say("BLOCK", b)
        return 2
    prev_path, prev = current(cwd)
    try:
        eid = M.approve(cwd, CONFIRM, M.rel(cwd, path), sha, o.get("by", ""), o.get("reason", ""),
                        baseline_id=bid, version=doc["version"], action="restore",
                        replaces=(prev or {}).get("baseline_id"))
    except ValueError as e:
        print("baseline restore: %s" % e, file=sys.stderr)
        return 64
    if prev and prev_path != path:
        prev["status"] = "superseded"
        M.save(prev_path, prev)
    doc["status"], doc["approval"] = "confirmed", {"event_id": eid, "by": o["by"], "at": M.now()}
    M.save(path, doc)
    print("[baseline] %s (v%d) restored by %s" % (bid, doc["version"], o["by"]), file=sys.stderr)
    return 0


def cmd_veto(cwd, bid, o):
    path = bpath(cwd, bid)
    doc, err = M.load(path)
    if not isinstance(doc, dict) or doc.get("status") not in ("proposed", "measured") or bid == current_id(cwd):
        say("BLOCK", "%s is not a proposed or measured version" % bid)
        return 2
    by, reason = (o.get("by") or "").strip(), (o.get("reason") or "").strip()
    if not by or not reason:
        print("baseline veto: --by NAME and --reason TEXT", file=sys.stderr)
        return 64
    M.record_human(cwd, "baseline.vetoed", M.rel(cwd, path), by, reason, baseline_id=bid, content_sha256=content_sha(doc))
    doc["status"] = "vetoed"
    M.save(path, doc)
    print("[baseline] %s vetoed by %s" % (bid, by), file=sys.stderr)
    return 0


def evaluate(cwd):
    """(exit code, problems, facts) of the current confirmed version."""
    path, doc = current(cwd)
    if not path:
        return 3, ["no confirmed baseline yet: measure one and the human confirms it"], {}
    facts = {"path": M.rel(cwd, path)}
    if doc is None:
        return 2, ["the confirmed version %s is gone or not a JSON object" % facts["path"]], facts
    bad = M.problems(doc, KIND, path) + M.ledger_problems(cwd)
    facts.update({"baseline_id": doc.get("baseline_id"), "version": doc.get("version")})
    if M.malformed(bad):
        return 2, bad, facts
    rec = M.newest(cwd, CONFIRM, M.rel(cwd, path))
    if rec["data"].get("sha256") != M.digest(doc):
        bad.append("its content changed after it was confirmed")
    bad += measurement_problems(doc)
    if doc.get("status") != "confirmed":
        bad.append("%s is the current version but says %s (the records decide)" % (doc["baseline_id"], doc.get("status")))
    for _, d in versions(cwd):
        if d.get("status") == "confirmed" and d.get("baseline_id") != doc["baseline_id"]:
            bad.append("%s says confirmed, but the records make %s current" % (d.get("baseline_id"), doc["baseline_id"]))
    asks, pin = [], J.pins(cwd)
    for j in doc["journeys"]:
        jp = J.jpath(cwd, j["journey_id"])
        jd, _ = M.load(jp)
        if not isinstance(jd, dict):
            bad.append("journey %s is gone" % j["journey_id"])
            continue
        jbad = J.problems(cwd, jp, jd, pin)
        bad += ["journey %s: %s" % (j["journey_id"], x) for x in jbad]
        if not jbad and J.lifecycle(cwd, jp, jd, pin[0]) == "retired":
            asks.append("journey %s was retired: a new baseline version supersedes this one" % j["journey_id"])
    if bad:
        return 2, bad, facts
    _, msha, mev = M.mission(cwd)
    if not mev or msha != doc["mission_sha256"]:
        asks.append("the baseline was confirmed for an earlier mission content: the human re-confirms the direction")
    if asks:
        return 3, asks, facts
    facts["confirmed_by"] = rec["data"].get("by")
    return 0, [], facts


def cmd_check(cwd, as_json):
    rc, bad, facts = evaluate(cwd)
    for b in bad:
        say("ASK" if rc == 3 else "BLOCK", b)
    if as_json:
        print(json.dumps({"exit": rc, "problems": bad, **facts}, indent=1))
    elif rc == 0:
        print("[baseline] %s (v%s) ok, confirmed by %s" % (facts["baseline_id"], facts["version"], facts["confirmed_by"]),
              file=sys.stderr)
    return rc


def cmd_show(cwd, bid, as_json):
    path, doc = (bpath(cwd, bid), M.load(bpath(cwd, bid))[0]) if bid else current(cwd)
    if not isinstance(doc, dict) or not isinstance(doc.get("measurements") or {}, dict):
        print("baseline: none", file=sys.stderr)
        return 3
    if as_json:
        print(json.dumps(doc, indent=1))
        return 0
    m = doc.get("measurements") or {}
    print("Baseline %s v%s (%s)\nBest looks like: %s\nJourneys: %d · failing now: %s · max drift %s "
          "(tolerance %g)" % (doc.get("baseline_id"), doc.get("version"), doc.get("status"), doc.get("definition_of_best"),
                              len(doc.get("journeys") or []), ", ".join(m.get("not_passing") or []) or "-",
                              m.get("max_drift", "-"), doc.get("tolerance", DEFAULT_TOLERANCE)))
    return 0


def parse(argv):
    return J.parse(argv, multi=("journey", "rival", "evidence"))


def main(argv, cwd):
    cmd, (o, pos) = argv[0], parse(argv[1:])
    if cmd == "new":
        return cmd_new(cwd, o)
    if cmd == "measure" and pos:
        return cmd_measure(cwd, pos[0], o)
    if cmd == "confirm" and pos:
        return cmd_confirm(cwd, pos[0], o)
    if cmd == "apply" and pos:
        return cmd_apply(cwd, pos[0])
    if cmd == "veto" and pos:
        return cmd_veto(cwd, pos[0], o)
    if cmd == "restore" and pos:
        return cmd_restore(cwd, pos[0], o)
    if cmd == "check":
        return cmd_check(cwd, o.get("json"))
    if cmd == "show":
        return cmd_show(cwd, pos[0] if pos else "", o.get("json"))
    if cmd == "current":
        _, doc = current(cwd)
        if doc:
            print(doc["baseline_id"])
        return 0 if doc else 3
    print("mission baseline: unknown or incomplete command (mission baseline --help)", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], os.getcwd()))
