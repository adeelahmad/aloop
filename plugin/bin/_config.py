"""_config.py — the portable project config, .aladdin/config.yaml (ROADMAP §4.9).

One file, sections budgets · context · semantic · models · provenance · memory · git, that
every host reads the same way. It replaces docs/agents/defaults.md as the human's file: when
it exists it is authoritative, and defaults.md is RENDERED from it (`config sync`, run by
SessionStart and `aloop-init --apply`) so every reader that still parses AGENTIC_* lines —
the bash gates included — sees the same values. Parsed with a small, dependency-free YAML
subset: `section:` headers, `  key: value` lines, `#` comments, quoted or bare scalars.
"""
import os
import re

import _paths

FILE = os.path.join(".aladdin", "config.yaml")
SCHEMA = "aladdin.config/1"
# section -> yaml key -> AGENTIC_* setting
MAP = {
    "budgets": {"tokens_soft": "TOKENS_SOFT", "tokens_hard": "TOKENS_HARD", "time_soft_min": "TIME_SOFT_MIN",
                "time_hard_min": "TIME_HARD_MIN", "max_replans": "MAX_REPLANS", "token_meter": "TOKEN_METER",
                "sprint_tokens_soft": "SPRINT_TOKENS_SOFT", "sprint_tokens_hard": "SPRINT_TOKENS_HARD",
                "time_mode": "TIME_MODE"},
    "context": {"boundary": "CONTEXT_BOUNDARY"},
    "semantic": {"lsp_command": "LSP_COMMAND"},
    "models": {"planning": "MODEL_PLANNING", "worker": "MODEL_WORKER",
               "planning_codex": "MODEL_PLANNING_CODEX", "worker_codex": "MODEL_WORKER_CODEX",
               "effort_planning_codex": "EFFORT_PLANNING_CODEX", "effort_worker_codex": "EFFORT_WORKER_CODEX",
               "planning_opencode": "MODEL_PLANNING_OPENCODE", "worker_opencode": "MODEL_WORKER_OPENCODE"},
    "provenance": {"impact_provider": "IMPACT_PROVIDER"},
    "memory": {},
    "git": {"gitignore": "GITIGNORE", "track_docs": "TRACK_DOCS", "commit_author_name": "COMMIT_AUTHOR_NAME",
            "commit_author_email": "COMMIT_AUTHOR_EMAIL", "claude_coauthor": "CLAUDE_COAUTHOR"},
}
BACK = {v: (s, k) for s, m in MAP.items() for k, v in m.items()}
LINE = re.compile(r"^(\s*)([A-Za-z_][\w-]*)\s*:\s*(.*?)\s*$")


def path(cwd=None):
    return os.path.join(_paths.project(cwd), FILE)


def _scalar(v):
    if v.startswith(("'", '"')) and len(v) >= 2 and v[-1] == v[0]:
        return v[1:-1]
    v = re.sub(r"\s+#.*$", "", v).strip()
    return v


def parse(text):
    """Nested mappings of string scalars: {section: {key: value | {…}}}; top-level scalars
    live under ''. Indentation nests; a key with no value opens a mapping."""
    root = {"": {}}
    stack = [(-1, root)]
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = LINE.match(raw)
        if not m:
            raise ValueError("config.yaml: cannot read line %r" % raw)
        indent, key, val = len(m.group(1)), m.group(2), m.group(3)
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            raise ValueError("config.yaml: unexpected indentation at %r" % raw)
        parent = stack[-1][1]
        if val == "":
            parent[key] = {}
            stack.append((indent, parent[key]))
        elif parent is root:
            root[""][key] = _scalar(val)
        else:
            parent[key] = _scalar(val)
    return root


def load(cwd=None):
    """The yaml as settings ({AGENTIC key: value}) or None when there is no config.yaml."""
    try:
        with open(path(cwd)) as f:
            doc = parse(f.read())
    except OSError:
        return None
    vals = {}
    for sec, m in MAP.items():
        for k, v in (doc.get(sec) or {}).items():
            if k in m and not isinstance(v, dict):
                vals[m[k]] = v
    # per-role budget profiles: budgets.roles.<role>: {tokens_soft: N, ...} -> AGENTIC_BUDGET_<ROLE>
    for role, prof in ((doc.get("budgets") or {}).get("roles") or {}).items():
        if isinstance(prof, dict):
            vals["BUDGET_" + role.upper().replace("-", "_")] = " ".join("%s=%s" % kv for kv in sorted(prof.items()))
    return vals


def check(cwd=None):
    """Problems in config.yaml, as strings (empty = fine)."""
    out = []
    try:
        with open(path(cwd)) as f:
            doc = parse(f.read())
    except OSError:
        return ["no %s" % FILE]
    except ValueError as e:
        return [str(e)]
    if doc[""].get("schema") != SCHEMA:
        out.append("schema must be %s" % SCHEMA)
    for sec, keys in doc.items():
        if sec == "":
            continue
        if sec not in MAP:
            out.append("unknown section %r" % sec)
            continue
        for k, v in keys.items():
            if isinstance(v, dict):
                if not (sec == "budgets" and k == "roles"):
                    out.append("%s.%s: nested keys are not allowed here" % (sec, k))
                continue
            if k not in MAP[sec]:
                out.append("%s.%s: unknown key" % (sec, k))
            elif MAP[sec][k] in _paths.NUMERIC and not str(v).isdigit():
                out.append("%s.%s: must be a whole number" % (sec, k))
    return out


def render(vals):
    """config.yaml text from settings (every known key, grouped by section)."""
    lines = ["# aloop project config (aladdin.config/1) — the human's file; docs/agents/defaults.md",
             "# is rendered from it by `aloop config sync`. Edit this, never that.",
             "schema: %s" % SCHEMA, ""]
    for sec, m in MAP.items():
        lines.append("%s:" % sec)
        for k, key in m.items():
            v = vals.get(key, _paths.DEFAULTS.get(key, ""))
            lines.append("  %s: %s" % (k, _yaml_scalar(v)))
        roles = {k[len("BUDGET_"):].lower().replace("_", "-"): v for k, v in vals.items() if k.startswith("BUDGET_")}
        if sec == "budgets" and roles:
            lines.append("  roles:")
            for role, spec in sorted(roles.items()):
                lines.append("    %s:" % role)
                for kv in str(spec).split():
                    if "=" in kv:
                        a, b = kv.split("=", 1)
                        lines.append("      %s: %s" % (a, b))
        lines.append("")
    return "\n".join(lines)


def _yaml_scalar(v):
    s = str(v)
    if s == "" or s != s.strip() or any(c in s for c in ":#'\"") or s.lower() in ("yes", "no", "true", "false", "null"):
        return '"%s"' % s.replace("\\", "\\\\").replace('"', '\\"')
    return s
