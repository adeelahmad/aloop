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
import calendar
import glob
import hashlib
import json
import os
import re
import subprocess
import time

import _events
import _mission as M
import _paths

ROLES_FILE = os.path.join(M.AREAS_DIR, "roles.json")
MISSION = "docs/agents/mission/"
INTENTS = "docs/agents/intents/"
LEDGER = MISSION + "ledger.jsonl"   # appended only by the mission CLI (_mission.record)
SNAPSHOTS = MISSION + "%s/snapshots/" % M.LAYOUT["research"]   # a source's archived bytes, named by their sha256
RUN_DATA = (".aladdin/", ".agentic/", ".transcripts/")
OPEN_RUN_TTL = 90 * 60        # an open run with no activity for this long (its stop never came) owns nothing:
                              # budget only observes a mission role's time box, so its last tool call decides
                              # (as the transcript store recorded it: activity())
TS = re.compile(rb'^\{"ts":\s*"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)"')   # a transcript store line's own time
LINE_CAP = 8 << 20            # a longer line keeps only its head (its time), so the read stays linear; that one
                              # call is not counted as activity (it can only make a run look idle, never active)


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


def ledger_bound(role):
    """The role's own commands that decide from the ledger, so they refuse while it is broken (roles.json
    `ledger_bound`): only for such a role is a run that wrote nothing on a ledger broken before it no defect."""
    return tuple(T["roles"][role].get("ledger_bound", ())) if role in T["roles"] else ()


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


def intent_stem(rel):
    """The intent id a file in docs/agents/intents/ is named for (`<id>.json` at its top: what `aladdin run`, which
    takes the newest *.json there, reads as a request), else None (the words `aladdin` kept beside an intent, a note)."""
    return os.path.basename(rel)[:-5] if under(rel, INTENTS) and rel.count("/") == 3 and rel.endswith(".json") else None


def rel_path(project, v):
    """A path an event names, relative to the project (a command records it absolute), "" when it is outside it."""
    if not isinstance(v, str) or not v:
        return ""
    root = os.path.realpath(project)
    full = os.path.realpath(os.path.join(root, v))
    return os.path.relpath(full, root).replace(os.sep, "/") if full.startswith(root.rstrip("/") + "/") else ""


def is_snapshot(rel):
    """A research source's archived bytes (research/snapshots/<sha256>)."""
    return under(rel, SNAPSHOTS) and rel.count("/") == SNAPSHOTS.count("/")


def cited_by(rel, *cites):
    """The roles a snapshot belongs to: those whose research reports cite it (in any of the `cites` maps that
    labels() returns), else the research dir's first owner (the Scout, whose evidence store it is)."""
    out = sorted({r for c in cites for r in (c or {}).get(rel) or () if r in ROLES})
    return out or owners(rel)[:1]


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
    """({path: the mission role its `role` field names}, {intent path: its `source`}, {snapshot path: the roles
    whose research reports cite it}) of the top-level JSON under docs/agents/mission/<dir>/ and
    docs/agents/intents/: who each file says it belongs to."""
    roles, sources, cites = {}, {}, {}
    for pat in (os.path.join(project, MISSION, "*", "*.json"), os.path.join(project, INTENTS, "*.json")):
        for p in glob.glob(pat):
            d, _ = M.load(p)
            rel = os.path.relpath(p, project).replace(os.sep, "/")
            if not isinstance(d, dict):
                continue
            if rel.startswith(INTENTS):
                sources[rel] = d.get("source") if isinstance(d.get("source"), str) else ""
                continue
            if d.get("role") in ROLES:
                roles[rel] = d["role"]
            if kind_of(rel) == "research" and isinstance(d.get("sources"), list):
                by = roles.get(rel) or owners(rel)[0]
                for src in d["sources"]:
                    snap = MISSION + src["snapshot"] if isinstance(src, dict) and isinstance(src.get("snapshot"), str) else ""
                    if is_snapshot(snap):
                        cites.setdefault(snap, set()).add(by)
    return roles, sources, {k: sorted(v) for k, v in cites.items()}


def _mark(raw):
    return {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _rows(raw):
    """{sha256 of each row's line: its event_id} of a ledger's bytes: the rows it holds, byte for byte."""
    out = {}
    for ln in raw.splitlines():
        try:
            row = json.loads(ln.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(row, dict) and isinstance(row.get("event_id"), str):
            out[hashlib.sha256(ln).hexdigest()] = row["event_id"]
    return out


def ledger_mark(project, problems=()):
    """{size, sha256, rows} of the ledger's bytes now, None when there is none: the CLI only appends to it, and
    `rows` names each row it holds (byte for byte, by event_id). A ledger with problems also marks its `good` rows:
    those before the first bad line (the supervisor's repair keeps them)."""
    try:
        with open(os.path.join(project, LEDGER), "rb") as f:
            raw = f.read()
    except OSError:
        return None
    mark = dict(_mark(raw), rows=_rows(raw))
    if problems:
        bad = [int(m.group(1)) for m in (re.match(r"ledger line (\d+) ", x) for x in problems) if m]
        lines = raw.splitlines(True)
        mark["good"] = _mark(b"".join(lines[:min(bad) - 1]) if bad else raw)
    return mark


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


def committed_ledger(project):
    """The ledger's bytes as HEAD has them, None when it is not committed (or there is no git)."""
    try:
        r = subprocess.run(["git", "-C", project, "show", "HEAD:./" + LEDGER], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def ledger_restored(project, mark, events=()):
    """What a ledger rewritten during a run (it no longer starts with the bytes `mark` recorded) was restored to,
    when that is the supervisor's repair: it verifies now, it is HEAD's committed copy (`git checkout`) or the good
    rows of a ledger that started broken (`mark` good), and every row after that is one the run started with, byte
    for byte, or one a command of the run appended (its ledger event in `events`, under the row's event_id: the
    mission CLI writes the row, then the event); "" for any other rewrite (an edited row whose event_id was kept)."""
    try:
        with open(os.path.join(project, LEDGER), "rb") as f:
            raw = f.read()
    except OSError:
        return ""
    if ledger_problems(project):
        return ""
    mark = mark if isinstance(mark, dict) else {}
    had = mark.get("rows") if isinstance(mark.get("rows"), dict) else {}
    ids = set(had.values())
    appended = {e.get("event_id") for e in events if (e.get("data") or {}).get("ledger") is True}

    def kept_after(size):
        for ln in raw[size:].splitlines():
            if not ln.strip() or hashlib.sha256(ln).hexdigest() in had:
                continue                                  # a row the run started with, byte for byte
            eid = next(iter(_rows(ln).values()), None)
            if eid is None or eid in ids or eid not in appended:
                return False                              # an edited row (its event_id kept), or one no command appended
        return True
    head = committed_ledger(project)
    if head is not None and raw.startswith(head) and kept_after(len(head)):
        return "HEAD's committed copy"
    good = mark.get("good")
    if isinstance(good, dict) and _mark(raw[:int(good.get("size") or 0)]) == good and kept_after(int(good.get("size") or 0)):
        return "the rows it had before its first bad line"
    return ""


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


def head_commit(project):
    """The commit HEAD names now, "" when there is none (no git, or no commit yet)."""
    return _paths._git(project, "rev-parse", "-q", "--verify", "HEAD^{commit}")


def reflog_path(project):
    """The file git keeps HEAD's reflog in for the project's worktree, "" when there is no git."""
    p = _paths._git(project, "rev-parse", "--git-path", "logs/HEAD")
    return os.path.join(project, p) if p and not os.path.isabs(p) else p


def reflog_size(project):
    """HEAD's reflog's size in bytes now (0 when there is none): a run's moves of HEAD are the entries after it."""
    try:
        return os.path.getsize(reflog_path(project))
    except (OSError, TypeError, ValueError):
        return 0


def moves_since(project, offset):
    """[(old commit, new commit, message)] of HEAD's reflog entries after byte `offset` (every move of HEAD since a
    run started, in order), None when the log no longer holds that offset (it was expired or rewritten)."""
    p = reflog_path(project)
    try:
        if not p or os.path.getsize(p) < offset:
            return None
        with open(p, "rb") as f:
            f.seek(offset)
            raw = f.read()
    except OSError:
        return [] if not offset else None
    out = []
    for ln in raw.decode("utf-8", "replace").splitlines():
        head, _, msg = ln.partition("\t")
        parts = head.split(" ")
        if len(parts) < 2:
            return None
        out.append((parts[0], parts[1], msg))
    return out


def move_kind(msg):
    """What a HEAD reflog entry did, by the message git wrote for it: "in" (a `git merge` or `git pull` brought in
    another branch's commits: a fast-forward, a merge it made, a pull's rebase onto the upstream), "moved" (a checkout
    or reset to a commit that exists), "made" (a commit made here: `commit`, `commit --amend`, a conflicted merge
    concluded by `commit`, a cherry-pick, a rebase's pick, anything else)."""
    action = msg.split(": ", 1)[0]                        # `<verb> [<args>] [(<step>)]: <details>`
    m = re.search(r" \(([a-z -]+)\)$", action)
    step, verb = (m.group(1) if m else ""), action.split(" ", 1)[0]
    if verb in ("merge", "pull") and step in ("", "start", "finish"):
        return "in"
    if verb in ("checkout", "reset", "branch", "clone") or verb == "rebase" and step in ("start", "finish"):
        return "moved"
    return "made"


def arrived(project, since, rels, was=None, reflog_at=None):
    """The files of `rels` (changed during a run) a `git merge` or `git pull` made during it brought in or removed:
    HEAD moved off commit `since` through its reflog entries after byte `reflog_at` (they chain from `since` to HEAD
    now), the last of them to change the file is a merge or a pull (for a merge it made, the file is the other side's
    blob, never one it merged with a local change), no commit made here during the run made the file as it is now
    (one made on a branch and merged is still a local commit of the run's tree), and the file now is HEAD's blob of
    it, or a file gone (`was`: {rel: [raw sha256, …]} as the run found it) was `since`'s blob of it and HEAD has none.
    No mission role writes git (gate-mission-scope refuses it), so that is the supervisor's; a commit of the working
    tree (the human's `git commit -am`) is not one, and a file changed after it is not one either."""
    head = head_commit(project)
    if not since or not head or head == since or not isinstance(reflog_at, int):
        return set()
    moves = moves_since(project, reflog_at)
    if not moves or moves[0][0] != since or moves[-1][1] != head or \
            any(a[1] != b[0] for a, b in zip(moves, moves[1:])):
        return set()                                      # HEAD's moves since the start are not all in the log
    trees = {}

    def blob(commit, rel):
        """The blob id `commit` has for `rel` ("" when none): docs/agents/intents/ is listed once per commit."""
        if not commit.strip("0"):
            return ""
        if not under(rel, INTENTS):
            return _paths._git(project, "rev-parse", "-q", "--verify", "%s:%s" % (commit, rel))
        if commit not in trees:
            trees[commit] = {}
            for ent in _paths._git(project, "ls-tree", "-r", "-z", "--full-tree", commit, "--", INTENTS).split("\0"):
                meta, _, path = ent.partition("\t")
                parts = meta.split()
                if len(parts) == 3 and parts[1] == "blob":
                    trees[commit][path] = parts[2]
        return trees[commit].get(rel, "")
    out = set()
    for rel in rels:
        full = os.path.join(project, rel)
        if os.path.isfile(full):
            now = _paths._git(project, "hash-object", "--", full)
            if not now or now != blob(head, rel):
                continue
        elif (was or {}).get(rel) and not blob(head, rel) and blob(since, rel):
            now = ""
            try:
                raw = subprocess.run(["git", "-C", project, "cat-file", "--filters", "%s:%s" % (since, rel)],
                                     capture_output=True, timeout=10).stdout
            except (OSError, subprocess.SubprocessError):
                continue
            if hashlib.sha256(raw).hexdigest() != was[rel][0]:
                continue
        else:
            continue
        changes = [(o, n, msg) for o, n, msg in moves if blob(o, rel) != blob(n, rel)]
        if not changes or any(move_kind(msg) == "made" and blob(n, rel) == now for o, n, msg in changes):
            continue                                      # a commit made here during the run made it as it is now
        o, n, msg = changes[-1]
        if move_kind(msg) != "in" or blob(n, rel) != now:
            continue
        if ": Merge made by " in msg and \
                not any(blob(p, rel) == now for p in _paths._git(project, "rev-parse", n + "^@").split()[1:]):
            continue                                      # the merge's own result (with a local change), not the other side's
        out.add(rel)
    return out


def marks(project):
    """What a stop diffs against: every file's fingerprint, who the files say they belong to, the event log's
    size, the ledger's bytes and its problems, the commit HEAD names and the size of HEAD's reflog."""
    roles, sources, cites = labels(project)
    problems = ledger_problems(project)
    return {"files": scan(project), "roles": roles, "sources": sources, "cites": cites, "events_at": events_size(project),
            "ledger": ledger_mark(project, problems), "ledger_problems": problems, "head": head_commit(project),
            "reflog_at": reflog_size(project)}


def record_start(project, agent_id, role):
    rec = dict(marks(project), agent_id=agent_id, role=role, started=time.time())
    M.save(run_file(project, agent_id), rec)
    return rec


def load_run(project, agent_id):
    d, _ = M.load(run_file(project, agent_id))
    return d if isinstance(d, dict) else None


_ACTIVITY = {}


def activity(project, since):
    """{agent id: the time of its newest tool call after `since`}, read from the transcript store: `transcripts
    record` (PostToolUse, on every host) appends each call's payload there, and no mission role writes the store
    (is_harness_state). Each file is read from its end back to `since` only, in linear time; once per process."""
    key = (project, int(since) // 60)
    if key in _ACTIVITY:
        return _ACTIVITY[key]
    out = {}
    for p in glob.glob(os.path.join(_paths.home(project, "transcripts", make=False), "*", "events.jsonl")):
        try:
            if os.path.getmtime(p) < since:
                continue
            with open(p, "rb") as f:
                end, tail, done = f.seek(0, 2), b"", False
                while end > 0 and not done:
                    n = min(1 << 20, end)
                    end -= n
                    f.seek(end)
                    lines = (f.read(n) + tail).split(b"\n")
                    tail = lines.pop(0)[:LINE_CAP] if end > 0 else b""   # a line the next block back completes
                    for ln in reversed(lines):
                        m = TS.match(ln)
                        if not m:
                            continue
                        t = calendar.timegm(time.strptime(m.group(1).decode(), "%Y-%m-%dT%H:%M:%SZ"))
                        if t < since:
                            done = True
                            break
                        if b'"agent_id"' not in ln:
                            continue
                        try:
                            e = json.loads(ln)
                        except ValueError:
                            continue
                        aid = (e.get("payload") or {}).get("agent_id") if isinstance(e, dict) else None
                        if isinstance(aid, str) and aid and t > out.get(aid, 0):
                            out[aid] = t
        except OSError:
            continue
    _ACTIVITY[key] = out
    return out


def seen(project, d, now=None):
    """When a run last showed activity: its start, or its newest tool call in the transcript store, whichever is
    later (a run's own budget state is run data any role may write, so it never decides)."""
    t = float(d.get("started") or 0)
    now = now or time.time()
    if now - t >= OPEN_RUN_TTL and isinstance(d.get("agent_id"), str):
        t = max(t, activity(project, now - OPEN_RUN_TTL).get(d["agent_id"], 0))
    return t


def alive(project, d, now=None):
    """A run that started, has not stopped, and was active within OPEN_RUN_TTL."""
    now = now or time.time()
    return not d.get("stopped") and now - seen(project, d, now) < OPEN_RUN_TTL


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
