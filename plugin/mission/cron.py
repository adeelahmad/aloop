"""cron — the mission loop on the host's scheduler (ROADMAP §4.12, M9.8).

  mission cron [--host claude|codex|opencode] [--every 30m] [--json] [--install | --remove]
        print the crontab line that drives the loop. Every --every (default 30m, never longer
        than the mission's cadence) cron runs `aloop mission due`, which reads files only and
        spends no tokens; only when it exits 0 does a host session start and run one tick
        (/aloop:mission). Output goes to <run data>/logs/mission-cron.log.
        --json also gives a routine spec (schedule + prompt) for a host scheduler, such as a
        Claude Code routine. --install puts the line in the user's crontab, replacing an
        earlier one for this project; --remove takes it out. No other line is touched.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import tick as T  # noqa: E402

BARE = True               # `mission cron` with no arguments runs (plugin/bin/mission run_area)
HOSTS = ("claude", "codex", "opencode")
EVERY = re.compile(r"^([1-9][0-9]*)([mhd])$")
MINUTES = (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30)     # the minute steps cron can repeat evenly
# a headless supervisor runs the playbook and dispatches the mission roles; research needs the web
TOOLS = "Agent Task Bash Read Write Edit MultiEdit Glob Grep Skill TaskStop TodoWrite WebSearch WebFetch"
PROMPT = {"claude": "/aloop:mission",
          "other": "Use the aloop mission skill (`aloop-mission`, or `mission` from the aloop plugin): run "
                   "`aloop mission tick` and act on the one action it names."}


def schedule(every):
    m = EVERY.match(every or "")
    if not m:
        return None
    n, u = int(m.group(1)), m.group(2)
    if u == "m" and n in MINUTES:
        return "* * * * *" if n == 1 else "*/%d * * * *" % n
    if u == "m" and n == 60 or u == "h" and n == 1:
        return "0 * * * *"
    if u == "h" and n < 24 and 24 % n == 0:
        return "0 */%d * * *" % n
    if u == "d" and n == 1 or u == "h" and n == 24:
        return "0 0 * * *"
    return None


def default_every(cadence_ms):
    mins = max(1, cadence_ms // 60000)
    return "%dm" % max(x for x in MINUTES if x <= min(30, mins))


def host_cmd(host):
    if host == "claude":
        exe = shutil.which("claude") or "claude"
        cmd = [exe, "-p", PROMPT["claude"]]
        if "/.claude/plugins/" not in T.M.PLUGIN + "/":       # not installed by Claude Code: load it from here
            cmd += ["--plugin-dir", T.M.PLUGIN]
        return cmd + ["--allowedTools", TOOLS]
    if host == "codex":
        return [shutil.which("codex") or "codex", "exec", "--full-auto", PROMPT["other"]]
    return [shutil.which("opencode") or "opencode", "run", PROMPT["other"]]


def render(cwd, host, every):
    root = os.path.realpath(T._paths.project(cwd))
    sched = schedule(every)
    log = os.path.join(T._paths.home(cwd, "logs", make=False), "mission-cron.log")
    aloop = os.path.join(T.BIN, "aloop")
    q = shlex.quote
    cmd = "cd %s && export PATH=%s && %s mission due -q && %s >> %s 2>&1" % (
        q(root), q(os.environ.get("PATH", "/usr/bin:/bin")), q(aloop), " ".join(q(x) for x in host_cmd(host)), q(log))
    return {"host": host, "every": every, "schedule": sched, "project": root, "marker": "# aloop-mission %s" % root,
            "crontab": "%s %s # aloop-mission %s" % (sched, cmd, root),
            "routine_prompt": "In %s, run `aloop mission due`. If it exits 1, stop at once: nothing is due. Otherwise "
                              "use the aloop mission skill (/aloop:mission) to run the tick it names." % root}


def crontab(lines_fn):
    try:
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    except OSError:
        return None
    lines = cur.stdout.splitlines() if cur.returncode == 0 else []
    new = lines_fn(lines)
    r = subprocess.run(["crontab", "-"], input="\n".join(new) + "\n", text=True, capture_output=True)
    return r.returncode == 0


def main(argv, cwd):
    try:
        o = T.parse(argv, ("--json", "--install", "--remove"), ("--host", "--every"))
    except ValueError as e:
        print("mission cron: %s (mission cron --help)" % e, file=sys.stderr)
        return 64
    host = o.get("host") or os.environ.get("AGENTIC_HOST") or "claude"
    mdoc, _ = T.M.load(T.M.path_of(cwd, "mission"))
    cad = (T.cli().cadence_seconds((mdoc or {}).get("cadence")) or 6 * 3600) * 1000
    every = o.get("every") or default_every(cad)
    bad = []
    if host not in HOSTS:
        bad.append("--host must be one of %s" % ", ".join(HOSTS))
    if not schedule(every):
        bad.append("--every must repeat evenly: 1-30m dividing 60 (5m, 15m, 30m), 1h-12h dividing 24, or 1d")
    if o.get("install") and o.get("remove"):
        bad.append("--install or --remove, not both")
    for b in bad:
        print("BLOCK[mission-cron]: %s" % b, file=sys.stderr)
    if bad:
        return 64
    spec = render(cwd, host, every)
    if o.get("install") or o.get("remove"):
        keep = (lambda ls: [l for l in ls if not l.rstrip().endswith(spec["marker"])]
                + ([spec["crontab"]] if o.get("install") else []))
        done = crontab(keep)
        if not done:
            print("BLOCK[mission-cron]: `crontab` is not available here; add this line with `crontab -e`:\n%s"
                  % spec["crontab"], file=sys.stderr)
            return 2
        print("[mission-cron] %s the loop for %s (%s)" % ("installed" if o.get("install") else "removed",
                                                          spec["project"], spec["schedule"]), file=sys.stderr)
        return 0
    if o.get("json"):
        print(json.dumps(spec, indent=1, sort_keys=True))
    else:
        print(spec["crontab"])
    return 0
