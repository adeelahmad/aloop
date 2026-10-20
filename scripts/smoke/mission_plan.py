#!/usr/bin/env python3
"""mission_plan — the plan of one live mission run (ROADMAP M9.13): what e2e-mission.sh hands the driver.

    mission_plan.py scrub-names
        print the names of the variables in this environment that no session and no seed step may
        inherit (one per line), for the shell to unset
    mission_plan.py preflight-plugins < `claude plugin list --json`
        exit 2 when an aloop (or agentic-agile) plugin is installed and enabled: its hooks would run
        twice next to --plugin-dir and capture every typed line twice
    mission_plan.py preflight-network [URL]
        exit 2 when URL (default https://example.com/) cannot be fetched: research needs the web
    mission_plan.py plan --run-dir T --repo R --host H --seed-sha SHA --shim-dir D
                         [--model M] [--worker-model M] [--scrubbed "NAMES"] [--dry-run]
        write T/plan.json and print the dry run's lines (cmd:, prompt:, reply:, surface:, cron:,
        caps:, allowlist:, seed:). It never runs a host.

Every session's argv starts from plugin/mission/cron.py's host_cmd(host), so the prompt slot,
--plugin-dir and --allowedTools are exactly the cron's; the measured flags go in before
--allowedTools. A reply is a NEW session (its own --session-id) whose prompt is the slash command
and the one line the human types: never --resume, so no sprint runs inside an earlier session's
conversation. plan.json holds names of scrubbed variables only, never a value.
"""
import datetime
import hashlib
import json
import math
import os
import re
import shlex
import subprocess
import sys
import urllib.request

sys.dont_write_bytecode = True     # importing the plugin's modules must not write under plugin/
HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.realpath(os.path.join(HERE, "..", ".."))
PLUGIN = os.path.join(ROOT, "plugin")
FIXTURE_DIR = os.path.join(HERE, "fixtures", "todo-mission")
RUNNER = "docs/agents/mission/journeys/runners/todo_journey.py"
BY = "scripted-human (e2e)"

# The only grants the run relays, each the line a session showed the human, recomputed on disk first.
LINE_RE = r"^(approve|confirm) ((msn|bln)_[0-9a-f]{20}) [0-9a-f]{8}$"
ALLOWLIST = [
    {"action": "CONFIRM_MISSION", "verb": "approve", "prefix": "msn", "event": "mission.approved"},
    {"action": "CONFIRM_BASELINE", "verb": "confirm", "prefix": "bln", "event": "baseline.confirmed"},
]
# every other human action stops the run with its reason; the run never grants anything else
STOPS = {
    "ASK_CONSENT": "a gray ethics verdict needs a real human",
    "SET_MISSION": "the first session did not set the mission",
    "STOP_BUDGET": "the mission's own budget cap or deadline stopped the loop",
    "BLOCKED": "the loop is blocked: a human fixes the subject (the run never lifts a block)",
    "OFFER_OPTIONS": "a goal pick is a spoon-fed human choice",
    "APPROVE_PLAN": "a sprint plan approval is a spoon-fed human choice",
}
NEVER = ["restore", "retire", "lift a block", "consent", "goal approve", "journey approve", "telemetry consent",
         "tick --force", "cron --install"]
# tick hands these out after goal.met to raise the bar: the run kills the session that starts one
KILL_AFTER_GOAL_MET = ["RESEARCH", "DEFINE_JOURNEYS", "BASELINE"]
DEFAULT_CAPS = {"sessions": ("E2E_MAX_SESSIONS", 8, int), "grants": ("E2E_MAX_GRANTS", 4, int),
                "usd": ("E2E_BUDGET_USD", 30.0, float), "session_timeout_s": ("E2E_SESSION_TIMEOUT", 5400, int),
                "total_timeout_s": ("E2E_TIMEOUT", 14400, int),
                "poll_s": ("E2E_POLL_S", 10, float)}      # how often a running session is checked (goal.met kill)
FIXED_CAPS = {"same_line_relays": 1, "per_session_usd": 10.0, "no_progress_sessions": 2, "max_turns": 400,
              "wait_max_s": 900, "waits_per_key": 1}

# The environment no session and no seed step inherits: every gate and loop override by prefix (a
# named list misses one: GATE_RUN_TESTS=0 skips RED's tests), the worker identity a hook reads, and
# the parent Claude Code session's binding (live runs 32-34 inherited it; e2e-live.sh unsets the same).
# Also by prefix: pytest's own overrides (PYTEST_ADDOPTS deselects a test in every gate), and the git
# identity and config (GIT_AUTHOR_NAME put the operator's name on every commit, past gate-commit-author,
# and GIT_CONFIG_* can set core.hooksPath for every git a gate runs).
SCRUB_PREFIXES = ("AGENTIC_", "ALADDIN_", "ALOOP_", "GATE_", "PYTEST_", "GIT_AUTHOR_", "GIT_COMMITTER_", "GIT_CONFIG")
# git addresses the run's own repo only (a GIT_DIR from a hook or a shell would redirect the seed)
GIT_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_COMMON_DIR", "GIT_NAMESPACE")
# The per-dispatch gate contract: _gatelib.sh reads these from the environment first and loads a key from the
# supervisor's task.env only when it is unset ("Real env still overrides"), so an inherited REPO_DIR or
# SCOPE_GLOBS judges another tree or widens GREEN's scope. BASH_ENV runs inside every bash gate.
GATE_CONTRACT = ("REPO_DIR", "STORY_DIR", "SPRINT_DIR", "BASE_REF", "SCOPE_GLOBS", "TEST_GLOBS", "SCAFFOLD_SYMBOLS",
                 "STANDARDS_FILE", "INTAKE_FILE", "MEMORY_FILE", "TASK_CONTRACT", "TOOL_FILE_PATH", "CLAUDE_ENV_FILE",
                 "CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT", "BASH_ENV")


def task_env_keys():
    """_gatelib.sh's TASK_ENV_KEYS (every contract key a gate loads only when the env leaves it unset): read from
    the file, so a key added there is scrubbed too."""
    try:
        with open(os.path.join(PLUGIN, "bin", "_gatelib.sh"), encoding="utf-8", errors="replace") as f:
            m = re.search(r'^TASK_ENV_KEYS="([^"]*)"', f.read(), re.M)
    except OSError:
        return ()
    return tuple(m.group(1).split()) if m else ()


SCRUB_NAMES = ("SKIP_HOOKS", "TASK_ID", "ATTEMPT", "AGENT_ROLE", "SPRINT_LOCK_TTL") + GATE_CONTRACT + GIT_VARS + \
    task_env_keys()
SCRUB_SUFFIXES = ("_SPRINT_LOCK_TTL",)
PARENT_SESSION = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_REMOTE_SESSION_ID", "CLAUDE_CODE_REMOTE_SDK_URL",
                  "CLAUDE_COWORK_MEMORY_PATH_OVERRIDE", "CLAUDE_MEMORY_STORES",
                  "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN")
# The host's own settings an operator's shell (or a parent session) carries: --setting-sources project does not
# cover the environment, so these would change what is measured with no trace (a model for every worker that
# overrides aloop's routing, the effort, thinking, compaction, the subagent depth, extra tools, another dir's
# CLAUDE.md, a synced plugin or skill). Auth, proxy and transport variables stay; plan.json and summary.json name
# every CLAUDE*/ANTHROPIC* variable a session still inherits (host_kept), never a value.
HOST_BEHAVIOUR = ("CLAUDE_EFFORT", "MAX_THINKING_TOKENS", "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
                  "CLAUDE_CODE_AUTO_COMPACT_WINDOW", "CLAUDE_CODE_MAX_OUTPUT_TOKENS", "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH",
                  "CLAUDE_CODE_COORDINATOR_EXTRA_TOOLS", "CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_ENTRYPOINT",
                  "CLAUDE_ADDITIONAL_DIRECTORIES", "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD",
                  "CLAUDE_AFTER_LAST_COMPACT", "CLAUDE_AUTO_BACKGROUND_TASKS", "CLAUDE_CODE_SYNC_PLUGINS",
                  "CLAUDE_CODE_SYNC_SKILLS", "CLAUDE_CODE_SKILL_PROPOSALS", "CLAUDE_CODE_TERMINAL_MCP_TOOLS",
                  "CLAUDE_CODE_SILENT_TURN_REMINDER", "CLAUDE_PID")
HOST_MODEL = re.compile(r"^(?:ANTHROPIC|CLAUDE)_\w*MODEL$")      # ANTHROPIC_MODEL, ANTHROPIC_DEFAULT_*_MODEL, CLAUDE_CODE_SUBAGENT_MODEL
HOST_VAR = re.compile(r"^(?:ANTHROPIC|CLAUDE)")
DEVIATIONS = ["ALOOP_MISSION_LOOP=<driver pid> is set on every session, the first and the replies too: a handout "
              "from an ended loop session is then due again at once instead of leased for 2h (due.py), which a "
              "run of a few hours needs. It labels the human's sessions as loop sessions."]
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def scrubbed(name):
    return name.startswith(SCRUB_PREFIXES) or name in SCRUB_NAMES or name.endswith(SCRUB_SUFFIXES) \
        or name in PARENT_SESSION or name in HOST_BEHAVIOUR or HOST_MODEL.match(name) is not None


def host_kept(env):
    """The host variables a session still inherits (auth, proxy, transport): names only, for the record."""
    return sorted(k for k in env if HOST_VAR.match(k) and not scrubbed(k) and IDENT.match(k))


def scrub(env):
    return {k: v for k, v in env.items() if not scrubbed(k)}


def session_env(env, driver_pid, shim_dir):
    """A session's environment: the scrubbed one, the loop's pid, and the failing crontab shim first on PATH."""
    out = scrub(env)
    out["ALOOP_MISSION_LOOP"] = str(driver_pid)
    out["PATH"] = shim_dir + (os.pathsep + out["PATH"] if out.get("PATH") else "")
    return out


# ── the first message: the SET_MISSION answers, set now ──────────────────────────────────────────
def answers(today=None):
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    return {"statement": "Anyone can add a task to this to-do app and see it in their list straight away.",
            "mode": "autonomous", "budget_usd": 25.0, "deadline": (today + datetime.timedelta(days=2)).isoformat(),
            "cadence": "6h",
            "constraints": [
                "Measure the product only with %s as it is (one journey; its docstring names the steps and the pin)."
                % RUNNER,
                "No rival product is installed or run (record each named rival as ruled out by this constraint).",
                "Research uses at most 3 sources."],
            "by": BY}


def first_prompt(a, lead):
    lines = [lead,
             "Set the mission now with these answers (do not ask me the setting questions), each value exactly as written:",
             "Mission: %s" % a["statement"],
             "Mode: %s" % a["mode"],
             "Budget: %g USD, deadline %s" % (a["budget_usd"], a["deadline"]),
             "Cadence: %s" % a["cadence"]]
    lines += ["Constraint: %s" % c for c in a["constraints"]]
    lines += ["Name for the records (--by): %s" % a["by"],
              "Decline the cron: I start each session myself."]
    return "\n".join(lines)


# ── argv ───────────────────────────────────────────────────────────────────────────────────────────
def cron_module():
    sys.path.insert(0, os.path.join(PLUGIN, "mission"))
    import cron
    return cron


def host_argv(cron, host, model):
    """cron.host_cmd(host) with the session's prompt slot as <PROMPT> and the measured flags before
    --allowedTools (Claude Code's --allowedTools takes every word after it)."""
    base = cron.host_cmd(host)
    m = ["--model", model] if model else []
    if host == "claude":
        i = base.index("--allowedTools")
        if base[1] != "-p" or base[2] != cron.PROMPT["claude"]:
            raise SystemExit("mission_plan: cron.host_cmd('claude') changed shape: %r" % base)
        flags = m + ["--setting-sources", "project", "--strict-mcp-config", "--output-format", "stream-json",
                     "--verbose", "--max-turns", str(FIXED_CAPS["max_turns"]), "--max-budget-usd", "<MAX_BUDGET_USD>",
                     "--session-id", "<SESSION_ID>"]
        return base[:2] + ["<PROMPT>"] + base[3:i] + flags + base[i:]
    if base[-1] != cron.PROMPT["other"]:
        raise SystemExit("mission_plan: cron.host_cmd(%r) changed shape: %r" % (host, base))
    out = ["--json"] if host == "codex" else ["--format", "json"]
    return base[:-1] + out + m + ["<PROMPT>"]


def caps():
    out = {}
    for k, (var, default, typ) in DEFAULT_CAPS.items():
        v = os.environ.get(var)
        try:
            out[k] = typ(v) if v not in (None, "") else default
        except (ValueError, OverflowError):
            raise SystemExit("mission_plan: %s=%r is not a number" % (var, v))
        if not math.isfinite(out[k]) or out[k] <= 0:       # nan <= 0 is False: a nan cap would never stop the run
            raise SystemExit("mission_plan: %s=%r must be a finite number above 0" % (var, v))
    out.update(FIXED_CAPS)
    return out


def sha256_file(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def crontab_before():
    """What `crontab -l` printed before the run (its sha256 and exit), for the post-run check."""
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return {"exit": r.returncode, "sha256": hashlib.sha256(r.stdout).hexdigest()}


def plan(o):
    cron = cron_module()
    a = answers()
    lead = cron.PROMPT["claude"] if o.host == "claude" else cron.PROMPT["other"]
    first = first_prompt(a, lead)
    argv = host_argv(cron, o.host, o.model)
    sessions = {
        "first": {"when": "once, at the start", "prompt": first},
        "reply": {"when": "a grant on the allowlist is pending and the previous session's final result text "
                          "showed the human its exact line", "prompt": lead + "\n<LINE>"},
        "surface": {"when": "a grant is pending but the previous session never showed its line (once per grant)",
                    "prompt": lead},
        "cron": {"when": "tick --peek names a role or the supervisor and due (recording nothing) says due",
                 "prompt": lead},
    }
    for s in sessions.values():
        s.update(argv=list(argv), new_session=True)
    runner = os.path.join(o.repo, RUNNER)
    doc = {
        "kind": "mission-e2e-plan", "version": 1, "fixture": "todo", "host": o.host,
        "dry_run": bool(o.dry_run), "live": not o.dry_run and o.host == "claude",
        "run_dir": o.run_dir, "repo": o.repo, "plugin": PLUGIN, "sessions_dir": os.path.join(o.run_dir, "sessions"),
        "models": {"supervisor": o.model, "worker": o.worker_model},
        "by": BY, "answers": a,
        "first_prompt": first, "first_prompt_sha256": hashlib.sha256(first.encode("utf-8")).hexdigest(),
        "sessions": sessions,
        "placeholders": {"<PROMPT>": "the session's prompt (argv, never stdin)",
                         "<SESSION_ID>": "a new uuid4 for every session (a reply too: never --resume)",
                         "<MAX_BUDGET_USD>": "min(per_session_usd, the outer cap's remainder)",
                         "<LINE>": "the grant line, as the previous session showed it and as recomputed on disk"},
        "line_re": LINE_RE, "allowlist": ALLOWLIST, "stops": STOPS, "never": NEVER,
        "kill_after_goal_met": KILL_AFTER_GOAL_MET, "caps": caps(),
        "outer_cap": "max(the host's summed total_cost_usd, tick's own estimate from tick --peek --json budget)",
        "seed": {"sha": o.seed_sha, "commits": 2, "runner": RUNNER, "runner_sha256": sha256_file(runner),
                 "fixture_runner_sha256": sha256_file(os.path.join(FIXTURE_DIR, RUNNER)),
                 "fixture": os.path.relpath(FIXTURE_DIR, ROOT)},
        "env": {"scrub_prefixes": list(SCRUB_PREFIXES), "scrub_names": list(SCRUB_NAMES),
                "scrub_suffixes": list(SCRUB_SUFFIXES), "parent_session": list(PARENT_SESSION),
                "host_behaviour": list(HOST_BEHAVIOUR), "host_model": HOST_MODEL.pattern,
                "scrubbed": sorted(set(n for n in (o.scrubbed or "").split() if IDENT.match(n))),
                "host_kept": host_kept(os.environ),
                "set": {"ALOOP_MISSION_LOOP": "<driver pid>"}, "deviations": DEVIATIONS},
        "crontab_shim": os.path.join(o.shim_dir, "crontab"), "session_path_first": o.shim_dir,
        "crontab_before": crontab_before(),
        "created_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with open(os.path.join(o.run_dir, "plan.json"), "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write("\n")
    show = lambda k: shlex.join(sessions[k]["argv"])   # noqa: E731
    print("host: %s · supervisor model: %s · worker model: %s" % (o.host, o.model or "<session model>", o.worker_model))
    print("cmd: %s" % show("first"))
    print("prompt: argv, a new session (sha256 %s):" % doc["first_prompt_sha256"])
    for ln in first.splitlines():
        print("    " + ln)
    print("reply: %s  (prompt %r, a new session per grant line)" % (show("reply"), sessions["reply"]["prompt"]))
    print("surface: %s  (prompt %r, once when a pending grant's line was never shown)" % (show("surface"), lead))
    print("cron: %s  (prompt %r, when due)" % (show("cron"), lead))
    c = doc["caps"]
    print("caps: sessions %d · grants %d · same line relayed %d · $%g outer (%s), $%g a session · session %ds · "
          "total %ds · no progress %d sessions · turns %d · a WAIT once per key, at most %ds"
          % (c["sessions"], c["grants"], c["same_line_relays"], c["usd"], doc["outer_cap"], c["per_session_usd"],
             c["session_timeout_s"], c["total_timeout_s"], c["no_progress_sessions"], c["max_turns"], c["wait_max_s"]))
    print("allowlist: %s; ASK_CONSENT stops the run (%s)" % (
        ", ".join("%s %s_… (%s)" % (x["verb"], x["prefix"], x["action"]) for x in ALLOWLIST), STOPS["ASK_CONSENT"]))
    print("seed: %s (2 commits; runner %s, sha256 %s)" % (o.seed_sha, RUNNER, doc["seed"]["runner_sha256"][:12]))
    return 0


# ── the gate log: what counts as a pass (the driver's checks and the summary read it alike) ───────────
GATE_PASSES = {"red": "gate-red-verify", "green": "gate-green-verify", "final": "gate-final"}
HARD_LIMITS = ("task.token_hard_limit", "task.time_hard_timeout")


def run_events(repo):
    """The run log's events (aladdin.event/1), either home's."""
    for h in (".aladdin", ".agentic"):
        p = os.path.join(repo, h, "logs", "events.jsonl")
        if os.path.isfile(p):
            out = []
            with open(p, encoding="utf-8", errors="replace") as f:
                for ln in f:
                    try:
                        e = json.loads(ln)
                    except ValueError:
                        continue
                    if isinstance(e, dict):
                        out.append(e)
            return out
    return []


def budget_released(events):
    """(task, attempt) of every attempt bin/budget stopped at its hard limit. _gatelib's budget_release lets its
    SubagentStop gate exit 0 with no `released` field, so the gate log alone shows a pass (ROADMAP M9.60)."""
    out = set()
    for e in events:
        ids = e.get("ids") if isinstance(e.get("ids"), dict) else {}
        task, attempt = e.get("task") or ids.get("task"), e.get("attempt") or ids.get("attempt")
        if e.get("event") in HARD_LIMITS and task:
            out.add((str(task), str(attempt or "")))
    return out


def budget_marked(v):
    """The gate line's worktree holds a budget-exceeded marker for its role and attempt (what budget_release reads)."""
    want, wt = "role=%s attempt=%s" % (v.get("role") or "", v.get("attempt") or ""), v.get("wt")
    if not isinstance(wt, str) or not os.path.isabs(wt):       # no worktree named: nothing to read (never the cwd's)
        return False
    for h in (".aladdin", ".agentic"):
        try:
            with open(os.path.join(wt, h, "budget-exceeded"), encoding="utf-8", errors="replace") as f:
                if want in f.read():
                    return True
        except OSError:
            continue
    return False


def gate_pass(v, released):
    """A host-run pass: exit 0, selfcheck false (a worker's AGENTIC_SELFCHECK=1 run has no hook payload, no role
    binding and no gate.passed: it is not a gate run), no `released` field (a stuck release), and not an attempt
    released for its budget (released: budget_released's set)."""
    return v.get("exit") == 0 and v.get("selfcheck") is False and "released" not in v \
        and (str(v.get("task") or ""), str(v.get("attempt") or "")) not in released and not budget_marked(v)


# ── preflight (live only) ──────────────────────────────────────────────────────────────────────────
def preflight_plugins(text):
    try:
        rows = json.loads(text)
    except ValueError:
        print("preflight: `claude plugin list --json` printed no JSON; refusing (an installed aloop would double "
              "every hook)", file=sys.stderr)
        return 2
    on = [r.get("id") for r in rows if isinstance(r, dict) and r.get("enabled")
          and str(r.get("id") or "").split("@")[0] in ("aloop", "agentic-agile")]
    if on:
        print("preflight: %s is installed and enabled: its hooks would run next to --plugin-dir and capture every "
              "typed line twice; disable it first (claude plugin disable %s)" % (", ".join(on), on[0]), file=sys.stderr)
        return 2
    return 0


def preflight_network(url):
    try:
        urllib.request.urlopen(url, timeout=20).read(64)
    except Exception as e:     # any failure: research would fail inside a paid session
        print("preflight: %s is not reachable (%s: %s): research archives its sources over the web"
              % (url, type(e).__name__, e), file=sys.stderr)
        return 2
    return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if argv else 64
    cmd = argv[0]
    if cmd == "scrub-names":
        for k in sorted(os.environ):
            if scrubbed(k) and IDENT.match(k):
                print(k)
        return 0
    if cmd == "preflight-plugins":
        return preflight_plugins(sys.stdin.read())
    if cmd == "preflight-network":
        return preflight_network(argv[1] if len(argv) > 1 else "https://example.com/")
    if cmd == "plan":
        import argparse
        p = argparse.ArgumentParser(prog="mission_plan.py plan")
        for f in ("--run-dir", "--repo", "--host", "--seed-sha", "--shim-dir"):
            p.add_argument(f, required=True)
        p.add_argument("--model", default="")
        p.add_argument("--worker-model", default="economical")
        p.add_argument("--scrubbed", default="")
        p.add_argument("--dry-run", action="store_true")
        return plan(p.parse_args(argv[1:]))
    print("mission_plan: unknown command %r (mission_plan.py --help)" % cmd, file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
