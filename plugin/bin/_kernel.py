"""_kernel.py — kernel extensions and profiles (ROADMAP M12.7, M12.8).

A kernel extension is a module the kernel loads next to its own gates: `~/.aloop/kernel/<name>/` holding
`extension.json` and the command it names:

    {"name": "brand-voice", "description": "...", "on": ["SubagentStop:green-worker", "Stop"],
     "command": "./check", "timeout": 120}

It is add-only by construction. It runs as its own hook beside the core gates on the same event, so a
core gate's block stands whatever it says; its exit 2 blocks with its stderr as the reason; exit 0
passes only itself. Anything else (a crash, a timeout, a missing or changed module) blocks too: what
the project declared runs, or the run stops.

A project loads an extension only by pinning it in its user-space profile
(`docs/agents/userspace/profile.json`, in git):

    {"profile": "software", "extensions": {"brand-voice": "<sha256 of the module>"}}

A module whose files changed since the pin is refused until the human re-pins it (`aloop kernel pin`).
`software` (today's TDD loop) is the default profile; a domain pack is a profile plus its extensions.
Every run is logged (`kernel.extension_run`, gate-owned).
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

DEFAULT_PROFILE = "software"
NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
EVENT = re.compile(r"^(SubagentStop(:[a-z0-9*-]+)?|Stop)$")


def kernel_dir():
    return os.path.join(_userspace.aloop_home(), "kernel")


def module_dir(name):
    if not NAME.match(name or ""):
        raise ValueError("bad extension name %r (lowercase letters, digits, . _ -)" % name)
    return os.path.join(kernel_dir(), name)


def module_sha(name):
    """sha256 over the module's files (relative path + content), so any change re-requires a pin."""
    d = module_dir(name)
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
    """The parsed extension.json, checked; raises ValueError."""
    p = os.path.join(module_dir(name), "extension.json")
    try:
        m = json.load(open(p))
    except (OSError, ValueError) as e:
        raise ValueError("%s: no readable extension.json (%s)" % (name, e))
    if not isinstance(m, dict) or not isinstance(m.get("command"), str) or not m["command"].strip():
        raise ValueError("%s: extension.json needs a \"command\"" % name)
    on = m.get("on")
    if not isinstance(on, list) or not on or not all(isinstance(e, str) and EVENT.match(e) for e in on):
        raise ValueError("%s: \"on\" must list events: SubagentStop, SubagentStop:<role>, Stop" % name)
    return m


def installed():
    d = kernel_dir()
    return sorted(x for x in os.listdir(d) if NAME.match(x) and os.path.isdir(os.path.join(d, x))) if os.path.isdir(d) else []


# ── the profile (user space, in git) ──────────────────────────────────
def profile_path(cwd=None):
    return os.path.join(_userspace.repo_dir(cwd), "profile.json")


def profile(cwd=None):
    try:
        p = json.load(open(profile_path(cwd)))
    except OSError:
        return {"profile": DEFAULT_PROFILE, "extensions": {}}
    except ValueError:
        raise ValueError("%s is not JSON: fix it (a declared policy that cannot be read blocks)" % profile_path(cwd))
    if not isinstance(p, dict) or not isinstance(p.get("extensions", {}), dict):
        raise ValueError("%s: \"extensions\" must map a name to its sha256" % profile_path(cwd))
    p.setdefault("profile", DEFAULT_PROFILE)
    p.setdefault("extensions", {})
    return p


def save_profile(cwd, p):
    _userspace.write(profile_path(cwd), json.dumps(p, indent=2, sort_keys=True) + "\n")


def pin(cwd, name):
    module(name)
    s = module_sha(name)
    p = profile(cwd)
    p["extensions"][name] = s
    save_profile(cwd, p)
    return s


def unpin(cwd, name):
    p = profile(cwd)
    p["extensions"].pop(name, None)
    save_profile(cwd, p)


# ── running ───────────────────────────────────────────────────────────
def wants(m, event, role):
    for e in m["on"]:
        if e == event or e == event + ":*" or (role and e == "%s:%s" % (event, role)):
            return True
    return False


def run(cwd, event, role="", payload=""):
    """Run every pinned extension that hooks (event, role). Returns (exit, [reasons]): 2 if any blocks."""
    try:
        prof = profile(cwd)
    except ValueError as e:
        return 2, [str(e)]
    blocks = []
    for name, pinned in sorted(prof["extensions"].items()):
        try:
            m = module(name)
        except ValueError as e:
            if module_sha(name) is None:
                blocks.append("kernel extension %s is pinned by this project but not installed in %s "
                              "(install it, or `aloop kernel unpin %s`)" % (name, kernel_dir(), name))
            else:
                blocks.append("kernel extension %s: %s" % (name, e))
            continue
        if not wants(m, event, role):
            continue
        actual = module_sha(name)
        if actual != pinned:
            blocks.append("kernel extension %s changed since it was pinned (%s, now %s): review it, then "
                          "`aloop kernel pin %s`" % (name, str(pinned)[:12], (actual or "")[:12], name))
            _log(cwd, name, actual, event, role, "refused")
            continue
        cmd = m["command"]
        d = module_dir(name)
        argv = [os.path.join(d, cmd[2:]) if cmd.startswith("./") else cmd] + [str(a) for a in m.get("args", [])]
        env = dict(os.environ, ALOOP_EVENT=event, ALOOP_ROLE=role or "", ALOOP_PROJECT=_paths.project(cwd),
                   ALOOP_PROFILE=str(prof["profile"]), ALOOP_EXTENSION=name)
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
            blocks.append("kernel extension %s %s: %s" % (
                name, "blocked" if code == 2 else "failed (exit %d), which blocks" % code, err[-2000:] or "(no reason given)"))
    return (2 if blocks else 0), blocks


def _log(cwd, name, sha, event, role, verdict):
    try:
        import _events
        _events.emit(cwd or os.getcwd(), "kernel.extension_run", role=role or "", extension=name,
                     sha256=sha or "", hook=event, verdict=verdict)
    except Exception:
        pass
