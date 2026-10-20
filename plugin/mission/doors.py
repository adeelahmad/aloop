"""mission doors — the one-way / two-way door rule, enforced (ROADMAP §4.12, M9.30).

A two-way door is reversible (a branch, a local edit, a test, a draft PR, a recorded default):
the agent decides it, records it, and does not ask. A one-way door (plugin/mission/doors.json)
is crossed only under the human's recorded authorization, a hash-chained `door.authorized` row in
docs/agents/mission/ledger.jsonl; without one the PreToolUse gate stops the action with
ASK[door] <id>, and the agent asks one question with a recommended option. A wall (grant
`never`: switching a gate off, forging a human record) is never crossed by an agent. While a
run is live (an approved autonomous mission or an autonomous run, or a sprint whose plan is
approved, for the session holding its lock), a question to the human must name a one-way door
the gate stopped and the human has not authorized: a two-way question is decided, not asked.
Anything the catalogue does not list is two-way.

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
        door or a wall, silent otherwise; exit 2 when it cannot decide within its deadline (fails
        closed). On every host (M9.31): Claude Code and Codex run it from hooks.json, OpenCode and
        dsh from their plugins; a patch (apply_patch) is judged path by path as a Write, dsh's
        JSON-string input of an MCP tool as the object it encodes, and a bound worker's
        tool_input.workdir (OpenCode, dsh) is where its command runs. A subagent that hits a door
        reports ASK[door] <id> to its supervisor, which asks the human

Grants: standing (one authorization covers the door), once (one exact action), never (a wall:
the human runs it in their own terminal), command (a question-only door, answered by its own
human command). An agent never runs `doors authorize` or writes the ledger (forge-human-record),
and `authorize` refuses a shell an agent host runs (CLAUDECODE, DSH_SHELL, CODEX_THREAD_ID or
ALOOP_AGENT_SHELL set).
"""
import contextlib
import functools
import fnmatch
import glob
import hashlib
import itertools
import json
import os
import re
import signal
import subprocess
import sys
import time

try:
    import fcntl
except ImportError:          # no flock (Windows): the gate runs unserialized
    fcntl = None

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import _events  # noqa: E402
import _mission as M  # noqa: E402
import _patch  # noqa: E402
import _paths  # noqa: E402
import _shellread as R  # noqa: E402

SOURCE = os.path.join(HERE, "doors.json")
PLUGIN = os.path.realpath(os.path.join(HERE, ".."))
GRANTS = ("standing", "once", "never", "command")
ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SHA = re.compile(r"^[0-9a-f]{64}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# read-only tools, as each host names them (OpenCode and dsh pass their own built-ins' names through)
QUIET = {"Read", "Grep", "Glob", "LS", "TodoWrite", "TodoRead", "WebSearch", "WebFetch", "NotebookRead", "ToolSearch",
         "grep", "glob", "list", "read_image", "todoread", "todowrite", "webfetch", "websearch", "codesearch"}
EDITS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
# the question tool: Claude Code's, Codex's (OpenCode's `question` and dsh's `ask_user_question` reach the
# gate as AskUserQuestion, mapped by their plugins)
QUESTIONS = ("AskUserQuestion", "request_user_input", "request_user_input_async")
# set in every shell an agent host runs (the agent's Bash tool): Claude Code, dsh, Codex, and OpenCode (its
# plugin exports ALOOP_AGENT_SHELL); an authorization never comes from there
AGENT_SHELL = ("CLAUDECODE", "DSH_SHELL", "CODEX_THREAD_ID", "ALOOP_AGENT_SHELL")
TAG = re.compile(r"\[door:([^\]\s]*)\]")
WRAPPERS = {"sudo", "doas", "env", "nohup", "time", "command", "exec", "nice", "timeout", "stdbuf", "caffeinate", "ionice",
            "builtin", "setsid", "taskset", "chrt", "flock"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# {DRY} in a pattern: the command is not a dry run or a help page, read from its words (dry()), so a
# value like --body "x --help" is not one
DRY = "{DRY}"
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
                    "env": {"type": "string"}, "writes": {"type": "string"}, "config": {"type": "string"},
                    "code": {"type": "string"}, "files": {"type": "string"},
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
            return re.compile(s.replace("{DEF}", default).replace(DRY, ""))
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
                bad.append("%s bash[%d]: a pattern names at least one of prog, args, flag, env, writes, config, code, files"
                           % (where, j))
            d["_bash"].append(dict({k: (rx(v, "%s bash[%d].%s" % (where, j, k)) if k != "when" else v) for k, v in b.items()},
                                   _dry=any(DRY in v for k, v in b.items() if k != "when")))
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


UNKNOWN = "\0unknown"   # a directory the gate cannot resolve (a variable, `cd -`): its git state is assumed
TOP = "\0top"           # $(git rev-parse --show-toplevel): the checkout's top, resolved per project
HERE = "\0pwd"          # the reading shell's $PWD or $(pwd) in a word (render(mark=True)): chain() binds it
CWD = "\0cwd"           # the call's cwd, in text a nested shell reads (nested(): where the outer shell was)
DEPTH = 8               # nested shells, evals, substitutions and find -exec read; deeper is unreadable (fails closed)


def render(parts, mark=False):
    """A word as the catalogue's patterns read it: literal text as is, a variable `$NAME` (`~` stays `~`),
    a substitution `$(…)` (its commands are read on their own), and `*` for what only running it tells
    (a glob, arithmetic, a positional parameter). `mark`: the shell's own $PWD and $(pwd) are HERE, so
    the text a nested shell reads keeps the directory this shell expanded them in."""
    pwd = HERE if mark else "$PWD"
    out = []
    for p in parts:
        if isinstance(p, str):
            out.append(p)
        elif p[0] == "var":
            out.append("~" if p[1] == "HOME" and p[2] is None else pwd if p[1] == "PWD" else "$" + p[1])
        elif p[0] == "cmd":
            t = " ".join(p[1].split())
            out.append(TOP if t == "git rev-parse --show-toplevel" else pwd if t in ("pwd", "pwd -L", "pwd -P") else "$(…)")
        elif p[0] == "proc":
            out.append("<(…)")
        else:
            out.append("*")
    text = "".join(out)                 # ~+ is $PWD, ~- is $OLDPWD
    return re.sub(r"^~([+-])(?=/|$)", lambda m: pwd if m.group(1) == "+" else "$OLDPWD", text)


def own(word):
    """A word as its command gets it: the shell's $PWD (where it is resolved, `shell`, is the view's)."""
    return word.replace(HERE, "$PWD")


def outer(cur):
    """This shell's $PWD as text another shell reads: the directory itself (CWD for the call's), or `$(…)`
    when it may be more than one."""
    if len(cur) != 1 or cur[0] == UNKNOWN:
        return "$(…)"
    return cur[0] if os.path.isabs(cur[0]) or cur[0].startswith((TOP, CWD)) else CWD + ("/" + cur[0] if cur[0] else "")


def nested(text, cur):
    """Text another shell reads after this one, in cur, expanded it (sh -c "…", eval, <<<, an unquoted
    heredoc, a pipe): this shell's $PWD is a directory the nested one's cd does not move."""
    return text.replace(HERE, outer(cur))


def anyname(path):
    """A path whose last name the shell substitutes (DIR/$F, DIR/${F:?}): any name in DIR, `DIR/?`."""
    head, _, name = path.rpartition("/")
    return head + "/?" if head and re.search(r"[$`]", name) else path


OUTER_PWD = re.compile(r"(?<!\\)(?:\$\(\s*pwd(?:\s+-[LP])?\s*\)|`\s*pwd(?:\s+-[LP])?\s*`|\$\{PWD\}|\$PWD(?!\w))")


def chdir(cur, arg, shell=None):
    """The directory a `cd ARG` (or `git -C ARG`) moves to, relative to the call's cwd ("" = it):
    `$PWD` is where the shell that expanded it is (`shell`, when env -C, git -C or find -execdir moved the
    command from there), `$(git rev-parse --show-toplevel)` the checkout's top, `$CLAUDE_PROJECT_DIR` the
    project's; `cd ''` stays; an absolute ARG is itself wherever the command was."""
    if arg is not None and arg.startswith((TOP, CWD)):
        return arg
    for var, base in (("$PWD", cur if shell is None else shell),
                      ("$CLAUDE_PROJECT_DIR", os.environ.get("CLAUDE_PROJECT_DIR") or UNKNOWN)):
        if arg is not None and re.match(re.escape(var) + r"(?:/|$)", arg):
            return UNKNOWN if base == UNKNOWN else os.path.join(base, arg[len(var) + 1:]) if arg[len(var):] else base
    if arg is None or arg == "-" or re.search(r"[$`*?]", arg):
        return UNKNOWN
    if arg == "":
        return cur
    arg = os.path.expanduser(arg)
    return arg if os.path.isabs(arg) else UNKNOWN if cur == UNKNOWN else os.path.join(cur, arg)


MAXDIRS = 8         # the directories a line's conditional cds may leave it in; past that, one more is UNKNOWN
GITENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR")     # git reads its repository from these, not its cwd


def union(*groups):
    """The directories a command may run in (a tuple, in order, without repeats)."""
    out = tuple(dict.fromkeys(d for g in groups for d in g))
    return out if len(out) <= MAXDIRS else out[:MAXDIRS - 1] + (UNKNOWN,)


def moved(prog, args, cur, old, dirs):
    """(cur, old) after a cd, pushd or popd (dirs, the pushd stack, is changed in place): `cd -` goes back
    to where the last cd left, a bare pushd swaps with the stack's top, `-n` moves nothing, a ±N rotation
    is unknown."""
    ops, flags, i = [], set(), 0
    while i < len(args):
        a = args[i]
        if a == "--":
            ops += args[i + 1:]
            break
        if re.fullmatch(r"-[A-Za-z@]+", a):     # -L, -P, -e, -n; `-` and ±N are operands
            flags |= set(a[1:])
        else:
            ops.append(a)
        i += 1
    arg = ops[0] if ops else None
    if prog == "cd":
        if arg == "-":
            return old, cur
        return union(*((chdir(d, "~" if arg is None else arg),) for d in cur)), cur
    if arg is not None and re.fullmatch(r"[-+]\d+", arg):     # a rotation of the stack: unknown
        return union(cur, (UNKNOWN,)), cur
    if prog == "pushd":
        if arg is None:
            if not dirs:
                return cur, old
            top = dirs.pop()
            dirs.append(cur)
            return top, cur
        to = union(*((chdir(d, arg),) for d in cur))
        if "n" in flags:
            dirs.append(to)
            return cur, old
        dirs.append(cur)
        return to, cur
    if "n" in flags:                                          # popd -n: the stack's top goes, the shell stays
        if dirs:
            dirs.pop()
        return cur, old
    return (dirs.pop() if dirs else (UNKNOWN,)), cur


def views(cmd, depth=0, cur=""):
    """Every (prog, args, env, writes, dir; a python's code) a command line runs, read by the shell reader
    the scope gate uses (plugin/bin/_shellread.py): one per simple command, one per wrapper (sudo, env,
    timeout, xargs, find -exec, …) around it, the commands inside `sh -c`, `eval`, a script a shell runs, a
    command substitution, and the stdin that feeds a shell or python (the last heredoc, here-string or
    `<` on fd 0, else the pipe, into a subshell, a group or a compound too), each with the directory it runs
    in (`cd DIR`, `cd -`, `pushd`/`popd`, `git -C DIR`; a subshell's, a pipeline element's, a background
    job's or a wrapped command's `cd` stays there; GIT_DIR or GIT_WORK_TREE makes git's own unknown). A
    group's, subshell's or compound's own redirect (`{ …; } > F`, `( … ) <<EOF`, `fi > F`) is every command's
    inside it that has none of its own, and a bare redirect (`> F`) is a view with no program. A `cd` that
    may not run (in an if, case or loop body or a `{ …; }` group, after `&&` or `||`, or one that may fail
    before `||`) leaves each later command in either directory: one view per directory (cur may be a
    tuple of them). Deeper than DEPTH is a ValueError: a gate that cannot read the whole command fails
    closed."""
    if depth > DEPTH:
        raise ValueError("the command nests shells, evals or substitutions deeper than the door gate reads (%d)" % DEPTH)
    cur = (cur,) if isinstance(cur, str) else tuple(cur)
    out, stack, dirs, fed, group = [], [], [], None, None
    opened, closed, scopes, prev, last = [], None, [], None, None
    maybe, failed, old, gitenv = None, None, (UNKNOWN,), False
    base = (cur, old, [])                   # where the current and-or list began (`a && b &` runs in a subshell)
    for c in R.read(cmd):
        if isinstance(c, str):
            link = getattr(c, "end", None)          # the list operator after a subshell or compound
            if c in ("(", "cwd+") and prev not in ("&&", "||"):
                base = (cur, old, list(dirs))
            inner = cur                     # where it starts: after `cd X ||`, where the cd failed
            if prev == "||" and failed is not None:
                inner = failed
            elif prev == "||" and maybe is not None:
                inner = union(maybe, cur)
            if c == "(":                    # a pipe into a subshell feeds the commands inside it
                stack.append((cur, group, maybe, failed, old, list(dirs), base))
                cur, group, maybe, failed = inner, (fed if fed is not None else group), None, None
                opened.append(len(out))
            elif c == ")":
                if stack:
                    cur, group, maybe, failed, old, dirs[:], base = stack.pop()
                closed = opened.pop() if opened else None
            elif c == "cwd+":               # an if, case, loop body or { …; } group: its `cd` may or may not run
                scopes.append((inner, [], group, maybe, failed, old, cur if prev == "||" else None, base))
                cur, group, maybe, failed = inner, (fed if fed is not None else group), None, None
                opened.append(len(out))
            elif c == "cwd|" and scopes:    # a branch ends: the next one starts where the compound did
                scopes[-1][1].append((cur, old))
                cur, old = scopes[-1][0], scopes[-1][5]
            elif c == "cwd-":
                if scopes:
                    entry, tails, group, maybe, failed, old0, skip, base = scopes.pop()
                    cur = union(entry, *(t for t, _ in tails), cur)
                    old = union(old0, *(o for _, o in tails), old)
                    if skip is not None and last in ("exit", "return"):
                        cur, maybe, failed = skip, None, None   # `cd X || { …; exit 1; }`: what follows runs in X
                    elif skip is not None:
                        cur = union(skip, cur)          # `A || { … }`: A succeeded, or the group ran
                closed = opened.pop() if opened else None
            if c in (")", "cwd-") and (link == "&&" or c == "cwd-"):
                failed = None                   # past it, `|| Y` is reached from the success path too
            if c in (")", "cwd-") and maybe is not None and link not in ("&&", "||"):
                cur, maybe, failed = union(maybe, cur), None, None      # its list ends here
            if c in (")", "cwd-") and link == "&":
                cur, old, dirs[:] = base[0], base[1], base[2]           # a background list moves no shell
                maybe, failed = None, None
            fed, last = None, None
            prev = link if c in (")", "cwd-") and link in ("&&", "||") else None
            continue
        own, closed = (closed if not c.words and not c.assigns else None), None
        if prev not in ("&&", "||", "|", "|&"):
            base = (cur, old, list(dirs))
        start = here = cur
        if prev == "||" and failed is not None:
            here = failed                   # `cd X || Y`: Y runs where the cd failed
        elif prev == "||" and maybe is not None:
            here = union(maybe, cur)        # `… && cd X && a || Y`: Y runs after any of them failed
        for sub in c.subs:
            out += views(sub, depth + 1, here)
        vs = simple(c, depth, here)
        for v in vs:                        # what ended it (`&&`: the next runs only if it succeeded)
            v.setdefault("end", c.end)
            v.setdefault("depth", depth)
            if v["prog"] == "git" and (gitenv or any(e.split("=", 1)[0] in GITENV for e in v["env"])):
                v["dir"] = UNKNOWN          # git's repository is elsewhere: its branch, its worktree unknown
        word0 = c.words[0].text() if c.words else None
        if word0 in ("export", "declare", "typeset", "readonly", "local") and any(
                (w.text() or "").split("=", 1)[0] in GITENV for w in c.words[1:]) or not c.words and any(
                n in GITENV for n, _ in c.assigns):
            gitenv = True
        lead = next((v for v in vs if v["prog"] not in ("builtin", "command", "time")), None)  # `nohup cd` moves none
        mover = lead if lead and lead["prog"] in ("cd", "pushd", "popd") and lead["depth"] == depth else None
        prog = vs[0]["prog"] if vs else ""
        if c.pipe or c.end == "&":          # a pipeline element or a background job runs in a subshell
            pass
        elif mover is not None:
            before, old0 = here, old
            cur, old = moved(mover["prog"], mover["args"], here, old, dirs)
            if prev in ("&&", "||"):        # it may not run: known only along its own list
                old = union(old0, old)
                maybe = before if maybe is None else union(maybe, before)
            if prev == "||":                # `A || cd X`: X, or where A left it
                cur = union(start, cur)
            if c.end == "||":               # `cd X || Y`: Y runs where the cd failed; after it, either
                failed, maybe = before, (maybe if maybe is not None else before)
        elif prev == "||" and failed is not None and prog in ("exit", "return"):
            maybe = None                    # `cd X || exit`: what follows runs in X
        if any(v["prog"] == "eval" and v["depth"] == depth for v in vs) and any(
                v["prog"] in ("cd", "pushd", "popd") and v["depth"] > depth for v in vs):
            cur = union(cur, (UNKNOWN,))    # eval's cd moves this shell too
        stdin = fed if fed is not None else group
        inherited = fed is None
        for op, fd, w in c.redirs:          # the last redirect of fd 0 wins, left to right
            if fd in ("", "0") and op in ("<<", "<<-", "<<<", "<", "<&", "<>"):
                stdin = (w.body if w.quoted or w.body is None else OUTER_PWD.sub(lambda _: outer(here), w.body)) \
                    if op in ("<<", "<<-") else nested(render(w.parts, True), here) if op == "<<<" else None
                inherited = False
        if own is not None:                 # the group's own redirects: its commands write and read them
            inner = out[own:]
            for v in inner:
                v["writes"] = v["writes"] + (vs[0]["writes"] if vs else [])
            if stdin is not None:           # each command inside reads it unless it has its own stdin
                targets = [v for v in inner if v.get("stdin") != "own"]
                for v in inner:
                    v["stdin"] = "own"
                out += own_stdin(feed(targets, stdin, depth))
        elif stdin is not None and vs:
            for v in vs:
                v["stdin"] = "inherited" if inherited else "own"
            out += own_stdin(feed(vs, stdin, depth))
        fed = stdout(vs, stdin) if c.end in ("|", "|&") else None
        out += vs
        if c.end == "&&":
            failed = None                   # `cd X || a && b || Y`: Y is reached from X too
        if c.end not in ("&&", "||", "|", "|&"):
            if maybe is not None:
                cur = union(maybe, cur)
            maybe, failed = None, None
        if c.end == "&" and prev in ("&&", "||", "|", "|&"):
            cur, old, dirs[:] = base[0], base[1], base[2]   # `cd X && make &`: the whole list is a background job
        sure = prog in ("echo", "true", ":") or prog == "printf" and vs[0]["args"][:1] and "%" not in \
            vs[0]["args"][0].replace("%%", "")         # cannot fail (printf's conversions can)
        always = prev not in ("&&", "||") or prev == "||" and last in ("exit", "return") or prev == "&&" and last == "ok"
        last = ("ok" if sure and not vs[0]["writes"] else prog) if always and not c.pipe and c.end != "&" else None
        prev = c.end
    return out


def own_stdin(vs):
    """Views read from a stdin text: theirs is that text, no enclosing group's."""
    for v in vs:
        v["stdin"] = "own"
    return vs


def stdout(vs, stdin):
    """The text a command writes to the pipe, when the gate can know it: echo's and printf's words, and
    cat or tee passing its own heredoc, here-string or pipe through."""
    if not vs:
        return None
    v = vs[-1]
    args = [a for a in v.get("said", v["args"]) if a != STDIN]
    if v["prog"] == "echo":
        esc = False
        while args and re.fullmatch(r"-[neE]+", args[0]):
            esc = esc or "e" in args[0]
            args = args[1:]
        return unescape(" ".join(args)) if esc else " ".join(args)
    if v["prog"] == "printf" and args:
        return printf(args[0], args[1:])
    if v["prog"] == "cat" and not [a for a in args if not a.startswith("-") or a == "-"][1:] or v["prog"] == "tee":
        return stdin
    return None


ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", "a": "\a", "r": "\r", "e": "\x1b", "0": "\0"}


def unescape(t):
    return re.sub(r"\\(.)", lambda m: ESCAPES.get(m.group(1), "\\" + m.group(1)), t)


def printf(fmt, args):
    """printf FORMAT ARGS: the format repeated while arguments remain (%s, %b, %d, %%; escapes)."""
    out, args = [], list(args)
    while True:
        used = [0]

        def conv(m):
            if m.group(1) == "%":
                return "%"
            used[0] += 1
            a = args.pop(0) if args else ""
            return unescape(a) if m.group(1) == "b" else a
        out.append(unescape(re.sub(r"%[-+ #0-9.]*([%sbdiqc])", conv, fmt)))
        if not args or not used[0]:
            return "".join(out)


SHELL_PAIRED = {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}
PY_PAIRED = {"-W", "-X", "--check-hash-based-pycs"}


def reads_stdin(v):
    """Does this shell or python run the program on its stdin? A shell with only options (no -c, no script
    operand), or with `-s` or `-`; a python with no -c, -m or script, or `-`. xargs feeds arguments, not stdin."""
    shell, args, i = v["prog"] in SHELLS, v["args"], 0
    if v["prog"] in ("source", "."):        # source /dev/stdin: this shell runs its stdin
        return bool(args) and args[0] in STDIN_FILES
    if STDIN in args or not (shell or PYTHON.fullmatch(v["prog"])):
        return False
    while i < len(args):
        a = args[i]
        if a == "-":
            return True
        if a == "--":
            return shell and i + 1 == len(args)
        if not a.startswith(("-", "+")) or a == "+":
            return a in STDIN_FILES                       # a script operand
        if re.match(r"^-[A-Za-z]*c" if shell else r"^-[A-Za-z]*[cm]", a) and not a.startswith("--"):
            return False
        if shell and re.match(r"^-[A-Za-z]*s", a) and not a.startswith("--"):
            return True                                   # bash -s ARG…: the script is stdin
        i += 2 if a in (SHELL_PAIRED if shell else PY_PAIRED) else 1
    return True


def feed(vs, text, depth):
    """What a command's stdin runs: a shell reading its stdin runs the text, and a python gets it as code
    (python3 - <<'EOF' … EOF, or -c that execs its stdin)."""
    out, shells = [], {}
    for v in vs:
        if "code" in v:                     # python: its stdin is code it may run (exec(sys.stdin.read()))
            v["code"] = (v["code"] + "\n" + text).lstrip("\n")
        elif v["prog"] in _patch.PATCHERS:         # apply_patch <<'EOF' … EOF: each path the patch names is a write
            v["writes"] = v["writes"] + patched(text)
        elif reads_stdin(v):                # one read per shell, in every directory it may run in
            shells.setdefault((v["prog"], tuple(v["args"]), tuple(v["env"])), []).append(v["dir"])
    for (_, _, env), where in shells.items():
        out += [dict(x, env=x["env"] + list(env)) for x in views(text, depth + 1, union(where))]
    return out


STDIN = "{stdin}"   # the arguments xargs or parallel adds from stdin: unseen, so any flag may be among them
STDIN_FILES = ("/dev/stdin", "/dev/fd/0", "/proc/self/fd/0")
PYTHON = re.compile(r"python[\d.]*")
XARGS_PAIRED = {"-I", "-n", "-P", "-L", "-s", "-d", "-E", "-a", "--max-args", "--max-procs", "--delimiter",
                "--arg-file", "--max-lines", "--max-chars", "--eof", "-j", "--jobs"}   # -i, -l, -e take no separate value
WRITES = (">", ">>", ">|", "&>", "&>>", "<>")
WRAPPER_PAIRED = {     # the options of a wrapper that take a separate value
    "sudo": {"-u", "-g", "-p", "-h", "-C", "-D", "-r", "-t", "-U", "-T", "--user", "--group", "--prompt", "--host",
             "--close-from", "--chdir", "--role", "--type", "--other-user", "--command-timeout"},
    "doas": {"-u", "-C"}, "nice": {"-n", "--adjustment"}, "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "stdbuf": {"-i", "-o", "-e", "--input", "--output", "--error"}, "caffeinate": {"-t", "-w"},
    "ionice": {"-c", "-n", "-p", "-P", "-u", "--class", "--classdata", "--pid", "--pgid", "--uid"},
    "exec": {"-a"}, "time": {"-f", "-o", "--format", "--output"},
    "flock": {"-w", "-E", "--wait", "--timeout", "--conflict-exit-code"},
    "chrt": {"-T", "-P", "-D", "--sched-runtime", "--sched-period", "--sched-deadline"}}


COPY_PAIRED = {     # the options of a copy, move, link or install that take a separate value
    "cp": {"-S", "--suffix", "--sparse", "--no-preserve"}, "mv": {"-S", "--suffix"}, "ln": {"-S", "--suffix"},
    "install": {"-m", "-o", "-g", "-S", "--mode", "--owner", "--group", "--suffix", "--strip-program"},
    "rsync": {"-e", "-f", "-B", "-M", "-T", "-@", "--rsh", "--rsync-path", "--filter", "--exclude", "--include",
              "--exclude-from", "--include-from", "--files-from", "--temp-dir", "--compare-dest", "--copy-dest",
              "--link-dest", "--backup-dir", "--suffix", "--chmod", "--chown", "--usermap", "--groupmap",
              "--password-file", "--log-file", "--log-file-format", "--out-format", "--partial-dir", "--max-size",
              "--min-size", "--timeout", "--contimeout", "--port", "--sockopts", "--bwlimit", "--iconv",
              "--checksum-choice", "--compress-choice", "--skip-compress", "--write-batch", "--read-batch",
              "--only-write-batch", "--max-delete", "--block-size", "--modify-window", "--address", "--outbuf",
              "--info", "--debug", "--stop-after", "--stop-at", "--copy-as", "--remote-option", "--log-format",
              "--compress-level", "--checksum-seed", "--protocol", "--max-alloc", "--cc", "--zc", "--zl",
              "--early-input", "--stderr"}}
COPY_FLAGS = {      # their long options that take no separate value (a bare --backup, not --backup=CONTROL)
    "cp": {"--archive", "--attributes-only", "--backup", "--copy-contents", "--debug", "--force", "--interactive",
           "--link", "--dereference", "--no-clobber", "--no-dereference", "--preserve", "--parents", "--recursive",
           "--reflink", "--remove-destination", "--strip-trailing-slashes", "--symbolic-link", "--update",
           "--verbose", "--one-file-system", "--context", "--keep-directory-symlink", "--help", "--version"},
    "mv": {"--backup", "--debug", "--exchange", "--force", "--interactive", "--no-clobber", "--no-copy",
           "--strip-trailing-slashes", "--update", "--verbose", "--context", "--help", "--version"},
    "ln": {"--backup", "--directory", "--force", "--interactive", "--logical", "--no-dereference", "--physical",
           "--relative", "--symbolic", "--verbose", "--help", "--version"},
    "install": {"--backup", "--compare", "--debug", "--preserve-timestamps", "--strip", "--verbose",
                "--preserve-context", "--context", "--help", "--version"},
    "rsync": {"--verbose", "--quiet", "--no-motd", "--checksum", "--archive", "--recursive", "--relative",
              "--no-implied-dirs", "--backup", "--update", "--inplace", "--append", "--append-verify", "--dirs",
              "--mkpath", "--links", "--copy-links", "--copy-unsafe-links", "--safe-links", "--munge-links",
              "--copy-dirlinks", "--keep-dirlinks", "--hard-links", "--perms", "--executability", "--acls",
              "--xattrs", "--owner", "--group", "--devices", "--copy-devices", "--write-devices", "--specials",
              "--times", "--atimes", "--open-noatime", "--crtimes", "--omit-dir-times", "--omit-link-times",
              "--super", "--fake-super", "--sparse", "--preallocate", "--dry-run", "--whole-file",
              "--one-file-system", "--existing", "--ignore-existing", "--remove-source-files", "--delete",
              "--delete-before", "--delete-during", "--delete-delay", "--delete-after", "--delete-excluded",
              "--ignore-missing-args", "--delete-missing-args", "--ignore-errors", "--force", "--partial",
              "--delay-updates", "--prune-empty-dirs", "--numeric-ids", "--ignore-times", "--size-only",
              "--fuzzy", "--compress", "--cvs-exclude", "--from0", "--protect-args", "--secluded-args",
              "--trust-sender", "--blocking-io", "--stats", "--8-bit-output", "--human-readable", "--progress",
              "--itemize-changes", "--list-only", "--ipv4", "--ipv6", "--fsync", "--old-args", "--old-dirs",
              "--help", "--version"}}
MAXDESTS = 8        # the operands a copy's unknown trailing options leave as its possible destination


def copied(prog, args):
    """What a copy, move, link or install writes, as plans place() resolves where the command runs:
    {prog, dest, srcs, target (-t DIR), whole (-T), parents (cp --parents, rsync -R: DEST/SRC), removes
    (mv, rsync --remove-source-files: its sources go), nodir (ln -n: a link DEST is replaced, not
    entered)}. A long option matches exactly, else by its unique GNU prefix. An operand right after a long
    option the gate does not know may be that option's value, so the operand before it may be the
    destination (at most MAXDESTS of them). Nothing for `install -d` or one operand (but `ln -s TARGET`)."""
    paired, flags, ops, target, i = COPY_PAIRED[prog], COPY_FLAGS[prog], [], None, 0
    whole, parents, removes, nodir, maybe = False, False, prog == "mv", False, set()

    def is_(k, name, least):
        return len(k) >= least and name.startswith(k)
    while i < len(args):
        a = args[i]
        if a == "--":
            ops += [(o, False) for o in args[i + 1:]]
            break
        if a.startswith("--"):
            k, eq, v = a.partition("=")
            longs = [p for p in paired if p.startswith("--") and is_(k, p, 3)]
            known = k in flags or [f for f in flags if is_(k, f, 3)]
            if prog != "rsync" and is_(k, "--target-directory", 3):
                target, i = (v, i) if eq else (args[i + 1] if i + 1 < len(args) else None, i + 1)
            elif prog != "rsync" and is_(k, "--no-target-directory", 6):
                whole = True
            elif prog == "install" and is_(k, "--directory", 4):
                return []
            elif prog == "cp" and is_(k, "--parents", 4) or prog == "rsync" and is_(k, "--relative", 5):
                parents = True
            elif prog == "rsync" and is_(k, "--remove-source-files", 7):
                removes = True
            elif prog == "ln" and is_(k, "--no-dereference", 6):
                nodir = True
            elif k in flags or eq:
                pass
            elif k in paired or longs and not known:
                i += 1                              # its value (a unique prefix of an option that takes one)
            elif not known and i + 1 < len(args):
                maybe.add(i + 1)                    # unknown: the next word may be its value
        elif a.startswith("-") and a != "-":
            for j in range(1, len(a)):
                f = "-" + a[j]
                if f == "-t" and prog != "rsync":
                    target, i = (a[j + 1:], i) if a[j + 1:] else (args[i + 1] if i + 1 < len(args) else None, i + 1)
                    break
                if f == "-T" and prog != "rsync":
                    whole = True
                elif f == "-d" and prog == "install":
                    return []
                elif f == "-R" and prog == "rsync":
                    parents = True
                elif f == "-n" and prog == "ln":
                    nodir = True
                elif f in paired:
                    i += 0 if a[j + 1:] else 1
                    break
        else:
            ops.append((a, i in maybe))
        i += 1
    plan = {"prog": prog, "target": False, "whole": whole, "parents": parents, "removes": removes, "nodir": nodir}
    words = [o for o, _ in ops]
    if target is not None:
        return [dict(plan, dest=target, srcs=words, target=True)]
    if prog == "ln" and len(ops) == 1:
        return [dict(plan, dest=".", srcs=words, target=True)]     # ln -s TARGET: a link named after it, here
    k, out = len(ops) - 1, []
    while k >= 1:                           # the last operand, and before it while it may be an option's value
        out.append(dict(plan, dest=words[k], srcs=words[:k] if len(out) < MAXDESTS else []))
        if not ops[k][1]:
            break
        k -= 1
    return out


def copy_writes(cwd, v, plan, made=()):
    """The paths a copy plan writes where its command runs: DEST/NAME for each source when DEST is a
    directory (or -t DIR, or more than one source or a trailing / or a DEST the gate cannot place), else
    DEST itself (the copy becomes DEST); a glob source's NAMEs are the files it names now; a source whose
    contents are copied (SRC/., rsync's SRC/) gives each of its entries (DEST/* when the gate cannot list
    them); a source whose name the gate cannot see ({}, xargs' arguments, a variable) is DEST/?; DEST/SRC
    under --parents or -R; `ln -n` over a link replaces DEST; and the sources a move takes away. A remote
    rsync destination is not this machine's. `made`: the absolute paths the line wrote before this command
    (a directory it filled, a file it staged), listed with what is there now."""
    dest, srcs, rsync = plan["dest"], plan["srcs"], plan["prog"] == "rsync"
    out = list(srcs) if plan["removes"] else []
    if dest is None or rsync and re.match(r"^[^/]*:", dest):
        return out
    base = dest.rstrip("/") or "/"

    def mine(f):                                            # the names the line wrote into f before this command
        return [m[len(f.rstrip("/")) + 1:].split("/")[0] for m in made if m.startswith(f.rstrip("/") + "/")]

    def contents(s):                                        # the entries of SRC/. (rsync SRC/), as copied
        w = where_of(v, s)
        f = resolve(cwd, w) if w != UNKNOWN else ""
        try:
            now = os.listdir(f) if f else None
        except OSError:
            now = None                                      # missing now: what fills it (tar, curl -o) is unseen
        if now is None or len(now) > GLOB_SCAN:
            return [base + "/*"]
        names = sorted(set(now) | set(mine(f)))
        keep = sorted(n for k, n in enumerate(names) if k < GLOB_MAX or KEEP.search(n))
        return [base + "/" + n for n in keep[:4 * GLOB_MAX]] + ([base + "/*"] if len(keep) > 4 * GLOB_MAX else []) + (
            [base + "/?"] if not now and names else [])     # empty now: the line filled it, not only by what it shows

    def a_dir(s):                                           # rsync copies a directory into DEST, as DEST/NAME
        w = where_of(v, s)
        f = resolve(cwd, w) if w != UNKNOWN else ""
        return not f or os.path.isdir(f) or any(m.startswith(f.rstrip("/") + "/") for m in made)
    if plan["whole"]:                                       # cp -T SRCDIR DEST: DEST gets SRCDIR's entries
        return out + [dest] + [w for s in srcs if plan["prog"] == "cp" and a_dir(s)
                               for w in contents(s.rstrip("/") + "/.")]
    where = where_of(v, dest)
    full = resolve(cwd, where) if where != UNKNOWN else ""
    if plan.get("nodir") and full and not dest.endswith("/") and os.path.islink(full):
        return out + [dest]                                 # ln -sfn X LINK: the link itself is replaced
    isdir = os.path.isdir(full) if full else None           # None: the gate cannot tell

    def globbed(s):                                         # what the shell's glob names (now, made, dot entries),
        now = [os.path.basename(m.rstrip("/")) for m in matches(cwd, v, s)]      # and whether it names any now
        head, last = os.path.split(s)
        w = where_of(v, head or ".") if not re.search(r"[*?\[]", head) else UNKNOWN
        f = resolve(cwd, w) if w != UNKNOWN else ""
        names = list(now)
        if f:
            names += [n for n in mine(f) if fnmatch.fnmatch(n, last)]
        if f and re.match(r"[*?\[]", last):                # dotglob or GLOBIGNORE: `*` names dotfiles too
            try:
                names += [n for n in os.listdir(f) if n.startswith(".") and fnmatch.fnmatch(n, last)]
            except OSError:
                pass
        return sorted(set(names)), bool(now)

    def inside(s):
        if plan["parents"]:
            rel = s.split("/./", 1)[1] if rsync and "/./" in s else s
            return contents(s) if rel.strip("/") in ("", ".") else [base + "/" + rel.lstrip("/")]
        if s.endswith("/.") or rsync and s.endswith("/"):
            return contents(s)
        if s in v.get("globs", ()) and re.search(r"[*?]", s):  # the shell expands it: each file it names
            names, now = globbed(s)                         # (none now: the line fills it, not only as it shows)
            return [base + "/" + n for n in names] + ([] if now else [base + "/?"])
        name = os.path.basename(s.rstrip("/"))
        if s in (STDIN, "{}") or re.search(r"[$`]", name):
            return [base + "/?"]                            # a name the gate cannot see
        return [base + "/" + name] if name not in ("", ".", "..") else contents(s + "/.")
    if plan["target"] or isdir:
        return out + [w for s in srcs or [STDIN] for w in inside(s)]
    if isdir is False and len(srcs) == 1 and not dest.endswith("/"):
        s = srcs[0]
        if rsync and not s.endswith("/") and a_dir(s):
            return out + inside(s)
        return out + [dest] + (contents(s) if s.endswith("/.") or rsync and s.endswith("/") else [])
    becomes = isdir is None and re.search(r"[$`]", dest) and len(srcs) == 1 and not rsync and not srcs[0].endswith("/.")
    return out + [dest] + ([base + "/*"] if becomes else []) + [w for s in srcs or [STDIN] for w in inside(s)]


def simple(c, depth, cur=""):
    """The views of one simple command (a _shellread.Cmd): one per directory it may run in."""
    cur = (cur,) if isinstance(cur, str) else tuple(cur)
    env = ["%s=%s" % (n, render(v)) for n, v in c.assigns]
    writes = [render(w.parts) for op, _, w in c.redirs
              if op in WRITES or (op == ">&" and not re.fullmatch(r"\d+|-", w.text() or "0"))]
    globs = {render(w.parts) for w in list(c.words) + [w for _, _, w in c.redirs]
             if any(isinstance(p, tuple) and p[0] == "unknown" for p in w.parts)}     # the shell expands these
    if not c.words:                 # `> F`, `X=1 > F`: no program, still a write
        return [{"prog": "", "args": [], "env": list(env), "writes": list(writes), "dir": d, "config": [],
                 "globs": globs} for d in cur] if writes else []
    vs = chain([render(w.parts, True) for w in c.words], env, writes, depth, cur)
    for v in vs:
        v.setdefault("globs", globs)
    return vs


def chain(words, env, writes, depth, cur, shell=None):
    """The views of a command's words: the program, then each program a wrapper runs, each once per
    directory it may run in (cur, a tuple of them). `shell`: where the shell that expanded the words is,
    when a wrapper (env -C, git -C, find -execdir) moved the command from there."""
    cur = (cur,) if isinstance(cur, str) else tuple(cur)
    out, config, stdin_args = [], [], False

    def moving():                       # the shell stays where it was when the command moves
        return shell if shell is not None else cur[0] if len(cur) == 1 else UNKNOWN

    def nest(text):                     # text a nested shell reads: this shell's $PWD is where it is
        return nested(text, (shell,) if shell is not None else cur)
    while words and ASSIGN.match(words[0]):
        env.append(own(words.pop(0)))
    while words:
        if words[0] == "--":    # the end of a wrapper's options (sudo -u root -- CMD, time -p -- CMD)
            words = words[1:]
            continue
        prog = os.path.basename(own(words[0]))
        rest = words[1:]
        code = None
        if PYTHON.fullmatch(prog):          # python [options] (-c CODE | -m MOD | SCRIPT | -) …
            j, code = 0, ""
            while j < len(rest) and rest[j].startswith("-") and rest[j] != "-":
                m = re.match(r"^-[bBdEhiIOqsSuvVxP]*([cm])(.*)$", rest[j])
                if m:
                    if m.group(1) == "c":
                        code = own(m.group(2) or (rest[j + 1] if j + 1 < len(rest) else ""))
                    break
                j += 2 if rest[j] in PY_PAIRED else 1
            else:
                if j < len(rest) and rest[j] != "-" and rest[j] not in STDIN_FILES:
                    prog, rest, code = os.path.basename(rest[j]), rest[j + 1:], None   # python SCRIPT: the script runs
        if prog == "tee":
            writes = writes + [own(w) for w in rest if not w.startswith("-")]
        if prog in _patch.PATCHERS and rest:       # apply_patch 'PATCH': each path it names is a write
            writes = writes + patched(own(rest[0]))
        if prog == "git":
            while rest and rest[0].startswith("-"):
                o, v = rest[0], rest[1] if len(rest) > 1 else None
                if o == "-C":
                    v = own(v) if v is not None else v
                    shell, cur = moving(), union(*((chdir(d, v, shell),) for d in cur))
                elif o in ("-c", "--config-env") and v:
                    config.append(own(v))
                elif o.startswith(("--git-dir", "--work-tree")):
                    cur = (UNKNOWN,)
                elif o.startswith("--config-env="):
                    config.append(own(o.split("=", 1)[1]))
                rest = rest[2:] if o in GIT_PAIRED else rest[1:]
        if prog == "command" and rest[:1] in (["-v"], ["-V"]):
            rest = []           # `command -v X` looks X up; it runs nothing
        args = [own(a) for a in rest] + ([STDIN] if stdin_args is True else [])
        plans = copied(prog, args) if prog in COPY_PAIRED else None    # placed where it runs
        for d in cur:
            out.append({"prog": prog, "args": args, "env": list(env), "writes": list(writes), "dir": d,
                        "config": list(config)})
            if shell is not None:
                out[-1]["shell"] = shell
            if prog in ("echo", "printf"):  # what it writes to a pipe another shell reads
                out[-1]["said"] = [nest(a) for a in rest]
            if plans is not None:
                out[-1]["copies"] = plans
            if code is not None:
                out[-1]["code"] = code
        if prog in SHELLS:      # sh -c [options] STRING: the first operand after the options is the command
            j, has_c, script = 0, False, None
            while j < len(rest):
                a = rest[j]
                if a in ("--", "-"):
                    script = rest[j + 1] if j + 1 < len(rest) else None
                    break
                if not a.startswith(("-", "+")):
                    script = a
                    break
                has_c = has_c or bool(re.match(r"^-[A-Za-z]*c[A-Za-z]*$", a))
                j += 2 if a in SHELL_PAIRED else 1
            if has_c and script is not None:
                out += [dict(v, env=v["env"] + env) for v in views(nest(script), depth + 1, cur)]
            elif script is not None and j < len(rest):
                words = rest[j + 1:] if rest[j] in ("--", "-") else rest[j:]    # bash SCRIPT ARGS: the script runs
                if words and words[0] != "-" and words[0] not in STDIN_FILES:
                    continue
            break
        if prog == "eval":
            out += views(nest(" ".join(rest)), depth + 1, cur)
            break
        if prog == "script":                # script [-q…] -c CMD [FILE]: CMD runs in a shell
            for j, a in enumerate(rest):
                if a.startswith("--command="):
                    out += [dict(v, env=v["env"] + env) for v in views(nest(a.split("=", 1)[1]), depth + 1, cur)]
                    break
                if (a == "--command" or re.fullmatch(r"-[a-zA-Z]*c", a)) and j + 1 < len(rest):
                    out += [dict(v, env=v["env"] + env) for v in views(nest(rest[j + 1]), depth + 1, cur)]
                    break
            break
        if prog == "find":
            j = 0
            while j < len(rest):
                if rest[j] in ("-exec", "-execdir", "-ok", "-okdir"):
                    inner = list(itertools.takewhile(lambda w: w not in (";", "+"), rest[j + 1:]))
                    if inner:
                        if depth >= DEPTH:
                            raise ValueError("find -exec nests deeper than the door gate reads (%d)" % DEPTH)
                        out += chain(inner, [], [], depth + 1, *(((UNKNOWN,), moving()) if rest[j] in ("-execdir", "-okdir")
                                                                 else (cur, shell)))
                    j += len(inner)
                j += 1
            break
        if prog in ("xargs", "parallel"):
            words, repl = rest, None
            while words and words[0].startswith("-"):
                o = words[0]
                if o == "-I" and len(words) > 1:
                    repl = words[1]
                elif o.startswith(("-I", "--replace=")):
                    repl = o.split("=", 1)[1] if o.startswith("--") else o[2:]
                elif o == "--replace" or o.startswith("-i"):
                    repl = o[2:] if o.startswith("-i") and len(o) > 2 else "{}"
                words = words[2:] if o in XARGS_PAIRED else words[1:]
            if repl and prog == "xargs":    # -I R: stdin replaces R in the words, nothing is appended
                words = [w.replace(repl, STDIN) for w in words]
                stdin_args = "replaced"
            else:
                stdin_args = True   # the arguments come from stdin: the gate cannot see them
            continue
        if prog not in WRAPPERS:
            break
        words = rest
        if prog == "env":   # env [-i] [-u NAME] [-S STRING] [NAME=VALUE]… COMMAND
            while words and (words[0].startswith("-") and words[0] != "--" or ASSIGN.match(words[0])):
                o = words[0]
                if o in ("-S", "--split-string") or o.startswith(("-S", "--split-string=")):
                    split = words[1] if o in ("-S", "--split-string") and len(words) > 1 else \
                        o.split("=", 1)[1] if o.startswith("--") else o[2:]
                    words = split.split() + words[2 if o in ("-S", "--split-string") else 1:]
                    continue
                if o in ("-C", "--chdir") or o.startswith("--chdir=") or o.startswith("-C") and len(o) > 2:
                    to = (words[1] if len(words) > 1 else None) if o in ("-C", "--chdir") else \
                        o.split("=", 1)[1] if o.startswith("--") else o[2:]
                    to = own(to) if to is not None else to
                    shell, cur = moving(), union(*((chdir(d, to, shell),) for d in cur))    # env -C DIR: the command runs there
                    words = words[2:] if o in ("-C", "--chdir") else words[1:]
                    continue
                if o in ("-u", "--unset"):
                    words = words[2:]
                    continue
                if ASSIGN.match(o):
                    env.append(own(o))
                words = words[1:]
        else:               # sudo -u USER …, timeout -s KILL 5 …, nice -n 5 …: skip options and their values
            paired = WRAPPER_PAIRED.get(prog, ())
            while words and words[0].startswith("-") and words[0] != "--":
                words = words[2:] if words[0] in paired else words[1:]
            if prog in ("timeout", "taskset", "chrt", "flock") and words and words[0] != "--":
                words = words[1:]   # the duration, the CPU mask, the priority, the lock file
            if prog == "flock" and words[:1] in (["-c"], ["--command"]):
                out += [dict(v, env=v["env"] + env) for v in views(nest(words[1]) if len(words) > 1 else "", depth + 1, cur)]
                break
    return out


class Context:
    """What an action is checked against: the project, the git state its `when` conditions read."""

    def __init__(self, cwd, assume_when=False, forced=False):
        self.cwd, self.assume, self.forced, self._git = cwd, assume_when, forced, {}

    def git(self, *args):
        if args not in self._git:
            r = subprocess.run(["git", "-C", self.cwd] + list(args), capture_output=True, text=True)
            self._git[args] = r.stdout.strip() if r.returncode == 0 else ""
        return self._git[args]

    def when(self, cond, default, branch=None):
        if self.forced or not cond:
            return True
        if cond == "on-default-branch" and branch is not None:
            names, live = branch
            if any(n == UNKNOWN or default.fullmatch(n) for n in names):
                return True
            if not live:
                return False
        if self.assume:
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


def contexts(cwd, assume_when):
    """The Context of each directory a command runs in (`cd DIR`, `git -C DIR`), made once: a directory
    the gate cannot resolve or find has its `when` conditions assumed (the door counts)."""
    made = {}

    def at(where):
        if where not in made:
            full = resolve(cwd, where) if where != UNKNOWN else ""
            made[where] = Context(full or cwd, assume_when or not (full and os.path.isdir(full)), assume_when)
            real = resolve(cwd, where, True) if full else ""
            if real and real != os.path.realpath(full):     # git -C, cd -P, a host workdir: a link before `..`
                made[where] = Either(made[where], Context(real, assume_when or not os.path.isdir(real), assume_when))
        return made[where]
    return at


class Either:
    """A directory read two ways (as written, and as the kernel walks it): a condition holds if it holds in
    either."""

    def __init__(self, *ctxs):
        self.ctxs = ctxs

    def when(self, *a, **k):
        return any(c.when(*a, **k) for c in self.ctxs)


@functools.lru_cache(maxsize=8)
def toplevel(cwd):
    r = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def resolve(cwd, where, kernel=False):
    """The absolute directory of a view's `dir` (TOP: the checkout's top, from git; "" when there is none);
    `kernel`: its symlinks resolved as the kernel walks it (before each `..`)."""
    if where.startswith(TOP):
        if not toplevel(cwd):
            return ""
        where = toplevel(cwd) + where[len(TOP):]
    if where.startswith(CWD):
        where = where[len(CWD):].lstrip("/") or "."
    where = os.path.join(cwd, os.path.expanduser(where))
    return os.path.realpath(where) if kernel else os.path.normpath(where)


def branches(cwd, vs):
    """Each view's branch when an earlier command of the line switched its checkout (git checkout -b,
    switch, worktree add): `git switch -c x && git push -u origin HEAD` pushes x, whatever the hook's
    HEAD; `git checkout main && … git push` pushes main. A branch is (names, live): live when the
    checkout's own HEAD may still be the one (the switch could fail and a `;` or `||` runs on). None:
    the checkout's own HEAD decides."""
    made, since = {}, []
    for v in vs:
        key = None if v["dir"] == UNKNOWN else os.path.normpath(v["dir"] or ".")
        v["branch"] = made.get(key) if key else None
        a, name, at = v["args"], None, key
        if v["prog"] == "git" and key and a[:1] in (["checkout"], ["switch"]):
            ops, j = [], 1
            while j < len(a):
                if a[j] in ("-b", "-B", "-c", "-C", "--orphan") and j + 1 < len(a):
                    name, j = a[j + 1], j + 2
                    continue
                if a[j] == "--":
                    ops.append(None)          # paths follow: a checkout of files
                    break
                if a[j] in ("-d", "--detach"):
                    name = "\0detached"
                elif not a[j].startswith("-"):
                    ops.append(a[j])
                j += 1
            if not name and len(ops) == 1 and ops[0] is not None and (
                    a[0] == "switch" or not os.path.exists(os.path.join(resolve(cwd, v["dir"] or ".") or cwd, ops[0]))):
                name = UNKNOWN if re.search(r"[$`*?]", ops[0]) or ops[0] == "-" else ops[0]
        elif v["prog"] == "git" and key and a[:2] == ["worktree", "add"]:
            ops, j = [], 2
            while j < len(a):
                if a[j] in ("-b", "-B") and j + 1 < len(a):
                    name, j = a[j + 1], j + 2
                    continue
                if a[j] == "--reason" and j + 1 < len(a):
                    j += 2
                    continue
                if a[j] == "--detach":
                    name = "\0detached"
                elif not a[j].startswith("-"):
                    ops.append(a[j])
                j += 1
            at = chdir(v["dir"], ops[0], v.get("shell")) if ops else UNKNOWN
            at = None if at == UNKNOWN else os.path.normpath(at)
            name = (name or (ops[1] if len(ops) > 1 else os.path.basename(ops[0].rstrip("/")))) if at else None
        if name and at:
            made[at] = ({name}, v.get("end") != "&&") if v.get("end") == "&&" or at not in made else \
                (made[at][0] | {name}, True)
            since.append(v.get("depth", 0))
        elif since and v.get("end") != "&&" and v.get("depth", 0) <= min(since):
            made = {k: (n, True) for k, (n, _) in made.items()}     # a failed switch reaches what follows
    return vs


def dry(args):
    """A dry run or a help page: a whole word --help, --dry-run or --dry-run=X (not none or false), before
    a bare `--`."""
    for a in args:
        if a == "--":
            return False
        if a in ("--help", "--dry-run") or a.startswith("--dry-run=") and a[10:] not in ("none", "false"):
            return True
    return False


# the run's own records, matched by name where a command's directory is unknown (cd "$X" && rm events.jsonl)
GUARDED = ("docs/agents/mission/ledger.jsonl", ".aladdin/logs/events.jsonl", ".agentic/logs/events.jsonl")


def placed(cwd, top, v, path, kernel=False, here=None):
    """A path a command names as its patterns read it: resolved where the command runs, relative to the
    project `top` or this checkout when inside one (`cd docs/agents && … mission/ledger.jsonl` is the
    ledger; /tmp/x/.aladdin is not the run's home). Where the gate cannot resolve it, a name a guarded
    record ends with is that record. `kernel`: walked as the kernel walks it, a symlink resolved before
    the `..` after it (link/../x is beside the link's target), instead of as the words say. `here`: this
    checkout's top, when the caller knows it."""
    if path.startswith("-") or path in (STDIN, "") or "\n" in path or len(path) > 4096:
        return path
    if re.match("(?:%s|%s|\\$PWD|\\$CLAUDE_PROJECT_DIR)(?:/|$)" % (re.escape(TOP), re.escape(CWD)), path):
        where = chdir(v["dir"], path, v.get("shell"))
    elif re.search(r"[$`]", path) or v["dir"] == UNKNOWN and not (os.path.isabs(path) or path.startswith("~")):
        where = UNKNOWN
    else:
        where = path if os.path.isabs(path) or path.startswith("~") or not v["dir"] else v["dir"] + "/" + path
    if where == UNKNOWN:
        x = os.path.normpath(path)        # as written (./.claude is .claude)
        return next((g for g in GUARDED if not os.path.isabs(x) and (g == x or g.endswith("/" + x) or re.fullmatch(
            r"\$[^/]*/" + re.escape(g), x))), x)
    full = resolve(cwd, where, kernel)
    if not full:
        return path
    for root in (top, toplevel(os.path.abspath(cwd)) if here is None else here):  # the project, else this checkout
        r = os.path.relpath(os.path.realpath(full), os.path.realpath(root)) if root else ".."
        if not r.startswith(".."):
            return r
    return full


NAMED = "\x01name"  # a name placed by its directory's template (place(): DIR/NAME is DIR's forms plus NAME)
GLOB_MAX = 512      # the files of a glob the gate lists (and any name a host's config or a run record may have)
GLOB_SCAN = 20000   # past this many, the glob's directory stands for the rest
KEEP = re.compile(r"(?:^|/)(?:\.[^/]*|[^/]*\.(?:jsonc?|jsonl|toml|ya?ml|m?js))$")


def where_of(v, path):
    """Where a command's path operand is, as resolve() takes it (UNKNOWN: the gate cannot tell)."""
    if re.search(r"[$`]", path) or "\n" in path or len(path) > 4096:
        return UNKNOWN
    if os.path.isabs(path) or path.startswith(("~", CWD)):
        return path                                         # where it says, wherever the command runs
    if v["dir"] == UNKNOWN:
        return UNKNOWN
    return path if path.startswith((TOP, CWD)) or not v["dir"] else v["dir"] + "/" + path


def matches(cwd, v, path):
    """The files a glob operand names now (the shell expands an unquoted one before the command runs):
    the first GLOB_MAX and every dotfile or config-like name, and past GLOB_SCAN its directory's `/*`."""
    if path not in v.get("globs", ()) or not re.search(r"[*?]", path):
        return []
    where = where_of(v, path)
    full = resolve(cwd, where) if where != UNKNOWN else ""
    out = []
    for n, m in enumerate(glob.iglob(full) if full else ()):
        if n >= GLOB_SCAN or len(out) >= 4 * GLOB_MAX:
            out.append(os.path.join(os.path.dirname(full), "*"))
            break
        if n < GLOB_MAX or KEEP.search(m):
            out.append(m)
    return sorted(out)


def ours(d, top):
    """Is the checkout at d this project's: the project, one inside it, or one of its linked worktrees?"""
    real = os.path.realpath(d)
    if real == top or real.startswith(top + os.sep):
        return True
    try:
        with open(os.path.join(d, ".git")) as f:
            m = re.match(r"gitdir:\s*(.+)", f.read(4096))
    except OSError:
        return False
    return bool(m) and os.path.realpath(os.path.join(d, m.group(1).strip())).startswith(
        os.path.join(top, ".git") + os.sep)


def checkout_rel(full, top):
    """A path relative to the checkout of this project that holds it (the nearest directory with a .git,
    if it is the project's: a nested or linked worktree's own root, where a host reads its config), or None."""
    d = os.path.dirname(full)
    while d:
        if os.path.exists(os.path.join(d, ".git")):
            return os.path.relpath(full, d) if ours(d, top) else None
        d = "" if os.path.dirname(d) == d else os.path.dirname(d)
    return None


def home_rel(full):
    """`~/PATH` for a path in the home directory (where a host reads its user settings), or None."""
    home = os.path.realpath(os.path.expanduser("~"))
    for f in dict.fromkeys((os.path.normpath(full), os.path.realpath(full))):
        if f == home:
            return "~"
        if f.startswith(home + os.sep):
            return "~/" + os.path.relpath(f, home)
    return None


def place(cwd, vs):
    """Each view's operands and writes as placed() reads them (files, placed_writes): as written and as the
    kernel walks them, each file a glob names, relative to the project's checkout that holds it and to
    the home directory; a copy's destination as copy_writes() places it."""
    top, seen, here, made = os.path.realpath(_paths.project(cwd)), {}, toplevel(os.path.abspath(cwd)), []

    def forms(v, a):                        # once per (directory, path): candidate dirs repeat them
        key = (v["dir"], v.get("shell"), a, a in v.get("globs", ()))
        if key not in seen:
            seen[key] = _forms(v, a)
        return seen[key]

    def named(v, a):                        # DIR/NAME as DIR/<name>'s forms, unless NAME is a link (or special)
        head, _, name = a.rpartition("/")
        where = where_of(v, a) if head and name not in ("", ".", "..") and not re.search(r"[*?$`\x01]", name) \
            and a not in v.get("globs", ()) else UNKNOWN
        full = resolve(cwd, where) if where != UNKNOWN else ""
        if not full or os.path.islink(full):
            return forms(v, a)
        return [f.replace(NAMED, name) for f in forms(v, head + "/" + NAMED)]

    def _forms(v, a):
        p = anyname(a)
        out = [placed(cwd, top, v, p, k, here) for k in (False, True)]
        out += [placed(cwd, top, v, m, k, here) for m in matches(cwd, v, a) for k in (False, True)]
        where = where_of(v, p) if not p.startswith("-") and p != STDIN else UNKNOWN
        if where != UNKNOWN and resolve(cwd, where):
            for k in (False, True):
                full = resolve(cwd, where, k)
                out += [x for x in (checkout_rel(full, top), home_rel(full)) if x and not x.startswith("..")]
        return out
    for v in vs:
        v["writes"] = list(dict.fromkeys(v["writes"] + [w for p in v.get("copies", ()) for w in copy_writes(cwd, v, p, made)]))
        v["files"] = list(dict.fromkeys(x for a in v["args"] if not a.startswith("-") for x in forms(v, a)))
        v["placed_writes"] = list(dict.fromkeys(x for w in v["writes"] for x in named(v, w)))
        made += [f for f in (resolve(cwd, w) for w in (where_of(v, w) for w in v["writes"]) if w != UNKNOWN) if f]
    return vs


def bash_hits(d, v, ctx, default):
    for b in d["_bash"]:
        if "prog" in b and not b["prog"].fullmatch(v["prog"]):
            continue
        if "args" in b and not b["args"].search(" ".join(v["args"])):
            continue
        if "files" in b and not any(b["files"].search(f) for f in v.get("files", [])):
            continue
        if b.get("_dry") and dry(v["args"]):
            continue
        if "code" in b and not b["code"].search(v.get("code") or ""):
            continue
        if "flag" in b and not any(b["flag"].fullmatch(a) or (a == STDIN and d["grant"] != "never") for a in v["args"]):
            continue
        if "env" in b and not any(b["env"].fullmatch(e) for e in v["env"]):
            continue
        if "writes" in b and not any(b["writes"].search(w) for w in v["writes"] + v.get("placed_writes", [])):
            continue
        if "config" in b and not any(b["config"].fullmatch(c) for c in v["config"]):
            continue
        if ("prog" in b or "args" in b or "flag" in b or "code" in b or "files" in b) and not v["prog"]:
            continue
        if ctx(v["dir"]).when(b.get("when"), default, v.get("branch")):
            return True
    return False


def rel_path(cwd, path):
    """(the forms a project path pattern is matched against, the path relative to the plugin or None): a
    path relative to the project (else to this checkout, a linked worktree), and the absolute path as
    written and resolved, so a suffix pattern (one that ends in /.claude/settings.json) also covers
    ~/.claude, a worktree's own and a linked file. Resolved twice: as the words say, and as the kernel
    walks it (a symlink before the `..` after it: link/../x is beside the link's target)."""
    raw = os.path.join(cwd, os.path.expanduser(path))
    given = os.path.normpath(raw)
    roots = [os.path.realpath(_paths.project(cwd))]
    forms, plug = [given], None
    for full in dict.fromkeys((os.path.realpath(given), os.path.realpath(raw))):
        r = os.path.relpath(full, roots[0])
        if r.startswith("..") and len(roots) == 1:
            roots.append(os.path.realpath(toplevel(cwd) or roots[0]))
        forms += [next((x for x in (os.path.relpath(full, t) for t in roots) if not x.startswith("..")), ".."), full,
                  checkout_rel(full, roots[0]) or "..", home_rel(full) or ".."]
        p = os.path.relpath(full, PLUGIN)
        plug = plug or (p if not p.startswith("..") else None)
    return [x for x in dict.fromkeys(forms) if not x.startswith("..")], plug


class Read(dict):
    """An input input_of already read: reading it again would decode a nested `arguments` string the tool
    itself gets as a string."""


def input_of(tool, inp):
    """The input the doors read. dsh hands a tool outside its built-ins (an MCP tool) its input as one JSON
    string, {"arguments": "{…}", "workdir": …}: it is read as the object it encodes, so an input pattern
    matches and a once-digest is the same as on a host that sends the object. Only that exact shape is
    decoded: an `arguments` string beside other fields is one more field the tool gets, and reading it as
    the input would hide the real ones (`branch: main` beside `arguments: '{"branch": "x"}'`)."""
    if isinstance(inp, Read):
        return inp
    inp = inp if isinstance(inp, dict) else {}
    if (tool not in ("Bash", "apply_patch") + EDITS and isinstance(inp.get("arguments"), str)
            and set(inp) <= {"arguments", "workdir"}):
        try:
            args = json.loads(inp["arguments"])
        except ValueError:
            return Read(inp)
        if isinstance(args, dict):
            return Read(args)
    return Read(inp)


def written(tool, inp):
    """The paths an edit writes: a Write's or an Edit's file, each file a patch adds, updates, deletes or
    moves to (apply_patch: Codex's command, OpenCode's patchText), each judged as a Write to it."""
    if tool in EDITS:
        paths = [inp.get("file_path") or inp.get("notebook_path")]
    elif tool == "apply_patch":
        paths = [x for k in ("command", "patchText") if isinstance(inp.get(k), str) for x in patched(inp[k])]
    else:
        paths = []
    return [x for x in paths if isinstance(x, str) and x]


def patched(text):
    """Each path a patch adds, updates, deletes or moves to (_patch.py: as the hosts read a header)."""
    return _patch.paths(text)


def workdir(inp, cwd):
    """Where a Bash command starts: OpenCode and dsh run a bound worker's command in tool_input.workdir (the
    payload's cwd stays the project, which still names the catalogue and the ledger), a relative one under
    the payload's cwd; a directory that is not there is UNKNOWN (its git state assumed). Anywhere else, or
    no path, the payload's cwd (""). Claude Code sends none."""
    wd = inp.get("workdir")
    if os.environ.get("AGENTIC_HOST") not in ("opencode", "dsh") or not isinstance(wd, str) or not wd:
        return ""
    wd = os.path.join(cwd, wd)          # as given: contexts() reads it as written and as the kernel walks it
    return wd if os.path.isdir(wd) else UNKNOWN


def classify(cwd, tool, inp, cat=None, assume_when=False):
    """[door] the action crosses, in catalogue order (none: two-way)."""
    cat = cat or catalogue()
    default = re.compile("(?:%s)" % cat.get("default_branch", "main|master"))
    ctx = contexts(cwd, assume_when)
    inp = input_of(tool, inp)
    vs = place(cwd, branches(cwd, views(str(inp.get("command", "")), 0, workdir(inp, cwd)))) if tool == "Bash" else []
    proj_rel, plug_rel = [], []
    for path in written(tool, inp):
        rel, plug = rel_path(cwd, path)
        proj_rel += rel
        plug_rel += [plug] if plug else []
    out = []
    for d in cat["doors"]:
        if d["grant"] == "command":
            continue
        hit = any(bash_hits(d, v, ctx, default) for v in vs)
        hit = hit or any(t["tool"].search(tool) and all(isinstance(inp.get(k), str) and r.fullmatch(inp[k])
                                                        for k, r in t["input"].items()) for t in d["_tools"])
        hit = hit or any(r.search(x) for r in d["_paths"] for x in proj_rel)
        hit = hit or any(r.search(x) for r in d["_plugin_paths"] for x in plug_rel)
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


def decisions(cwd, did):
    """The door's records, newest first by time (file order breaks a tie, so a union merge that puts an
    older row last changes nothing): the ledger's rows, plus the run log's revokes and crossings the
    ledger does not have (recorded on another branch of this checkout; they only ever close)."""
    rows, _ = M.ledger_rows(cwd)
    have = {r.get("event_id") for r in rows}
    log = [e for e in _events.read(cwd) if e.get("event") in ("door.revoked", "door.crossed")
           and e.get("event_id") not in have and isinstance(e.get("data"), dict) and e["data"].get("ledger") is True
           and isinstance(e.get("ts"), str)]
    mine = [(r["ts"], 0, i, r) for i, r in enumerate(rows)] + [(e["ts"], 1, i, e) for i, e in enumerate(log)]
    return [r for _, _, _, r in sorted(mine, key=lambda x: x[:3], reverse=True)
            if r["data"].get("subject") == subject(did)]


def _authorization(cwd, d, sha, now=None):
    if d is None or d["grant"] not in ("standing", "once"):
        return None
    now = now or M.now()
    rows = decisions(cwd, d["id"])
    used = {r["data"].get("authorization") for r in rows if r["event"] == "door.crossed"}
    # a revoke only the run log has carries the run log's clock, while a ledger row's time is clamped to
    # the newest row (a branch whose clock ran ahead can push it into the future): compare the two on the
    # run log's clock, where the authorization's copy has its real time; a tie closes
    clock = {e.get("event_id"): e.get("ts") for e in _events.read(cwd) if isinstance(e.get("ts"), str)}
    late = [r["ts"] for r in rows if r["event"] == "door.revoked" and r.get("role") == "human" and "hash" not in r]

    def opens(r):
        return None if any(t >= clock.get(r["event_id"], r["ts"]) for t in late) else r
    for r in rows:
        if r.get("role") != "human":
            continue
        if r["event"] == "door.revoked":
            return None
        if r["event"] != "door.authorized" or (r["data"].get("until") and r["data"]["until"] < now):
            continue
        if r["data"].get("action_sha256"):
            if r["data"]["action_sha256"] == sha and r["event_id"] not in used:
                return opens(r)
            continue
        if d["grant"] == "standing":
            return opens(r)
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


def verdict(cwd, tool, inp, cat=None, hits=None):
    """(exit, lines, crossings): 0 two-way or authorized, 2 a wall, 3 one-way and not authorized;
    crossings = [(door, row)] of the authorized doors."""
    sha = action_sha(tool, inp)
    lines, crossings, rc = [], [], 0
    hits = classify(cwd, tool, inp, cat) if hits is None else hits
    broken = M.ledger_problems(cwd) if any(d["grant"] in ("standing", "once") for d in hits) else []
    for d in hits:
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
    if rc == 3 and broken:
        lines.append("The mission ledger is broken, so no authorization counts until it is restored (%s; git checkout "
                     "-- %s)." % ("; ".join(broken), M.rel(cwd, M.ledger(cwd))))
    if not lines:
        lines.append("[door] two-way: decide it, record it, don't ask")
    return rc, lines, crossings


# ── the gate ─────────────────────────────────────────────────────────────────────────
def live_run(cwd, session=""):
    """A run is live (a two-way question is decided, not asked) under an approved autonomous mission or an
    autonomous run, or while this session's sprint executes: its lock is fresh and names this session,
    the latest frozen plan is approved, its sprint has not closed (stats sprint-close) and no new intent
    came after it. Intake and planning are not live: the plan's own approval is the human's question."""
    doc, _, ev = M.mission(cwd)
    if (doc and doc.get("mode") == "autonomous" and ev) or os.environ.get("AGENTIC_AUTONOMY_MODE") == "autonomous":
        return True
    lock = os.path.join(_paths.home(cwd, make=False), "state", "sprint.lock")
    try:
        ttl = int(os.environ.get("AGENTIC_SPRINT_LOCK_TTL") or 14400)
        if not os.path.isfile(lock) or time.time() - os.path.getmtime(lock) >= ttl:
            return False
        with open(lock, encoding="utf-8") as f:
            owner = f.readline().strip()
    except (OSError, ValueError):
        return True
    if owner and session and owner != session:
        return False        # another session's sprint
    plan, approved, intake = None, set(), False
    for e in _events.read(cwd):
        if e.get("event") in ("plan.created", "plan.revised"):
            plan, intake = e, False
        elif e.get("event") == "plan.approved":
            approved.add(e.get("caused_by"))
        elif e.get("event") == "intent.received":
            intake = True               # a new ask: its intake is the human's
    if not plan or plan.get("event_id") not in approved or intake:
        return False
    try:
        with open(os.path.join(_paths.home(cwd, make=False), "state", "sprints.json"), encoding="utf-8") as f:
            closed = {"S%s" % c.get("sprint") for c in json.load(f).get("closed") or [] if isinstance(c, dict)}
    except (OSError, ValueError, AttributeError):
        closed = set()
    return (plan.get("data") or {}).get("sprint") not in closed


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


def worker_text(asked):
    """A subagent has no question tool: it stops and hands the door to the supervisor, which asks."""
    return ("You are a subagent with no question tool: do not ask; stop this step and report %s in your output, "
            "so your supervisor asks the human." % ", ".join("ASK[door] %s" % d["id"] for d in asked))


def hook(cwd_default):
    if os.environ.get("SKIP_HOOKS") == "1":
        return 0
    with deadline(READ_SECONDS) as end:     # the whole decision, its lock included: past it the gate fails closed
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            raise ValueError("the hook payload is not an object")
        tool = payload.get("tool_name") or ""
        if tool in QUIET:
            return 0
        cwd = payload.get("cwd") or cwd_default
        if not os.path.isdir(cwd) or not os.path.isdir(_paths.docs(cwd)):
            return 0      # not an aloop project
        inp = input_of(tool, payload.get("tool_input"))
        session = payload.get("session_id") or ""
        # the role that hit the door: a subagent's tool call names its agent (every host's payload)
        who = {k: payload[k] for k in ("agent_id", "agent_type") if isinstance(payload.get(k), str) and payload[k]}
        cat = catalogue()
        if tool in QUESTIONS:
            why = question_problem(cwd, inp, session, cat) if live_run(cwd, session) else None
            return deny(why) if why else 0
        hits = classify(cwd, tool, inp, cat)
        if not hits:
            return 0
        with serialized(cwd, end):       # check, then record: a once-authorization is crossed once
            rc, lines, crossings = verdict(cwd, tool, inp, cat, hits)
            sha = action_sha(tool, inp)
            asked = [d for d in hits if d["grant"] in ("standing", "once") and not authorization(cwd, d, sha)] if rc else []
            disarm()                     # decided: the records below are written whole, never cut by the alarm
            if rc:
                for d in asked:
                    M.emit(cwd, "door.denied", role="gate", door=d["id"], grant=d["grant"], action_sha256=sha, tool=tool,
                           session_id=session, **who)
                reason = " ".join(l for l in lines if not l.startswith("[door]"))
                return deny(reason + (" " + worker_text(asked) if asked and "agent_type" in who else ""))
            for d, row in crossings:
                if row["data"].get("action_sha256"):   # a once-authorization is used up by its first crossing
                    M.record(cwd, "door.crossed", subject(d["id"]), "harness", door=d["id"], authorization=row["event_id"],
                             action_sha256=sha, session_id=session, **who)
                else:
                    M.emit(cwd, "door.crossed", role="harness", door=d["id"], authorization=row["event_id"],
                           action_sha256=sha, session_id=session, **who)
    return 0


READ_SECONDS = 8     # under the hook's own timeout (plugin/hooks/hooks.json, the OpenCode and dsh plugins: 15 s)


class Deadline(BaseException):
    """The decision ran past its deadline. Not an Exception, so no `except Exception` or `except OSError` in
    a step (a git call, a parse) can swallow it and let the gate fall through to an allow."""


@contextlib.contextmanager
def deadline(seconds):
    """A Deadline after `seconds` (POSIX); yields the monotonic time it ends at, which the lock waits for
    too: a gate that cannot decide in time fails closed (exit 2) before the host's hook timeout, which a host
    may treat as a non-blocking error, lets the call through."""
    end = time.monotonic() + seconds
    if not hasattr(signal, "setitimer"):
        yield end
        return

    def stop(*_):
        raise Deadline("the door gate could not decide within %g s" % seconds)
    old = signal.signal(signal.SIGALRM, stop)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield end
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def disarm():
    """Stop the deadline's alarm once the decision is made."""
    if hasattr(signal, "setitimer"):
        signal.setitimer(signal.ITIMER_REAL, 0)


@contextlib.contextmanager
def serialized(cwd, end):
    """One door decision at a time per project (an exclusive lock on .aladdin/state/doors.lock), waited for
    until the decision's deadline `end` (time.monotonic()), never forever: a lock another gate holds past
    it fails closed."""
    if fcntl is None:
        yield
        return
    path = os.path.join(_paths.home(cwd, "state"), "doors.lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= end:
                        raise Deadline from None
                    time.sleep(0.02)
        except Deadline:                # the poll's own deadline, or the alarm's
            raise Deadline("another door gate held %s past this decision's deadline" % M.rel(cwd, path)) from None
        yield
    finally:
        os.close(fd)


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
        rows, _ = M.ledger_rows(cwd)
        bad += ["ledger: %s" % b for b in M.ledger_problems(cwd)]
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
    inp = input_of(tool, inp)       # as the hook reads it, so the digest it prints is the hook's
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
    if any(os.environ.get(k) for k in AGENT_SHELL):
        print("BLOCK[doors]: this shell belongs to an agent session (%s is set): the human %ss a door in their own "
              "terminal" % (" ".join(k for k in AGENT_SHELL if os.environ.get(k)), verb), file=sys.stderr)
        return 2
    if M.ledger_block(cwd, "doors"):
        return 2
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
    except M.LedgerBroken as e:
        print("BLOCK[doors]: the ledger is broken (%s); restore it: git checkout -- %s" % (e, M.rel(cwd, M.ledger(cwd))),
              file=sys.stderr)
        return 2
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
        except (Exception, Deadline) as e:  # noqa: BLE001 — a gate that cannot decide fails closed
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
