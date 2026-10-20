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
RUN_DATA = (".aladdin/", ".agentic/", ".transcripts/")
OPEN_RUN_TTL = 90 * 60        # an open run older than this (its stop never came) owns nothing: a role's
                              # hard time box (budget ROLE_PROFILES, 60 min) plus a grace


def table():
    with open(ROLES_FILE) as f:
        return json.load(f)


T = table()
ROLES = tuple(T["roles"])
KIND_AREAS = T["kind_areas"]
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


def kind_of(rel):
    """The artifact kind of docs/agents/mission/<dir>/<id>.json (a top-level artifact file), else None."""
    m = re.match(r"^docs/agents/mission/([a-z]+)/([^/]+)\.json$", rel)
    return DIR_KIND.get(m.group(1)) if m else None


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
    """The event log and the gates' state under run data: written by the harness's own commands."""
    return any(under(rel, h + d) for h in (".aladdin/", ".agentic/") for d in ("logs/", "state/"))


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


def runs_dir(project):
    return _paths.home(project, "state/mission-runs")


def run_file(project, agent_id):
    return os.path.join(runs_dir(project), re.sub(r"[^A-Za-z0-9_.-]", "_", agent_id) + ".json")


def events_size(project):
    try:
        return os.path.getsize(_events.path(project))
    except OSError:
        return 0


def human_events_since(project, offset):
    """The human's events (approvals, consents, a mission set) appended to the event log after
    byte `offset`; [] when the log was rotated below it."""
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
                if isinstance(e, dict) and (e.get("role") == "human" or e.get("event") in M.APPROVAL_EVENTS):
                    out.append(e)
    except OSError:
        pass
    return out


def record_start(project, agent_id, role):
    rec = {"agent_id": agent_id, "role": role, "started": time.time(), "files": scan(project),
           "events_at": events_size(project)}
    M.save(run_file(project, agent_id), rec)
    return rec


def load_run(project, agent_id):
    d, _ = M.load(run_file(project, agent_id))
    return d if isinstance(d, dict) else None


def open_runs(project, now=None):
    """Mission role runs that started and have not stopped (within OPEN_RUN_TTL)."""
    now = now or time.time()
    out = []
    for p in glob.glob(os.path.join(runs_dir(project), "*.json")):
        d, _ = M.load(p)
        if isinstance(d, dict) and not d.get("stopped") and now - float(d.get("started") or 0) < OPEN_RUN_TTL:
            out.append(d)
    return out


def overlapping_roles(project, run, now=None):
    """The roles of other runs that overlapped `run`: still open, or stopped after it started."""
    now = now or time.time()
    since = float(run.get("started") or 0)
    out = set()
    for p in glob.glob(os.path.join(runs_dir(project), "*.json")):
        d, _ = M.load(p)
        if not isinstance(d, dict) or d.get("agent_id") == run.get("agent_id") or not d.get("role"):
            continue
        stopped = d.get("stopped")
        if (stopped and float(stopped) >= since) or (not stopped and now - float(d.get("started") or 0) < OPEN_RUN_TTL):
            out.add(d["role"])
    return out


def latest_open(project, role):
    """The newest open run of a role: what a `selfcheck`, which has no agent id, is checking."""
    mine = [d for d in open_runs(project) if d.get("role") == role]
    return max(mine, key=lambda d: d.get("started") or 0) if mine else None
