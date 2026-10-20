"""_mission.py — the mission layer's shared core (ROADMAP §4.12, milestone M9).

The mission layer sits on top of Aladdin. A human sets a mission (bigger than a goal),
fixed roles establish a baseline and an evaluation layer, and goals derived from the gap
list become Aladdin intents. Every artifact is a JSON file under docs/agents/mission/,
checked here against plugin/mission/schemas/<kind>.json, hashed canonically, and tied to
the event stream. A model makes the judgments; this module checks only that each one was
made in the right structure and is backed by evidence (models explore, gates decide).

The contract every area module (plugin/mission/<area>.py) builds on:
  - an artifact has `status` and `created_at`, plus its id field, and lives at LAYOUT[kind];
  - its id is issued by new_id(), never by a model;
  - `sha256` is digest() of the rest of the document, so a changed file is detectable;
  - a human approval is an event (APPROVAL_EVENTS) whose data names the subject and sha256;
  - snapshot() reads only those fields, so `mission tick` can decide from facts.
"""
import glob
import hashlib
import json
import os
import re
import secrets
import time

import _events
import _paths

PLUGIN = os.path.realpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))
AREAS_DIR = os.path.join(PLUGIN, "mission")
SCHEMAS_DIR = os.path.join(AREAS_DIR, "schemas")

# id prefix per artifact kind: <prefix>_<12 hex ms><8 hex random>, like intent ids
PREFIX = {"mission": "msn", "research": "rsr", "baseline": "bln", "journey": "jrn", "state": "sta",
          "eval": "evl", "goal": "gol", "ethics": "eth", "rubric": "rub", "telemetry": "tlm"}
ID = re.compile(r"^(%s)_[0-9a-f]{20}$" % "|".join(PREFIX.values()))

# where each kind lives under docs/agents/mission/ (a file, or a dir of <id>.json)
LAYOUT = {"mission": "mission.json", "research": "research", "baseline": "baseline", "journey": "journeys",
          "state": "state", "eval": "eval", "goal": "goals", "ethics": "ethics", "rubric": "judges",
          "telemetry": "telemetry"}

# the status vocabulary per kind; area modules may not invent others
STATUSES = {
    "mission": ("draft", "approved"),
    "research": ("recorded", "retired"),
    "baseline": ("proposed", "measured", "confirmed", "superseded", "vetoed"),
    "journey": ("proposed", "active", "retired"),
    "state": ("recorded",),
    "eval": ("recorded",),
    "goal": ("derived", "approved", "active", "met", "retired", "blocked"),
    "ethics": ("recorded",),
    "rubric": ("proposed", "active", "retired"),
    "telemetry": ("off", "consented", "configured", "revoked"),
}

# human approvals: the event type, and the subject kind it approves
APPROVAL_EVENTS = {"mission.approved": "mission", "baseline.confirmed": "baseline",
                   "journey.approved": "journey", "goal.approved": "goal", "ethics.consent": "goal",
                   "telemetry.consent": "telemetry"}

# fields left out of the digest: the digest itself and the bookkeeping that changes on approval
UNHASHED = ("sha256", "status", "approval")


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_id(kind):
    """Time-ordered, collision-safe, issued here — never by a model (ROADMAP §7)."""
    return "%s_%012x%s" % (PREFIX[kind], int(time.time() * 1000), secrets.token_hex(4))


def mdir(cwd=None, *parts):
    """docs/agents/mission[/parts…] of the project (the main worktree)."""
    return os.path.join(_paths.docs(cwd), "mission", *parts)


def path_of(cwd, kind, artifact_id=""):
    """The file of one artifact: mission.json, or <dir>/<id>.json."""
    loc = LAYOUT[kind]
    if loc.endswith(".json"):
        return mdir(cwd, loc)
    return mdir(cwd, loc, artifact_id + ".json")


def canonical(doc, drop=UNHASHED):
    """The canonical bytes of a document: sorted keys, no whitespace, `drop` fields removed."""
    body = {k: v for k, v in doc.items() if k not in drop}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(doc, drop=UNHASHED):
    return hashlib.sha256(canonical(doc, drop)).hexdigest()


def load(path):
    try:
        with open(path) as f:
            return json.load(f), None
    except (OSError, ValueError) as e:
        return None, str(e)


def save(path, doc):
    """Write atomically (a reader never sees half a file)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    return path


def seal(doc):
    """Set doc['sha256'] from the rest of the document; returns the digest."""
    doc["sha256"] = digest(doc)
    return doc["sha256"]


# ── schema checks: a small JSON Schema subset, no dependency ──────────────────────────
TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _type_ok(v, t):
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, TYPES[t])


def validate(doc, schema, where="$"):
    """Problems (one line each) of `doc` against a schema using: type (or a list of types),
    required, properties, additionalProperties: false, items, enum, const, pattern,
    minLength, minItems, minimum, maximum. Unknown keywords are ignored."""
    out = []
    t = schema.get("type")
    if t:
        ts = t if isinstance(t, list) else [t]
        if not any(_type_ok(doc, x) for x in ts):
            return ["%s must be %s" % (where, " or ".join(ts))]
    if "const" in schema and doc != schema["const"]:
        out.append("%s must be %r" % (where, schema["const"]))
    if "enum" in schema and doc not in schema["enum"]:
        out.append("%s must be one of %s" % (where, ", ".join(map(str, schema["enum"]))))
    if isinstance(doc, str):
        if len(doc.strip()) < schema.get("minLength", 0):
            out.append("%s is empty" % where if not doc.strip() else "%s is too short" % where)
        if "pattern" in schema and not re.search(schema["pattern"], doc):
            out.append("%s does not match %s" % (where, schema["pattern"]))
    if isinstance(doc, (int, float)) and not isinstance(doc, bool):
        if "minimum" in schema and doc < schema["minimum"]:
            out.append("%s must be >= %s" % (where, schema["minimum"]))
        if "maximum" in schema and doc > schema["maximum"]:
            out.append("%s must be <= %s" % (where, schema["maximum"]))
    if isinstance(doc, list):
        if len(doc) < schema.get("minItems", 0):
            out.append("%s needs at least %d item(s)" % (where, schema["minItems"]))
        if "items" in schema:
            for i, v in enumerate(doc):
                out += validate(v, schema["items"], "%s[%d]" % (where, i))
    if isinstance(doc, dict):
        props = schema.get("properties", {})
        for k in schema.get("required", []):
            if k not in doc:
                out.append("%s.%s is missing" % (where, k))
        for k, v in doc.items():
            if k in props:
                out += validate(v, props[k], "%s.%s" % (where, k))
            elif schema.get("additionalProperties") is False:
                out.append("%s.%s is not a known field" % (where, k))
    return out


def schema(kind):
    with open(os.path.join(SCHEMAS_DIR, kind + ".json")) as f:
        return json.load(f)


def problems(doc, kind, path=""):
    """Schema problems plus the core contract: harness-issued id matching the file name,
    a known status, and (when present) a sha256 that matches the content."""
    out = validate(doc, schema(kind))
    if not isinstance(doc, dict):
        return out
    idf = kind + "_id"
    iid = doc.get(idf, "")
    if not ID.match(str(iid)) or not str(iid).startswith(PREFIX[kind] + "_"):
        out.append("%s %r was not issued by the harness (mission %s)" % (idf, iid, "new"))
    elif path and not LAYOUT[kind].endswith(".json") and os.path.basename(path) != iid + ".json":
        out.append("%s %s does not match the file name (ids are frozen once issued)" % (idf, iid))
    if doc.get("status") not in STATUSES[kind]:
        out.append("status must be one of %s" % ", ".join(STATUSES[kind]))
    if "sha256" in doc and doc["sha256"] != digest(doc):
        out.append("sha256 does not match the content (the file changed after it was sealed)")
    return out


# ── events and approvals ─────────────────────────────────────────────────────────────
def emit(cwd, event, role="", refs=None, **data):
    return _events.emit(cwd, event, role=role, refs=refs, **data)


def approve(cwd, event, subject_ref, sha, by, reason="", **data):
    """Record a human approval of exactly this content (subject_ref at sha). The event is the
    proof; the artifact only points at it."""
    if event not in APPROVAL_EVENTS:
        raise ValueError("not an approval event: %r" % event)
    by, reason = (by or "").strip(), (reason or "").strip()
    if not by:
        raise ValueError("an approval names who approved (--by)")
    if "\n" in reason or len(reason) > 200:
        raise ValueError("an approval reason is one line of at most 200 characters")
    return emit(cwd, event, role="human", refs={"subject": subject_ref},
                subject=subject_ref, sha256=sha, by=by, reason=reason, **data)


def approvals(cwd, event, subject_ref):
    """Approval events for one subject, oldest first."""
    return [e for e in _events.read(cwd)
            if e.get("event") == event and (e.get("data") or {}).get("subject") == subject_ref]


def approved(cwd, event, subject_ref, sha):
    """The approval event that covers exactly this content, or None."""
    for e in reversed(approvals(cwd, event, subject_ref)):
        if (e.get("data") or {}).get("sha256") == sha:
            return e
    return None


def rel(cwd, path):
    """A project-relative path for refs and messages."""
    try:
        return os.path.relpath(os.path.realpath(path), os.path.realpath(_paths.project(cwd)))
    except ValueError:
        return path


# ── the snapshot `mission tick` decides from ─────────────────────────────────────────
def artifacts(cwd, kind):
    """Every artifact of a dir kind as (path, doc), oldest id first; unreadable files skipped."""
    loc = LAYOUT[kind]
    if loc.endswith(".json"):
        p = mdir(cwd, loc)
        d, _ = load(p)
        return [(p, d)] if isinstance(d, dict) else []
    out = []
    for p in sorted(glob.glob(mdir(cwd, loc, "*.json"))):
        d, _ = load(p)
        if isinstance(d, dict):
            out.append((p, d))
    return out


def snapshot(cwd):
    """What exists and in which status, per kind: {kind: {"count", "by_status", "latest"}},
    plus mission.approved_ok (its current content carries a human approval). Reads only
    the contract fields, so it never depends on an area module."""
    snap = {}
    for kind in LAYOUT:
        docs = artifacts(cwd, kind)
        by = {}
        for _, d in docs:
            by[d.get("status", "")] = by.get(d.get("status", ""), 0) + 1
        latest = max(docs, key=lambda pd: (pd[1].get("created_at", ""), pd[0]))[1] if docs else None
        snap[kind] = {"count": len(docs), "by_status": by,
                      "latest": {k: latest.get(k) for k in (kind + "_id", "status", "created_at") if k in latest}
                      if latest else None}
    m = artifacts(cwd, "mission")
    snap["mission"]["approved_ok"] = bool(m and m[0][1].get("status") == "approved" and approved(
        cwd, "mission.approved", rel(cwd, m[0][0]), digest(m[0][1])))
    return snap
