"""_runner.py — run a pinned measurement script the same way every time (ROADMAP §4.12).

A probe is {"command": [argv…], "pinned": {tool: {"version": V, "probe": [argv…]}}, "timeout_s": N,
"files": {path: sha256}}. Before it runs, every file the command runs must still have its pinned
content and every pinned tool's probe must print its version; then the command runs in
the project root and its LAST non-empty stdout line is the result, a JSON object:
    {"steps": {"<step id>": true|false, …}, "metrics": {"<metric id>": <number>|<bool>, …}}
A model never reports a measurement: only a script's output counts.
"""
import hashlib
import json
import math
import os
import re
import subprocess

DEFAULT_TIMEOUT = 300


def argv_ok(cmd):
    return isinstance(cmd, list) and bool(cmd) and all(isinstance(c, str) and c for c in cmd)


def version_seen(version, seen):
    """The pinned version appears whole: '3.12' matches 'Python 3.12.4', '3.1' does not, nor '18' 'v20.18.1'."""
    return re.search(r"(?<![0-9.])%s(?![0-9])" % re.escape(str(version)), seen) is not None


def file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def file_problems(files, cwd):
    """One line per runner file whose content is not the content pinned when it was sealed."""
    out = []
    for rel, sha in sorted((files or {}).items()):
        p = os.path.join(cwd, rel)
        try:
            now = file_sha(p)
        except OSError:
            out.append("runner file %s is gone" % rel)
            continue
        if now != sha:
            out.append("runner file %s changed since it was pinned (a changed runner is a new journey or baseline version)" % rel)
    return out


def pin_problems(pinned, cwd, env=None):
    """One line per pinned tool whose probe does not print its pinned version."""
    out = []
    for tool, pin in sorted((pinned or {}).items()):
        if not isinstance(pin, dict) or not pin.get("version") or not argv_ok(pin.get("probe")):
            out.append("pinned %s needs a version and a probe command (an argv list)" % tool)
            continue
        try:
            r = subprocess.run(pin["probe"], cwd=cwd, env=env, capture_output=True, text=True, timeout=30)
            seen = (r.stdout + r.stderr).strip()
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            out.append("pinned %s %s: its probe %s failed (%s)" % (tool, pin["version"], " ".join(pin["probe"]), e))
            continue
        if not version_seen(pin["version"], seen):
            out.append("pinned %s %s, but its probe printed %r" % (tool, pin["version"], seen.splitlines()[0] if seen else ""))
    return out


def run(probe, cwd, env=None, extra_env=None):
    """(result dict or None, problem or "") for one run of a probe."""
    env = dict(env or os.environ, **(extra_env or {}))
    bad = file_problems(probe.get("files"), cwd) + pin_problems(probe.get("pinned"), cwd, env)
    if bad:
        return None, "; ".join(bad)
    cmd = probe.get("command")
    if not argv_ok(cmd):
        return None, "command must be a non-empty argv list"
    try:
        r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                           timeout=probe.get("timeout_s") or DEFAULT_TIMEOUT)
    except FileNotFoundError:
        return None, "command %r not found" % cmd[0]
    except subprocess.TimeoutExpired:
        return None, "command timed out after %ss" % (probe.get("timeout_s") or DEFAULT_TIMEOUT)
    except (OSError, ValueError) as e:
        return None, "command %r could not start: %s" % (cmd[0], e)
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    if not lines:
        return None, "command printed no result line (exit %d): %s" % (r.returncode, r.stderr.strip()[-200:])
    try:
        res = json.loads(lines[-1], parse_constant=_refuse)
    except ValueError as e:
        return None, "the last stdout line is not JSON (%s): %r" % (e, lines[-1][:200])
    if not isinstance(res, dict):
        return None, "the result line must be a JSON object"
    steps, metrics = res.get("steps", {}), res.get("metrics", {})
    if not isinstance(steps, dict) or not isinstance(metrics, dict):
        return None, "steps and metrics must be objects"
    if not all(isinstance(v, bool) for v in steps.values()):
        return None, "every step result must be true or false"
    if not all(isinstance(v, (int, float)) for v in metrics.values()):
        return None, "every metric must be a number or a boolean"
    return {"steps": steps, "metrics": metrics, "exit": r.returncode}, ""


def _refuse(c):
    raise ValueError("%s is not a measurement" % c)


def drift(a, b):
    """Relative difference of two measurements (0 when equal); booleans and non-numbers differ by
    1, and a non-finite number by infinity, so it never passes a tolerance."""
    if isinstance(a, bool) or isinstance(b, bool):
        return 0.0 if a == b else 1.0
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return 0.0 if a == b else 1.0
    if not (math.isfinite(a) and math.isfinite(b)):
        return float("inf")
    if a == b:
        return 0.0
    return abs(a - b) / max(abs(a), abs(b))
