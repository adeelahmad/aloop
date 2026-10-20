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
# the paths a patch adds, updates, deletes or moves to (host-adapter's PATCH_PATHS)
PATCH = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$|^\*\*\* Move to: (.+)$", re.M)
TAG = re.compile(r"\[door:([^\]\s]*)\]")
WRAPPERS = {"sudo", "doas", "env", "nohup", "time", "command", "exec", "nice", "timeout", "stdbuf", "caffeinate", "ionice"}
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
DEPTH = 8               # nested shells, evals, substitutions and find -exec read; deeper is unreadable (fails closed)


def render(parts):
    """A word as the catalogue's patterns read it: literal text as is, a variable `$NAME` (`~` stays `~`),
    a substitution `$(…)` (its commands are read on their own), and `*` for what only running it tells
    (a glob, arithmetic, a positional parameter)."""
    out = []
    for p in parts:
        if isinstance(p, str):
            out.append(p)
        elif p[0] == "var":
            out.append("~" if p[1] == "HOME" and p[2] is None else "$" + p[1])
        elif p[0] == "cmd":
            out.append(TOP if " ".join(p[1].split()) == "git rev-parse --show-toplevel" else "$(…)")
        elif p[0] == "proc":
            out.append("<(…)")
        else:
            out.append("*")
    return "".join(out)


def chdir(cur, arg):
    """The directory a `cd ARG` (or `git -C ARG`) moves to, relative to the call's cwd ("" = it):
    `$PWD` is where it is, `$(git rev-parse --show-toplevel)` the checkout's top, `$CLAUDE_PROJECT_DIR`
    the project's."""
    if arg is not None and arg.startswith(TOP):
        return arg
    for var, base in (("$PWD", cur), ("$CLAUDE_PROJECT_DIR", os.environ.get("CLAUDE_PROJECT_DIR") or UNKNOWN)):
        if arg is not None and re.match(re.escape(var) + r"(?:/|$)", arg):
            return UNKNOWN if base == UNKNOWN else os.path.join(base, arg[len(var) + 1:]) if arg[len(var):] else base
    if cur == UNKNOWN or arg is None or arg == "-" or re.search(r"[$`*?]", arg):
        return UNKNOWN
    arg = os.path.expanduser(arg or "~")
    return arg if os.path.isabs(arg) else os.path.join(cur, arg)


def views(cmd, depth=0, cur=""):
    """Every (prog, args, env, writes, dir; a python's code) a command line runs, read by the shell reader
    the scope gate uses (plugin/bin/_shellread.py): one per simple command, one per wrapper (sudo, env,
    timeout, xargs, find -exec, …) around it, the commands inside `sh -c`, `eval`, a script a shell runs, a
    command substitution, and the stdin that feeds a shell or python (the last heredoc, here-string or
    `<` on fd 0, else the pipe, into a subshell too), each with the directory it runs in (`cd DIR`,
    `pushd`/`popd`, `git -C DIR`; a subshell's `cd` stays in the subshell). Deeper than DEPTH is a
    ValueError: a gate that cannot read the whole command fails closed."""
    if depth > DEPTH:
        raise ValueError("the command nests shells, evals or substitutions deeper than the door gate reads (%d)" % DEPTH)
    out, stack, dirs, fed, group = [], [], [], None, None
    for c in R.read(cmd):
        if c in ("(", ")"):
            if c == "(":                    # a pipe into a subshell feeds the commands inside it
                stack.append((cur, group))
                group = fed if fed is not None else group
            elif stack:
                cur, group = stack.pop()
            fed = None
            continue
        for sub in c.subs:
            out += views(sub, depth + 1, cur)
        vs = simple(c, depth, cur)
        for v in vs:                        # what ended it (`&&`: the next runs only if it succeeded)
            v.setdefault("end", c.end)
            v.setdefault("depth", depth)
        prog = vs[0]["prog"] if vs else ""
        if prog in ("cd", "pushd"):
            if prog == "pushd":
                dirs.append(cur)
            cur = chdir(cur, next((a for a in vs[0]["args"] if not a.startswith("-")), ""))
        elif prog == "popd":
            cur = dirs.pop() if dirs else UNKNOWN
        stdin = fed if fed is not None else group
        for op, fd, w in c.redirs:          # the last redirect of fd 0 wins, left to right
            if fd in ("", "0") and op in ("<<", "<<-", "<<<", "<", "<&", "<>"):
                stdin = w.body if op in ("<<", "<<-") else render(w.parts) if op == "<<<" else None
        if stdin is not None and vs:
            out += feed(vs, stdin, depth)
        fed = stdout(vs, stdin) if c.end in ("|", "|&") else None
        out += vs
    return out


def stdout(vs, stdin):
    """The text a command writes to the pipe, when the gate can know it: echo's and printf's words, and
    cat or tee passing its own heredoc, here-string or pipe through."""
    if not vs:
        return None
    v = vs[-1]
    args = [a for a in v["args"] if a != STDIN]
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
    out = []
    for v in vs:
        if "code" in v:                     # python: its stdin is code it may run (exec(sys.stdin.read()))
            v["code"] = (v["code"] + "\n" + text).lstrip("\n")
        elif reads_stdin(v):
            out += [dict(x, env=x["env"] + v["env"]) for x in views(text, depth + 1, v["dir"])]
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
    "exec": {"-a"}, "time": {"-f", "-o", "--format", "--output"}}


def simple(c, depth, cur=""):
    """The views of one simple command (a _shellread.Cmd)."""
    env = ["%s=%s" % (n, render(v)) for n, v in c.assigns]
    writes = [render(w.parts) for op, _, w in c.redirs
              if op in WRITES or (op == ">&" and not re.fullmatch(r"\d+|-", w.text() or "0"))]
    return chain([render(w.parts) for w in c.words], env, writes, depth, cur)


def chain(words, env, writes, depth, cur):
    """The views of a command's words: the program, then each program a wrapper runs."""
    out, config, stdin_args = [], [], False
    while words and ASSIGN.match(words[0]):
        env.append(words.pop(0))
    while words:
        if words[0] == "--":    # the end of a wrapper's options (sudo -u root -- CMD, time -p -- CMD)
            words = words[1:]
            continue
        prog = os.path.basename(words[0])
        rest = words[1:]
        code = None
        if PYTHON.fullmatch(prog):          # python [options] (-c CODE | -m MOD | SCRIPT | -) …
            j, code = 0, ""
            while j < len(rest) and rest[j].startswith("-") and rest[j] != "-":
                m = re.match(r"^-[bBdEhiIOqsSuvVxP]*([cm])(.*)$", rest[j])
                if m:
                    if m.group(1) == "c":
                        code = m.group(2) or (rest[j + 1] if j + 1 < len(rest) else "")
                    break
                j += 2 if rest[j] in PY_PAIRED else 1
            else:
                if j < len(rest) and rest[j] != "-" and rest[j] not in STDIN_FILES:
                    prog, rest, code = os.path.basename(rest[j]), rest[j + 1:], None   # python SCRIPT: the script runs
        if prog == "tee":
            writes = writes + [w for w in rest if not w.startswith("-")]
        if prog == "git":
            while rest and rest[0].startswith("-"):
                o, v = rest[0], rest[1] if len(rest) > 1 else None
                if o == "-C":
                    cur = chdir(cur, v)
                elif o in ("-c", "--config-env") and v:
                    config.append(v)
                elif o.startswith(("--git-dir", "--work-tree")):
                    cur = UNKNOWN
                elif o.startswith("--config-env="):
                    config.append(o.split("=", 1)[1])
                rest = rest[2:] if o in GIT_PAIRED else rest[1:]
        if prog == "command" and rest[:1] in (["-v"], ["-V"]):
            rest = []           # `command -v X` looks X up; it runs nothing
        out.append({"prog": prog, "args": rest + ([STDIN] if stdin_args is True else []), "env": list(env),
                    "writes": list(writes), "dir": cur, "config": list(config)})
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
                out += [dict(v, env=v["env"] + env) for v in views(script, depth + 1, cur)]
            elif script is not None and j < len(rest):
                words = rest[j + 1:] if rest[j] in ("--", "-") else rest[j:]    # bash SCRIPT ARGS: the script runs
                if words and words[0] != "-" and words[0] not in STDIN_FILES:
                    continue
            break
        if prog == "eval":
            out += views(" ".join(rest), depth + 1, cur)
            break
        if prog == "find":
            j = 0
            while j < len(rest):
                if rest[j] in ("-exec", "-execdir", "-ok", "-okdir"):
                    inner = list(itertools.takewhile(lambda w: w not in (";", "+"), rest[j + 1:]))
                    if inner:
                        if depth >= DEPTH:
                            raise ValueError("find -exec nests deeper than the door gate reads (%d)" % DEPTH)
                        out += chain(inner, [], [], depth + 1, cur)
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
                if o in ("-u", "--unset", "-C", "--chdir"):
                    words = words[2:]
                    continue
                if ASSIGN.match(o):
                    env.append(o)
                words = words[1:]
        else:               # sudo -u USER …, timeout -s KILL 5 …, nice -n 5 …: skip options and their values
            paired = WRAPPER_PAIRED.get(prog, ())
            while words and words[0].startswith("-") and words[0] != "--":
                words = words[2:] if words[0] in paired else words[1:]
            if prog == "timeout" and words and words[0] != "--":
                words = words[1:]   # the duration
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
        return made[where]
    return at


@functools.lru_cache(maxsize=8)
def toplevel(cwd):
    r = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def resolve(cwd, where):
    """The absolute directory of a view's `dir` (TOP: the checkout's top, from git; "" when there is none)."""
    if where.startswith(TOP):
        if not toplevel(cwd):
            return ""
        where = toplevel(cwd) + where[len(TOP):]
    return os.path.normpath(os.path.join(cwd, os.path.expanduser(where)))


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
            at = chdir(v["dir"], ops[0]) if ops else UNKNOWN
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


def placed(cwd, top, v, path):
    """A path a command names as its patterns read it: resolved where the command runs, relative to the
    project `top` or this checkout when inside one (`cd docs/agents && … mission/ledger.jsonl` is the
    ledger; /tmp/x/.aladdin is not the run's home). Where the gate cannot resolve it, a name a guarded
    record ends with is that record."""
    if path.startswith("-") or path in (STDIN, "") or "\n" in path or len(path) > 4096:
        return path
    if re.match("(?:%s|\\$PWD|\\$CLAUDE_PROJECT_DIR)(?:/|$)" % re.escape(TOP), path):
        where = chdir(v["dir"], path)
    elif v["dir"] == UNKNOWN or re.search(r"[$`]", path):
        where = UNKNOWN
    else:
        where = path if os.path.isabs(path) or path.startswith("~") or not v["dir"] else v["dir"] + "/" + path
    if where == UNKNOWN:
        x = os.path.normpath(path)
        return next((g for g in GUARDED if not os.path.isabs(x) and (g == x or g.endswith("/" + x))), path)
    full = resolve(cwd, where)
    if not full:
        return path
    for root in (top, toplevel(os.path.abspath(cwd))):     # the project, else this checkout (a worktree)
        r = os.path.relpath(os.path.realpath(full), os.path.realpath(root)) if root else ".."
        if not r.startswith(".."):
            return r
    return full


def place(cwd, vs):
    """Each view's operands and writes as placed() reads them (files, placed_writes)."""
    top = os.path.realpath(_paths.project(cwd))
    for v in vs:
        v["files"] = [placed(cwd, top, v, a) for a in v["args"] if not a.startswith("-")]
        v["placed_writes"] = [placed(cwd, top, v, w) for w in v["writes"]]
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
    path relative to the project, and the absolute path as written and resolved, so a suffix pattern
    (one that ends in /.claude/settings.json) also covers ~/.claude, a worktree's own and a linked file."""
    given = os.path.normpath(os.path.join(cwd, os.path.expanduser(path)))
    full = os.path.realpath(given)
    r = os.path.relpath(full, os.path.realpath(_paths.project(cwd)))
    plug = os.path.relpath(full, PLUGIN)
    return [x for x in (r, given, full) if not x.startswith("..")], (plug if not plug.startswith("..") else None)


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
        paths = [(a or b).strip() for k in ("command", "patchText") if isinstance(inp.get(k), str)
                 for a, b in PATCH.findall(inp[k])]
    else:
        paths = []
    return [x for x in paths if isinstance(x, str) and x]


def workdir(inp):
    """Where a Bash command starts: OpenCode and dsh run a bound worker's command in tool_input.workdir (the
    payload's cwd stays the project, which still names the catalogue and the ledger) when it is an absolute
    directory that exists; anywhere else the payload's cwd (""). Claude Code sends none."""
    wd = inp.get("workdir")
    if os.environ.get("AGENTIC_HOST") in ("opencode", "dsh") and isinstance(wd, str) and os.path.isabs(wd) \
            and os.path.isdir(wd):
        return wd
    return ""


def classify(cwd, tool, inp, cat=None, assume_when=False):
    """[door] the action crosses, in catalogue order (none: two-way)."""
    cat = cat or catalogue()
    default = re.compile("(?:%s)" % cat.get("default_branch", "main|master"))
    ctx = contexts(cwd, assume_when)
    inp = input_of(tool, inp)
    vs = place(cwd, branches(cwd, views(str(inp.get("command", "")), 0, workdir(inp)))) if tool == "Bash" else []
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


@contextlib.contextmanager
def deadline(seconds):
    """A TimeoutError after `seconds` (POSIX); yields the monotonic time it ends at, which the lock waits for
    too: a gate that cannot decide in time fails closed (exit 2) before the host's hook timeout, which a host
    may treat as a non-blocking error, lets the call through."""
    end = time.monotonic() + seconds
    if not hasattr(signal, "setitimer"):
        yield end
        return

    def stop(*_):
        raise TimeoutError("the door gate could not decide within %g s" % seconds)
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
                        raise TimeoutError from None
                    time.sleep(0.02)
        except TimeoutError:            # the poll's own deadline, or the alarm's
            raise TimeoutError("another door gate held %s past this decision's deadline" % M.rel(cwd, path)) from None
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
