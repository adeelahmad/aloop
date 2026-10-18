"""_timebox.py — the time budget's `auto` mode record (ROADMAP §4.2); see bin/timebox."""
import json
import os

import _paths


def path(cwd=None):
    return os.path.join(_paths.home(cwd, "state"), "timebox.json")


def current(cwd=None):
    """The recorded timebox when AGENTIC_TIME_MODE=auto, else None."""
    if (_paths.settings(cwd).get("TIME_MODE") or "fixed") != "auto":
        return None
    try:
        with open(path(cwd)) as f:
            tb = json.load(f)
        return tb if int(tb.get("minutes", 0)) > 0 else None
    except (OSError, ValueError, TypeError):
        return None


def limits(tb):
    """Hard = the recorded minutes; soft = 2/3 of it (the 20/30 default ratio)."""
    m = int(tb["minutes"])
    return {"TIME_HARD_MIN": m, "TIME_SOFT_MIN": max(1, m * 2 // 3)}
