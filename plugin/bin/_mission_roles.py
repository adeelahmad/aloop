"""_mission_roles.py — the six fixed mission roles and the paths each may write (ROADMAP §4.12, M9.9).

Shared by gate-mission-scope (PreToolUse: role path guards) and gate-mission-artifact (SubagentStart
records what exists; SubagentStop checks what the role wrote). The table is plugin/mission/roles.json.

Paths are classified against the project's git worktrees: a target inside one of them has a
project-relative path; anything else (a scratch dir, /dev/null) is outside the project. The rules:
  - a mission role writes only its `writes` paths in the main tree, plus run data (.aladdin/) but
    not the event log or the gates' state (logs/, state/: its start record and approvals live there);
  - any other dispatched agent (a builder, a planner) never writes docs/agents/mission/, in any tree;
  - the supervisor never hand-writes a role's artifact (mission.json is the human's, sealed by approval).
"""
import glob
import hashlib
import json
import os
import re
import time

import _events
import _mission as M
import _paths

ROLES_FILE = os.path.join(M.AREAS_DIR, "roles.json")
MISSION = "docs/agents/mission/"
INTENTS = "docs/agents/intents/"
LEDGER = MISSION + "ledger.jsonl"   # appended only by the mission CLI (_mission.record)
RUN_DATA = (".aladdin/", ".agentic/", ".transcripts/")
OPEN_RUN_TTL = 90 * 60        # an open run with no activity for this long (its stop never came) owns nothing:
                              # budget only observes a mission role's time box, so its last tool call decides


def table():
    with open(ROLES_FILE) as f:
        return json.load(f)


T = table()
ROLES = tuple(T["roles"])
KIND_AREAS = T["kind_areas"]
AREA_CHECKS = T.get("area_checks", {})
RECORDS = T.get("records", {})
# the mission layer's event prefixes: only its own commands and gates emit them (never `events emit` by hand)
MISSION_EVENTS = frozenset(["mission", "artifact"] + list(M.LAYOUT) + list(KIND_AREAS.values()))
HUMAN_VERBS = frozenset(T["human_verbs"])
READ_ONLY = frozenset(T["read_only"])
DIR_KIND = {loc: kind for kind, loc in M.LAYOUT.items() if not loc.endswith(".json")}


def role_of(agent_type):
    """The bare role of a host's agent type (`aloop:evaluator` → evaluator)."""
    return (agent_type or "").split(":")[-1]


def writes(role):
    return tuple(T["roles"][role]["writes"]) if role in T["roles"] else ()


def under(rel, prefix):
    """A project-relative path is the dir `prefix` (ends with /) or inside it."""
    return (rel.rstrip("/") + "/").startswith(prefix)


def owners(rel):
    """The mission roles allowed to write a project-relative path."""
    return [r for r in ROLES if any(under(rel, p) for p in writes(r))]


def owners_of(rel, field):
    """The roles a file belongs to: the role its `role` field names (`research new --role product-tester`)
    when that role may write it, else every role whose paths hold it."""
    by = owners(rel)
    return [field] if field in by else by


def kind_of(rel):
    """The artifact kind of docs/agents/mission/<dir>/<id>.json (a top-level artifact file), else None."""
    m = re.match(r"^docs/agents/mission/([a-z]+)/([^/]+)\.json$", rel)
    return DIR_KIND.get(m.group(1)) if m else None


def record_of(rel):
    """The record kind of a file a role's command writes beside its artifacts (roles.json `records`: a judge
    run log, a usage report), else None."""
    for kind, spec in RECORDS.items():
        name = rel[len(spec["path"]):] if rel.startswith(spec["path"]) else ""
        if name.endswith(spec["ext"]) and "/" not in name and len(name) > len(spec["ext"]):
            return kind
    return None


def area_of(kind):
    """The area whose `check` judges an artifact or record kind."""
    return KIND_AREAS.get(kind) or (RECORDS.get(kind) or {}).get("area") or ""


def ident(kind, doc, rel):
    """An artifact's id (its `<kind>_id`, a record's `id` field), or a log's file name: the id of the artifact
    it records (judges/runs/<rub_id>.jsonl)."""
    spec = RECORDS.get(kind)
    if spec and not spec.get("id"):
        return os.path.basename(rel)[:-len(spec["ext"])]
    return (doc or {}).get(spec["id"] if spec else kind + "_id", "")


def is_draft(rel):
    """A file at the top of an artifact dir that no harness id names (a draft, a saved page): never an
    artifact, so removing it deletes no id from the audit trail."""
    m = re.match(r"^docs/agents/mission/[a-z]+/([^/]+)$", rel)
    return bool(m) and not (m.group(1).endswith(".json") and M.ID.match(m.group(1)[:-5]))


# ── classifying a target path ────────────────────────────────────────────────────────
def roots(project):
    """Every worktree root of the project's repo (main first), symlinks resolved."""
    out = []
    for line in _paths._git(project, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            out.append(os.path.realpath(line[len("worktree "):]))
    return out or [os.path.realpath(project)]


def locate(path, base, rts):
    """(root, rel) for a target path: the longest worktree root holding it and the path relative
    to that root, or (None, None) when it is outside the project."""
    full = os.path.realpath(os.path.join(base, os.path.expanduser(path)))
    best = None
    for r in rts:
        if full == r or full.startswith(r.rstrip("/") + "/"):
            if best is None or len(r) > len(best):
                best = r
    if best is None:
        return None, None
    return best, os.path.relpath(full, best).replace(os.sep, "/")


def is_harness_state(rel):
    """The event log, the gates' state and the captured prompts under run data: written by the
    harness's own commands and hooks."""
    return any(under(rel, h + d) for h in (".aladdin/", ".agentic/") for d in ("logs/", "state/", "transcripts/"))


def is_prompt_log(rel):
    """The prompts the UserPromptSubmit hook captured (`transcripts prompt`): a mission grant is read from
    the human's own words there (M9.17), so no tool call writes it."""
    return any(under(rel, h + "transcripts/") for h in (".aladdin/", ".agentic/"))


def is_run_data(rel):
    """Run data (.aladdin/ and the like) but not a task worktree under it, which holds source."""
    if any(under(rel, h + "worktrees/") for h in (".aladdin/", ".agentic/")):
        return False
    return any(under(rel, h) for h in RUN_DATA)


# ── what exists (the before/after record gate-mission-artifact diffs) ──────────────────
def _fingerprint(path):
    """(raw sha256, content digest): the content digest of a JSON artifact leaves out its bookkeeping
    (sha256, status, approval), so a human approval of a file is not an edit of it."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    sha = hashlib.sha256(raw).hexdigest()
    content = sha
    if path.endswith(".json"):
        try:
            d = json.loads(raw.decode("utf-8"))
            if isinstance(d, dict):
                content = M.digest(d)
        except (ValueError, UnicodeDecodeError):
            pass
    return [sha, content]


def scan(project):
    """{project-relative path: [raw sha256, content digest]} for every file under
    docs/agents/mission/ and docs/agents/intents/."""
    out = {}
    for top in (MISSION, INTENTS):
        base = os.path.join(project, top)
        for p in glob.glob(os.path.join(base, "**", "*"), recursive=True):
            if os.path.isfile(p) and ".tmp." not in os.path.basename(p):
                fp = _fingerprint(p)
                if fp:
                    out[os.path.relpath(p, project).replace(os.sep, "/")] = fp
    return out


def labels(project):
    """({path: the mission role its `role` field names}, {intent path: its `source`}) of the top-level JSON
    under docs/agents/mission/<dir>/ and docs/agents/intents/: who each file says it belongs to."""
    roles, sources = {}, {}
    for pat in (os.path.join(project, MISSION, "*", "*.json"), os.path.join(project, INTENTS, "*.json")):
        for p in glob.glob(pat):
            d, _ = M.load(p)
            rel = os.path.relpath(p, project).replace(os.sep, "/")
            if not isinstance(d, dict):
                continue
            if rel.startswith(INTENTS):
                sources[rel] = d.get("source") if isinstance(d.get("source"), str) else ""
            elif d.get("role") in ROLES:
                roles[rel] = d["role"]
    return roles, sources


def ledger_mark(project):
    """{size, sha256} of the ledger's bytes now, None when there is none: the CLI only appends to it."""
    try:
        with open(os.path.join(project, LEDGER), "rb") as f:
            raw = f.read()
    except OSError:
        return None
    return {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def ledger_kept(project, mark):
    """True when the ledger still starts with exactly the bytes `mark` recorded (it was only appended to)."""
    if not isinstance(mark, dict):
        return True
    try:
        with open(os.path.join(project, LEDGER), "rb") as f:
            raw = f.read(int(mark.get("size") or 0))
    except OSError:
        return False
    return len(raw) == int(mark.get("size") or 0) and hashlib.sha256(raw).hexdigest() == mark.get("sha256")


def runs_dir(project):
    return _paths.home(project, "state/mission-runs")


def run_file(project, agent_id):
    return os.path.join(runs_dir(project), re.sub(r"[^A-Za-z0-9_.-]", "_", agent_id) + ".json")


def events_size(project):
    try:
        return os.path.getsize(_events.path(project))
    except OSError:
        return 0


def events_since(project, offset):
    """The events appended to the event log after byte `offset`; [] when the log was rotated below it."""
    if events_size(project) < offset:
        return []
    out = []
    try:
        with open(_events.path(project), "rb") as f:
            f.seek(offset)
            for line in f.read().decode("utf-8", "replace").splitlines():
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if isinstance(e, dict):
                    out.append(e)
    except OSError:
        pass
    return out


def is_human_event(e):
    """An approval, a consent, a mission set: the human's (or the loop's veto-window confirmation)."""
    return e.get("role") == "human" or e.get("event") in M.APPROVAL_EVENTS


def human_events_since(project, offset):
    return [e for e in events_since(project, offset) if is_human_event(e)]


def own_areas(role):
    """The artifact kinds a role writes and their areas: the event prefixes its own commands emit
    (`ethics.blocked`, `baseline.confirmed`, `judge.verdict`…)."""
    out = set()
    for w in writes(role):
        m = re.match(r"^docs/agents/mission/([a-z]+)/$", w)
        kind = DIR_KIND.get(m.group(1)) if m else None
        if kind:
            out |= {kind, KIND_AREAS.get(kind, kind)}
    return out


def named_paths(e):
    """The project paths an event names (its refs, and a `subject` in its data)."""
    vals = list((e.get("refs") or {}).values()) + [(e.get("data") or {}).get("subject")]
    return {v for v in vals if isinstance(v, str) and v}


def effects(e):
    """{path: the status its step leaves, None for any} of the files a recorded step changes, as its record says:
    the paths it names; a baseline confirmation's journeys (frozen: active) and the version a restore replaced, the
    one a `baseline.superseded` record names (superseded); the goal an ethics hard line blocks (blocked)."""
    ev, d = e.get("event"), e.get("data") or {}
    out = {p: "superseded" if ev == "baseline.superseded" else None for p in named_paths(e)}
    if ev == "ethics.blocked" and under(str(d.get("subject")), MISSION + M.LAYOUT["goal"] + "/"):
        out[d["subject"]] = "blocked"
    if ev == "baseline.confirmed":
        for j in d.get("journeys") if isinstance(d.get("journeys"), list) else []:
            if isinstance(j, dict) and isinstance(j.get("journey_id"), str):
                out.setdefault(MISSION + "%s/%s.json" % (M.LAYOUT["journey"], j["journey_id"]), "active")
        if isinstance(d.get("replaces"), str) and d["replaces"]:
            out.setdefault(MISSION + "%s/%s.json" % (M.LAYOUT["baseline"], d["replaces"]), "superseded")
    return out


def ledger_problems(project):
    """The ledger's problems now (_mission.ledger_problems), so a stop blames a run only for new ones."""
    try:
        return M.ledger_problems(project)
    except Exception as e:                                # noqa: BLE001 — a start record never fails on it
        return ["cannot verify the ledger: %s" % e]


def marks(project):
    """What a stop diffs against: every file's fingerprint, who the files say they belong to, the event log's
    size, the ledger's bytes and its problems."""
    roles, sources = labels(project)
    return {"files": scan(project), "roles": roles, "sources": sources, "events_at": events_size(project),
            "ledger": ledger_mark(project), "ledger_problems": ledger_problems(project)}


def record_start(project, agent_id, role):
    rec = dict(marks(project), agent_id=agent_id, role=role, started=time.time())
    M.save(run_file(project, agent_id), rec)
    return rec


def load_run(project, agent_id):
    d, _ = M.load(run_file(project, agent_id))
    return d if isinstance(d, dict) else None


def seen(project, d):
    """When a run last showed activity: its start, or its newest tool call (bin/budget's PostToolUse hook rewrites
    the agent's budget state on every call, on each host), whichever is later."""
    t = float(d.get("started") or 0)
    try:
        aid = re.sub(r"[^A-Za-z0-9_.-]", "_", d.get("agent_id") or "")
        t = max(t, os.path.getmtime(os.path.join(_paths.home(project, "budget", make=False), aid + ".json")))
    except (OSError, TypeError):
        pass
    return t


def alive(project, d, now=None):
    """A run that started, has not stopped, and was active within OPEN_RUN_TTL."""
    return not d.get("stopped") and (now or time.time()) - seen(project, d) < OPEN_RUN_TTL


def open_runs(project, now=None):
    """Mission role runs that started and have not stopped (active within OPEN_RUN_TTL)."""
    out = []
    for p in glob.glob(os.path.join(runs_dir(project), "*.json")):
        d, _ = M.load(p)
        if isinstance(d, dict) and alive(project, d, now):
            out.append(d)
    return out


def overlapping_runs(project, run, now=None):
    """The other runs that overlapped `run`: still open (and alive), or stopped after it started."""
    since = float(run.get("started") or 0)
    out = []
    for p in glob.glob(os.path.join(runs_dir(project), "*.json")):
        d, _ = M.load(p)
        if not isinstance(d, dict) or d.get("agent_id") == run.get("agent_id") or not d.get("role"):
            continue
        if (d.get("stopped") and float(d["stopped"]) >= since) or alive(project, d, now):
            out.append(d)
    return out


def answers(d, rel, fp):
    """True when run `d` answers for `rel` as it is now (fingerprint `fp`, None when deleted): an open run judges
    at its stop what changed since its start; a passed run judged what its pass recorded (`judged`), so a change
    after it, or one it never saw, is not its; a released run owns nothing."""
    if not d.get("stopped"):
        return (d.get("files") or {}).get(rel) != fp
    return not d.get("released") and rel in (d.get("judged") or {}) and d["judged"][rel] == fp


def latest_open(project, role):
    """The newest open run of a role: what a `selfcheck`, which has no agent id, is checking."""
    mine = [d for d in open_runs(project) if d.get("role") == role]
    return max(mine, key=lambda d: d.get("started") or 0) if mine else None
