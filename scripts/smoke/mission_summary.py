#!/usr/bin/env python3
"""mission_summary — the measured record of one live mission run (ROADMAP §4.11, M9.13): the mission
mode of run-summary (plugin/bin/run-summary), with the same flags it will take there.

    mission_summary.py <run-dir> --sessions DIR --kind mission [--checks N/M] [--secs WALL] [--run N]
                       [--host claude] [--outcome TEXT] [--plugin DIR]

Reads, never writes, the run: each session's stream-json in DIR (NN.jsonl), the driver's
<run-dir>/sessions.jsonl and driver.json when they exist, <run-dir>/plan.json, and the run repo
(<run-dir>/repo): its gate log, mission ledger, sprint closes and `mission tick --peek --json`
(which records nothing). Writes <run-dir>/summary.json and summary.md and prints the
docs/evidence/README.md row (`## Mission runs`). Numbers come from the harness, never a model:
  cost_usd        the sum of each session's LAST `result` line (total_cost_usd); a session with no
                  result line (killed, crashed) leaves cost_complete false and the cell reads ≥$x.xx
  tick_usd        tick's own estimate (budget.usd of tick --peek --json): it prices the token
                  ledger from mission.set on, so it misses the first session's spend before that
  gate lines      parsed as JSON; stuck_releases counts "released": "stuck" in either spelling, and
                  RED, GREEN and the final gate pass only with exit 0 and no `released` field
  grants          the mission ledger's human grant rows (each cites the typed line it relayed)
Exit 0; a usage error is 64."""
import glob
import json
import os
import re
import subprocess
import sys
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.realpath(__file__))
GRANTS = ("mission.approved", "baseline.confirmed", "journey.approved", "goal.approved", "ethics.consent",
          "telemetry.consent")
PASS_GATES = {"red": "gate-red-verify", "green": "gate-green-verify", "final": "gate-final"}
CRASH = "the gate itself failed"


def arg(argv, flag, default=None):
    return argv[argv.index(flag) + 1] if flag in argv[:-1] else default


def wall(secs):
    return "%dm%02ds" % (int(secs) // 60, int(secs) % 60)


def jsonl(path):
    """The JSON objects of a JSON-lines file, skipping what is not one."""
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return out
    for ln in lines:
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        if isinstance(e, dict):
            out.append(e)
    return out


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def session(path, n):
    rows = jsonl(path)
    res = [e for e in rows if e.get("type") == "result"]
    last = res[-1] if res else {}
    mu = last.get("modelUsage")
    return {"n": n, "file": os.path.basename(path), "has_result": bool(res), "results": len(res),
            "cost_usd": last.get("total_cost_usd") if res else None, "turns": last.get("num_turns") or 0,
            "subtype": last.get("subtype"), "is_error": last.get("is_error"),
            "duration_ms": last.get("duration_ms"), "models": sorted(mu) if isinstance(mu, dict) else []}


def gates(repo):
    out = {"gate_verdicts": 0, "gate_blocks": 0, "selfcheck_blocks": 0, "stuck_releases": 0,
           "passed": {k: False for k in PASS_GATES}, "log": None}
    for h in (".aladdin", ".agentic"):
        g = os.path.join(repo, h, "logs", "gates.jsonl")
        if not os.path.isfile(g):
            continue
        out["log"] = g
        for v in jsonl(g):
            out["gate_verdicts"] += 1
            if v.get("exit") == 2:
                out["gate_blocks"] += 1
                out["selfcheck_blocks"] += v.get("selfcheck") is True
            if v.get("released") == "stuck":
                out["stuck_releases"] += 1
            for k, name in PASS_GATES.items():      # a stuck release exits 0 and is not a pass
                if v.get("gate") == name and v.get("exit") == 0 and "released" not in v:
                    out["passed"][k] = True
        break
    return out


def crashes(paths):
    n = 0
    for p in paths:
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                n += f.read().count(CRASH)
        except OSError:
            continue
    return n


def mission_facts(repo, plugin):
    """Ledger grants, goal.met and sprint closes from the run repo (read only)."""
    ledger = os.path.join(repo, "docs", "agents", "mission", "ledger.jsonl")
    rows = jsonl(ledger)
    grants = [{"event": r.get("event"), "by": (r.get("data") or {}).get("by"),
               "line": ((r.get("data") or {}).get("prompt") or {}).get("line"),
               "session": ((r.get("data") or {}).get("prompt") or {}).get("session")}
              for r in rows if r.get("event") in GRANTS and r.get("role") == "human"]
    sprints = 0
    for h in (".aladdin", ".agentic"):
        st = load(os.path.join(repo, h, "state", "sprints.json"))
        if isinstance(st.get("closed"), list):
            sprints = len(st["closed"])
            break
    tick = None
    if os.path.isfile(os.path.join(repo, "docs", "agents", "mission", "mission.json")):
        try:
            r = subprocess.run([os.path.join(plugin, "bin", "mission"), "tick", "--peek", "--json"], cwd=repo,
                               capture_output=True, text=True, timeout=120)
            b = json.loads(r.stdout).get("budget") or {}
            tick = round(float(b["usd"]), 2) if isinstance(b.get("usd"), (int, float)) else None
        except (OSError, ValueError, subprocess.TimeoutExpired, AttributeError):
            tick = None
    return {"grants": grants, "goal_met": any(r.get("event") == "goal.met" for r in rows), "sprints": sprints,
            "tick_usd": tick}


def git(path, *args):
    try:
        return subprocess.run(["git", "-C", path] + list(args), capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if argv else 64
    run = os.path.abspath(argv[0])
    sdir, kind = arg(argv, "--sessions"), arg(argv, "--kind")
    if kind != "mission":
        print("mission_summary: --kind mission is the only kind (run-summary covers e2e-live runs)", file=sys.stderr)
        return 64
    if not sdir or not os.path.isdir(sdir) or not os.path.isdir(run):
        print("mission_summary: no run dir or sessions dir (%s, %s)" % (run, sdir), file=sys.stderr)
        return 64
    plugin = os.path.abspath(arg(argv, "--plugin") or os.path.join(HERE, "..", "..", "plugin"))
    plan, driver = load(os.path.join(run, "plan.json")), load(os.path.join(run, "driver.json"))
    records = jsonl(os.path.join(run, "sessions.jsonl"))
    host = arg(argv, "--host") or plan.get("host") or "claude"
    streams = sorted(p for p in glob.glob(os.path.join(sdir, "*.jsonl")) if re.match(r"^\d+\.jsonl$", os.path.basename(p)))
    per = [session(p, i) for i, p in enumerate(streams, 1)]
    costs = [s["cost_usd"] for s in per if isinstance(s["cost_usd"], (int, float))]
    complete = bool(per) and all(s["has_result"] for s in per) and not any(r.get("cost_complete") is False for r in records)
    cost = round(sum(costs), 2) if costs else None
    repo = os.path.join(run, "repo")
    g = gates(repo)
    f = mission_facts(repo, plugin)
    secs = arg(argv, "--secs") or driver.get("wall_s")
    if secs is None:
        secs = sum(int(s["duration_ms"] or 0) for s in per) // 1000
    checks = arg(argv, "--checks") or driver.get("checks") or ""
    outcome = arg(argv, "--outcome") or driver.get("outcome") or ("GOAL MET" if f["goal_met"] else "no goal met")
    version = load(os.path.join(plugin, ".claude-plugin", "plugin.json")).get("version", "")
    head = git(plugin, "rev-parse", "--short", "HEAD")
    transcripts = glob.glob(os.path.join(repo, ".aladdin", "transcripts", "*", "events.jsonl"))
    s = {"kind": "mission", "run_dir": run, "fixture": plan.get("fixture") or "todo", "host": host,
         "plugin_version": version, "plugin_head": head, "checks": checks, "wall": wall(float(secs or 0)),
         "sessions": len(per), "per_session": per, "records": records, "cost_usd": cost, "cost_complete": complete,
         "cost_basis": "the host's total_cost_usd, the last result line of each session",
         "turns": sum(int(x["turns"] or 0) for x in per), "tick_usd": f["tick_usd"],
         "drift_usd": round(cost - f["tick_usd"], 2) if cost is not None and f["tick_usd"] is not None else None,
         "grants": f["grants"], "sprints": f["sprints"], "goal_met": f["goal_met"], "outcome": outcome,
         "gate_verdicts": g["gate_verdicts"], "gate_blocks": g["gate_blocks"], "selfcheck_blocks": g["selfcheck_blocks"],
         "stuck_releases": g["stuck_releases"], "passed": g["passed"],
         "hook_crashes": crashes(streams + transcripts + ([g["log"]] if g["log"] else [])),
         "seed_sha": (plan.get("seed") or {}).get("sha"), "deviations": (plan.get("env") or {}).get("deviations") or [],
         "scripted_human": plan.get("by") or "scripted-human (e2e)", "driver": driver,
         "run_repo_head": git(repo, "rev-parse", "HEAD"),
         "reproduce": "make e2e-mission YES=1" + (" HOST=%s" % host if host != "claude" else ""),
         "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    cost_cell = "—" if cost is None else ("$%.2f" if complete else "≥$%.2f") % cost
    tick_cell = "—" if f["tick_usd"] is None else "$%.2f" % f["tick_usd"]
    label = "mission %s" % (arg(argv, "--run") or "?")
    plug = "v%s%s" % (version, " (%s)" % head if head else "")
    row = "| %s | %s (%s) | %s | %s | %d / %d | %d | %s | %s | %s | %s | `%s` |" % (
        label, s["fixture"], host, plug, checks or "?", len(per), len(f["grants"]), f["sprints"], s["wall"],
        cost_cell, tick_cell, outcome, s["reproduce"])
    md = ("# Mission run %s\n\n| run | fixture | plugin | checks | sessions / grants | sprints | wall | cost | "
          "tick estimate | outcome | reproduce |\n|---|---|---|---|---|---|---|---|---|---|---|\n%s\n\n"
          "The grants were typed by the script as %s: the run proves the relay, not a human's review.\n"
          "seed: `%s` · run repo head: `%s` · gate verdicts: %d (%d blocks, %d stuck releases) · cost complete: %s\n"
          % (os.path.basename(run), row, s["scripted_human"], s["seed_sha"], s["run_repo_head"], g["gate_verdicts"],
             g["gate_blocks"], g["stuck_releases"], "yes" if complete else "no"))
    if driver.get("check_results"):          # the driver's post-run checks and why the loop stopped
        md += "\nstop: %s · path: %s\n\n## Checks (%s)\n\n%s\n" % (
            (driver.get("stop") or {}).get("reason"), driver.get("path"), checks,
            "\n".join("- %s %s: %s" % ("ok" if c.get("ok") else "FAIL", c.get("name"), c.get("detail"))
                      for c in driver["check_results"]))
    with open(os.path.join(run, "summary.json"), "w") as fh:
        json.dump(s, fh, indent=1)
    with open(os.path.join(run, "summary.md"), "w") as fh:
        fh.write(md)
    print("cost: %s (host%s) · tick's estimate: %s · turns: %d · wall: %s" % (
        cost_cell, "" if complete else ", a session without a result line", tick_cell, s["turns"], s["wall"]))
    print("sessions: %d · grants: %d · sprints: %d · outcome: %s" % (len(per), len(f["grants"]), f["sprints"], outcome))
    print("gate verdicts: %d (%d blocks, %d stuck releases) · hook crashes: %d" % (
        g["gate_verdicts"], g["gate_blocks"], g["stuck_releases"], s["hook_crashes"]))
    print("evidence row (docs/evidence/README.md, ## Mission runs):")
    print(row)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
