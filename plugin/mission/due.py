"""due — is a mission tick worth a model call? (ROADMAP §4.12, M9.8). Zero tokens: it reads files.

  mission due [--json] [--now ISO] [-q]
        exit 0 due: start a host session and run one tick (/aloop:mission) · 1 not due ·
        3 the human is needed (once per action: no model session is started to say it) ·
        2 the facts could not be read (BLOCK[mission-due] on stderr, so the cron log shows it)

Due when `mission tick` would hand a role or the supervisor work and no session is on it: a
`mission cron --fire` session holds the loop lock while it runs, and a handout from any other
session is leased for 2h, whatever its key (a role's partial write changes the key, not the
work in flight). A handout recorded by a cron session that has ended is due again at once.
A human action (BLOCKED and STOP_BUDGET included) is never due: due records that tick itself,
prints it and exits 3, once per action (then 1: already told), so no session spends tokens to
say it and nothing runs past a cap. WAIT is never due. Otherwise due decides exactly as
`mission tick --peek` does and records nothing, so a cron can run it every few minutes (see
`mission cron`).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import tick as T  # noqa: E402

try:
    import fcntl
except ImportError:          # no flock (Windows): the time lease alone guards the loop
    fcntl = None

NO_ARGS = True            # `mission due` with no arguments runs (plugin/bin/mission run_area)
LEASE = 2 * 3600 * 1000   # a handout from an interactive session: after this it is handed out again


def lock_path(cwd, make=False):
    return os.path.join(T._paths.home(cwd, "state", make=make), "mission-loop.lock")


def holder(cwd):
    """The running `mission cron --fire` session ({pid, at, host, action}) when it holds the loop
    lock, else None."""
    p = lock_path(cwd)
    if fcntl is None or not os.path.exists(p):
        return None
    fd = os.open(p, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError:
        try:
            info = json.loads(os.read(fd, 4096).decode() or "{}")
        except ValueError:
            info = {}
        return info if isinstance(info, dict) else {}
    finally:
        os.close(fd)
    return None


def due(cwd, now=None, record=True, locked=False):
    """locked: the caller (`mission cron --fire`) holds the loop lock itself."""
    held = None if locked else holder(cwd)
    if held is not None:
        return {"due": False, "code": 1, "action": held.get("action"), "who": None, "subject": "", "key": None,
                "next_check": None, "reason": "a mission session is running (pid %s, %s, since %s)"
                % (held.get("pid"), held.get("host"), held.get("at"))}
    d = T.decide(cwd, now)
    now = d["_facts"]["now"]
    ticks = d["_facts"]["ticks"]
    last = ticks[-1]["data"] if ticks else {}
    at = last.get("at") or (ticks[-1].get("ts") if ticks else None)   # the tick's own clock (--now)
    lt = T.ms_ts(at)
    out = {"action": d["action"], "who": d["who"], "subject": d["subject"], "key": d["key"],
           "next_check": d["next_check"]}
    if d["action"] == "WAIT":
        return dict(out, due=False, code=1, reason=d["reason"])
    if d["who"] == "human":
        told = last.get("key") == d["key"]
        if not told and record:
            T.record(cwd, d)
        return dict(out, due=False, code=1 if told else 3, told=told, recorded=not told and record,
                    reason=("already told the human at %s: %s" % (at, d["reason"])) if told else d["reason"])
    if last.get("who") in ("role", "supervisor") and not last.get("loop") and lt is not None and now - lt < LEASE:
        return dict(out, due=False, code=1, reason="%s was handed out at %s; waiting for its result (leased until %s)"
                    % (last.get("action"), at, T.iso(lt + LEASE)))
    return dict(out, due=True, code=0, reason=d["reason"])


def main(argv, cwd):
    try:
        o = T.parse(argv, ("--json", "-q"), ("--now",))
        now = T.now_of(o)
    except ValueError as e:
        print("mission due: %s (mission due --help)" % e, file=sys.stderr)
        return 64
    try:
        r = due(cwd, now)
    except Exception as e:       # a crash must not read as "not due": the loop would stop without a trace
        print("BLOCK[mission-due]: the mission facts could not be read (%s: %s); run `aloop mission tick --peek`"
              % (type(e).__name__, e), file=sys.stderr)
        return 2
    if o.get("json"):
        print(json.dumps(r, indent=1, sort_keys=True))
    elif r.get("recorded"):
        print("[mission] the human is needed: %s — %s" % (r["action"], r["reason"]))
    elif not o.get("q"):
        print("%s: %s — %s" % ("due" if r["due"] else "not due", r["action"], r["reason"]))
    return r["code"]
