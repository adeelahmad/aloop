"""due — is a mission tick worth a model call? (ROADMAP §4.12, M9.8). Zero tokens: it reads files.

  mission due [--json] [--now ISO] [-q]
        exit 0 due: start a host session and run one tick (/aloop:mission) · 1 not due ·
        2 the facts could not be read (BLOCK[mission-due] on stderr, so the cron log shows it)

Due when `mission tick` would hand a role or the supervisor work that is not already handed
out (a handed-out action is leased for 2h, so a second session never starts on it), or would
tell the human something the last tick did not already tell them. Never due on WAIT, and never
on STOP_BUDGET: no session starts once a cap is reached; due records that tick itself (once, with
mission.budget_exhausted) and says so on stderr. Otherwise it decides exactly as `mission tick
--peek` does and records nothing, so a cron can run it every few minutes:
`aloop mission due -q && <host> /aloop:mission` (see `mission cron`).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import tick as T  # noqa: E402

NO_ARGS = True            # `mission due` with no arguments runs (plugin/bin/mission run_area)
LEASE = 2 * 3600 * 1000   # a role's action may take a while; after this it is handed out again


def due(cwd, now=None, record=True):
    d = T.decide(cwd, now)
    now = d["_facts"]["now"]
    ticks = [e for e in d["_facts"]["events"] if e.get("event") == "mission.tick"]
    last = ticks[-1] if ticks else None
    lk = ((last or {}).get("data") or {}).get("key")
    at = ((last or {}).get("data") or {}).get("at") or (last or {}).get("ts")   # the tick's own clock (--now)
    lt = T.ms_ts(at) if last else None
    out = {"action": d["action"], "who": d["who"], "subject": d["subject"], "key": d["key"],
           "next_check": d["next_check"]}
    if d["action"] == "WAIT":
        return dict(out, due=False, reason=d["reason"])
    if d["action"] == "STOP_BUDGET":       # a session would spend past the cap only to say so
        told = lk == d["key"]
        if not told and record:
            T.record(cwd, d)
        return dict(out, due=False, recorded=not told and record,
                    reason="no session starts once a cap is reached: %s" % d["reason"])
    if d["who"] == "human":
        if lk == d["key"]:
            return dict(out, due=False, reason="already told the human at %s: %s" % (at, d["reason"]))
        return dict(out, due=True, reason=d["reason"])
    if lk == d["key"] and lt is not None and now - lt < LEASE:
        return dict(out, due=False, reason="%s was handed out at %s; waiting for its result (leased until %s)"
                    % (d["action"], at, T.iso(lt + LEASE)))
    return dict(out, due=True, reason=d["reason"])


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
    if r.get("recorded"):
        print("[mission-due] %s" % r["reason"], file=sys.stderr)
    if o.get("json"):
        print(json.dumps(r, indent=1, sort_keys=True))
    elif not o.get("q"):
        print("%s: %s — %s" % ("due" if r["due"] else "not due", r["action"], r["reason"]))
    return 0 if r["due"] else 1
