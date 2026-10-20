"""_events.py — the normalized event stream, `aladdin.event/1` (ROADMAP §4.7).

One JSON line per event in <project>/.agentic/logs/events.jsonl. The envelope carries
ids (event / sprint / story / task / attempt / agent), `parent_event_id` and `caused_by`,
role, host, model, authority, decision, and artifact / evidence REFS. It references
transcripts and never copies them. The stream is the source of truth; any graph built
from it is downstream and rebuildable. Emitting never fails the caller.
"""
import json
import os
import re
import time
import uuid

import _paths

SCHEMA = "aladdin.event/1"
EVENT_TYPES = {
    "intent.received", "intent.normalized", "plan.created", "plan.approved", "plan.revised",
    "sprint.started", "sprint.token_soft_limit", "sprint.admission_closed", "sprint.rolled_over",
    "sprint.context_handoff", "context.compacted", "context.rotated",
    "task.created", "task.dispatched", "task.completed", "task.retry", "task.time_soft_preempt",
    "task.time_soft_limit", "task.time_hard_timeout", "task.token_soft_limit", "task.token_hard_limit",
    "task.cancelled",
    "gate.started", "gate.passed", "gate.failed", "worktree.created", "worktree.discarded", "worktree.merged",
    "artifact.created", "artifact.updated", "semantic_target.created", "semantic_target.resolved",
    "decision.recorded", "commit.created",
    "memory.candidate", "memory.promoted", "memory.retired",
    "human.approval", "human.override", "run.completed", "run.aborted",
    # the mission layer (ROADMAP §4.12): registered up front so its parallel tracks never edit this set
    "mission.set", "mission.approved", "mission.tick", "mission.budget_exhausted",
    "research.recorded",
    "baseline.proposed", "baseline.measured", "baseline.confirmed", "baseline.superseded", "baseline.vetoed",
    "journey.proposed", "journey.approved", "journey.retired", "journey.run",
    "state.assessed", "eval.completed",
    "goal.derived", "goal.approved", "goal.met", "goal.retired",
    "ethics.reviewed", "ethics.consent", "ethics.blocked",
    "telemetry.consent", "telemetry.revoked", "telemetry.setup", "telemetry.report",
    "judge.run",
}
ENVELOPE = ("schema", "event_id", "ts", "event", "ids", "parent_event_id", "caused_by", "role", "host",
            "model", "authority", "decision", "refs", "data")
TASK_ID = re.compile(r"^S(\d+)-(\d+)-T\d+$")


def path(cwd=None):
    return os.path.join(_paths.home(cwd, "logs"), "events.jsonl")


def ids_for(task="", attempt="", agent_id=""):
    m = TASK_ID.match(task or "")
    return {k: v for k, v in (("sprint", "S%s" % m.group(1) if m else ""),
                              ("story", "S%s-%s" % (m.group(1), m.group(2)) if m else ""),
                              ("task", task or ""), ("attempt", str(attempt or "")),
                              ("agent", agent_id or "")) if v}


def canonical(cwd, ids):
    """story_uid / task_uid from the frozen sprintN/ids.json (ROADMAP §4.5), when there is one."""
    sp = ids.get("sprint")
    if not sp:
        return {}
    try:
        with open(os.path.join(_paths.docs(cwd), "sprint" + sp[1:], "ids.json")) as f:
            m = json.load(f).get("ids", {})
    except (OSError, ValueError):
        return {}
    return {k + "_uid": m[ids[k]]["id"] for k in ("story", "task") if ids.get(k) in m}


def emit(cwd, event, task="", attempt="", agent_id="", role="", host="", model="", parent_event_id=None,
         caused_by=None, authority=None, decision=None, refs=None, **data):
    """Append one aladdin.event/1 record; returns its event_id ("" on any failure)."""
    if event not in EVENT_TYPES:
        raise ValueError("unknown event type %r (see _events.EVENT_TYPES)" % event)
    ev = {"schema": SCHEMA, "event_id": uuid.uuid4().hex,
          "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event,
          "ids": ids_for(task, attempt, agent_id),
          "parent_event_id": parent_event_id, "caused_by": caused_by,
          "role": role or None, "host": host or os.environ.get("AGENTIC_HOST") or "claude",
          "model": model or None, "authority": authority, "decision": decision,
          "refs": {k: v for k, v in (refs or {}).items() if v}, "data": data}
    # flat aliases kept for readers of the pre-1 lines (stats, older tooling)
    ev.update({"agent_id": agent_id, "task": task, "attempt": str(attempt or "")})
    ev["ids"].update(canonical(cwd, ev["ids"]))
    try:
        with open(path(cwd), "a") as f:
            f.write(json.dumps(ev) + "\n")
        return ev["event_id"]
    except OSError:
        return ""


DECISION_KINDS = ("plan", "split", "target", "implementation", "replan", "acceptance",
                  "mission", "baseline", "journey", "goal", "ethics", "telemetry")
AUTONOMY = ("reviewed", "autonomous")


def authority_file(cwd=None):
    return os.path.join(_paths.home(cwd, "state"), "authority.json")


def record_authority(cwd, initiated_by, autonomy_mode, scope, policy="default", overrides=None, sprint="", grant=None):
    """The run's authority record (who started it, under which policy and approval mode,
    for which scope, with which overrides). Stored in .agentic/state/authority.json and
    emitted as human.approval; decisions point at it. `sprint` and `grant` are the human's
    grant about that sprint (_approval.grant_for, ROADMAP M0.9): a plan grant also emits
    plan.approved for the sprint's latest frozen plan."""
    if autonomy_mode not in AUTONOMY:
        raise ValueError("autonomy_mode must be one of %s" % ", ".join(AUTONOMY))
    if not initiated_by or not scope:
        raise ValueError("an authority record needs initiated_by and scope")
    auth = {"initiated_by": initiated_by, "policy": policy, "autonomy_mode": autonomy_mode,
            "scope": scope, "overrides": overrides or {}}
    if sprint:
        auth.update(sprint=sprint, grant=grant)
    eid = emit(cwd, "human.approval", role="human", authority=auth, refs={"authority": authority_file(cwd)})
    auth["event_id"] = eid
    with open(authority_file(cwd), "w") as f:
        json.dump(auth, f, indent=1)
    plan = None
    for ev in read(cwd):
        if ev.get("event") in ("plan.created", "plan.revised") and (ev.get("data") or {}).get("sprint") == sprint:
            plan = ev
    if plan and (grant or {}).get("kind") == "plan":  # the approval covers the sprint's latest frozen plan
        emit(cwd, "plan.approved", role="human", parent_event_id=eid, caused_by=plan.get("event_id"),
             authority=auth, refs=plan.get("refs"), sprint=sprint, plan_sha256=grant.get("plan_sha256"))
    return eid


def current_authority(cwd=None):
    try:
        with open(authority_file(cwd)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def record_decision(cwd, kind, reason, task="", evidence=None, authority_ref=None, parent_event_id=None, role=""):
    """Structured rationale at a durable boundary: ONE line of reason, evidence refs and an
    authority ref. No chain-of-thought is captured."""
    if kind not in DECISION_KINDS:
        raise ValueError("decision kind must be one of %s" % ", ".join(DECISION_KINDS))
    reason = (reason or "").strip()
    if not reason or "\n" in reason or len(reason) > 200:
        raise ValueError("a decision reason is one line of at most 200 characters")
    auth = authority_ref or (current_authority(cwd) or {}).get("event_id")
    if not auth:
        raise ValueError("no authority: record one first (events authority ...) or pass --authority")
    return emit(cwd, "decision.recorded", task=task, role=role, parent_event_id=parent_event_id, caused_by=auth,
                decision={"kind": kind, "reason": reason, "evidence_refs": list(evidence or []), "authority_ref": auth})


def read(cwd=None):
    try:
        with open(path(cwd)) as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict):       # every reader keys into it; a stray line is skipped
                    yield ev
    except OSError:
        return


def last(cwd, **match):
    """The latest event whose ids/fields match (e.g. task=..., event=...)."""
    hit = None
    for ev in read(cwd):
        if all(ev.get(k) == v or (ev.get("ids") or {}).get(k) == v for k, v in match.items()):
            hit = ev
    return hit
