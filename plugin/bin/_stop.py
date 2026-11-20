"""The project's stop flag (ROADMAP M10.4): one file in the main tree's run-data home.

`aloop stop` sets it; `budget hook pre` (run on every tool call by all four hosts) refuses every
acting call while it is set; `mission tick` answers STOPPED. Only the human clears it.

The flag is run data (git-ignored), so in a project with a mission ledger the stop and its clear are
also ledger rows (`run.stopped`, `run.resumed`): a fresh clone (another machine, a scheduled run) is
stopped while the ledger's newest of the two is a stop, like the budget stop (M9.24).
"""
import json
import os

import _paths

# tools that only read: let through while stopped, so the agent's next one ends it (continue: false)
READS = {"Read", "Grep", "Glob", "LS", "TodoRead", "NotebookRead", "ToolSearch",
         "read", "grep", "glob", "list", "view"}


def path(cwd=None):
    return os.path.join(_paths.home(cwd, "state", make=False), "STOP")


def ledger_stop(cwd=None):
    """The ledger's newest stop row as a stop record when no resume row follows it, else None. Only
    the intact rows count; the ledger is read only when it names a stop (this runs on every tool call)."""
    import _mission
    try:
        with open(_mission.ledger(cwd), encoding="utf-8") as f:
            if '"run.stopped"' not in f.read():
                return None
    except OSError:
        return None
    last = None
    for row in _mission.ledger_rows(cwd)[0]:
        if row["event"] in ("run.stopped", "run.resumed"):
            last = row
    if not last or last["event"] != "run.stopped":
        return None
    return {"at": last["ts"], "reason": last["data"].get("reason", ""), "by": last.get("role", ""), "ledger": True}


def read(cwd=None):
    """The stop record, or None when the project runs. A flag that is not JSON is still a stop, and
    with no flag the mission ledger's stop row holds (a clone)."""
    try:
        with open(path(cwd)) as f:
            raw = f.read()
    except OSError:
        return ledger_stop(cwd)
    try:
        rec = json.loads(raw)
        return rec if isinstance(rec, dict) else {"reason": raw.strip()}
    except ValueError:
        return {"reason": raw.strip()}


def message(rec):
    return ("STOP: the human stopped this project (%s, at %s): do nothing else, no edits, no commands, no "
            "dispatch, no report. Only the human resumes it (`aloop stop --clear` in their own terminal)."
            % (rec.get("reason") or "no reason given", rec.get("at") or "?"))
