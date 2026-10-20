"""mission doors — the one-way / two-way door rule, enforced (ROADMAP §4.12, M9.30).

A two-way door is reversible (a branch, a local edit, a test, a draft PR, a recorded default):
the agent decides it, records it, and does not ask. A one-way door (plugin/mission/doors.json)
is crossed only under the human's recorded authorization, a hash-chained `door.authorized` row in
docs/agents/mission/ledger.jsonl; without one the PreToolUse gate stops the action with
ASK[door] <id>, and the agent asks one question with a recommended option. A wall (grant
`never`: switching a gate off, forging a human record) is never crossed by an agent. While a
run is live (a fresh sprint lock, or an approved autonomous mission), a question to the human
must name a one-way door the gate stopped and the human has not authorized: a two-way question
is decided, not asked. Anything the catalogue does not list is two-way.

Usage:
  mission doors list [--json]
        the catalogue: each door's grant, why it is one-way, and its live authorization
  mission doors classify (--bash CMD | --tool NAME [--input JSON] [--path P]) [--json]
        which doors an action crosses. Exit 0 two-way or authorized · 2 BLOCK[door] (a wall) ·
        3 ASK[door] (one-way, not authorized) · 64 usage
  mission doors authorize DOOR --by NAME --reason TEXT [--until YYYY-MM-DD] [--once SHA256]
        the human opens a door: standing until revoked or --until; --once binds it to the one
        action whose digest the ASK printed (required for a once-door; it expires in 24 hours
        and its first crossing uses it up). A wall or a question-only door is refused (2)
  mission doors revoke DOOR --by NAME --reason TEXT
  mission doors check [--file PATH] [--json]
        the catalogue is valid and proves itself (every example classifies to its own door,
        every two-way example to none) and the ledger is intact. Exit 0 · 2
  mission doors hook
        the PreToolUse gate (the hook payload on stdin): deny JSON for an unauthorized one-way
        door or a wall, silent otherwise; exit 2 when it cannot decide (fails closed)

Grants: standing (one authorization covers the door), once (one exact action), never (a wall:
the human runs it in their own terminal), command (a question-only door, answered by its own
human command). An agent never runs `doors authorize` or writes the ledger (forge-human-record).
"""
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import _events  # noqa: E402
import _mission as M  # noqa: E402
import _paths  # noqa: E402

SOURCE = os.path.join(HERE, "doors.json")
PLUGIN = os.path.realpath(os.path.join(HERE, ".."))
GRANTS = ("standing", "once", "never", "command")
ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SHA = re.compile(r"^[0-9a-f]{64}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
QUIET = {"Read", "Grep", "Glob", "LS", "TodoWrite", "TodoRead", "WebSearch", "WebFetch", "NotebookRead", "ToolSearch"}
EDITS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
QUESTION = "AskUserQuestion"
TAG = re.compile(r"\[door:([^\]\s]*)\]")
WRAPPERS = {"sudo", "doas", "env", "nohup", "time", "command", "exec", "nice", "timeout", "stdbuf", "caffeinate", "ionice"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
SEPARATORS = {";", "&&", "||", "|", "&", "|&", ";;", "(", ")", "\n"}
REDIRECTS = {">", ">>", ">|", "&>", "&>>"}
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
GIT_PAIRED = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--super-prefix", "--config-env"}
SCHEMA = {
    "type": "object", "required": ["version", "doors", "two_way_examples"],
    "properties": {
        "version": {"const": 1},
        "default_branch": {"type": "string", "minLength": 1},
        "doors": {"type": "array", "minItems": 1, "items": {
            "type": "object", "required": ["id", "grant", "title", "why"], "additionalProperties": False,
            "properties": {
                "id": {"type": "string", "pattern": ID.pattern}, "grant": {"enum": list(GRANTS)},
                "title": {"type": "string", "minLength": 1}, "why": {"type": "string", "minLength": 1},
                "answer": {"type": "string", "minLength": 1},
                "bash": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {
                    "prog": {"type": "string"}, "args": {"type": "string"}, "flag": {"type": "string"},
                    "env": {"type": "string"}, "writes": {"type": "string"},
                    "when": {"enum": ["on-default-branch", "main-worktree"]}}}},
                "tools": {"type": "array"}, "paths": {"type": "array", "items": {"type": "string"}},
                "plugin_paths": {"type": "array", "items": {"type": "string"}},
                "examples": {"type": "object", "additionalProperties": False, "properties": {
                    "bash": {"type": "array", "items": {"type": "string"}}, "tools": {"type": "array", "items": {"type": "string"}},
                    "paths": {"type": "array", "items": {"type": "string"}}}}}}},
        "two_way_examples": {"type": "object", "additionalProperties": False, "properties": {
            "bash": {"type": "array", "items": {"type": "string"}}, "tools": {"type": "array", "items": {"type": "string"}},
            "paths": {"type": "array", "items": {"type": "string"}}}},
    },
}


# ── the catalogue ────────────────────────────────────────────────────────────────────
def load(path=None):
    """(catalogue, problems): the doors with their patterns compiled."""
    try:
        with open(path or SOURCE, encoding="utf-8") as f:
            cat = json.load(f)
    except (OSError, ValueError) as e:
        return None, ["cannot read %s: %s" % (path or SOURCE, e)]
    bad = M.validate(cat, SCHEMA)
    if bad:
        return None, bad
    default = "(?:%s)" % cat.get("default_branch", "main|master")

    def rx(s, where):
        try:
            return re.compile(s.replace("{DEF}", default))
        except re.error as e:
            bad.append("%s: %r does not compile (%s)" % (where, s, e))
            return None
    seen = set()
    for i, d in enumerate(cat["doors"]):
        where = "doors[%d] (%s)" % (i, d["id"])
        if d["id"] in seen:
            bad.append("%s: a duplicate id" % where)
        seen.add(d["id"])
        d["_bash"] = []
        for j, b in enumerate(d.get("bash") or []):
            if not set(b) - {"when"}:
                bad.append("%s bash[%d]: a pattern names at least one of prog, args, flag, env, writes" % (where, j))
            d["_bash"].append({k: (rx(v, "%s bash[%d].%s" % (where, j, k)) if k != "when" else v) for k, v in b.items()})
        d["_tools"] = []
        for j, t in enumerate(d.get("tools") or []):
            t = {"tool": t} if isinstance(t, str) else t
            if not isinstance(t, dict) or not isinstance(t.get("tool"), str):
                bad.append("%s tools[%d]: a tool pattern is a regex or {tool, input}" % (where, j))
                continue
            d["_tools"].append({"tool": rx(t["tool"], "%s tools[%d]" % (where, j)),
                                "input": {k: rx(v, "%s tools[%d].input.%s" % (where, j, k)) for k, v in (t.get("input") or {}).items()}})
        d["_paths"] = [rx(x, "%s paths" % where) for x in d.get("paths") or []]
        d["_plugin_paths"] = [rx(x, "%s plugin_paths" % where) for x in d.get("plugin_paths") or []]
        acts = d.get("bash") or d.get("tools") or d.get("paths") or d.get("plugin_paths")
        if d["grant"] == "command":
            if acts or not d.get("answer"):
                bad.append("%s: a question-only door names the command that answers it, and no action patterns" % where)
        elif not acts:
            bad.append("%s: an action door names the actions it covers (bash, tools or paths)" % where)
        elif not any((d.get("examples") or {}).get(k) for k in ("bash", "tools", "paths")):
            bad.append("%s: an action door carries examples that prove its patterns" % where)
    return cat, bad


def catalogue(path=None):
    cat, bad = load(path)
    if bad:
        raise ValueError("; ".join(bad))
    return cat


def door(cwd, did, cat=None):
    for d in (cat or catalogue())["doors"]:
        if d["id"] == did:
            return d
    return None


def public(d):
    return {k: v for k, v in d.items() if not k.startswith("_")}


# ── what an action is ────────────────────────────────────────────────────────────────
def action_sha(tool, inp):
    """The digest a once-authorization binds to: the command with its whitespace collapsed, or the
    tool and its canonical input."""
    if tool == "Bash":
        return hashlib.sha256(("bash\0" + " ".join(str((inp or {}).get("command", "")).split())).encode()).hexdigest()
    return hashlib.sha256((tool + "\0" + json.dumps(inp or {}, sort_keys=True, separators=(",", ":"))).encode()).hexdigest()


def tokens(cmd):
    """Shell words and operators (quotes respected); a line with an unbalanced quote falls back to a
    plain split, so it is still read."""
    lex = shlex.shlex(cmd.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        return list(lex)
    except ValueError:
        return re.findall(r"&&|\|\||>>|[;&|()<>]|[^\s;&|()<>]+", cmd.replace("\n", " ; "))


def views(cmd, depth=0):
    """Every (prog, args, env, writes) a command line runs: one per simple command, one per wrapper
    (sudo, env, timeout, …) around it, and the commands inside `sh -c` and `eval`."""
    out, seg = [], []
    for t in tokens(cmd) + [";"]:
        if t not in SEPARATORS:
            seg.append(t)
            continue
        if seg:
            out += simple(seg, depth)
        seg = []
    return out


def simple(seg, depth):
    out, env, writes, words = [], [], [], []
    i = 0
    while i < len(seg):
        t = seg[i]
        if t in REDIRECTS or re.match(r"^\d*>+[|&]?$", t):
            if i + 1 < len(seg):
                writes.append(seg[i + 1])
            i += 2
            continue
        if t in ("<", "<<", "<<<"):
            i += 2
            continue
        words.append(t)
        i += 1
    while words and ASSIGN.match(words[0]):
        env.append(words.pop(0))
    while words:
        prog = os.path.basename(words[0])
        rest = words[1:]
        if prog in ("python", "python3") and rest and not rest[0].startswith("-"):
            prog, rest = os.path.basename(rest[0]), rest[1:]
        if prog == "tee":
            writes += [w for w in rest if not w.startswith("-")]
        if prog == "git":
            while rest and rest[0].startswith("-"):
                rest = rest[2:] if rest[0] in GIT_PAIRED else rest[1:]
        out.append({"prog": prog, "args": rest, "env": list(env), "writes": list(writes)})
        if prog in SHELLS and depth < 4:
            for j, a in enumerate(rest):
                if re.match(r"^-[A-Za-z]*c[A-Za-z]*$", a) and j + 1 < len(rest):
                    out += [dict(v, env=v["env"] + env) for v in views(rest[j + 1], depth + 1)]
                    break
            break
        if prog == "eval" and depth < 4:
            out += views(" ".join(rest), depth + 1)
            break
        if prog not in WRAPPERS:
            break
        words = rest
        if prog == "env":   # env [-i] [-u NAME] [NAME=VALUE]… COMMAND
            while words and (words[0].startswith("-") or ASSIGN.match(words[0])):
                if words[0] in ("-u", "--unset", "-C", "--chdir", "-S"):
                    words = words[2:]
                    continue
                if ASSIGN.match(words[0]):
                    env.append(words[0])
                words = words[1:]
        else:               # sudo -u USER …, timeout -s KILL 5 …, nice -n 5 …: skip options and their values
            while words and words[0].startswith("-"):
                words = words[2:] if words[0] in ("-u", "-g", "-n", "-s", "-k", "-p", "-o", "-e", "-i", "-c") \
                    and prog not in ("command", "exec", "nohup", "time") else words[1:]
            if prog == "timeout" and words:
                words = words[1:]   # the duration
    return out


class Context:
    """What an action is checked against: the project, the git state its `when` conditions read."""

    def __init__(self, cwd, assume_when=False):
        self.cwd, self.assume, self._git = cwd, assume_when, {}

    def git(self, *args):
        if args not in self._git:
            r = subprocess.run(["git", "-C", self.cwd] + list(args), capture_output=True, text=True)
            self._git[args] = r.stdout.strip() if r.returncode == 0 else ""
        return self._git[args]

    def when(self, cond, default):
        if self.assume or not cond:
            return True
        if cond == "on-default-branch":
            return bool(self.git("symbolic-ref", "--short", "-q", "HEAD")) and \
                default.fullmatch(self.git("symbolic-ref", "--short", "-q", "HEAD")) is not None
        if cond == "main-worktree":
            gd, common = self.git("rev-parse", "--git-dir"), self.git("rev-parse", "--git-common-dir")
            if not gd or not common:
                return True
            return os.path.realpath(os.path.join(self.cwd, gd)) == os.path.realpath(os.path.join(self.cwd, common))
        return True


def bash_hits(d, v, ctx, default):
    for b in d["_bash"]:
        if "prog" in b and not b["prog"].fullmatch(v["prog"]):
            continue
        if "args" in b and not b["args"].search(" ".join(v["args"])):
            continue
        if "flag" in b and not any(b["flag"].fullmatch(a) for a in v["args"]):
            continue
        if "env" in b and not any(b["env"].fullmatch(e) for e in v["env"]):
            continue
        if "writes" in b and not any(b["writes"].search(w) for w in v["writes"]):
            continue
        if ("prog" in b or "args" in b or "flag" in b) and not v["prog"]:
            continue
        if ctx.when(b.get("when"), default):
            return True
    return False


def rel_path(cwd, path):
    """A path as the catalogue reads it: relative to the project (or the plugin, for its own files)."""
    full = os.path.realpath(os.path.join(cwd, path))
    proj = os.path.realpath(_paths.project(cwd))
    r = os.path.relpath(full, proj)
    plug = os.path.relpath(full, PLUGIN)
    return (r if not r.startswith("..") else full), (plug if not plug.startswith("..") else None)


def classify(cwd, tool, inp, cat=None, assume_when=False):
    """[door] the action crosses, in catalogue order (none: two-way)."""
    cat = cat or catalogue()
    default = re.compile("(?:%s)" % cat.get("default_branch", "main|master"))
    ctx = Context(cwd, assume_when)
    inp = inp if isinstance(inp, dict) else {}
    vs = views(str(inp.get("command", ""))) if tool == "Bash" else []
    path = inp.get("file_path") or inp.get("notebook_path") if tool in EDITS else None
    proj_rel, plug_rel = rel_path(cwd, path) if isinstance(path, str) and path else (None, None)
    out = []
    for d in cat["doors"]:
        if d["grant"] == "command":
            continue
        hit = any(bash_hits(d, v, ctx, default) for v in vs)
        hit = hit or any(t["tool"].search(tool) and all(isinstance(inp.get(k), str) and r.fullmatch(inp[k])
                                                        for k, r in t["input"].items()) for t in d["_tools"])
        hit = hit or bool(proj_rel and any(r.search(proj_rel) for r in d["_paths"]))
        hit = hit or bool(plug_rel and any(r.search(plug_rel) for r in d["_plugin_paths"]))
        if hit:
            out.append(d)
    return out


# ── authorizations (the human's ledger rows) ─────────────────────────────────────────
def subject(did):
    return "door:" + did


def authorization(cwd, d, sha, now=None):
    """The human's ledger row that opens this door for this action now, or None: the newest decision
    about the door counts (a revoke closes every older row), an expired row counts for nothing, and a
    once-row covers its one action until its first crossing. A broken ledger opens nothing: an edited
    or lost row could be the revoke."""
    row = _authorization(cwd, d, sha, now)
    return row if row and not M.ledger_problems(cwd) else None


def _authorization(cwd, d, sha, now=None):
    if d is None or d["grant"] not in ("standing", "once"):
        return None
    now = now or M.now()
    rows, _ = M.ledger_rows(cwd)
    used = {r["data"].get("authorization") for r in rows if r["event"] == "door.crossed"
            and r["data"].get("subject") == subject(d["id"])}
    for r in reversed(rows):
        if r["data"].get("subject") != subject(d["id"]) or r.get("role") != "human":
            continue
        if r["event"] == "door.revoked":
            return None
        if r["event"] != "door.authorized" or (r["data"].get("until") and r["data"]["until"] < now):
            continue
        if r["data"].get("action_sha256"):
            if r["data"]["action_sha256"] == sha and r["event_id"] not in used:
                return r
            continue
        if d["grant"] == "standing":
            return r
    return None


def ask_text(d, sha):
    once = " --once %s" % sha if d["grant"] == "once" else ""
    return ("ASK[door] %s: %s (one-way: %s). Ask the human one question with a recommended option, tagged "
            "[door:%s]; they authorize it in their own terminal: aloop mission doors authorize %s --by NAME "
            "--reason TEXT%s. Until then take the two-way path (a branch, a draft PR)."
            % (d["id"], d["title"], d["why"], d["id"], d["id"], once))


def wall_text(d):
    return ("BLOCK[door] %s: %s is a wall: an agent never crosses it (%s). The human does it in their own "
            "terminal, if at all." % (d["id"], d["title"], d["why"]))


def verdict(cwd, tool, inp, cat=None):
    """(exit, lines, crossings): 0 two-way or authorized, 2 a wall, 3 one-way and not authorized;
    crossings = [(door, row)] of the authorized doors."""
    sha = action_sha(tool, inp)
    lines, crossings, rc = [], [], 0
    for d in classify(cwd, tool, inp, cat):
        if d["grant"] == "never":
            lines.append(wall_text(d))
            rc = 2
            continue
        row = authorization(cwd, d, sha)
        if row:
            crossings.append((d, row))
            lines.append("[door] %s: authorized by %s (%s, ledger row %s)"
                         % (d["id"], row["data"].get("by"), "once" if row["data"].get("action_sha256") else "standing",
                            row["event_id"][:12]))
        else:
            lines.append(ask_text(d, sha))
            rc = rc or 3
    if not lines:
        lines.append("[door] two-way: decide it, record it, don't ask")
    return rc, lines, crossings


# ── the gate ─────────────────────────────────────────────────────────────────────────
def live_run(cwd):
    """A run is live while its sprint lock is fresh or an approved autonomous mission is running."""
    lock = os.path.join(_paths.home(cwd, make=False), "state", "sprint.lock")
    try:
        ttl = int(os.environ.get("AGENTIC_SPRINT_LOCK_TTL") or 14400)
        if os.path.isfile(lock) and time.time() - os.path.getmtime(lock) < ttl:
            return True
    except (OSError, ValueError):
        return True
    doc, _, ev = M.mission(cwd)
    return bool(doc and doc.get("mode") == "autonomous" and ev)


def question_problem(cwd, inp, session, cat):
    """Why a question to the human may not be asked now (None: it may)."""
    text = json.dumps(inp, ensure_ascii=False)
    tags = TAG.findall(text)
    if not tags:
        return ("two-way question: decide it yourself, record it (aloop events decide KIND --reason …) and say the "
                "choice in one line. Ask only at a one-way door the gate stopped, tagged [door:<id>] (aloop mission doors list)")
    for t in tags:
        d = door(cwd, t, cat)
        if d is None:
            return "unknown door %r (aloop mission doors list)" % t
        if d["grant"] == "never":
            return wall_text(d)
        if d["grant"] == "command":
            continue
        denied = [e for e in _events.read(cwd) if e.get("event") == "door.denied"
                  and (e.get("data") or {}).get("door") == t and (not session or e["data"].get("session_id") == session)]
        if not denied:
            return ("no door.denied for %s in this session: take the action; the gate says whether it needs the human" % t)
        if d["grant"] == "standing":
            row = authorization(cwd, d, "")
            if row:
                return "%s is already authorized by %s: cross it, don't ask" % (t, row["data"].get("by"))
    return None


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": "doors: " + reason}}))
    return 0


def hook(cwd_default):
    if os.environ.get("SKIP_HOOKS") == "1":
        return 0
    payload = json.loads(sys.stdin.read() or "{}")
    if not isinstance(payload, dict):
        raise ValueError("the hook payload is not an object")
    tool = payload.get("tool_name") or ""
    if tool in QUIET:
        return 0
    cwd = payload.get("cwd") or cwd_default
    if not os.path.isdir(cwd) or not os.path.isdir(_paths.docs(cwd)):
        return 0      # not an aloop project
    inp = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    session = payload.get("session_id") or ""
    cat = catalogue()
    if tool == QUESTION:
        why = question_problem(cwd, inp, session, cat) if live_run(cwd) else None
        return deny(why) if why else 0
    rc, lines, crossings = verdict(cwd, tool, inp, cat)
    sha = action_sha(tool, inp)
    if rc:
        for d in classify(cwd, tool, inp, cat):
            if d["grant"] in ("standing", "once") and not authorization(cwd, d, sha):
                M.emit(cwd, "door.denied", role="gate", door=d["id"], grant=d["grant"], action_sha256=sha, tool=tool,
                       session_id=session)
        return deny(" ".join(l for l in lines if not l.startswith("[door]")))
    for d, row in crossings:
        if row["data"].get("action_sha256"):   # a once-authorization is used up by its first crossing
            M.record(cwd, "door.crossed", subject(d["id"]), "harness", door=d["id"], authorization=row["event_id"],
                     action_sha256=sha, session_id=session)
        else:
            M.emit(cwd, "door.crossed", role="harness", door=d["id"], authorization=row["event_id"], action_sha256=sha,
                   session_id=session)
    return 0


# ── commands ─────────────────────────────────────────────────────────────────────────
def cmd_check(cwd, path, as_json):
    cat, bad = load(path)
    if cat and not bad:
        for d in cat["doors"]:
            ex = d.get("examples") or {}
            for kind, items in ex.items():
                for x in items:
                    tool, inp = example(kind, x)
                    if d not in classify(cwd, tool, inp, cat, assume_when=True):
                        bad.append("doors %s: example %r does not classify to its door" % (d["id"], x))
        for kind, items in cat["two_way_examples"].items():
            for x in items:
                tool, inp = example(kind, x)
                hit = classify(cwd, tool, inp, cat, assume_when=True)
                if hit:
                    bad.append("a two-way example %r classifies to %s" % (x, ", ".join(d["id"] for d in hit)))
        ids = {d["id"] for d in cat["doors"]}
        rows, lbad = M.ledger_rows(cwd)
        bad += ["ledger: %s" % b for b in lbad]
        for r in rows:
            s = str(r["data"].get("subject", ""))
            if r["event"].startswith("door.") and s[len("door:"):] not in ids:
                bad.append("ledger: %s names an unknown door %s" % (r["event"], s))
    for b in bad:
        print("BLOCK[doors]: %s" % b, file=sys.stderr)
    if as_json:
        print(json.dumps({"problems": bad}, indent=1))
    if not bad:
        print("[doors] ok: %d doors, every example proven" % len(cat["doors"]), file=sys.stderr)
    return 2 if bad else 0


def example(kind, x):
    if kind == "bash":
        return "Bash", {"command": x}
    if kind == "paths":
        return "Write", {"file_path": x}
    return x, {}


def cmd_list(cwd, as_json):
    cat = catalogue()
    out = []
    for d in cat["doors"]:
        row = authorization(cwd, d, "") if d["grant"] == "standing" else None
        out.append({"id": d["id"], "grant": d["grant"], "title": d["title"], "why": d["why"], "answer": d.get("answer"),
                    "authorized": {"by": row["data"].get("by"), "reason": row["data"].get("reason"),
                                   "until": row["data"].get("until"), "event_id": row["event_id"]} if row else None})
    if as_json:
        print(json.dumps({"doors": out}, indent=1))
        return 0
    for x in out:
        state = ("authorized by %s" % x["authorized"]["by"]) if x["authorized"] else \
            {"never": "a wall", "command": "answered by: %s" % x["answer"]}.get(x["grant"], "asks")
        print("%-28s %-8s %s (%s) [%s]" % (x["id"], x["grant"], x["title"], x["why"], state))
    print("Anything not listed is a two-way door: decide it, record it, don't ask.")
    return 0


def cmd_classify(cwd, o):
    if o.get("bash") is not None:
        tool, inp = "Bash", {"command": o["bash"]}
    elif o.get("tool") or o.get("path"):
        tool = o.get("tool") or "Write"
        try:
            inp = json.loads(o["input"]) if o.get("input") else {}
        except ValueError:
            print("mission doors classify: --input is JSON", file=sys.stderr)
            return 64
        if o.get("path"):
            inp = dict(inp, file_path=o["path"])
    else:
        print("mission doors classify (--bash CMD | --tool NAME [--input JSON] [--path P])", file=sys.stderr)
        return 64
    rc, lines, _ = verdict(cwd, tool, inp)
    if o.get("json"):
        print(json.dumps({"exit": rc, "doors": [d["id"] for d in classify(cwd, tool, inp)], "lines": lines,
                          "action_sha256": action_sha(tool, inp)}, indent=1))
    for l in lines:
        print(l, file=sys.stderr)
    return rc


def cmd_authorize(cwd, did, o, revoke=False):
    verb = "revoke" if revoke else "authorize"
    d = door(cwd, did)
    if d is None:
        print("mission doors %s: unknown door %r (aloop mission doors list)" % (verb, did), file=sys.stderr)
        return 64
    if d["grant"] == "never":
        print("BLOCK[doors]: %s is a wall: an agent never crosses it, and no authorization opens it" % did, file=sys.stderr)
        return 2
    if d["grant"] == "command":
        print("BLOCK[doors]: %s is answered by its own command: %s" % (did, d["answer"]), file=sys.stderr)
        return 2
    by, reason = (o.get("by") or "").strip(), (o.get("reason") or "").strip()
    if not by or not reason:
        print("mission doors %s DOOR --by NAME --reason TEXT" % verb, file=sys.stderr)
        return 64
    data = {}
    if not revoke:
        once = o.get("once") or ""
        if d["grant"] == "once" and not once:
            print("mission doors authorize: %s is a once-door: --once SHA256 (the action digest its ASK printed)" % did,
                  file=sys.stderr)
            return 64
        if once and not SHA.match(once):
            print("mission doors authorize: --once is the 64-hex digest the ASK printed", file=sys.stderr)
            return 64
        until = o.get("until") or ""
        if until and not DATE.match(until):
            print("mission doors authorize: --until YYYY-MM-DD", file=sys.stderr)
            return 64
        until = until + "T23:59:59Z" if until else ""
        if once and not until:
            until = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 86400))
        data = {"grant": "once" if once else "standing", "until": until, "action_sha256": once}
    try:
        eid = M.record_human(cwd, "door.revoked" if revoke else "door.authorized", subject(did), by, reason, **data)
    except ValueError as e:
        print("mission doors %s: %s" % (verb, e), file=sys.stderr)
        return 64
    print("[door] %s %s by %s%s (ledger row %s)" % (did, "revoked" if revoke else "authorized", by,
                                                    "" if revoke else " (%s%s)" % (data["grant"], ", until " + data["until"]
                                                                                   if data["until"] else ""), eid[:12]),
          file=sys.stderr)
    return 0


def parse_args(argv):
    opts, pos, k = {}, [], None
    for a in argv:
        if k:
            opts[k], k = a, None
        elif a == "--json":
            opts["json"] = True
        elif a.startswith("--"):
            k = a[2:].replace("-", "_")
        else:
            pos.append(a)
    if k:
        opts[k] = ""
    return opts, pos


def main(argv, cwd):
    cmd, (o, pos) = argv[0], parse_args(argv[1:])
    if cmd == "hook":
        try:
            return hook(cwd)
        except Exception as e:  # noqa: BLE001 — a gate that cannot decide fails closed
            print("BLOCK[doors]: the door gate could not decide (%s: %s); fix it or run the action yourself"
                  % (type(e).__name__, e), file=sys.stderr)
            return 2
    try:
        if cmd == "check":
            return cmd_check(cwd, o.get("file") or None, o.get("json"))
        if cmd == "list":
            return cmd_list(cwd, o.get("json"))
        if cmd == "classify":
            return cmd_classify(cwd, o)
        if cmd in ("authorize", "revoke"):
            if not pos:
                print("mission doors %s DOOR --by NAME --reason TEXT" % cmd, file=sys.stderr)
                return 64
            return cmd_authorize(cwd, pos[0], o, revoke=cmd == "revoke")
    except ValueError as e:
        print("BLOCK[doors]: %s" % e, file=sys.stderr)
        return 2
    print("mission doors: unknown command %r (list, classify, authorize, revoke, check, hook)" % cmd, file=sys.stderr)
    return 64
