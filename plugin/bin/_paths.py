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
    # optional cross-project impact provider (ROADMAP §4.8): a command, empty = none
    "IMPACT_PROVIDER": "",
    "MODEL_PLANNING": "claude-opus-5-5",
    "MODEL_WORKER": "sonnet",
    "GITIGNORE": "yes",
    "TRACK_DOCS": "no",
    "COMMIT_AUTHOR_NAME": "",
    "COMMIT_AUTHOR_EMAIL": "",
    "CLAUDE_COAUTHOR": "no",
    # Other hosts (aloop install codex|opencode). Empty = sensible default:
    # Codex -> the session's model (Claude model ids don't exist there) at these efforts;
    # OpenCode -> anthropic/<MODEL_PLANNING|MODEL_WORKER>.
    "MODEL_PLANNING_CODEX": "",
    "MODEL_WORKER_CODEX": "",
    "EFFORT_PLANNING_CODEX": "high",
    "EFFORT_WORKER_CODEX": "medium",
    "MODEL_PLANNING_OPENCODE": "",
    "MODEL_WORKER_OPENCODE": "",
}
NUMERIC = {"TOKENS_SOFT", "TOKENS_HARD", "TIME_SOFT_MIN", "TIME_HARD_MIN", "MAX_REPLANS",
           "SPRINT_TOKENS_SOFT", "SPRINT_TOKENS_HARD"}
SETTING = re.compile(r"^\s*(?:export\s+)?AGENTIC_([A-Z_]+)\s*=\s*['\"]?([^'\"#\n]*?)['\"]?\s*(?:#.*)?$")


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
    d = os.path.join(project(cwd), ".agentic", sub) if sub else os.path.join(project(cwd), ".agentic")
    if make:
        os.makedirs(d, exist_ok=True)
    return d


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
    return vals
