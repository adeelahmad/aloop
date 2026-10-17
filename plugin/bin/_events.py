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
    try:
        with open(path(cwd), "a") as f:
            f.write(json.dumps(ev) + "\n")
        return ev["event_id"]
    except OSError:
        return ""


def read(cwd=None):
    try:
        with open(path(cwd)) as f:
            for line in f:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def last(cwd, **match):
    """The latest event whose ids/fields match (e.g. task=..., event=...)."""
    hit = None
    for ev in read(cwd):
        if all(ev.get(k) == v or (ev.get("ids") or {}).get(k) == v for k, v in match.items()):
            hit = ev
    return hit
