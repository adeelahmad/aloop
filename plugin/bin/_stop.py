"""The project's stop flag (ROADMAP M10.4): one file in the main tree's run-data home.

`aloop stop` sets it; `budget hook pre` (run on every tool call by all four hosts) refuses every
acting call while it is set; `mission tick` answers STOPPED. Only the human clears it.
"""
import json
import os

import _paths

# tools that only read: let through while stopped, so the agent's next one ends it (continue: false)
READS = {"Read", "Grep", "Glob", "LS", "TodoRead", "NotebookRead", "ToolSearch",
         "read", "grep", "glob", "list", "view"}


def path(cwd=None):
    return os.path.join(_paths.home(cwd, "state", make=False), "STOP")


def read(cwd=None):
    """The stop record, or None when the project runs. A flag that is not JSON is still a stop."""
    try:
        with open(path(cwd)) as f:
            raw = f.read()
    except OSError:
        return None
    try:
        rec = json.loads(raw)
        return rec if isinstance(rec, dict) else {"reason": raw.strip()}
    except ValueError:
        return {"reason": raw.strip()}


def message(rec):
    return ("STOP: the human stopped this project (%s, at %s): do nothing else, no edits, no commands, no "
            "dispatch, no report. Only the human resumes it (`aloop stop --clear` in their own terminal)."
            % (rec.get("reason") or "no reason given", rec.get("at") or "?"))
