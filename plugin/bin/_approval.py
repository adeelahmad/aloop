"""_approval.py — a sprint runs only on the human's own word (ROADMAP M0.9).

"A human approves the plan" was a playbook rule: nothing checked it, so a supervisor could dispatch
workers before the human said go. Now the grant is a record, and gate-dispatch reads it:

  - reviewed (the default): `events authority --mode reviewed [--sprint N]` records the human's approval
    of exactly the plan they saw. It counts only when a prompt the UserPromptSubmit hook captured has
    the line `approve SN <code>` (M9.17's mechanism: the code is the first 8 hex of the grant's digest,
    which covers every plan file of the sprint), and until then it asks (exit 3) with that line;
  - autonomous: `events authority --mode autonomous --sprint N` grants sprint N autonomy (re-plans
    included). It asks for `autonomous SN <code>` (the code names the authority record), unless the
    project's mission is approved in autonomous mode: that approval is already the human's word, and it
    covers the sprints that serve the mission's own intents.

gate-dispatch admits an execution role of sprint N only while the sprint's newest grant holds: its
cited line is still in the captured prompts and grants exactly that content, the plan is still the one
approved (reviewed), and the mission still grants autonomy (mission-backed). An unverifiable newest
grant blocks: a hand-written record can only take a grant away, never add one. A line captured before
a later decision about the same sprint is stale, so an old approval never re-grants a replaced plan.
"""
import glob
import hashlib
import json
import os
import re

import _events
import _mission as M
import _paths

# the plan of a sprint: what the planning activities write and the human approves, the intent and the
# standards it is built and judged by included. Comms (init.md, output.md), plan-ready.md ticks, logs and
# stats change during execution and are not the plan.
SPRINT_FILES = ("stories.md", "plan.md", "ids.json", "intake.md", "standards.md")
NEW_SPRINT_FILES = ("intake.md", "standards.md")   # in the plan since per-file pins; an older approval left them out
STORY_FILES = ("tasks.md", "validate.md", "plan.md", "targets.json")
STORY_DIR = re.compile(r"^s(\d+)-(\d+)-")
TASK = re.compile(r"^S(\d+)-(\d+)-T\d+$", re.I)
TASK_ROLES = ("red-worker", "scaffolder", "green-worker")   # one task each; a review or the final gate may span the sprint
VERBS = {"plan": "approve", "autonomy": "autonomous"}
AUTH_FIELDS = ("initiated_by", "autonomy_mode", "scope", "policy", "overrides", "sprint")


def sprint_of(s):
    """"S1" from 1, "1", "S1", "s1", "sprint1" or a task id (S1-01-T1, S1-SR-W1); "" otherwise."""
    m = re.match(r"^\s*(?:sprint|s)?(\d+)(?:-[A-Za-z0-9._-]*)?\s*$", str(s if s is not None else ""), re.I)
    return "S%d" % int(m.group(1)) if m and int(m.group(1)) > 0 else ""


def sprint_dir(cwd, sp):
    return os.path.join(_paths.docs(cwd), "sprint" + sp[1:])


def plan_files(cwd, sp):
    """(project-relative path, absolute path) of every plan file of the sprint, in a fixed order."""
    sdir = sprint_dir(cwd, sp)
    out = [os.path.join(sdir, n) for n in SPRINT_FILES]
    for d in sorted(glob.glob(os.path.join(sdir, "s*-*"))):
        if os.path.isdir(d) and STORY_DIR.match(os.path.basename(d)):
            out += [os.path.join(d, n) for n in STORY_FILES]
    return [(M.rel(cwd, p), p) for p in out if os.path.isfile(p)]


def file_sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def sprint_rels(cwd, sp, names):
    return {M.rel(cwd, os.path.join(sprint_dir(cwd, sp), n)) for n in names}


def plan_map(cwd, sp):
    """{project-relative path: sha256} of the sprint's plan files as they are now."""
    return {rel: file_sha(p) for rel, p in plan_files(cwd, sp)}


def map_digest(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def plan_digest(cwd, sp):
    """The sha256 of the sprint's plan as it is now (None when the sprint has no dir)."""
    if not os.path.isdir(sprint_dir(cwd, sp)):
        return None
    return map_digest(plan_map(cwd, sp))


def content(auth):
    return {k: auth.get(k) for k in AUTH_FIELDS}


def grant_sha(auth, plan_sha=None):
    """What the human's code names: the plan (with the sprint and any override) for an approval, the
    whole authority record for autonomy."""
    if auth.get("autonomy_mode") == "autonomous":
        return M.digest(content(auth), drop=())
    return M.digest({"sprint": auth.get("sprint"), "plan": plan_sha, "overrides": auth.get("overrides") or {}}, drop=())


def data(e):
    """An event's data, {} when a hand-written line has none or another shape."""
    d = e.get("data")
    return d if isinstance(d, dict) else {}


def plans(cwd, sp=None):
    return [e for e in _events.read(cwd) if e.get("event") in ("plan.created", "plan.revised")
            and (sp is None or data(e).get("sprint") == sp)]


def grants(cwd, sp):
    """The human's grants about sprint sp (authority records that name it), oldest first."""
    out = []
    for e in _events.read(cwd):
        a = e.get("authority")
        if e.get("event") == "human.approval" and isinstance(a, dict) and a.get("sprint") == sp \
                and isinstance(a.get("grant"), dict):
            out.append(e)
    return out


def stale_floor(qs, decisions, sha):
    """How many captured prompts are too old to grant `sha`: every one captured before a decision about the
    same sprint that granted other content (as M9.17's stale_before, over the run log's grants)."""
    clock, floor = M.prompt_clock(qs), 0
    for e in decisions:
        g = e["authority"]["grant"]
        if g.get("sha256") == sha:
            continue                      # this very content again undoes nothing
        seen = g.get("prompt_log") if isinstance(g.get("prompt_log"), dict) else {}
        if clock["id"] and seen.get("id") == clock["id"] and isinstance(seen.get("seen"), int):
            floor = max(floor, seen["seen"])
        else:                             # another prompt log: a prompt from that second or before is older
            floor = max(floor, sum(1 for q in qs if q["ts"] <= str(e.get("ts") or "")))
    return floor


def ask(verb, sp, sha, what):
    return M.NeedsHuman("%s is the human's own decision: they reply  %s %s %s  (the code names exactly %s), "
                        "then run this again" % (what, verb, sp, M.code(sha), "this plan" if verb == "approve" else
                                                 "this authority record"))


def provenance(cwd, sp, verb, sha, what):
    """The newest captured prompt line that grants `sha` (not stale), as the record's `prompt`; raises
    NeedsHuman with the line to reply when there is none."""
    qs = M.prompts(cwd)
    for q in reversed(qs[stale_floor(qs, grants(cwd, sp), sha):]):
        for line in q["text"].splitlines():
            line = line.strip()[:200]     # what the record keeps is what the gate re-reads
            if M.grants(line, verb, sha):
                return {"ts": q["ts"], "sha256": q["sha256"], "session": q["session"], "line": line}
    raise ask(verb, sp, sha, what)


def mission_autonomy(cwd):
    """The approved mission's grant of autonomy, or None: mission.json approved as it is now (the human's
    typed line, M9.17), in autonomous mode, on a ledger that verifies."""
    path = M.path_of(cwd, "mission")
    doc, _ = M.load(path)
    if not isinstance(doc, dict) or doc.get("mode") != "autonomous" or M.ledger_problems(cwd):
        return None
    try:
        sha = M.digest(doc)
    except (TypeError, ValueError):
        return None
    rec = M.approved(cwd, "mission.approved", M.rel(cwd, path), sha)
    if not rec or not M.cites(rec):
        return None
    return {"ref": M.rel(cwd, path), "mission_id": doc.get("mission_id"), "sha256": sha, "event_id": rec.get("event_id"),
            "by": rec["data"].get("by")}


def grant_for(cwd, initiated_by, mode, scope, sprint="", policy="default", overrides=None):
    """(sprint, grant) for a new authority record: the grant cites the human's typed line (or, for
    autonomy, the approved autonomous mission). Raises NeedsHuman until the human has typed it,
    ValueError on a bad request. A reviewed record with no sprint and no frozen plan grants nothing."""
    sp = sprint_of(sprint) if sprint not in (None, "") else ""
    if sprint not in (None, "") and not sp:
        raise ValueError("--sprint names a sprint: N, SN or sprintN (got %r)" % sprint)
    if mode == "autonomous" and not sp:
        raise ValueError("autonomy is granted per sprint: name it with --sprint N (one sprint per autonomous run)")
    if mode == "reviewed" and not sp:
        latest = plans(cwd)
        sp = data(latest[-1]).get("sprint", "") if latest else ""
        if not sp:
            return "", None               # nothing planned yet: the record says who started the run
    if mode == "reviewed" and not plans(cwd, sp):
        raise ValueError("the plan of %s is not frozen: run gate-stage2-complete %s (its `ids freeze` records the "
                         "plan), then show it to the human" % (sp, M.rel(cwd, sprint_dir(cwd, sp))))
    auth = {"initiated_by": initiated_by, "autonomy_mode": mode, "scope": scope, "policy": policy,
            "overrides": overrides or {}, "sprint": sp}
    if mode == "reviewed":
        files = plan_map(cwd, sp)
        plan_sha = map_digest(files)
        sha = grant_sha(auth, plan_sha)
        grant = {"kind": "plan", "sha256": sha, "plan_sha256": plan_sha, "files": sorted(files), "file_sha256": files,
                 "prompt": provenance(cwd, sp, VERBS["plan"], sha, "The approval of %s's plan" % sp)}
        snapshot(cwd, files)              # the text they approved counts after later edits too (deps unplanned)
    else:
        sha = grant_sha(auth)
        # the mission's autonomy covers only its own sprints: one planned from another intent asks the human
        mission = mission_autonomy(cwd) if mission_intent(cwd, sp) or not plans(cwd, sp) else None
        grant = {"kind": "autonomy", "sha256": sha}
        if mission:
            grant["mission"] = mission
        else:
            grant["prompt"] = provenance(cwd, sp, VERBS["autonomy"], sha, "Autonomy for %s" % sp)
    grant["prompt_log"] = M.prompt_clock(M.prompts(cwd))
    return sp, grant


def cited(cwd, prompt, verb, sha, floor_decisions):
    """Problems with a grant's cited line: it must be a line of a captured prompt (not a stale one) that
    grants exactly `sha`."""
    if not isinstance(prompt, dict) or not isinstance(prompt.get("line"), str):
        return ["it cites no line the human typed"]
    if not M.grants(prompt["line"], verb, sha):
        return ["its cited line %r does not %s this content (code %s)" % (prompt["line"], verb, M.code(sha))]
    qs = M.prompts(cwd)
    floor = stale_floor(qs, floor_decisions, sha)
    seen = [i for i, q in enumerate(qs) if q["sha256"] == prompt.get("sha256")
            and any(ln.strip()[:200] == prompt["line"] for ln in q["text"].splitlines())]
    if any(i >= floor for i in seen):     # the same words typed again after a later decision count again
        return []
    if seen:
        return ["its cited line %r was typed before a later decision about this sprint" % prompt["line"]]
    return ["its cited line %r is not among the human's captured prompts in this checkout" % prompt["line"]]


def mission_intent(cwd, sp):
    """The intent the sprint's newest plan serves, when it is a mission intent (intent.received with
    source mission, written only by `intent new`), else None."""
    ps = plans(cwd, sp)
    iid = data(ps[-1]).get("intent_id") if ps else None
    if not iid:
        return None
    for e in _events.read(cwd):
        d = data(e)
        if e.get("event") == "intent.received" and d.get("intent_id") == iid and d.get("source") == "mission":
            return iid
    return None


def placement(cwd, sp, tid, story_dir="", role=""):
    """Problems with what the dispatch works on: the approval of sp covers only sp's own plan, so the task
    contract's STORY_DIR must be sp's dir or one of its stories (sN-NN-<slug>/ with N = sp, live in the frozen
    ids.json the approval digests), a task id must be a live task there, and a RED/SCAFFOLD/GREEN worker works
    on a task."""
    m = TASK.match(tid)
    if role in TASK_ROLES and not m:
        return ["a %s works on one task of the plan: its TASK_ID is SN-NN-Tk (got %s)" % (role, tid)]
    sdir = os.path.realpath(sprint_dir(cwd, sp))
    try:
        with open(os.path.join(sdir, "ids.json")) as f:
            frozen = json.load(f).get("ids")
    except (OSError, ValueError, AttributeError):
        frozen = None                     # no frozen map: the approved digest pins that there is none
    frozen = frozen if isinstance(frozen, dict) else None

    def live(alias, path=None):
        e = frozen.get(alias) if frozen is not None else {}
        return frozen is None or (isinstance(e, dict) and not e.get("retired") and (path is None or e.get("path") == path))
    if story_dir:
        rp = os.path.realpath(story_dir)
        sm = STORY_DIR.match(os.path.basename(rp))
        if rp != sdir:
            if os.path.dirname(rp) != sdir or not sm or int(sm.group(1)) != int(sp[1:]):
                return ["STORY_DIR %s is not in %s's plan: the human's approval of %s covers %s and its story dirs"
                        " (s%s-NN-<slug>/) only" % (M.rel(cwd, rp), sp, sp, M.rel(cwd, sdir), sp[1:])]
            if not live("S%s-%s" % sm.groups(), os.path.basename(rp)):
                return ["STORY_DIR %s is no live story of %s's frozen plan (%s/ids.json): plan it first"
                        % (M.rel(cwd, rp), sp, M.rel(cwd, sdir))]
            if m and int(m.group(2)) != int(sm.group(2)):
                return ["TASK_ID %s is not a task of the story dir %s" % (tid, M.rel(cwd, rp))]
    if m and not live(tid):
        return ["%s is not a live task of %s's frozen plan (%s/ids.json): plan it first" % (tid, sp, M.rel(cwd, sdir))]
    return []


def check(cwd, task_or_sprint, story_dir="", role=""):
    """(True, why) when an execution role of this sprint may be dispatched now, else (False, problems).
    `story_dir` (the task contract's STORY_DIR) and `role` tie the dispatch to the approved plan."""
    sp = sprint_of(task_or_sprint)
    if not sp:
        return False, ["TASK_ID %s names no sprint: a task id is SN-NN-Tk (SN-SR-Wk for a wave review), so the gate"
                       " knows whose plan approval it needs" % task_or_sprint]
    plan_sha = plan_digest(cwd, sp)
    if plan_sha is None:
        return False, ["%s has no plan (%s does not exist)" % (sp, M.rel(cwd, sprint_dir(cwd, sp)))]
    good, why = holds(cwd, sp, plan_sha)
    if not good:
        return good, why
    bad = placement(cwd, sp, str(task_or_sprint), story_dir, role)
    return (False, bad) if bad else (True, why)


def holds(cwd, sp, plan_sha):
    """(True, why) when the sprint's newest grant holds for its plan as it is now, else (False, problems)."""
    how = ("show the human the plan of %s; on their go run `aloop events authority --initiated-by \"<their name>\""
           " --mode reviewed --scope \"%s as approved\" --sprint %s`: it asks for their typed line `approve %s <code>`"
           " (an autonomous run: `--mode autonomous`, their line `autonomous %s <code>`)" % (sp, sp, sp[1:], sp, sp))
    gs = grants(cwd, sp)
    if not gs:
        return False, ["the human has not approved the plan of %s: nothing of it is dispatched before they do." % sp,
                       "  To get it: " + how]
    e = gs[-1]
    auth, g = e["authority"], e["authority"]["grant"]
    who = "%s (%s)" % (auth.get("initiated_by"), auth.get("autonomy_mode"))
    if g.get("kind") == "plan" and auth.get("autonomy_mode") == "reviewed":
        if g.get("plan_sha256") != plan_sha and "file_sha256" not in g and g.get("plan_sha256") == map_digest(
                {r: h for r, h in plan_map(cwd, sp).items() if r not in sprint_rels(cwd, sp, NEW_SPRINT_FILES)}):
            return False, ["the human's approval of %s is older than its intake.md and standards.md being part of the"
                           " plan: they approve the plan again." % sp, "  " + how]
        if g.get("plan_sha256") != plan_sha:
            return False, ["the plan of %s changed after the human approved it (approved %s, now %s): a changed plan is"
                           " a new decision." % (sp, M.code(g.get("plan_sha256") or ""), M.code(plan_sha)),
                           "  Show them what changed (`git diff` of %s); then: %s" % (M.rel(cwd, sprint_dir(cwd, sp)), how)]
        if g.get("sha256") != grant_sha(auth, plan_sha):
            return False, ["the newest approval of %s does not match its own record (a hand-edited record); %s" % (sp, how)]
        bad = cited(cwd, g.get("prompt"), VERBS["plan"], g["sha256"], gs[:-1])
        if bad:
            return False, ["the newest approval of %s is not the human's own words: %s." % (sp, bad[0]), "  " + how]
        return True, "%s approved by %s, code %s" % (sp, who, M.code(g["sha256"]))
    if g.get("kind") == "autonomy" and auth.get("autonomy_mode") == "autonomous":
        if g.get("sha256") != grant_sha(auth):
            return False, ["the newest autonomy grant of %s does not match its own record (a hand-edited record); %s"
                           % (sp, how)]
        if isinstance(g.get("mission"), dict):
            if not mission_autonomy(cwd):
                return False, ["%s's autonomy came from the mission, which is no longer approved in autonomous mode"
                               " (aloop mission check); %s" % (sp, how)]
            if not mission_intent(cwd, sp):
                return False, ["%s's plan serves no mission intent: the mission's autonomy covers only the sprints of its"
                               " own goals (`aloop mission goal next`); %s" % (sp, how)]
            return True, "%s autonomous under mission %s (approved by %s)" % (sp, g["mission"].get("mission_id"),
                                                                             g["mission"].get("by"))
        bad = cited(cwd, g.get("prompt"), VERBS["autonomy"], g["sha256"], gs[:-1])
        if bad:
            return False, ["the newest autonomy grant of %s is not the human's own words: %s." % (sp, bad[0]), "  " + how]
        return True, "%s autonomous, granted by %s, code %s" % (sp, who, M.code(g["sha256"]))
    return False, ["the newest grant of %s is neither a plan approval nor an autonomy grant (a hand-written record);"
                   " %s" % (sp, how)]


def snap_dir(cwd, make=False):
    return _paths.home(cwd, os.path.join("state", "plan"), make=make)


def snapshot(cwd, files):
    """Keep each pinned plan file's bytes under its sha256 (run data), so the text the human's grant pins
    outlives later edits of the file; a copy counts only while it still hashes to its name."""
    root, d = _paths.project(cwd), None
    for rel, sha in sorted(files.items()):
        try:
            with open(os.path.join(root, rel), "rb") as f:
                b = f.read()
        except OSError:
            continue
        if hashlib.sha256(b).hexdigest() != sha:
            continue
        d = d or snap_dir(cwd, make=True)
        dst = os.path.join(d, sha)
        if pinned_text(cwd, rel, sha, live=False) is None:
            with open(dst + ".tmp", "wb") as f:
                f.write(b)
            os.replace(dst + ".tmp", dst)


def pinned_text(cwd, rel, sha, live=True):
    """The text of plan file `rel` as pinned (`sha`): the file while it is unchanged, else its kept copy;
    None when neither holds that text."""
    paths = ([os.path.join(_paths.project(cwd), rel)] if live else []) + [os.path.join(snap_dir(cwd), sha)]
    for p in paths:
        try:
            with open(p, "rb") as f:
                b = f.read()
        except OSError:
            continue
        if hashlib.sha256(b).hexdigest() == sha:
            return b.decode("utf-8", "replace")
    return None


def gated(cwd, sp):
    """{path: sha256} of the sprint's plan as the planner's gate last passed it (gate-stage2-complete records
    `plan.gated` on the planner's stop), or {}."""
    for e in reversed(list(_events.read(cwd))):
        d = data(e)
        if e.get("event") == "plan.gated" and d.get("sprint") == sp and isinstance(d.get("file_sha256"), dict) \
                and map_digest(d["file_sha256"]) == d.get("plan_sha256"):
            return d["file_sha256"]
    return {}


def pinned(cwd, sp):
    """({path: sha256}, why) of the plan files the human's newest grant of sprint sp pins; ({}, the reason) when
    none: a reviewed approval pins the plan they approved, whatever changed since; their autonomy grant pins the
    plan as the planner's gate last passed it. Text that differs from its pin is no plan the human stands behind."""
    gs = grants(cwd, sp)
    if not gs:
        return {}, "the human has not approved the plan of %s" % sp
    g = gs[-1]["authority"]["grant"]
    if g.get("kind") == "plan":
        files = g.get("file_sha256")
        if not isinstance(files, dict) or map_digest(files) != g.get("plan_sha256"):
            return {}, ("the newest approval of %s pins no plan files (recorded before they were pinned file by file, "
                        "or edited): the human approves the plan again" % sp)
        good, why = holds(cwd, sp, g.get("plan_sha256"))
        return (files, "") if good else ({}, why[0])
    good, why = holds(cwd, sp, None)
    if not good:
        return {}, why[0]
    files = gated(cwd, sp)
    return (files, "") if files else ({}, "%s is autonomous, but no plan of it has passed the planner's gate yet" % sp)
