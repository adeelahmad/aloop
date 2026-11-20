"""_userspace.py — the user-space layers of the loop (ROADMAP M12).

aloop is split like an operating system:

  kernel      the gate engine, the gates (scope, door, plan, final...), the audit log and the rule that
              what a project declares runs and is logged. It lives in the plugin and is never
              overridden: a project copy of a gate is reference only.
  user space  the loop's opinions: the agents (roles and prompts) and the skills. TDD is one opinion
              (the default "software" profile). A project ejects them into git and changes them freely.

Layers, highest first (the first that holds a file wins; every apply logs which one was used):

  home     $ALOOP_HOME/projects/<name>-<hash>/<rel>   personal, per project, never in git (CI and
                                                      teammates cannot see it)
  repo     docs/agents/userspace/<rel>                in git: evolves with the project, reverts with it
  plugin   <plugin>/<source of rel>                   the shipped default

<rel> is `agents/<role>.md` or `skills/<skill>/<file>` (effective) or `reference/<plugin path>`
(reference: the pipeline sources, hooks.json and the gate scripts, copied so a user can read
what the kernel does; editing them changes nothing).

Upgrades are three-way: the repo layer keeps `.base/<rel>`, the plugin text it was last synced
from. Untouched files follow the plugin; an edited file is never overwritten: the new plugin text
is written beside it (`<rel>.plugin-new`) for `diff` / `merge` / `accept` / `reset`.
"""
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import _paths  # noqa: E402

DIR = "userspace"                      # docs/agents/userspace/
MANIFEST = "manifest.json"
BASE = ".base"
NEW = ".plugin-new"
MERGE = ".merge"
MARK = "aloop-userspace (rendered by `aloop userspace apply` from %s; edit that file, not this one)"
MARK_RE = re.compile(r"aloop-userspace \(rendered by `aloop userspace apply`")
REFERENCE_NOTE = "reference/"


def sha(text):
    return hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()


def read(path):
    try:
        with open(path, encoding="utf-8", errors="surrogateescape") as f:
            return f.read()
    except OSError:
        return None


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(text)


def plugin_version():
    try:
        return json.load(open(os.path.join(ROOT, ".claude-plugin", "plugin.json")))["version"]
    except (OSError, ValueError, KeyError):
        return "0"


# ── what the plugin ships ─────────────────────────────────────────────
def roles():
    """The kernel's role names: the plugin's agents. A user-space agent can re-shape a role, never add one
    (a new name would run under no gate: the hooks match the plugin's role names)."""
    d = os.path.join(ROOT, "agents")
    return sorted(p[:-3] for p in os.listdir(d) if p.endswith(".md"))


def plugin_files():
    """{rel: (plugin path, kind)}; kind is "effective" or "reference"."""
    out = {}
    for r in roles():
        out["agents/%s.md" % r] = (os.path.join(ROOT, "agents", r + ".md"), "effective")
    sk = os.path.join(ROOT, "skills")
    for s in sorted(os.listdir(sk)):
        for fn in ("SKILL.md", "reference.md"):
            p = os.path.join(sk, s, fn)
            if os.path.isfile(p):
                out["skills/%s/%s" % (s, fn)] = (p, "effective")
    pl = os.path.join(ROOT, "pipeline")
    for dp, _, fns in os.walk(pl):
        for fn in sorted(fns):
            p = os.path.join(dp, fn)
            out[REFERENCE_NOTE + os.path.relpath(p, ROOT)] = (p, "reference")
    out[REFERENCE_NOTE + "hooks/hooks.json"] = (os.path.join(ROOT, "hooks", "hooks.json"), "reference")
    for fn in sorted(os.listdir(HERE)):
        if fn.startswith("gate-"):
            out[REFERENCE_NOTE + "bin/" + fn] = (os.path.join(HERE, fn), "reference")
    return out


# ── where the layers are ──────────────────────────────────────────────
def repo_dir(cwd=None):
    return os.path.join(_paths.docs(cwd), DIR)


def aloop_home():
    return os.environ.get("ALOOP_HOME") or os.path.join(os.path.expanduser("~"), ".aloop")


def home_dir(cwd=None):
    root = os.path.realpath(_paths.project(cwd))
    name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(root)) or "project"
    return os.path.join(aloop_home(), "projects", "%s-%s" % (name, sha(root)[:8]))


def manifest(cwd=None):
    try:
        m = json.load(open(os.path.join(repo_dir(cwd), MANIFEST)))
        return m if isinstance(m, dict) and isinstance(m.get("files"), dict) else None
    except (OSError, ValueError):
        return None


def save_manifest(cwd, m):
    write(os.path.join(repo_dir(cwd), MANIFEST), json.dumps(m, indent=2, sort_keys=True) + "\n")


def resolve(cwd, rel):
    """(layer, path) of the copy that wins for an effective rel: home > repo > plugin."""
    pf = plugin_files().get(rel)
    if pf and pf[1] == "effective":
        h = os.path.join(home_dir(cwd), rel)
        if os.path.isfile(h):
            return "home", h
        r = os.path.join(repo_dir(cwd), rel)
        if os.path.isfile(r):
            return "repo", r
    return "plugin", pf[0] if pf else None


# ── eject / status / upgrade ──────────────────────────────────────────
README = """# User space

The loop's opinions, ejected from the aloop plugin %s so this project can change them.
Change anything under `agents/` and `skills/`: it takes effect on the next session (or
`aloop userspace apply`). TDD is the default opinion, not a rule.

What you cannot change from here is the **kernel**: the gates, the audit log and the
hooks that run them. `reference/` holds a copy of them to read; editing it does nothing.
Add checks of your own with your host's hooks (for Claude Code, `.claude/settings.json`).

Layers, highest first: your home copy (`aloop userspace path --home`), this folder, the plugin.

    aloop userspace status          what is customized, stale or in conflict
    aloop userspace diff <rel>      your copy against the plugin's
    aloop userspace merge <rel>     three-way merge a plugin upgrade into your copy
    aloop userspace accept <rel>    keep your copy as resolved against the new plugin text
    aloop userspace reset <rel>     go back to the plugin's copy

`.base/` and `manifest.json` are what the upgrade compares with: commit them too.
"""


def eject(cwd=None):
    """Copy every plugin file the repo layer does not have yet. Never overwrites a file. Returns the rels written."""
    d = repo_dir(cwd)
    m = manifest(cwd) or {"plugin_version": plugin_version(), "files": {}}
    done = []
    for rel, (src, kind) in sorted(plugin_files().items()):
        if rel in m["files"]:
            continue
        text = read(src)
        if text is None:
            continue
        dst = os.path.join(d, rel)
        if not os.path.exists(dst):
            write(dst, text)
        write(os.path.join(d, BASE, rel), text)
        m["files"][rel] = {"kind": kind, "base": sha(text)}
        done.append(rel)
    if done or not os.path.isfile(os.path.join(d, "README.md")):
        write(os.path.join(d, "README.md"), README % plugin_version())
    m["plugin_version"] = plugin_version()
    save_manifest(cwd, m)
    return done


def status(cwd=None):
    """[(rel, kind, state, layer)]. state: default | customized | stale (untouched, the plugin moved on) |
    conflict (both changed) | removed (deleted here: the plugin copy is used) | orphan (the plugin no longer
    ships it) | ignored (an agents/ file for a role the kernel has no gates for)."""
    d = repo_dir(cwd)
    m = manifest(cwd) or {"files": {}}
    pf = plugin_files()
    out = []
    for rel in sorted(set(pf) | set(m["files"])):
        kind = pf[rel][1] if rel in pf else m["files"][rel].get("kind", "effective")
        mine = read(os.path.join(d, rel))
        plug = read(pf[rel][0]) if rel in pf else None
        base = (m["files"].get(rel) or {}).get("base")
        if plug is None:
            state = "orphan"
        elif mine is None:
            state = "removed"
        elif sha(mine) == sha(plug):
            state = "default"
        elif base is None or sha(mine) == base:
            state = "stale"
        elif base == sha(plug):
            state = "customized"
        else:
            state = "conflict"
        layer = resolve(cwd, rel)[0] if kind == "effective" else "plugin"
        out.append((rel, kind, state, layer))
    ad = os.path.join(d, "agents")
    known = set(roles())
    if os.path.isdir(ad):
        for fn in sorted(os.listdir(ad)):
            if fn.endswith(".md") and fn[:-3] not in known:
                out.append(("agents/" + fn, "effective", "ignored", "-"))
    return out


def upgrade(cwd=None):
    """Bring the repo layer to this plugin: untouched files follow it, new files are ejected, an edited file
    whose plugin text changed keeps the user's text and gets `<rel>.plugin-new`. Returns (updated, conflicts)."""
    if manifest(cwd) is None:
        return [], []
    d = repo_dir(cwd)
    m = manifest(cwd)
    pf = plugin_files()
    updated, conflicts = [], []
    for rel, kind, state, _ in status(cwd):
        if rel not in pf:
            continue
        plug = read(pf[rel][0])
        if state == "stale":
            write(os.path.join(d, rel), plug)
            write(os.path.join(d, BASE, rel), plug)
            m["files"][rel] = {"kind": kind, "base": sha(plug)}
            updated.append(rel)
        elif state == "conflict":
            if read(os.path.join(d, rel + NEW)) != plug:
                write(os.path.join(d, rel + NEW), plug)
            conflicts.append(rel)
        elif state == "default" and (m["files"].get(rel) or {}).get("base") != sha(plug):
            write(os.path.join(d, BASE, rel), plug)
            m["files"][rel] = {"kind": kind, "base": sha(plug)}
    m["plugin_version"] = plugin_version()
    save_manifest(cwd, m)
    updated += eject(cwd)
    return updated, conflicts


def _check_rel(rel):
    if rel not in plugin_files():
        raise ValueError("%s is not a plugin file (see `aloop userspace status`)" % rel)


def diff(cwd, rel):
    _check_rel(rel)
    d = repo_dir(cwd)
    plug = read(plugin_files()[rel][0]) or ""
    mine = read(os.path.join(d, rel))
    base = read(os.path.join(d, BASE, rel))
    out = []
    if base is not None and base != plug:
        out += difflib.unified_diff(base.splitlines(True), plug.splitlines(True), "plugin@base/" + rel, "plugin@now/" + rel)
    if mine is not None:
        out += difflib.unified_diff(mine.splitlines(True), plug.splitlines(True), "yours/" + rel, "plugin@now/" + rel)
    return "".join(out)


def merge(cwd, rel):
    """Three-way merge (git merge-file): clean -> your copy takes it and the base moves on (0); a conflict ->
    `<rel>.merge` holds the markers and your copy is untouched (1)."""
    _check_rel(rel)
    d = repo_dir(cwd)
    p = os.path.join(d, rel)
    plug = read(plugin_files()[rel][0])
    base = read(os.path.join(d, BASE, rel))
    if read(p) is None:
        raise ValueError("%s: no copy of yours to merge into (`aloop userspace reset %s` restores it)" % (rel, rel))
    tmp = p + ".tmp-merge"
    write(tmp + ".base", base if base is not None else "")
    write(tmp + ".plug", plug)
    try:
        r = subprocess.run(["git", "merge-file", "-p", "-L", "yours", "-L", "base", "-L", "plugin",
                            p, tmp + ".base", tmp + ".plug"], capture_output=True)
    finally:
        for x in (tmp + ".base", tmp + ".plug"):
            try:
                os.remove(x)
            except OSError:
                pass
    text = r.stdout.decode("utf-8", "surrogateescape")
    if r.returncode != 0:
        write(p + MERGE, text)
        return 1
    write(p, text)
    _rebase(cwd, rel, plug)
    return 0


def _rebase(cwd, rel, plug):
    d = repo_dir(cwd)
    write(os.path.join(d, BASE, rel), plug)
    m = manifest(cwd) or {"plugin_version": plugin_version(), "files": {}}
    m["files"][rel] = {"kind": plugin_files()[rel][1], "base": sha(plug)}
    save_manifest(cwd, m)
    for x in (NEW, MERGE):
        try:
            os.remove(os.path.join(d, rel + x))
        except OSError:
            pass


def accept(cwd, rel):
    """Your copy stands, resolved against the plugin's current text."""
    _check_rel(rel)
    _rebase(cwd, rel, read(plugin_files()[rel][0]))


def reset(cwd, rel):
    _check_rel(rel)
    plug = read(plugin_files()[rel][0])
    write(os.path.join(repo_dir(cwd), rel), plug)
    _rebase(cwd, rel, plug)


# ── what the hosts load ───────────────────────────────────────────────
FM = re.compile(r"^---\n(.*?)\n---\n?", re.S)


def agent_text(cwd, role):
    """(text, layer, path) of the role's winning agent file."""
    layer, path = resolve(cwd, "agents/%s.md" % role)
    return read(path) or "", layer, path


def skill_text(cwd, skill, fn="SKILL.md"):
    layer, path = resolve(cwd, "skills/%s/%s" % (skill, fn))
    return read(path), layer, path


def overrides(cwd=None):
    """[(rel, layer, sha256)] of every effective file whose winning copy differs from the plugin's."""
    out = []
    for rel, (src, kind) in sorted(plugin_files().items()):
        if kind != "effective":
            continue
        layer, path = resolve(cwd, rel)
        if layer == "plugin":
            continue
        text = read(path)
        if text is not None and text != read(src):
            out.append((rel, layer, sha(text)))
    return out


def _claude_agent(cwd, role, text, layer, path):
    """The project agent file for Claude Code: the user's text with its `name` forced to the role (the
    kernel's gates match role names) and a marker that makes it ours to replace or remove."""
    m = FM.match(text)
    fm, body = (m.group(1), text[m.end():]) if m else ("description: %s (customized)" % role, text)
    fm = "\n".join(l for l in fm.splitlines() if not re.match(r"^name\s*:", l))
    body = body.replace("${CLAUDE_PLUGIN_ROOT}", ROOT)
    rel = os.path.relpath(path, _paths.project(cwd)) if layer == "repo" else path
    return "---\nname: %s\n%s\n---\n<!-- %s -->\n%s" % (role, fm.strip("\n"), MARK % rel, body.lstrip("\n"))


def claude_agent_path(cwd, role):
    return os.path.join(_paths.project(cwd), ".claude", "agents", role + ".md")


def rendered(cwd, role):
    """True when .claude/agents/<role>.md is a user-space render (so dispatch must use it)."""
    t = read(claude_agent_path(cwd, role))
    return bool(t and MARK_RE.search(t))


def apply(cwd=None, log=True):
    """Render the winning copies the host loads (Claude Code: .claude/agents/<role>.md for each customized
    role; a render that is no longer customized is removed; a file we did not write is never touched) and
    log which copy every customized file came from (`userspace.applied`). Returns (overrides, notes)."""
    notes = []
    ov = overrides(cwd)
    changed = {r for r, _, _ in ov}
    for role in roles():
        p = claude_agent_path(cwd, role)
        cur = read(p)
        mine = cur is not None and bool(MARK_RE.search(cur))
        if "agents/%s.md" % role in changed:
            if cur is not None and not mine:
                notes.append("%s exists and is not aloop's: the customized %s is not loaded on Claude Code" % (
                    os.path.relpath(p, _paths.project(cwd)), role))
                continue
            text, layer, path = agent_text(cwd, role)
            new = _claude_agent(cwd, role, text, layer, path)
            if cur != new:
                write(p, new)
        elif mine:
            os.remove(p)
    for rel, kind, state, _ in status(cwd):
        if state == "ignored":
            notes.append("%s names no kernel role, so it is not loaded (a role runs under the plugin's gates; "
                         "re-shape one of: %s)" % (rel, ", ".join(roles())))
    if log and (ov or _last_logged(cwd)):
        try:
            import _events
            _events.emit(cwd or os.getcwd(), "userspace.applied", role="harness",
                         overrides=["%s=%s:%s" % (r, l, s[:12]) for r, l, s in ov],
                         plugin_version=plugin_version())
        except Exception:
            pass
        _remember(cwd, ov)
    return ov, notes


def _state(cwd):
    return os.path.join(_paths.home(cwd, "state"), "userspace.json")


def _last_logged(cwd):
    try:
        return bool(json.load(open(_state(cwd))).get("overrides"))
    except (OSError, ValueError):
        return False


def _remember(cwd, ov):
    try:
        write(_state(cwd), json.dumps({"overrides": [list(x) for x in ov]}) + "\n")
    except OSError:
        pass


def context_note(cwd, ov, conflicts, notes):
    """The SessionStart lines: which opinions this project changed, and what needs the human."""
    lines = []
    skills = [(r, l) for r, l, _ in ov if r.startswith("skills/")]
    agents = [(r, l) for r, l, _ in ov if r.startswith("agents/")]
    if agents:
        lines.append("User space: this project customizes the role(s) %s; dispatch them by their bare name "
                     "(e.g. `%s`), not `aloop:<role>`." % (", ".join("%s (%s)" % (r[7:-3], l) for r, l in agents),
                                                          agents[0][0][7:-3]))
    for r, l in skills:
        path = resolve(cwd, r)[1]
        lines.append("User space: the project's %s (%s copy) supersedes the plugin's; read %s and follow it "
                     "where the two differ." % (r, l, path))
    if conflicts:
        lines.append("User space: the plugin upgrade changed %d file(s) this project customized (%s); the "
                     "project's copies still apply. Tell the human: `aloop userspace diff <rel>` / `merge <rel>`."
                     % (len(conflicts), ", ".join(conflicts)))
    lines += ["User space: " + n for n in notes]
    return "\n".join(lines)
