"""cron — the mission loop on the host's scheduler (ROADMAP §4.12, M9.8).

  mission cron [--host claude|codex|opencode] [--every 30m] [--json] [--install | --remove]
        print the crontab line that drives the loop. Every --every (default 30m, never longer
        than the mission's cadence) cron runs `aloop mission cron --fire`, which decides like
        `aloop mission due` (reads files, spends no tokens) and starts a host session that runs
        the tick (/aloop:mission) only when one is due. --json also gives a routine spec for a
        host scheduler (a Claude Code routine): it is a model session on every run, so its
        schedule is the cadence. --install puts the line in the user's crontab, replacing an
        earlier one for this project; --remove takes it out. No other line is touched.
  mission cron --fire [--host claude|codex|opencode]
        what the crontab line runs: holds the loop lock (<run data>/state/mission-loop.lock)
        while the host session runs, so a second fire never starts a second session; prints a
        human action once without starting one; logs to <run data>/logs/mission-cron.log
        (resolved at each run, so `aloop layout migrate` does not break the line).
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import due as D  # noqa: E402
import tick as T  # noqa: E402

NO_ARGS = True            # `mission cron` with no arguments runs (plugin/bin/mission run_area)
HOSTS = ("claude", "codex", "opencode")
EVERY = re.compile(r"^([1-9][0-9]*)([mhd])$")
MINUTES = (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30)     # the minute steps cron can repeat evenly
STEPS = MINUTES + (60, 120, 180, 240, 360, 480, 720, 1440)
# a headless supervisor runs the playbook and dispatches the mission roles; research needs the web
TOOLS = "Agent Task Bash Read Write Edit MultiEdit Glob Grep Skill TaskStop TodoWrite WebSearch WebFetch"
PROMPT = {"claude": "/aloop:mission",
          "other": "Use the aloop mission skill (`aloop-mission`, or `mission` from the aloop plugin): run "
                   "`aloop mission tick` and act on the one action it names."}
# Codex's --full-auto sandbox has no network by default; research and journeys need it and web search
CODEX_WEB = ["-c", "sandbox_workspace_write.network_access=true", "-c", "tools.web_search=true"]


def minutes(every):
    m = EVERY.match(every or "")
    if not m:
        return None
    return int(m.group(1)) * {"m": 1, "h": 60, "d": 1440}[m.group(2)]


def schedule(every):
    n = minutes(every)
    if n in MINUTES:
        return "* * * * *" if n == 1 else "*/%d * * * *" % n
    if n == 60:
        return "0 * * * *"
    if n and n % 60 == 0 and n < 1440 and 1440 % n == 0:
        return "0 */%d * * *" % (n // 60)
    if n == 1440:
        return "0 0 * * *"
    return None


def as_every(mins):
    return "%dm" % mins if mins < 60 else "%dh" % (mins // 60) if mins < 1440 else "1d"


def default_every(cadence_ms):
    return "%dm" % max(x for x in MINUTES if x <= min(30, max(1, cadence_ms // 60000)))


def cadence_every(cadence_ms):
    """The longest even cron step no longer than the cadence (a routine pays per run)."""
    return as_every(max(x for x in STEPS if x <= max(1, cadence_ms // 60000)))


def host_cmd(host):
    if host == "claude":
        exe = shutil.which("claude") or "claude"
        cmd = [exe, "-p", PROMPT["claude"]]
        if "/.claude/plugins/" not in T.M.PLUGIN + "/":       # not installed by Claude Code: load it from here
            cmd += ["--plugin-dir", T.M.PLUGIN]
        return cmd + ["--allowedTools", TOOLS]
    if host == "codex":
        return [shutil.which("codex") or "codex", "exec", "--full-auto"] + CODEX_WEB + [PROMPT["other"]]
    return [shutil.which("opencode") or "opencode", "run", PROMPT["other"]]


def cron_escape(s):
    return s.replace("%", "\\%")       # cron turns a bare % into a newline


def render(cwd, host, every, cadence_ms):
    root = os.path.realpath(T._paths.project(cwd))
    aloop = os.path.join(T.BIN, "aloop")
    q = shlex.quote
    cmd = "cd %s && export PATH=%s && exec %s mission cron --fire --host %s" % (
        q(root), q(os.environ.get("PATH", "/usr/bin:/bin")), q(aloop), host)
    marker = cron_escape("# aloop-mission %s" % root)
    return {"host": host, "every": every, "schedule": schedule(every), "project": root, "marker": marker,
            "crontab": "%s %s %s" % (schedule(every), cron_escape(cmd), marker), "bin": aloop,
            "routine": {"schedule": schedule(cadence_every(cadence_ms)), "every": cadence_every(cadence_ms),
                        "prompt": "In the project at %s, run `aloop mission due`. Exit 0: use the aloop mission skill "
                                  "(/aloop:mission) to run the tick it names. Exit 3: tell the human what it printed, "
                                  "then stop. Any other exit: stop at once, nothing is due." % root,
                        "note": "every run of a routine is a model session, even when nothing is due, and the loop "
                                "needs this checkout's run data (%s/) to persist between runs; the crontab line "
                                "decides before any model starts" % T._paths.home_rel(cwd)}}


def crontab(lines_fn):
    try:
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    except OSError:
        return None
    lines = cur.stdout.splitlines() if cur.returncode == 0 else []
    new = lines_fn(lines)
    r = subprocess.run(["crontab", "-"], input="\n".join(new) + "\n", text=True, capture_output=True)
    return r.returncode == 0


def installed(spec):
    """This project's installed line, when `crontab -l` works and has one."""
    try:
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    except OSError:
        return None
    for ln in cur.stdout.splitlines() if cur.returncode == 0 else []:
        if ln.rstrip().endswith(spec["marker"]):
            return ln
    return None


def stamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def fire(cwd, host):
    log = open(os.path.join(T._paths.home(cwd, "logs"), "mission-cron.log"), "a")

    def say(msg):
        log.write("%s %s\n" % (stamp(), msg))
        log.flush()

    fd = os.open(D.lock_path(cwd, make=True), os.O_RDWR | os.O_CREAT, 0o644)
    if D.fcntl is not None:
        try:
            D.fcntl.flock(fd, D.fcntl.LOCK_EX | D.fcntl.LOCK_NB)
        except OSError:
            return 1                     # a session is running: the lock is its lease
    try:
        r = D.due(cwd, locked=True)
    except Exception as e:
        say("BLOCK[mission-cron]: the mission facts could not be read (%s: %s); run `aloop mission tick --peek`"
            % (type(e).__name__, e))
        return 2
    if not r["due"]:
        if r.get("recorded"):
            say("[mission] the human is needed: %s — %s" % (r["action"], r["reason"]))
        return r["code"]
    os.ftruncate(fd, 0)
    os.write(fd, json.dumps({"pid": os.getpid(), "host": host, "action": r["action"], "at": stamp()}).encode())
    cmd = host_cmd(host)
    say("[mission-cron] due: %s — %s; starting %s" % (r["action"], r["reason"], os.path.basename(cmd[0])))
    try:
        rc = subprocess.call(cmd, cwd=os.path.realpath(T._paths.project(cwd)), stdout=log, stderr=log,
                             stdin=subprocess.DEVNULL, env=dict(os.environ, ALOOP_MISSION_LOOP=str(os.getpid())))
    except OSError as e:
        say("BLOCK[mission-cron]: %s could not start (%s)" % (cmd[0], e))
        return 2
    say("[mission-cron] %s exited %d" % (os.path.basename(cmd[0]), rc))
    return rc


def main(argv, cwd):
    try:
        o = T.parse(argv, ("--json", "--install", "--remove", "--fire"), ("--host", "--every"))
    except ValueError as e:
        print("mission cron: %s (mission cron --help)" % e, file=sys.stderr)
        return 64
    host = o.get("host") or os.environ.get("AGENTIC_HOST") or "claude"
    if host not in HOSTS:
        print("BLOCK[mission-cron]: --host must be one of %s" % ", ".join(HOSTS), file=sys.stderr)
        return 64
    if o.get("fire"):
        return fire(cwd, host)
    mdoc, _ = T.M.load(T.M.path_of(cwd, "mission"))
    c = mdoc.get("cadence") if isinstance(mdoc, dict) else None
    cad = (T.cli().cadence_seconds(c if isinstance(c, str) else None) or 6 * 3600) * 1000
    every = o.get("every") or default_every(cad)
    bad = []
    if not schedule(every):
        bad.append("--every must repeat evenly: 1-30m dividing 60 (5m, 15m, 30m), 1h-12h dividing 24, or 1d")
    elif minutes(every) * 60000 > cad:
        bad.append("--every %s is longer than the mission's cadence (%s): the loop would re-assess late"
                   % (every, as_every(cad // 60000)))
    if o.get("install") and o.get("remove"):
        bad.append("--install or --remove, not both")
    for b in bad:
        print("BLOCK[mission-cron]: %s" % b, file=sys.stderr)
    if bad:
        return 64
    spec = render(cwd, host, every, cad)
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
    cur = installed(spec)
    if cur and cur != spec["crontab"]:
        print("[mission-cron] the installed line differs (another plugin version, host or schedule); "
              "`aloop mission cron --install` replaces it", file=sys.stderr)
    if o.get("json"):
        print(json.dumps(spec, indent=1, sort_keys=True))
    else:
        print(spec["crontab"])
    return 0
