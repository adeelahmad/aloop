"""mission journeys — the user journeys the evaluation layer runs (ROADMAP §4.12, M9.3).

A journey is a user's path through the product, with scripted steps (each emits a usage
event), metrics (each with a query definition), pass criteria, a pinned runner and the
research claims it comes from. Journeys are the mission's RED test: once a confirmed
baseline covers one it is frozen, and removing or weakening it needs a human. What a journey
is (proposed, active, retired) is read from the ledger, never from its status field; the
runner's script is pinned by its sha256 like the tools it runs.

Usage:
  mission journeys new --from DRAFT.json [--title T] [--persona P]
        issue the harness id and write docs/agents/mission/journeys/<id>.json (proposed);
        emits journey.proposed. Prints the path. Only a complete journey is written: a draft
        that `check` would block is refused with every problem, and nothing is written (so keep
        the draft outside the project). --title and --persona fill in what the draft leaves
        out. Exit 0 · 2
  mission journeys check [ID…] [--json]
        every journey that is not retired (or the ones named): schema; unique ids; each step's
        event is a valid GA4 event name; each metric has a query definition; a pass criterion
        names `steps: all` or a script/judge metric (usage telemetry is never a gate); every
        source ref is <research_id>#<claim_id> and that claim exists; an active journey is
        unchanged since a confirmed baseline froze it. Exit 0 · 2 block
  mission journeys run ID [--json]
        run its pinned runner once and score the pass criteria (judge metrics stay pending
        until `mission judge` scores them); emits journey.run
  mission journeys diff OLD NEW [--json]
        what NEW drops or weakens from OLD: a step, its event, a metric, its query, a criterion
        or its threshold, or the runner. Exit 0 none (or a human approved exactly this pair) · 2 weakened
  mission journeys approve OLD [--replaced-by NEW] --by NAME --reason TEXT
        a human approves retiring OLD as it stands, for exactly that replacement (or none)
  mission journeys retire OLD --evidence REF… [--replaced-by NEW]
        retire with evidence (a research claim, or an eval, state or research id). A pinned
        journey (frozen by a confirmed baseline, or the replacement of one) retires without a
        human only when NEW, another valid journey, keeps everything OLD checks; NEW is then
        pinned at that content. Exit 0 · 2 block · 3 needs the human
  mission journeys list
"""
import glob
import json
import os
import re
import sys

import _mission as M
import _paths
import _runner

KIND = "journey"
REF = re.compile(r"^(rsr_[0-9a-f]{20})#([A-Za-z0-9_.-]+)$")
GA4_RESERVED = ("firebase_", "google_", "ga_")
# event names GA4 and the Measurement Protocol reserve: a step that emits one never gets its own history
GA4_RESERVED_NAMES = frozenset((
    "ad_activeview", "ad_click", "ad_exposure", "ad_impression", "ad_query", "ad_reward", "adunit_exposure",
    "app_background", "app_clear_data", "app_exception", "app_remove", "app_store_refund",
    "app_store_subscription_cancel", "app_store_subscription_convert", "app_store_subscription_renew",
    "app_update", "app_upgrade", "dynamic_link_app_open", "dynamic_link_app_update", "dynamic_link_first_open",
    "error", "first_open", "first_visit", "in_app_purchase", "notification_dismiss", "notification_foreground",
    "notification_open", "notification_receive", "os_update", "screen_view", "session_start", "user_engagement",
    # also refused by the telemetry area (M9.6), which imports this list
    "app_install", "notification_send", "session_start_with_rollout"))
GATING = ("script", "judge")       # metric kinds a pass criterion may use
LOOSER = {"<=": 1, "<": 1, ">=": -1, ">": -1}   # sign of a value change that loosens the criterion
TIGHTER = {("<=", "<"), (">=", ">")}             # (old op, new op): the new one accepts less at the same value


def jpath(cwd, jid):
    return M.path_of(cwd, KIND, jid)


def load(cwd, jid):
    doc, err = M.load(jpath(cwd, jid))
    return doc, err


def all_journeys(cwd):
    return M.artifacts(cwd, KIND)


def ref_problems(cwd, refs, where, what="source ref"):
    out = []
    for r in refs or []:
        m = REF.match(str(r))
        if not m:
            out.append("%s %s %r must be <research_id>#<claim_id>" % (where, what, r))
            continue
        doc, _ = M.load(M.path_of(cwd, "research", m.group(1)))
        if not isinstance(doc, dict):
            out.append("%s %s %s: research %s does not exist" % (where, what, r, m.group(1)))
        elif m.group(2) not in [c.get("claim_id", c.get("id")) for c in doc.get("claims") or [] if isinstance(c, dict)]:
            out.append("%s %s %s: research %s has no claim %s" % (where, what, r, m.group(1), m.group(2)))
    return out


EVIDENCE = re.compile(r"^(evl|sta|rsr)_[0-9a-f]{20}$")
EVIDENCE_KIND = {"evl": "eval", "sta": "state", "rsr": "research"}


# an eval or a state is evidence only as its command recorded it (a file nobody computed is not)
EVIDENCE_RECORD = {"eval": "eval.completed", "state": "state.assessed"}


def evidence_problems(cwd, refs, where):
    """Evidence is a research claim (<research_id>#<claim_id>) or an eval, state or research id
    that exists (an eval or state with the ledger record of its content): a placeholder string is
    not evidence."""
    out = []
    for r in refs or []:
        m = EVIDENCE.match(str(r))
        if m:
            kind = EVIDENCE_KIND[m.group(1)]
            path = M.path_of(cwd, kind, r)
            doc = M.load(path)[0]
            rec = M.newest(cwd, EVIDENCE_RECORD[kind], M.rel(cwd, path)) if kind in EVIDENCE_RECORD else None
            if not isinstance(doc, dict):
                out.append("%s %s does not exist" % (where, r))
            elif kind in EVIDENCE_RECORD and (rec or {}).get("data", {}).get("sha256") != M.digest(doc):
                out.append("%s %s has no %s record for this content" % (where, r, EVIDENCE_RECORD[kind]))
        elif REF.match(str(r)):
            out += ref_problems(cwd, [r], where, "evidence")
        else:
            out.append("%s %r must be <research_id>#<claim_id> or an eval, state or research id" % (where, r))
    return out


def unretired(doc):
    """The journey as it stood before its retirement was recorded."""
    return {k: v for k, v in doc.items() if k != "retirement"}


def retired(cwd, path, doc):
    """The journey.retired record of this journey, or None. The record decides: a status field or
    a retirement block alone proves nothing, and editing a retired file never un-retires it
    (problems() reports the edit)."""
    return M.newest(cwd, "journey.retired", M.rel(cwd, path)) if path else None


def pins(cwd):
    """(frozen, pinned): {journey_id: sha256} of every journey a confirmed baseline version froze
    (the content it froze first: a frozen journey never changes; a new version is a new journey),
    and that plus every journey that replaced a pinned one, at the content its retirement recorded.
    Weakening or dropping a pinned journey needs a human."""
    frozen = {}
    for rec in M.records(cwd, "baseline.confirmed"):     # the records' list: an edited baseline file unfreezes nothing
        js = rec["data"].get("journeys")
        if not isinstance(js, list):                      # a record written before confirmations carried it
            b, _ = M.load(os.path.join(_paths.project(cwd), str(rec["data"].get("subject", ""))))
            js = b.get("journeys") if isinstance(b, dict) else []
        for j in js or []:
            if isinstance(j, dict) and isinstance(j.get("journey_id"), str):
                frozen.setdefault(j["journey_id"], j.get("sha256"))
    pinned = dict(frozen)
    rets = M.records(cwd, "journey.retired")             # the records, so hiding a file unpins nothing
    grew = True
    while grew:
        grew = False
        for r in rets:
            jid, nxt = r["data"].get("journey_id"), r["data"].get("replaced_by")
            if isinstance(jid, str) and jid in pinned and isinstance(nxt, str) and nxt not in pinned:
                pinned[nxt] = r["data"].get("replaced_by_sha256")
                grew = True
    return frozen, pinned


def lifecycle(cwd, path, doc, frozen=None):
    """What the records say a journey is: retired, active (a confirmed baseline froze it) or proposed."""
    if retired(cwd, path, doc):
        return "retired"
    jid = doc.get("journey_id")
    return "active" if isinstance(jid, str) and jid in (pins(cwd)[0] if frozen is None else frozen) else "proposed"


INLINE = ("-c", "-e", "--eval", "--command")       # flags that run code given in argv instead of a pinned file


def runner_files(cwd, runner):
    """({path: sha256}, problems) of the project files the runner's command names, plus any it
    lists in `files`: the script is part of the measurement, so editing it is a new journey
    version. A link (its target can change), inline code or a script outside the project could
    change what is measured without changing a pinned file, so each is refused."""
    top = os.path.realpath(_paths.project(cwd))
    cmd, listed, bad = runner.get("command"), runner.get("files"), []
    if not isinstance(cmd, list) or not all(isinstance(c, str) for c in cmd):
        return {}, ["runner.command must be an argv list of strings"]
    if listed is not None and not isinstance(listed, (list, dict)):
        return {}, ["runner.files must be a list of project paths"]
    listed = [n for n in listed or [] if isinstance(n, str)]
    bad += ["runner runs inline code (%s): put it in a project script, which is then pinned" % a for a in cmd[1:] if a in INLINE]
    out = {}
    for i, n in enumerate(cmd + listed):
        p = os.path.abspath(os.path.join(top, n))
        if not os.path.lexists(p):
            if i >= len(cmd):
                bad.append("runner.files names %s, which is not a file in the project" % n)
            continue
        if not p.startswith(top + os.sep):      # the program itself (/usr/bin/python3) is a tool, pinned by version
            if i > 0 and os.path.isfile(p):
                bad.append("runner names %s, outside the project: copy it into the project, where it is pinned" % n)
        elif os.path.islink(p) or os.path.realpath(p) != p:
            bad.append("runner names a symlink %s: name the script itself, so its content is what is pinned" % n)
        elif os.path.isfile(p):
            out[os.path.relpath(p, top)] = _runner.file_sha(p)
    return out, bad


def rubric_problems(cwd, mid, q):
    """A judged metric pins its rubric's content (query.rubric_sha256), so a weaker rubric (a lower
    pass mark, a dropped criterion) is a change to this journey that its approval does not cover."""
    rdoc, _ = M.load(M.path_of(cwd, "rubric", q["rubric"]))
    sha = M.digest(rdoc) if isinstance(rdoc, dict) else ""
    if not q.get("rubric_sha256"):
        return ["metric %s does not pin its rubric's content (query.rubric_sha256%s)"
                % (mid, " = " + sha if sha else "")]
    if not sha:
        return ["metric %s names rubric %s, which does not exist" % (mid, q["rubric"])]
    if q["rubric_sha256"] != sha:
        return ["metric %s: rubric %s changed since this journey pinned it (re-pin it in a new journey version)"
                % (mid, q["rubric"])]
    return []


def problems(cwd, path, doc, pin=None):
    """One line per problem of a journey; pin is pins(cwd), passed in when checking many."""
    if not isinstance(doc, dict):
        return ["not a JSON object"]
    out = M.problems(doc, KIND, path)
    if M.malformed(out):
        return out      # shape is wrong: the semantic checks below would only add noise
    jid = doc["journey_id"]
    for field in ("steps", "metrics", "pass_criteria"):
        ids = [x.get("id") for x in doc[field]]
        dup = sorted(set(i for i in ids if ids.count(i) > 1))
        if dup:
            out.append("%s ids repeat: %s" % (field, ", ".join(dup)))
    for s in doc["steps"]:
        if s["event"].lower().startswith(GA4_RESERVED):
            out.append("step %s event %r uses a GA4-reserved prefix (%s)" % (s["id"], s["event"], ", ".join(GA4_RESERVED)))
        elif s["event"] in GA4_RESERVED_NAMES:
            out.append("step %s event %r is a name GA4 reserves for itself" % (s["id"], s["event"]))
    metrics = {m["id"]: m for m in doc["metrics"]}
    for m in doc["metrics"]:
        q = m["query"]
        if q.get("kind") == "judge" and not q.get("rubric"):
            out.append("metric %s is judged but names no rubric (query.rubric)" % m["id"])
        elif q.get("kind") == "judge":
            out += rubric_problems(cwd, m["id"], q)
        if q.get("kind") == "ga4" and not (q.get("report") or {}).get("metrics"):
            out.append("metric %s reads GA4 but its query.report names no metrics" % m["id"])
        out += ref_problems(cwd, m["source_refs"], "metric %s" % m["id"])
    for c in doc["pass_criteria"]:
        if "steps" in c and "metric" in c:
            out.append("criterion %s names both steps and a metric (one each)" % c["id"])
        elif "steps" in c:
            continue
        elif "metric" not in c or "op" not in c or "value" not in c:
            out.append("criterion %s needs `steps: all` or metric + op + value" % c["id"])
        elif c["metric"] not in metrics:
            out.append("criterion %s names metric %s, which the journey does not define" % (c["id"], c["metric"]))
        elif metrics[c["metric"]]["query"].get("kind") not in GATING:
            out.append("criterion %s gates on %s metric %s: usage telemetry informs goals and is never a gate"
                       % (c["id"], metrics[c["metric"]]["query"].get("kind"), c["metric"]))
    out += ref_problems(cwd, doc["source_refs"], "journey")
    if not doc["runner"].get("pinned"):
        out.append("runner.pinned names at least one tool, its version and a probe: an unpinned runner is not reproducible")
    for p in (doc["runner"].get("pinned") or {}).values():
        if not isinstance(p, dict) or not p.get("version") or not _runner.argv_ok(p.get("probe")):
            out.append("runner.pinned entries need a version and a probe command (an argv list)")
            break
    files = doc["runner"].get("files") or {}
    if not all(isinstance(v, str) and re.match(r"^[0-9a-f]{64}$", v) for v in files.values()):
        out.append("runner.files maps each path to the sha256 the harness pinned (mission journeys new)")
    else:
        want, fbad = runner_files(cwd, dict(doc["runner"], files=list(files)))
        out += fbad
        out += ["runner.files does not pin %s, which the command runs" % n for n in sorted(set(want) - set(files))]
        if not files and not fbad:
            out.append("runner.files is empty: the command names no project script, so nothing pins what it measures")
        if not retired(cwd, path, doc):          # a retired journey is no longer measured
            out += _runner.file_problems(files, _paths.project(cwd))
    if doc.get("supersedes"):
        old, _ = load(cwd, doc["supersedes"])
        if not isinstance(old, dict):
            out.append("supersedes %s, which does not exist" % doc["supersedes"])
        if not doc.get("evidence_refs"):
            out.append("a journey that supersedes another needs evidence_refs")
        out += evidence_problems(cwd, doc.get("evidence_refs"), "evidence")
    frozen, pinned = pins(cwd) if pin is None else pin
    if jid in pinned and M.digest(unretired(doc)) != pinned[jid]:
        out.append("journey %s changed after it was %s: propose a new journey that supersedes it" % (
            jid, "frozen by a confirmed baseline" if jid in frozen else "recorded as a replacement"))
    rec = retired(cwd, path, doc)
    if rec and rec["data"].get("sha256") != M.digest(doc):
        out.append("journey %s changed after it was retired (its journey.retired record covers other content)" % jid)
    life = lifecycle(cwd, path, doc, frozen)
    if doc.get("status") != life:
        out.append("journey %s says %s but its records say %s (the status field is not a record)"
                   % (jid, doc.get("status"), life))
    return out


def cmd_new(cwd, o):
    draft = {}
    if o.get("from"):
        draft, err = M.load(o["from"])
        if not isinstance(draft, dict):
            print("BLOCK[journeys]: cannot read the draft %s: %s" % (o["from"], err), file=sys.stderr)
            return 2
    jid = M.new_id(KIND)
    doc = {"journey_id": jid, "title": o.get("title", ""), "persona": o.get("persona", ""), "steps": [],
           "metrics": [], "pass_criteria": [], "runner": {"command": []}, "source_refs": []}
    doc.update({k: v for k, v in draft.items() if k not in ("journey_id", "status", "sha256", "approval", "retirement")})
    doc.update({"journey_id": jid, "status": "proposed", "created_at": M.now()})
    bad = []
    if isinstance(doc.get("runner"), dict):
        files, bad = runner_files(cwd, doc["runner"])
        doc["runner"] = dict(doc["runner"], files=files)
    bad += [b for b in problems(cwd, jpath(cwd, jid), doc) if b not in bad]
    if bad:     # its role can never rewrite or delete a written journey, so a broken one would block for good
        for b in bad:
            print("BLOCK[journeys]: %s" % b, file=sys.stderr)
        print("journeys: nothing written: fix the draft (keep it outside the project) and run "
              "`mission journeys new --from DRAFT.json` again", file=sys.stderr)
        return 2
    path = M.save(jpath(cwd, jid), doc)
    M.emit(cwd, "journey.proposed", role="mission", refs={"journey": M.rel(cwd, path)}, journey_id=jid)
    print(path)
    return 0


def cmd_check(cwd, ids, as_json):
    """Every journey file (retired ones too: their records are checked) or the ones named; a file
    that is not a JSON object fails the check instead of being skipped."""
    rows, report, rc = [], [], 0
    for p in sorted(glob.glob(M.mdir(cwd, M.LAYOUT[KIND], "*.json"))):
        d, err = M.load(p)
        if not isinstance(d, dict):
            if not ids:
                report.append({"journey_id": os.path.basename(p), "problems": ["not a JSON object (%s)" % (err or type(d).__name__)]})
        elif not ids or d.get("journey_id") in ids:
            rows.append((p, d))
    missing = [i for i in ids if i not in [d.get("journey_id") for _, d in rows]]
    for i in missing:
        report.append({"journey_id": i, "problems": ["no such journey"]})
    pin = pins(cwd)
    have = [d.get("journey_id") for _, d in rows]
    for jid in sorted(pin[1]) if not ids else []:
        if jid not in have and not os.path.exists(jpath(cwd, jid)):
            report.append({"journey_id": jid, "problems": ["pinned (%s) but its file is gone" % (
                "frozen by a confirmed baseline" if jid in pin[0] else "a recorded replacement")]})
    for p, d in rows:
        report.append({"journey_id": d.get("journey_id"), "status": d.get("status"), "problems": problems(cwd, p, d, pin)})
    led = M.ledger_problems(cwd)
    if led:
        report.append({"journey_id": None, "problems": led})
    if not rows and not ids and not report:
        report.append({"journey_id": None, "problems": ["no journeys yet: the researchers propose them (mission journeys new)"]})
    for r in report:
        for b in r["problems"]:
            rc = 2
            print("BLOCK[journeys]: %s: %s" % (r["journey_id"], b), file=sys.stderr)
    if as_json:
        print(json.dumps(report, indent=1))
    elif rc == 0:
        print("[journeys] %d journey(s) ok" % len(rows), file=sys.stderr)
    return rc


def compare(value, op, target):
    if op == "==":
        return value == target
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return {"<=": value <= target, "<": value < target, ">=": value >= target, ">": value > target}[op]


def score(doc, result):
    """Per criterion: {"id", "passed": True|False|None, "why"}; None = a judge metric not scored yet."""
    metrics = {m["id"]: m for m in doc["metrics"]}
    out = []
    for c in doc["pass_criteria"]:
        if c.get("steps") == "all":
            failed = [s["id"] for s in doc["steps"] if result["steps"].get(s["id"]) is not True]
            out.append({"id": c["id"], "passed": not failed,
                        "why": "steps failed or not reported: %s" % ", ".join(failed) if failed else "every step passed"})
            continue
        v = result["metrics"].get(c["metric"])
        if v is None:
            judged = metrics.get(c["metric"], {}).get("query", {}).get("kind") == "judge"
            out.append({"id": c["id"], "passed": None if judged else False,
                        "why": "%s not %s" % (c["metric"], "scored by a judge yet" if judged else "reported")})
            continue
        good = compare(v, c["op"], c["value"])
        out.append({"id": c["id"], "passed": good, "why": "%s = %s %s %s" % (c["metric"], v, c["op"], c["value"])})
    return out


def verdict(criteria):
    """True every criterion passed · False one failed · None nothing failed but some are pending."""
    if any(c["passed"] is False for c in criteria):
        return False
    return None if any(c["passed"] is None for c in criteria) else True


def run_journey(cwd, doc):
    """(record, problem): one run of a journey's runner, scored."""
    res, err = _runner.run(doc["runner"], _paths.project(cwd), extra_env={"ALADDIN_JOURNEY": doc["journey_id"]})
    if res is None:
        return None, err
    crit = score(doc, res)
    unreported = [s["id"] for s in doc["steps"] if s["id"] not in res["steps"]] + \
        [m["id"] for m in doc["metrics"] if m["query"].get("kind") == "script" and m["id"] not in res["metrics"]]
    return {"journey_id": doc["journey_id"], "sha256": M.digest(doc), "steps": res["steps"],
            "metrics": res["metrics"], "criteria": crit, "passed": verdict(crit), "exit": res["exit"],
            "unreported": unreported}, ""


def cmd_run(cwd, jid, as_json):
    path = jpath(cwd, jid)
    doc, err = M.load(path)
    if not isinstance(doc, dict):
        print("BLOCK[journeys]: no journey %s (%s)" % (jid, err), file=sys.stderr)
        return 2
    bad = problems(cwd, path, doc)
    if bad:
        for b in bad:
            print("BLOCK[journeys]: %s: %s" % (jid, b), file=sys.stderr)
        return 2
    rec, err = run_journey(cwd, doc)
    if rec is None:
        print("BLOCK[journeys]: %s: the runner did not produce a result: %s" % (jid, err), file=sys.stderr)
        return 2
    M.emit(cwd, "journey.run", role="mission", refs={"journey": M.rel(cwd, path)}, journey_id=jid, passed=rec["passed"])
    if as_json:
        print(json.dumps(rec, indent=1))
    else:
        for c in rec["criteria"]:
            print("%s %s — %s" % ({True: "PASS", False: "FAIL", None: "PENDING"}[c["passed"]], c["id"], c["why"]))
        if rec["unreported"]:
            print("UNREPORTED %s (a baseline refuses a run that leaves them out)" % ", ".join(rec["unreported"]))
    return 0


def weakenings(old, new):
    """Every way NEW checks less than OLD (an empty list: NEW keeps everything OLD checks)."""
    out = []
    ns = {s["id"]: s for s in new.get("steps", [])}
    for s in old.get("steps", []):
        if s["id"] not in ns:
            out.append("step %s (%s) is dropped" % (s["id"], s["action"]))
        elif ns[s["id"]]["event"] != s["event"]:
            out.append("step %s emits %s instead of %s (its usage history breaks)" % (s["id"], ns[s["id"]]["event"], s["event"]))
    nm = {m["id"]: m for m in new.get("metrics", [])}
    for m in old.get("metrics", []):
        if m["id"] not in nm:
            out.append("metric %s is dropped" % m["id"])
        elif nm[m["id"]]["query"] != m["query"] or nm[m["id"]]["direction"] != m["direction"]:
            out.append("metric %s is measured differently (query or direction changed)" % m["id"])
        elif nm[m["id"]].get("tolerance", 0) > m.get("tolerance", 0):
            out.append("metric %s tolerates more drift (%s > %s)" % (m["id"], nm[m["id"]].get("tolerance"), m.get("tolerance", 0)))
    nc = {c["id"]: c for c in new.get("pass_criteria", [])}
    for c in old.get("pass_criteria", []):
        n = nc.get(c["id"])
        if n is None:
            out.append("criterion %s is dropped" % c["id"])
        elif c.get("steps") == "all":
            if n.get("steps") != "all":
                out.append("criterion %s no longer requires every step" % c["id"])
        elif n.get("metric") != c.get("metric") or (n.get("op") != c.get("op") and (c.get("op"), n.get("op")) not in TIGHTER):
            out.append("criterion %s checks %s %s instead of %s %s" % (c["id"], n.get("metric"), n.get("op"),
                                                                       c.get("metric"), c.get("op")))
        elif n.get("value") != c.get("value"):
            sign = LOOSER.get(c.get("op"))
            looser = sign is None or not all(isinstance(x, (int, float)) for x in (n.get("value"), c.get("value"))) or \
                (n["value"] - c["value"]) * sign > 0
            if looser:
                out.append("criterion %s is looser: %s %s %s instead of %s" % (c["id"], c.get("metric"), n.get("op"),
                                                                            n.get("value"), c.get("value")))
    if new.get("runner") != old.get("runner"):
        out.append("the runner changed (command, pins or timeout): the old measurements no longer compare")
    return out


def cmd_diff(cwd, a, b, as_json):
    old, e1 = load(cwd, a)
    new, e2 = load(cwd, b)
    if not isinstance(old, dict) or not isinstance(new, dict):
        print("BLOCK[journeys]: cannot read %s" % (a if not isinstance(old, dict) else b), file=sys.stderr)
        return 2
    bad = ["%s: %s" % (i, x) for i, d in ((a, old), (b, new)) for x in M.problems(d, KIND, jpath(cwd, i)) if M.malformed([x])]
    if bad:
        for x in bad:
            print("BLOCK[journeys]: %s (only valid journeys compare)" % x, file=sys.stderr)
        return 2
    w = weakenings(unretired(old), new)
    ok = retire_approval(cwd, jpath(cwd, a), unretired(old), b, M.digest(new)) if w else None
    if as_json:
        print(json.dumps({"old": a, "new": b, "weakenings": w, "approval_event": (ok or {}).get("event_id")}, indent=1))
    for x in w:
        print("WEAKER[journeys]: %s" % x, file=sys.stderr)
    if ok:
        print("[journeys] the weakening is approved by %s (%s)" % (ok["data"].get("by"), ok["ts"]), file=sys.stderr)
    return 2 if w and not ok else 0


def replacement(cwd, jid, rep, pin):
    """(doc, sha256, problems) of the journey named to replace jid (None, None, [] for none)."""
    if not rep:
        return None, None, []
    path = jpath(cwd, rep)
    new, _ = M.load(path)
    if rep == jid:
        return None, None, ["a journey cannot replace itself"]
    if not isinstance(new, dict):
        return None, None, ["replacement %s does not exist" % rep]
    bad = ["replacement %s: %s" % (rep, x) for x in problems(cwd, path, new, pin)]
    if lifecycle(cwd, path, new, pin[0]) == "retired":
        bad.append("replacement %s is retired itself" % rep)
    return new, M.digest(new), bad


def retire_approval(cwd, path, doc, rep, rep_sha):
    """The human approval of retiring exactly this content with exactly this replacement (or none)."""
    rec = M.approved(cwd, "journey.approved", M.rel(cwd, path), M.digest(doc))
    d = (rec or {}).get("data") or {}
    return rec if rec and d.get("replaced_by") == rep and d.get("replaced_by_sha256") == rep_sha else None


def cmd_approve(cwd, jid, o):
    path = jpath(cwd, jid)
    doc, err = M.load(path)
    if not isinstance(doc, dict):
        print("BLOCK[journeys]: no journey %s" % jid, file=sys.stderr)
        return 2
    if not (o.get("reason") or "").strip():
        print("journeys approve: a human approval of a retirement names its reason (--reason)", file=sys.stderr)
        return 64
    pin = pins(cwd)
    _, rep_sha, bad = replacement(cwd, jid, o.get("replaced_by"), pin)
    if bad:
        for b in bad:
            print("BLOCK[journeys]: %s" % b, file=sys.stderr)
        return 2
    try:
        M.approve(cwd, "journey.approved", M.rel(cwd, path), M.digest(doc), o.get("by", ""), o["reason"],
                  journey_id=jid, action="retire", replaced_by=o.get("replaced_by"), replaced_by_sha256=rep_sha)
    except ValueError as e:
        print("journeys approve: %s" % e, file=sys.stderr)
        return 64
    print("[journeys] retiring %s%s approved by %s" % (
        jid, " for %s" % o["replaced_by"] if o.get("replaced_by") else " with no replacement", o["by"]), file=sys.stderr)
    return 0


def cmd_retire(cwd, jid, o):
    path = jpath(cwd, jid)
    doc, err = M.load(path)
    if not isinstance(doc, dict):
        print("BLOCK[journeys]: no journey %s" % jid, file=sys.stderr)
        return 2
    pin = pins(cwd)
    if lifecycle(cwd, path, doc, pin[0]) == "retired":
        print("[journeys] %s is already retired" % jid, file=sys.stderr)
        return 0
    evidence, rep = o.get("evidence", []), o.get("replaced_by")
    bad = ["%s: %s (restore it before retiring it)" % (jid, x) for x in problems(cwd, path, doc, pin)]
    bad += evidence_problems(cwd, evidence, "evidence") if evidence else \
        ["a journey is never retired without evidence (--evidence REF)"]
    new, rep_sha, rbad = replacement(cwd, jid, rep, pin)
    bad += rbad
    if bad:
        for b in bad:
            print("BLOCK[journeys]: %s" % b, file=sys.stderr)
        return 2
    weaker = weakenings(doc, new) if new else ["no replacement names what still checks it"]
    approval = retire_approval(cwd, path, doc, rep, rep_sha)
    if weaker and jid in pin[1] and not approval:
        for w in weaker:
            print("ASK[journeys]: retiring %s weakens the evaluation: %s" % (jid, w), file=sys.stderr)
        print("journeys: a human approves it first (mission journeys approve %s%s --by NAME --reason …)"
              % (jid, " --replaced-by %s" % rep if rep else ""), file=sys.stderr)
        return 3
    before = M.digest(doc)
    doc["status"] = "retired"
    doc["retirement"] = {"at": M.now(), "evidence_refs": evidence, "replaced_by": rep, "replaced_by_sha256": rep_sha,
                         "sha256_before": before, "weakenings": weaker if new else [],
                         "approval_event": (approval or {}).get("event_id")}
    M.seal(doc)
    M.save(path, doc)
    M.record(cwd, "journey.retired", M.rel(cwd, path), "mission", sha256=doc["sha256"], journey_id=jid,
             replaced_by=rep, replaced_by_sha256=rep_sha, evidence_refs=evidence)
    print("[journeys] %s retired" % jid, file=sys.stderr)
    return 0


def cmd_list(cwd):
    for _, d in all_journeys(cwd):
        print("%s  %-8s %s" % (d.get("journey_id"), d.get("status"), d.get("title")))
    return 0


def parse(argv, multi=("evidence",), known=None):
    """Options and positionals: --k=v is --k v; a multi-value flag takes every value up to the next
    --flag; a flag outside `known` is listed in o["_unknown"], so the caller refuses it (exit 64)
    instead of acting on less than the human typed."""
    o, pos, k, args = {}, [], None, []
    for a in argv:
        args += a.split("=", 1) if a.startswith("--") and "=" in a else [a]
    for a in args:
        if a == "--json":
            o["json"], k = True, None
        elif a.startswith("--"):
            k = a[2:].replace("-", "_")
            if known is not None and k not in known:
                o.setdefault("_unknown", []).append(a)
            if k in multi:
                o.setdefault(k, [])
        elif k in multi:
            o[k].append(a)
        elif k:
            o[k], k = a, None
        else:
            pos.append(a)
    return o, pos


def unknown_flags(o, area):
    """Exit 64 with one line per flag the area does not know (0 when there is none)."""
    for a in o.get("_unknown", []):
        print("mission %s: unknown flag %s (mission %s --help)" % (area, a, area), file=sys.stderr)
    return 64 if o.get("_unknown") else 0


FLAGS = ("from", "title", "persona", "evidence", "replaced_by", "by", "reason")


def main(argv, cwd):
    cmd, (o, pos) = argv[0], parse(argv[1:], known=FLAGS)
    if unknown_flags(o, "journeys"):
        return 64
    if cmd in ("approve", "retire") and M.ledger_block(cwd, "journeys"):
        return 2      # nothing decides from a broken ledger
    if cmd == "new":
        return cmd_new(cwd, o)
    if cmd == "check":
        return cmd_check(cwd, pos, o.get("json"))
    if cmd == "run" and pos:
        return cmd_run(cwd, pos[0], o.get("json"))
    if cmd == "diff" and len(pos) == 2:
        return cmd_diff(cwd, pos[0], pos[1], o.get("json"))
    if cmd == "approve" and pos:
        return cmd_approve(cwd, pos[0], o)
    if cmd == "retire" and pos:
        return cmd_retire(cwd, pos[0], o)
    if cmd == "list":
        return cmd_list(cwd)
    print("mission journeys: unknown or incomplete command (mission journeys --help)", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], os.getcwd()))
