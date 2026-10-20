"""_paths.py — Python mirror of bin/_paths.sh: THE aloop file layout.

No model picks these names and no environment variable overrides them. See _paths.sh
for the full map. <project> is the MAIN worktree, so linked worktrees share it.
"""
import os
import re
import subprocess

DEFAULTS = {
    "TOKENS_SOFT": 150000,
    "TOKENS_HARD": 250000,
    "TIME_SOFT_MIN": 20,
    "TIME_HARD_MIN": 30,
    "MAX_REPLANS": 2,
    # budget token charge: fresh (uncached input + output) | context (context in use + output)
    "TOKEN_METER": "fresh",
    # sprint aggregate envelope (every metered attempt in the sprint, TOKEN_METER charge):
    # soft = rollover warning; hard = admission closes, not-started tasks carry over
    "SPRINT_TOKENS_SOFT": 4000000,
    "SPRINT_TOKENS_HARD": 6000000,
    # time budget mode: fixed (TIME_*_MIN above) | auto (one project timebox, bin/timebox)
    "TIME_MODE": "fixed",
    # structural (LSP) backend for `targets refs|callers|callees` (ROADMAP §4.6): a command
    "LSP_COMMAND": "",
    # sprint boundary: rotate (fresh session) | compact (only if the host advertises it)
    "CONTEXT_BOUNDARY": "rotate",
    "CONTEXT_WINDOW": 200000,
    # optional cross-project impact provider (ROADMAP §4.8): a command, empty = none
    "IMPACT_PROVIDER": "",
    # a ROUTE (strongest | strong | economical, resolved per host by bin/models) or a pinned id
    "MODEL_PLANNING": "strongest",
    "MODEL_WORKER": "strong",
    "GITIGNORE": "yes",
    "TRACK_DOCS": "no",
    "COMMIT_AUTHOR_NAME": "",
    "COMMIT_AUTHOR_EMAIL": "",
    "CLAUDE_COAUTHOR": "no",
    # Other hosts (aloop install codex|opencode|dsh). Empty = sensible default:
    # Codex -> the session's model (Claude model ids don't exist there) at these efforts;
    # OpenCode -> anthropic/<MODEL_PLANNING|MODEL_WORKER>; dsh -> the route's DeepSeek id.
    "MODEL_PLANNING_CODEX": "",
    "MODEL_WORKER_CODEX": "",
    "EFFORT_PLANNING_CODEX": "high",
    "EFFORT_WORKER_CODEX": "medium",
    "MODEL_PLANNING_OPENCODE": "",
    "MODEL_WORKER_OPENCODE": "",
    "MODEL_PLANNING_DSH": "",
    "MODEL_WORKER_DSH": "",
}
NUMERIC = {"TOKENS_SOFT", "TOKENS_HARD", "TIME_SOFT_MIN", "TIME_HARD_MIN", "MAX_REPLANS",
           "SPRINT_TOKENS_SOFT", "SPRINT_TOKENS_HARD", "CONTEXT_WINDOW"}
SETTING = re.compile(r"^\s*(?:export\s+)?(?:AGENTIC|ALADDIN)_([A-Z_]+)\s*=\s*['\"]?([^'\"#\n]*?)['\"]?\s*(?:#.*)?$")
# ALADDIN_<KEY> is the new spelling of every AGENTIC_<KEY> environment override (ROADMAP M1):
# both are read, the old one wins when both are set; scripts keep using the AGENTIC_ name.
ENV_KEYS = ("HOST", "CWD", "NO_AUTOINSTALL", "NO_BUDGET", "LSP_COMMAND", "IMPACT_PROVIDER", "SELFCHECK", "WORKTREE",
            "AUTONOMY_MODE", "STUCK_BLOCKS", "SPRINT_LOCK_TTL", "TRANSCRIPTS_KEEP", "AUTO_TRIGGER")
for _k in ENV_KEYS:
    if not os.environ.get("AGENTIC_" + _k) and os.environ.get("ALADDIN_" + _k):
        os.environ["AGENTIC_" + _k] = os.environ["ALADDIN_" + _k]


def env(key, default=None):
    """An environment override, AGENTIC_<key> or ALADDIN_<key>."""
    return os.environ.get("AGENTIC_" + key) or os.environ.get("ALADDIN_" + key) or default


# the run-data dir (ROADMAP M1): .aladdin/, or .agentic/ where a project still has one
# (read-old fallback; `aloop layout migrate` moves it)
NEW_HOME, OLD_HOME = ".aladdin", ".agentic"
RUN_DIRS = ("transcripts", "budget", "ledger", "logs", "state", "host", "worktrees")
GUARD = "# aloop run data (transcripts, budgets, ledger, logs, state) — never committed\n*\n!.gitignore\n!config.yaml\n"
# a worker worktree's marker dir ignores itself too: nothing there may reach the task's diff
WT_GUARD = "# aloop worktree markers — never committed\n*\n"


def under(path, root):
    """True when path is root or inside it, compared after resolving symlinks: on macOS a temp
    dir is /var/... but its realpath (what `budget contract` records) is /private/var/..."""
    if not path or not root:
        return False
    p, r = os.path.realpath(path), os.path.realpath(root).rstrip("/")
    return p == r or p.startswith(r + "/")


def home_name(root):
    if any(os.path.isdir(os.path.join(root, NEW_HOME, d)) for d in RUN_DIRS):
        return NEW_HOME
    return OLD_HOME if os.path.isdir(os.path.join(root, OLD_HOME)) else NEW_HOME


def home_guard(root):
    """.aladdin/.gitignore — config.yaml is committed, everything else there never is."""
    d = os.path.join(root, NEW_HOME)
    if os.path.isdir(d) and not os.path.isfile(os.path.join(d, ".gitignore")):
        try:
            with open(os.path.join(d, ".gitignore"), "w") as f:
                f.write(GUARD)
        except OSError:
            pass


def _git(cwd, *args):
    try:
        return subprocess.run(("git", "-C", cwd) + args, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def project(cwd=None):
    cwd = cwd or os.getcwd()
    for line in _git(cwd, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            return line[len("worktree "):]
    return _git(cwd, "rev-parse", "--show-toplevel") or cwd


def home(cwd=None, sub=None, make=True):
    root = project(cwd)
    base = os.path.join(root, home_name(root))
    d = os.path.join(base, sub) if sub else base
    if make:
        os.makedirs(d, exist_ok=True)
        home_guard(root)
    return d


def contract_path(cwd, task_id):
    """The task contract <run-data>/state/tasks/<TASK_ID>.env: the resolved home first, then the
    other one (a supervisor that wrote the literal legacy path is still found). The resolved
    path is returned even when no file exists, so callers can name it."""
    root = project(cwd)
    first = os.path.join(root, home_name(root), "state", "tasks", task_id + ".env")
    for f in (first,) + tuple(os.path.join(root, h, "state", "tasks", task_id + ".env") for h in (NEW_HOME, OLD_HOME)):
        if os.path.isfile(f):
            return f
    return first


# Worktree-local markers (task.env copy, budget-exceeded, scaffold-symbols): written to
# .aladdin/ in the worker's worktree (guarded like the main tree's), or .agentic/ in a
# legacy project; read from either, so hosts and older sprints that still write
# .agentic/<marker> are understood.
MARKERS = ("task.env", "budget-exceeded", "scaffold-symbols")


def marker_dir(top, make=True):
    """Where a worktree's markers are written."""
    if home_name(project(top)) == OLD_HOME:
        d = os.path.join(top, OLD_HOME)
    else:
        d = os.path.join(top, NEW_HOME)
    if make:
        try:
            os.makedirs(d, exist_ok=True)
            if d.endswith(NEW_HOME) and not os.path.isfile(os.path.join(d, ".gitignore")):
                with open(os.path.join(d, ".gitignore"), "w") as f:
                    f.write(WT_GUARD)
        except OSError:
            pass
    return d


def marker(top, name):
    """The marker's path: an existing .aladdin/<name> or .agentic/<name>, else where it would be written."""
    for h in (NEW_HOME, OLD_HOME):
        f = os.path.join(top, h, name)
        if os.path.isfile(f):
            return f
    return os.path.join(marker_dir(top, make=False), name)


def home_rel(cwd=None, *parts):
    """A run-data path relative to the project (for refs and messages), e.g. .aladdin/logs/events.jsonl."""
    return "/".join((home_name(project(cwd)),) + parts)


def docs(cwd=None):
    return os.path.join(project(cwd), "docs", "agents")


def defaults_file(cwd=None):
    return os.path.join(docs(cwd), "defaults.md")


def next_file(cwd=None):
    return os.path.join(docs(cwd), "NEXT.md")


def settings(cwd=None):
    """defaults.md settings over built-in defaults (legacy docs/agents/agentic.conf too)."""
    vals = dict(DEFAULTS)
    for path in (os.path.join(docs(cwd), "agentic.conf"), defaults_file(cwd)):
        try:
            with open(path) as f:
                for line in f:
                    m = SETTING.match(line)
                    if not m or m.group(1) not in DEFAULTS:
                        continue
                    k, v = m.group(1), m.group(2).strip()
                    if k in NUMERIC:
                        if v.isdigit():
                            vals[k] = int(v)
                    else:
                        vals[k] = v
        except OSError:
            pass
    import _config  # the portable config (.aladdin/config.yaml) wins when it exists
    for k, v in (_config.load(cwd) or {}).items():
        if k in DEFAULTS:
            if k in NUMERIC:
                if str(v).isdigit():
                    vals[k] = int(v)
            else:
                vals[k] = v
    return vals
