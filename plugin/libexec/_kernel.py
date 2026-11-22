"""_kernel.py — the kernel's module loader and the project's distro (ROADMAP M12.7, M12.8).

The host harness (Claude Code, Codex, OpenCode, dsh) is the hardware, aloop is the kernel, and
**modules** plug into the kernel. A module lives at `~/.aloop/modules/<name>/`: `module.json` and the
command it names:

    {"name": "brand-voice", "description": "...", "on": ["SubagentStop:green-worker", "Stop"],
     "command": "./check", "timeout": 120}

A module is add-only by construction. It runs as its own hook beside the core gates on the same event,
so a core gate's block stands whatever it says; its exit 2 blocks with its stderr as the reason; exit 0
passes only itself. Anything else (a crash, a timeout, a missing or changed module) blocks too: what
the project declared runs, or the run stops.

A **distro** is the project's full user-space bundle for its domain: the roles, skills and modules it
runs (`software`, the TDD loop, is the default). The project names it, and pins every module it loads,
in `docs/agents/userspace/distro.json` (in git):

    {"distro": "software", "modules": {"brand-voice": "<sha256 of the module>"}}

A module whose files changed since it was added is refused until the human adds it again
(`aloop module add`). Every run is logged (`module.run`, gate-owned).
"""
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import _paths  # noqa: E402
import _userspace  # noqa: E402

DEFAULT_DISTRO = "software"
NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
EVENT = re.compile(r"^(SubagentStop(:[a-z0-9*-]+)?|Stop)$")


def modules_dir():
    return os.path.join(_userspace.aloop_home(), "modules")


def module_dir(name):
    if not NAME.match(name or ""):
        raise ValueError("bad module name %r (lowercase letters, digits, . _ -)" % name)
    return os.path.join(modules_dir(), name)


def module_sha(name):
    """sha256 over the module's files (relative path + content), so any change needs adding again."""
    return dir_sha(module_dir(name))


def dir_sha(d):
    if not os.path.isdir(d):
        return None
    h = hashlib.sha256()
    for dp, dns, fns in os.walk(d):
        dns[:] = sorted(x for x in dns if x not in (".git", "__pycache__"))
        for fn in sorted(fns):
            p = os.path.join(dp, fn)
            h.update(os.path.relpath(p, d).encode() + b"\0")
            with open(p, "rb") as f:
                h.update(f.read())
            h.update(b"\0")
    return h.hexdigest()


def module(name):
    """The parsed module.json, checked; raises ValueError."""
    return module_at(module_dir(name), name)


def module_at(d, name):
    """The module.json of the module at d, checked (also for a distro not installed yet)."""
    p = os.path.join(d, "module.json")
    try:
        m = json.load(open(p))
    except (OSError, ValueError) as e:
        raise ValueError("%s: no readable module.json (%s)" % (name, e))
    if not isinstance(m, dict) or not isinstance(m.get("command"), str) or not m["command"].strip():
        raise ValueError("%s: module.json needs a \"command\"" % name)
    on = m.get("on")
    if not isinstance(on, list) or not on or not all(isinstance(e, str) and EVENT.match(e) for e in on):
        raise ValueError("%s: \"on\" must list events: SubagentStop, SubagentStop:<role>, Stop" % name)
    return m


def installed():
    d = modules_dir()
    return sorted(x for x in os.listdir(d) if NAME.match(x) and os.path.isdir(os.path.join(d, x))) if os.path.isdir(d) else []


# ── the distro (user space, in git) ───────────────────────────────────
def distro_path(cwd=None):
    return os.path.join(_userspace.repo_dir(cwd), "distro.json")


def distro(cwd=None):
    try:
        p = json.load(open(distro_path(cwd)))
    except OSError:
        return {"distro": DEFAULT_DISTRO, "modules": {}}
    except ValueError:
        raise ValueError("%s is not JSON: fix it (a declared policy that cannot be read blocks)" % distro_path(cwd))
    if not isinstance(p, dict) or not isinstance(p.get("modules", {}), dict):
        raise ValueError("%s: \"modules\" must map a name to its sha256" % distro_path(cwd))
    p.setdefault("distro", DEFAULT_DISTRO)
    p.setdefault("modules", {})
    return p


def save_distro(cwd, p):
    _userspace.write(distro_path(cwd), json.dumps(p, indent=2, sort_keys=True) + "\n")


def add(cwd, name):
    module(name)
    s = module_sha(name)
    p = distro(cwd)
    p["modules"][name] = s
    save_distro(cwd, p)
    return s


def remove(cwd, name):
    p = distro(cwd)
    p["modules"].pop(name, None)
    save_distro(cwd, p)


# ── running ───────────────────────────────────────────────────────────
def wants(m, event, role):
    for e in m["on"]:
        if e == event or e == event + ":*" or (role and e == "%s:%s" % (event, role)):
            return True
    return False


def run(cwd, event, role="", payload=""):
    """Run every module the distro pins that hooks (event, role). Returns (exit, [reasons]): 2 if any blocks."""
    try:
        prof = distro(cwd)
    except ValueError as e:
        return 2, [str(e)]
    blocks = []
    for name, pinned in sorted(prof["modules"].items()):
        try:
            m = module(name)
        except ValueError as e:
            if module_sha(name) is None:
                blocks.append("module %s is added to this project but not installed in %s "
                              "(install it, or `aloop module remove %s`)" % (name, modules_dir(), name))
            else:
                blocks.append("module %s: %s" % (name, e))
            continue
        if not wants(m, event, role):
            continue
        actual = module_sha(name)
        if actual != pinned:
            blocks.append("module %s changed since it was added (%s, now %s): review it, then "
                          "`aloop module add %s`" % (name, str(pinned)[:12], (actual or "")[:12], name))
            _log(cwd, name, actual, event, role, "refused")
            continue
        cmd = m["command"]
        d = module_dir(name)
        argv = [os.path.join(d, cmd[2:]) if cmd.startswith("./") else cmd] + [str(a) for a in m.get("args", [])]
        env = dict(os.environ, ALOOP_EVENT=event, ALOOP_ROLE=role or "", ALOOP_PROJECT=_paths.project(cwd),
                   ALOOP_DISTRO=str(prof["distro"]), ALOOP_MODULE=name)
        try:
            r = subprocess.run(argv, cwd=_paths.project(cwd), input=payload, env=env, capture_output=True,
                               text=True, timeout=float(m.get("timeout", 120)))
            code, err = r.returncode, (r.stderr or r.stdout).strip()
        except subprocess.TimeoutExpired:
            code, err = 124, "timed out after %ss" % m.get("timeout", 120)
        except OSError as e:
            code, err = 126, "cannot run %s (%s)" % (cmd, e)
        _log(cwd, name, actual, event, role, "pass" if code == 0 else "block")
        if code != 0:
            blocks.append("module %s %s: %s" % (
                name, "blocked" if code == 2 else "failed (exit %d), which blocks" % code, err[-2000:] or "(no reason given)"))
    return (2 if blocks else 0), blocks


def _log(cwd, name, sha, event, role, verdict):
    try:
        import _events
        _events.emit(cwd or os.getcwd(), "module.run", role=role or "", module=name,
                     sha256=sha or "", hook=event, verdict=verdict)
    except Exception:
        pass


# ── scaffolds: how others build their own (ROADMAP M12.8) ─────────────
CHECK = """#!/usr/bin/env bash
# %(name)s — an aloop module. It ADDS a check; it can never remove or weaken a core gate.
#
# The kernel runs this on %(on)s, from the project root, with the hook payload (JSON) on stdin and:
#   ALOOP_EVENT      SubagentStop | Stop
#   ALOOP_ROLE       the role that stopped (red-worker, green-worker, planner, ...; empty on Stop)
#   ALOOP_PROJECT    the project root
#   ALOOP_DISTRO     the project's distro (software, marketing, ...)
#   ALOOP_MODULE     this module's name
# Exit 0: pass. Exit 2: block, and stderr is the reason the agent is handed back. Any other exit,
# a crash or a timeout blocks too (a check the project declared always runs).
set -uo pipefail
cd "$ALOOP_PROJECT" || exit 2

# Example: refuse changed Markdown that still says TODO. Replace with your own check.
bad="$(git diff --name-only HEAD -- '*.md' 2>/dev/null | while read -r f; do
  [ -f "$f" ] && grep -l 'TODO' "$f"; done)"
if [ -n "$bad" ]; then
  echo "%(name)s: these files still say TODO: $bad" >&2
  exit 2
fi
exit 0
"""

README = """# %(name)s

An aloop module (ROADMAP M12.7): it plugs into the kernel. It adds a check on %(on)s and can never remove a core gate.

- `module.json`: the events it runs on (`on`), its `command` and `timeout` (seconds).
- `check`: the check. Exit 0 passes, exit 2 blocks with stderr as the reason.

Add it to a project (pins this exact content; any later edit is refused until you add it again):

    aloop module add %(name)s
    aloop module list
"""


def scaffold(name, on=None, root=None):
    """Write a new module at <root or ~/.aloop/modules>/<name>/; never overwrites. Returns its dir."""
    if not NAME.match(name or ""):
        raise ValueError("bad module name %r (lowercase letters, digits, . _ -)" % name)
    on = on or ["SubagentStop:green-worker"]
    for e in on:
        if not EVENT.match(e):
            raise ValueError("bad event %r (SubagentStop, SubagentStop:<role>, Stop)" % e)
    d = os.path.join(root or modules_dir(), name)
    if os.path.exists(d):
        raise ValueError("%s already exists" % d)
    v = {"name": name, "on": ", ".join(on)}
    _userspace.write(os.path.join(d, "module.json"), json.dumps(
        {"name": name, "description": "what this check refuses, in one line", "on": on,
         "command": "./check", "timeout": 120}, indent=2) + "\n")
    _userspace.write(os.path.join(d, "check"), CHECK % v)
    os.chmod(os.path.join(d, "check"), 0o755)
    _userspace.write(os.path.join(d, "README.md"), README % v)
    return d
