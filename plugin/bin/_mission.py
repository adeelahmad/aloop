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
  - a human approval is a ledger record (APPROVAL_EVENTS) whose data names the subject and
    sha256; gates decide from ledger records, never from a status field;
  - snapshot() reads only those fields, so `mission tick` can decide from facts.
"""
import glob
import hashlib
import json
import math
import os
import re
import secrets
import sys
import time
import uuid

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
    "ethics": ("draft", "recorded"),
    "rubric": ("proposed", "active", "retired"),
    "telemetry": ("off", "consented", "configured", "revoked"),
}

# human approvals: the event type, and the subject kind it approves
APPROVAL_EVENTS = {"mission.approved": "mission", "baseline.confirmed": "baseline",
                   "journey.approved": "journey", "goal.approved": "goal", "ethics.consent": "goal",
                   "telemetry.consent": "telemetry"}
# a human grant counts only with the human's own words (M9.17): a line of a prompt the
# UserPromptSubmit hook captured (`transcripts prompt`) that says the grant's verb and the code of
# the content it grants (the first 8 hex of its sha256). A veto needs none: refusing is always safe.
GRANT_VERBS = {"mission.approved": "approve", "baseline.confirmed": "confirm", "journey.approved": "approve",
               "goal.approved": "approve", "ethics.consent": "consent", "telemetry.consent": "consent"}
ACTION_VERBS = ("restore", "retire")    # a grant whose data.action is one of these says that verb
SUPERSEDE_ACROSS = ("baseline.confirmed",)     # the newest confirmation of any version is the current one
NEGATION = re.compile(r"\b(?:not|no|never|don'?t|veto\w*|reject\w*|cancel\w*|stop|wait|undo)\b")

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


def cli_text(argv):
    """argv as the UTF-8 it was typed in, and a stdout/stderr that never crash on it, whatever the
    locale: a C locale decodes argv as ASCII and leaves lone surrogates in it."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
    return [os.fsencode(a).decode("utf-8", "replace") for a in argv]


def canonical(doc, drop=UNHASHED):
    """The canonical bytes of a document: sorted keys, no whitespace, `drop` fields removed. A lone
    surrogate (only a hand-edited file holds one) is encoded as is, so digesting never crashes."""
    body = {k: v for k, v in doc.items() if k not in drop}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8", "surrogatepass")


def digest(doc, drop=UNHASHED):
    return hashlib.sha256(canonical(doc, drop)).hexdigest()


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except (OSError, ValueError) as e:
        return None, str(e)


def save(path, doc):
    """Write atomically (a reader never sees half a file)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
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
        if re.search("[\ud800-\udfff]", doc):
            out.append("%s holds a lone surrogate, which is not text" % where)
    if isinstance(doc, float) and not math.isfinite(doc):
        return ["%s must be a finite number" % where]
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
    with open(os.path.join(SCHEMAS_DIR, kind + ".json"), encoding="utf-8") as f:
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


# a human decision that is not an approval: a veto, an opt-out
HUMAN_EVENTS = tuple(APPROVAL_EVENTS) + ("baseline.vetoed", "telemetry.revoked")
# every record a mission gate decides from: the human decisions, plus what the harness measured,
# assessed, retired, reviewed (a hard ethics line stays blocked on a fresh checkout) and derived
LEDGER_EVENTS = HUMAN_EVENTS + ("baseline.measured", "state.assessed", "journey.retired", "ethics.reviewed",
                                "ethics.blocked", "goal.derived", "goal.met")


def ledger(cwd=None):
    """docs/agents/mission/ledger.jsonl: every record a mission gate decides from (human decisions,
    measurements, retirements), committed with the artifacts it covers. The event stream is
    git-ignored run data, so a fresh checkout (another machine, a scheduled run) would otherwise
    lose them. Each row carries the hash of the row it follows, so an edited or removed row and a
    backdated one are detected; `mission init` marks it merge=union so branches append cleanly."""
    return mdir(cwd, "ledger.jsonl")


def _row_hash(row):
    return digest(row, drop=("hash",))


def ledger_rows(cwd):
    """(intact rows in file order, problems): one problem per malformed line, edited row, row
    whose predecessor is gone, or row dated before the row it follows."""
    try:
        with open(ledger(cwd), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return [], []
    rows, bad, seen = [], [], {}
    for n, ln in enumerate(lines, 1):
        if not ln.strip():
            continue
        try:
            row = json.loads(ln)
        except ValueError:
            bad.append("ledger line %d is not JSON" % n)
            continue
        if not isinstance(row, dict) or not isinstance(row.get("data"), dict) or not isinstance(row.get("ts"), str) \
                or row.get("event") not in LEDGER_EVENTS:
            bad.append("ledger line %d is not a ledger row" % n)
            continue
        if row.get("hash") != _row_hash(row):
            bad.append("ledger line %d was edited (its hash does not match its content)" % n)
            continue
        prev = row.get("prev") or ""
        if (prev and prev not in seen) or (not prev and rows):
            bad.append("ledger line %d follows a row that is no longer there (a row was removed or edited)" % n)
        elif prev and row["ts"] < seen[prev]["ts"]:
            bad.append("ledger line %d is dated before the row it follows" % n)
        seen[row["hash"]] = row
        if row["event"] in GRANT_VERBS and row.get("role") == "human" and not cites(row):
            bad.append("ledger line %d is a human %s that cites none of the human's own words (no captured "
                       "prompt line says it)" % (n, row["event"]))
            continue
        rows.append(row)
    return rows, bad


def branch(cwd):
    """The main worktree's branch (the ledger is that worktree's file), or "" on a detached HEAD."""
    return _paths._git(_paths.project(cwd), "symbolic-ref", "-q", "--short", "HEAD")


class LedgerBroken(RuntimeError):
    """The ledger has problems, so no gate decides from it and no row is chained past them."""


def ledger_problems(cwd):
    """The ledger's own problems, plus every ledger event the run log recorded on this branch that
    the ledger lost (the run log is shared by every branch of the checkout; another branch's
    ledger never had this branch's rows)."""
    rows, bad = ledger_rows(cwd)
    have = set(r.get("event_id") for r in rows)
    here = None
    for e in _events.read(cwd):
        d = e.get("data")
        if not isinstance(d, dict) or d.get("ledger") is not True or e.get("event_id") in have:
            continue
        here = branch(cwd) if here is None else here
        if d.get("branch", here) == here:
            bad.append("the ledger lost %s %s (the run log recorded it at %s)" % (e.get("event"), d.get("subject"), e.get("ts")))
    return bad


def ledger_block(cwd, area):
    """Print why and return 2 when the ledger is broken (nothing may decide from it), else 0."""
    bad = ledger_problems(cwd)
    for b in bad:
        print("BLOCK[%s]: %s (restore it: git checkout -- %s)" % (area, b, rel(cwd, ledger(cwd))), file=sys.stderr)
    return 2 if bad else 0


def record(cwd, event, subject_ref, role, **data):
    """Append one record a gate decides from to the ledger (and the event stream); returns the row."""
    if event not in LEDGER_EVENTS:
        raise ValueError("not a ledger event: %r" % event)
    bad = ledger_problems(cwd)
    if bad:
        raise LedgerBroken("; ".join(bad))
    body = dict(data, subject=subject_ref)
    if event in HUMAN_EVENTS:      # a decision (or the veto window's) dates every prompt captured before it
        body["prompt_log"] = prompt_clock(prompts(cwd))
    eid = emit(cwd, event, role=role, refs={"subject": subject_ref}, ledger=True, branch=branch(cwd), **body) \
        or uuid.uuid4().hex
    rows, _ = ledger_rows(cwd)
    ts = max([now()] + [r["ts"] for r in rows[-1:]])
    row = {"event_id": eid, "ts": ts, "event": event, "role": role, "data": body,
           "prev": rows[-1]["hash"] if rows else ""}
    row["hash"] = _row_hash(row)
    os.makedirs(os.path.dirname(ledger(cwd)), exist_ok=True)
    with open(ledger(cwd), "a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    return row


class NeedsHuman(Exception):
    """A grant the human has not said in their own words yet: ASK (exit 3) with the line to reply."""


def code(sha):
    """The short code a human types to grant exactly this content."""
    return (sha or "")[:8]


def grant_verb(event, data):
    a = data.get("action")
    return a if a in ACTION_VERBS else GRANT_VERBS[event]


def grants(line, verb, sha):
    """True when one typed line grants exactly this content: it starts with the verb (after an optional
    yes or ok), names the content's code (or more of its sha256), and says nothing that undoes it."""
    s = re.sub(r"^(?:yes|ok|okay|sure)\b[\s,.!:;-]*", "", line.strip().strip("`'\"").strip().lower())
    hexes = [t for t in re.findall(r"\b[0-9a-f]{8,64}\b", s) if sha and sha.startswith(t)]
    return bool(re.match(r"%s\b" % re.escape(verb), s)) and bool(hexes) and not NEGATION.search(s)


def cites(row):
    """A human grant's record names a captured prompt line that grants exactly its content."""
    d, c = row["data"], row["data"].get("prompt")
    return isinstance(c, dict) and isinstance(c.get("line"), str) and isinstance(d.get("sha256"), str) \
        and grants(c["line"], grant_verb(row["event"], d), d["sha256"])


def prompts(cwd):
    """The human prompts the UserPromptSubmit hook captured in this checkout (`transcripts prompt`),
    oldest first: {ts, sha256, session, text}. Run data, so another checkout has its own."""
    out = []
    for p in sorted(glob.glob(os.path.join(_paths.home(cwd, "transcripts", make=False), "*", "events.jsonl"))):
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            continue
        for ln in lines:
            if '"prompt"' not in ln:        # the store also holds every tool call's full payload
                continue
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            pl = e.get("payload") if isinstance(e, dict) and e.get("kind") == "prompt" else None
            if isinstance(pl, dict) and isinstance(pl.get("prompt"), str) and isinstance(e.get("ts"), str):
                out.append({"ts": e["ts"], "session": str(pl.get("session_id") or ""), "text": pl["prompt"],
                            "sha256": hashlib.sha256(pl["prompt"].encode("utf-8", "surrogatepass")).hexdigest()})
    return sorted(out, key=lambda q: q["ts"])     # stable: file order within a second


def prompt_clock(qs):
    """Which prompt log this is (its first prompt) and how many prompts it had: a human decision records
    it, so a line captured before that decision is known to be older, even within the same second."""
    return {"id": hashlib.sha256((qs[0]["ts"] + qs[0]["sha256"]).encode()).hexdigest()[:16] if qs else "",
            "seen": len(qs)}


def stale_before(cwd, event, subject_ref, sha, qs):
    """How many of the captured prompts are too old to grant this: every one captured before a later human
    decision about the same subject (other content approved, a veto, a revocation) or, for a baseline,
    before any version's confirmation (the newest one is the current version)."""
    clock, floor = prompt_clock(qs), 0
    for r in ledger_rows(cwd)[0]:
        d = r["data"]
        if r["event"] not in HUMAN_EVENTS or (r["event"] == event and d.get("subject") == subject_ref
                                              and d.get("sha256") == sha):
            continue                       # this very content (a consent per review, a repeat) undoes nothing
        if d.get("subject") != subject_ref and not (event in SUPERSEDE_ACROSS and r["event"] == event):
            continue
        seen = d.get("prompt_log") if isinstance(d.get("prompt_log"), dict) else {}
        if clock["id"] and seen.get("id") == clock["id"] and isinstance(seen.get("seen"), int):
            floor = max(floor, seen["seen"])
        else:   # recorded on another checkout: a prompt from that second or before is older
            floor = max(floor, sum(1 for q in qs if q["ts"] <= r["ts"]))
    return floor


def provenance(cwd, event, subject_ref, sha, data):
    """The newest captured prompt line that grants this content, as the record's `prompt`; raises
    NeedsHuman with the line to reply when there is none. A line older than a later decision about the
    subject is stale, so an old "approve" never re-grants content that was replaced or vetoed."""
    verb, qs = grant_verb(event, data), prompts(cwd)
    for q in reversed(qs[stale_before(cwd, event, subject_ref, sha, qs):]):
        for line in q["text"].splitlines():
            if grants(line, verb, sha):
                return {"ts": q["ts"], "sha256": q["sha256"], "session": q["session"], "line": line.strip()[:200]}
    name = next((data[k] for k in ("mission_id", "baseline_id", "journey_id", "goal_id", "subject_id", "telemetry_id")
                 if isinstance(data.get(k), str) and data[k]), os.path.basename(subject_ref))
    raise NeedsHuman("%s is the human's own decision: they reply  %s %s %s  (the code names exactly this "
                     "content), then run this again" % (event, verb, name, code(sha)))


def record_human(cwd, event, subject_ref, by, reason="", **data):
    """Record a human decision about one subject; returns its event id. A grant also cites the
    captured prompt line that says it (raises NeedsHuman until the human has typed one)."""
    if event not in HUMAN_EVENTS:
        raise ValueError("not a human decision event: %r" % event)
    by, reason = (by or "").strip(), (reason or "").strip()
    if not by:
        raise ValueError("a human decision names who made it (--by)")
    if "\n" in reason or len(reason) > 200:
        raise ValueError("a reason is one line of at most 200 characters")
    if event in GRANT_VERBS:
        data["prompt"] = provenance(cwd, event, subject_ref, data.get("sha256") or "", data)
    return record(cwd, event, subject_ref, "human", by=by, reason=reason, **data)["event_id"]


def approve(cwd, event, subject_ref, sha, by, reason="", **data):
    """Record a human approval of exactly this content (subject_ref at sha). The record is the
    proof; the artifact only points at it."""
    if event not in APPROVAL_EVENTS:
        raise ValueError("not an approval event: %r" % event)
    return record_human(cwd, event, subject_ref, by, reason, sha256=sha, **data)


def records(cwd, event, subject_ref=None, role=None):
    """The ledger's intact rows of one event type (one subject's, one role's), oldest first."""
    rows, _ = ledger_rows(cwd)
    return sorted([r for r in rows if r["event"] == event and (subject_ref is None or r["data"].get("subject") == subject_ref)
                   and (role is None or r.get("role") == role)], key=lambda r: r["ts"])


def human_events(cwd, event, subject_ref):
    return records(cwd, event, subject_ref, role="human")


approvals = human_events


def newest(cwd, event, subject_ref, role=None):
    rows = records(cwd, event, subject_ref, role)
    return rows[-1] if rows else None


def approved(cwd, event, subject_ref, sha):
    """The subject's newest human approval when it covers exactly this content, else None: an
    older approval of other content is superseded, so restoring that content is not approved."""
    rec = newest(cwd, event, subject_ref, role="human")
    return rec if rec and rec["data"].get("sha256") == sha else None


def covered(cwd, kind, event):
    """(path, doc, record) of every artifact of a kind whose current content the subject's newest
    `event` record covers exactly (the status field is outside the digest, so it is never trusted)."""
    out = []
    for p, d in artifacts(cwd, kind):
        rec = newest(cwd, event, rel(cwd, p))
        if rec and rec["data"].get("sha256") == digest(d):
            out.append((p, d, rec))
    return out


def malformed(probs):
    """True when schema problems say the shape is wrong (semantic checks would only crash or add noise)."""
    return any(p.startswith("$") and ("missing" in p or "must be" in p) for p in probs)


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
    snap["mission"]["approved_ok"] = bool(mission(cwd)[2])
    return snap


def mission(cwd):
    """(doc, sha256, approval event) of the mission; approval is the event covering its current
    content (None when it is a draft or changed since). doc is None when there is no mission."""
    p = path_of(cwd, "mission")
    doc, _ = load(p)
    if not isinstance(doc, dict):
        return None, "", None
    sha = digest(doc)
    ev = approved(cwd, "mission.approved", rel(cwd, p), sha) if doc.get("status") == "approved" else None
    return doc, sha, ev
