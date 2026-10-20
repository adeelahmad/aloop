#!/usr/bin/env python3
"""mission_driver — the live loop of one mission run (ROADMAP M9.13): e2e-mission.sh hands it plan.json.

    mission_driver.py --plan RUN/plan.json

It plays the human in three ways only, then checks the run:
  first    the first message (plan.json's first_prompt: the SET_MISSION answers, set now)
  reply    a grant line, typed in a NEW session (its own --session-id, never --resume) whose prompt is
           "/aloop:mission\\n<line>". Only mission approve and baseline confirm, and only a line that
           (1) is the one `mission tick --peek --json`'s reason names, (2) equals the line recomputed on
           disk (the code: the mission's sha256 from `mission check --json`, or the baseline's digest),
           (3) passes the pre-grant checks (mission.json is the typed answers, whole; one journey on the
           seeded runner run as `python3 <runner>` with nothing else beside it, no benchmark, no rival, no
           looser tolerance, measured twice and agreeing, failing at baseline), and (4) the previous session
           showed the human: its final result text holds the exact line. The relay records the subject and
           sha256 it checked; its grant must cover exactly them. A line not shown gets ONE surface session
           (prompt "/aloop:mission"); a line relayed once and still pending stops the run, as "hook did
           not capture" (its prompt never reached the prompt log) or "captured, not granted"
  cron     "/aloop:mission" when the peek names a role or the supervisor and `due` says due (called in
           process with record=False and the peek's own --now, so it records nothing)

Everything else a human does stops the run with its reason (a gray ethics verdict needs a real human);
the driver never runs a writing mission command, never writes a mission.tick or a ledger row, never
uses --force. Every session: the env scrubbed by prefix with ALOOP_MISSION_LOOP=<this pid> (a recorded
deviation), the failing crontab shim first on PATH, its own process group (a timeout or a kill takes the
whole group), the mission loop lock held as `mission cron --fire` holds it. A SIGTERM, SIGINT or SIGHUP
kills the running session's group, writes its record (killed: interrupted) and stops the run (interrupted);
the checks and driver.json still follow. A failure of the driver itself while a session runs does the same
(killed: the driver failed; the stop is driver_error). While a session runs it is
polled: after goal.met, a RESEARCH, DEFINE_JOURNEYS or BASELINE tick kills its group (cost_complete
false). Caps, from plan.json: sessions, grants, the same line relayed once, the outer USD cap on
max(the host's summed cost, tick's own estimate) with --max-budget-usd min(10, what is left), the
session and total timeouts, no progress in 2 sessions in a row, a WAIT slept once per key (at most
15 min). No retries.

Writes RUN/sessions/NN.jsonl (stream) and NN.err, RUN/sessions.jsonl (one record a session) and
RUN/driver.json (the checks, the stop, the grants, the path), which mission_summary.py (e2e-mission.sh runs
it next) folds into RUN/summary.json (kind mission) and summary.md. Prints every post-run check (each one
runs, whatever the others say). The outcome, the grants and the run's ledger, events and gate log are read
before any check runs the repo's own code (the runner, pytest), which runs as a session would: the scrubbed
env, the crontab shim first on PATH, its own process group (killed whole when it ends), and no bytecode cache
read from the repo.
Exit 0 every check passed · 1 a check failed · 2 the plan or the run dir was refused.
"""
import ast
import glob
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

sys.dont_write_bytecode = True     # importing the plugin's modules must not write under plugin/
HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.realpath(os.path.join(HERE, "..", ".."))
PLUGIN = os.path.join(ROOT, "plugin")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(PLUGIN, "bin"))
sys.path.insert(0, os.path.join(PLUGIN, "mission"))
import mission_plan as MP  # noqa: E402
import _events  # noqa: E402
import _mission as M  # noqa: E402
import _paths  # noqa: E402
import baseline as B  # noqa: E402
import due as DUE  # noqa: E402

BY = MP.BY
RUNNER = MP.RUNNER
LEAD = "/aloop:mission"
MIN_SESSION_USD = 1.0             # below this, the outer cap stops the run rather than start a session
TIMEOUT_RC = (124, 137, 142)      # timeout(1), a SIGKILL, perl's alarm: each a timeout, never a host failure
REPLY = re.compile(r"repl(?:ies|ying) `([^`]+)`, then `aloop mission ([^`]+)`")
GRANTISH = re.compile(r"^\W*(?:(?:yes|ok|okay|sure)\W+)?(approve|confirm|consent|restore|retire|veto|revoke)\b"
                      r".*\b[0-9a-f]{8,64}\b", re.I)
TRACE = re.compile(r"mission\s+msn_\S+\s+approved by scripted-human \(e2e\)")
# what drops or fakes a test: a skip or xfail mark (or a skip raised), any pytest hook or plugin (a hook that no list
# names can still re-mark, re-report or not run a test: pytest_pyfunc_call returning True), pytest's internals, and
# what swaps a module a test imports (sys.modules)
SUPPRESS = re.compile(r"pytest\.mark\.(?:skip|skipif|xfail)|pytest\.(?:skip|xfail|importorskip|exit)\b|unittest\.skip"
                      r"|@skip\b|expectedFailure|\bSkipTest\b|\bskipTest\b|\bpytest_\w+|\bcollect_ignore(?:_glob)?\b"
                      r"|\badd_marker\(|\b_pytest\b|\bpluggy\b|\bhookimpl\b|\bhookwrapper\b|\bsys\.modules\b"
                      r"|\bimport\s+pytest\s+as\b|\bfrom\s+pytest\s+import\s*\(?[\w\s,]*\b(?:skip|skipif|xfail|importorskip"
                      r"|mark|exit)\b")
# pytest's own norecursedirs (and a virtualenv): what it never collects, so tests_of never reads it
SKIP_DIRS = re.compile(r"^(?:\..*|__pycache__|node_modules|site-packages|build|dist|venv|CVS|_darcs|\{arch\}|.*\.egg)$")
# what the suppression scan leaves out: git's own dir and the worktrees the sessions worked in (not main's tree)
NOT_SCANNED = (".git",)
WORKTREE_HOMES = (".aladdin", ".agentic", ".claude")
# a module python imports that no scan can read (sourceless bytecode, an extension module)
OPAQUE = re.compile(r"\.(?:pyc|pyo|so|pyd)$")
IMPORTABLE = re.compile(r"^([A-Za-z_]\w*)(?:\.py|\.pyc|\.pyo|(?:\.[\w-]+)?\.so|(?:\.[\w-]+)?\.pyd)$")
# what a conftest.py may hold beside a docstring: imports of these, and sys.path.insert/append of a path built from
# names, strings, attributes and these calls (anything else runs before every test and can change what runs)
CONFTEST_IMPORTS = ("os", "os.path", "sys", "pathlib")
PATH_CALLS = ("dirname", "abspath", "realpath", "join", "normpath", "resolve", "absolute", "Path", "str", "getcwd", "cwd")
# a pytest config: the first of these from the repo up is pytest's inifile; a key below drops tests
PYTEST_CONFIGS = ("pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg")
PYTEST_SECTIONS = ("pytest", "tool:pytest", "tool.pytest.ini_options", "tool.pytest")
PYTEST_DROPS = re.compile(r"^\s*(?:addopts|testpaths|norecursedirs|python_files|python_classes|python_functions"
                          r"|collect_ignore\w*|required_plugins|confcutdir)\b")
CRASH = "the gate itself failed"
GIT_VARS = MP.GIT_VARS
PROGRESS_EVENTS = ("plan.created", "plan.revised", "commit.created", "worktree.merged", "intent.received",
                   "artifact.created", "artifact.updated", "gate.passed")
GATES = {"red": "gate-red-verify", "green": "gate-green-verify", "final": "gate-final"}


# ── small helpers ─────────────────────────────────────────────────────────────────────────────────
def iso(t=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if t is None else t))


def ms_of(stamp):
    """Epoch ms of an ISO-8601 UTC stamp (tick's own parser), or None."""
    return DUE.T.ms_ts(stamp) if stamp else None


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha_file(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def jsonl(path):
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
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def clean_env():
    """This process's environment as a session's would be scrubbed (no loop pid, no shim): for the CLI calls."""
    return {k: v for k, v in MP.scrub(os.environ).items() if k not in GIT_VARS}


def run(argv, cwd, timeout=300, env=None):
    try:
        return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env or clean_env(),
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(argv, 127, "", "%s: %s" % (type(e).__name__, e))


def mission(repo, *args):
    return run([os.path.join(PLUGIN, "bin", "mission")] + list(args), repo)


def git(repo, *args):
    return run(["git"] + list(args), repo, timeout=60)


def events(repo):
    return [e for e in _events.read(repo) if isinstance(e, dict)]


def stream(path):
    """A session's stream-json: its last result line, that line's final text and the top-level tool output."""
    rows = jsonl(path)
    res = [e for e in rows if e.get("type") == "result"]
    tools = []
    for e in rows:
        if e.get("type") == "user" and e.get("parent_tool_use_id") is None:
            for b in ((e.get("message") or {}).get("content") or []):
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    c = b.get("content")
                    tools.append(c if isinstance(c, str) else json.dumps(c))
    last = res[-1] if res else None
    return {"result": last, "text": str((last or {}).get("result") or ""), "tools": "\n".join(tools), "rows": len(rows)}


# ── the line the human types: from the reason, checked against the disk ──────────────────────────
def reason_line(reason):
    """(line, then) of a hand-off reason: `the human replies `<line>`, then `aloop mission <then>``."""
    m = REPLY.search(reason or "")
    return (m.group(1), m.group(2)) if m else (None, None)


def rule_of(plan, action):
    return next((x for x in plan.get("allowlist") or [] if x.get("action") == action), None)


def shown(text, line):
    """The exact line appears in the text, not as part of a longer token."""
    return bool(line) and re.search(r"(?<![\w])%s(?![\w])" % re.escape(line), text or "") is not None


def disk_line(repo, peek, plan):
    """(line, None) when the peek's action is on the allowlist and its reason names exactly the line the disk
    gives (the verb that fits, the id, the code recomputed); else (None, why)."""
    action = peek.get("action")
    rule = rule_of(plan, action)
    if not rule:
        return None, "%s is not on the allowlist (%s)" % (action, ", ".join(x["action"] for x in plan.get("allowlist") or []))
    line, then = reason_line(peek.get("reason"))
    if not line:
        return None, "the %s reason names no reply line: %s" % (action, (peek.get("reason") or "")[:200])
    m = re.match(plan["line_re"], line)
    if not m:
        return None, "the line %r is not an allowlisted grant line (verb approve|confirm, an msn_/bln_ id, an 8-hex code)" % line
    verb, ident, prefix = m.group(1), m.group(2), m.group(3)
    if verb != rule["verb"] or prefix != rule["prefix"]:
        return None, "the verb %r (%s_…) does not fit %s, which takes %s %s_…" % (verb, prefix, action, rule["verb"],
                                                                               rule["prefix"])
    if action == "CONFIRM_MISSION":
        r = mission(repo, "check", "--json")
        try:
            chk = json.loads(r.stdout)
        except ValueError:
            return None, "mission check --json printed no JSON (exit %d)" % r.returncode
        want = "approve %s %s" % (chk.get("mission_id"), M.code(chk.get("sha256") or ""))
        if chk.get("mission_id") != peek.get("mission_id", chk.get("mission_id")):
            return None, "the peek names mission %s, the disk %s" % (peek.get("mission_id"), chk.get("mission_id"))
        if not then.startswith("approve "):
            return None, "the command after the line is %r, not mission approve" % then
    else:
        path = M.path_of(repo, "baseline", ident)
        doc = load(path)
        if doc is None:
            return None, "no baseline %s on disk" % ident
        want = "confirm %s %s" % (ident, M.code(M.digest(doc)))
        if peek.get("subject") and peek["subject"] != M.rel(repo, path):
            return None, "the peek's subject %s is not baseline %s" % (peek["subject"], ident)
        if not then.startswith("baseline confirm %s " % ident):
            return None, "the command after the line is %r, not baseline confirm %s" % (then, ident)
    if line != want:
        return None, "the reason's line %r is not the one on disk %r (its code is recomputed from the content)" % (line, want)
    return line, None


def typed_mission(a):
    """mission.json's content as the typed answers set it (`mission init` with no other flag: the default veto
    window), without its id, its dates and what approval changes."""
    return {"statement": a["statement"], "mode": a["mode"], "cadence": a["cadence"],
            "budget": {"usd": float(a["budget_usd"]), "deadline": a["deadline"]}, "constraints": list(a["constraints"]),
            "veto_window_h": float(B.DEFAULT_VETO_H), "provenance": {"set_by": BY}}


MISSING = "<missing>"
NOT_TYPED = ("mission_id", "created_at") + M.UNHASHED      # what no answer sets: the id, the date, the seal, approval


def runner_extras(repo):
    """Every file or dir under the seeded runner's dir but the runner itself (a json.py there, or a helper,
    would stand in for what it imports or measure another way)."""
    d, out = os.path.join(repo, os.path.dirname(RUNNER)), []
    for top, dirs, files in os.walk(d):
        for n in sorted(dirs) + sorted(files):
            rp = os.path.relpath(os.path.join(top, n), repo)
            if rp != RUNNER:
                out.append(rp)
    return sorted(out)


def pregrant(repo, plan, action, ident=None):
    """What a careful human checks before typing the line; [] when it holds."""
    a, probs = plan["answers"], []
    if action == "CONFIRM_MISSION":
        doc = load(M.path_of(repo, "mission"))
        if doc is None:
            return ["no mission.json"]
        want = typed_mission(a)
        have = {k: v for k, v in doc.items() if k not in NOT_TYPED}
        prov = have.pop("provenance", None)
        for k in sorted(set(have) - set(want)):
            probs.append("mission.json has %s, which the typed answers do not set" % k)
        for k in ("statement", "mode", "cadence", "budget", "constraints", "veto_window_h"):
            if have.get(k, MISSING) != want[k]:
                probs.append("mission.json %s is %r, the typed answer %r" % (k, have.get(k, MISSING), want[k]))
        p = prov if isinstance(prov, dict) else {}
        if p.get("set_by") != BY or set(p) - {"set_by", "set_at"}:
            probs.append("mission.json was set by %r (provenance %r), not %s" % (p.get("set_by"), prov, BY))
        if not re.match(r"^msn_[0-9A-Za-z]+$", str(doc.get("mission_id"))):
            probs.append("mission.json's mission_id %r is not an msn_ id" % doc.get("mission_id"))
        return probs
    if action != "CONFIRM_BASELINE":
        return ["%s is not on the allowlist" % action]
    bls = M.artifacts(repo, "baseline")
    pick = [(p, d) for p, d in bls if d.get("baseline_id") == ident] if ident else bls[-1:]
    if not pick:
        return ["no baseline %s on disk" % (ident or "")]
    bdoc = pick[-1][1]
    journeys = [d for _, d in M.artifacts(repo, "journey") if d.get("status") != "retired"]
    if len(journeys) != 1:
        probs.append("%d journeys, not exactly 1 (the constraint: one journey)" % len(journeys))
    bj = bdoc.get("journeys") if isinstance(bdoc.get("journeys"), list) else []
    if len(bj) != 1:
        probs.append("baseline %s covers %d journeys, not exactly 1" % (bdoc.get("baseline_id"), len(bj)))
    if bdoc.get("benchmarks"):
        probs.append("the baseline lists benchmark(s) %r: the constraint measures one journey, nothing else"
                     % [b.get("id") if isinstance(b, dict) else b for b in bdoc["benchmarks"]])
    if bdoc.get("rivals"):
        probs.append("the baseline lists rival(s) %r: a rival on the same tasks is run, and the constraint installs "
                     "or runs none" % (bdoc["rivals"],))
    tol = bdoc.get("tolerance")
    if isinstance(tol, bool) or not isinstance(tol, (int, float)) or not 0 <= tol <= B.DEFAULT_TOLERANCE:
        probs.append("the baseline's tolerance %r is not a number from 0 to %s (the default, never looser)"
                     % (tol, B.DEFAULT_TOLERANCE))
    extras = runner_extras(repo)
    if extras:
        probs.append("other files beside the seeded runner under %s/: %s" % (os.path.dirname(RUNNER), ", ".join(extras)))
    if journeys and bj:
        j, want = journeys[0], plan["seed"]["fixture_runner_sha256"]
        if bj[0].get("journey_id") != j.get("journey_id") or bj[0].get("sha256") != M.digest(j):
            probs.append("the baseline's journey is not the journey on disk")
        rn = j.get("runner") if isinstance(j.get("runner"), dict) else {}
        if rn.get("files") != {RUNNER: want}:
            probs.append("the journey's runner pins %r, not the seeded runner %s at %s" % (rn.get("files"), RUNNER, want[:12]))
        if rn.get("command") != ["python3", RUNNER]:
            probs.append("the journey's runner command %r is not `python3 %s`" % (rn.get("command"), RUNNER))
        if sha_file(os.path.join(repo, RUNNER)) != want:
            probs.append("the runner on disk is not the seeded one (sha256 differs)")
        if [s.get("id") for s in j.get("steps") or [] if isinstance(s, dict)] != ["open", "add", "see"]:
            probs.append("the journey's steps are not open, add, see")
        pc = j.get("pass_criteria") or []
        if len(pc) != 1 or not isinstance(pc[0], dict) or pc[0].get("steps") != "all" or j.get("metrics"):
            probs.append("the journey's criterion is not the one `steps: all` (no metrics)")
        ms = bdoc.get("measurements") if isinstance(bdoc.get("measurements"), dict) else {}
        runs = ms.get("runs") if isinstance(ms.get("runs"), list) else []
        if len(runs) != 2 or ms.get("agreed") is not True:
            probs.append("baseline measured %d time(s), agreed %r: not twice and agreeing" % (len(runs), ms.get("agreed")))
        jid = j.get("journey_id")
        failing = runs and all(((r.get("journeys") or {}).get(jid) or {}).get("passed") is False for r in runs) \
            and jid in (ms.get("not_passing") or [])
        if not failing:
            probs.append("the journey does not fail at baseline: nothing to earn")
    return probs


def granted_subject(repo, action, ident):
    """(subject, sha256) of the content a line grants, as the pre-grant checks read it just before it is typed."""
    p = M.path_of(repo, "mission") if action == "CONFIRM_MISSION" else M.path_of(repo, "baseline", ident)
    doc = load(p)
    return M.rel(repo, p), (M.digest(doc) if doc is not None else None)


def wait_step(waits, peek, now_ms, wait_max_s):
    """WAIT: ("sleep", secs) once per key (action, subject, next_check), when the next check is at most
    wait_max_s away; ("stop", code, reason) for a repeat or a longer wait."""
    nc = peek.get("next_check")
    key = "%s %s %s" % (peek.get("action"), peek.get("subject") or "", nc)
    if key in waits:
        return ("stop", "wait_repeat", "WAIT came back after its one sleep (%s): %s" % (nc, peek.get("reason")))
    t = ms_of(nc)
    if t is None:
        return ("stop", "wait_long", "WAIT with no next check: %s" % peek.get("reason"))
    secs = (t - now_ms) / 1000.0
    if secs > wait_max_s:
        return ("stop", "wait_long", "WAIT until %s, %ds away (more than %ds): %s" % (nc, secs, wait_max_s,
                                                                                   peek.get("reason")))
    waits.add(key)
    return ("sleep", max(0.0, secs))


def gate_facts(repo):
    """The gate log, parsed as JSON. A pass is the host's own run (selfcheck false: a worker's self-check is no
    gate run), exit 0, no `released` field (a stuck release, either spelling), and not an attempt released for
    its budget (a hard-limit event for its task and attempt, or a budget-exceeded marker in its worktree)."""
    released = MP.budget_released(MP.run_events(repo))
    out = {"lines": 0, "stuck_releases": 0, "budget_releases": 0, "selfcheck_false": False,
           "passed": {k: False for k in GATES}, "log": None}
    for h in (".aladdin", ".agentic"):
        p = os.path.join(repo, h, "logs", "gates.jsonl")
        if not os.path.isfile(p):
            continue
        out["log"] = p
        for v in jsonl(p):
            out["lines"] += 1
            if v.get("released") == "stuck":
                out["stuck_releases"] += 1
            if v.get("exit") == 0 and ((str(v.get("task") or ""), str(v.get("attempt") or "")) in released
                                       or MP.budget_marked(v)):
                out["budget_releases"] += 1
            if v.get("selfcheck") is False:
                out["selfcheck_false"] = True
            for k, name in GATES.items():
                if v.get("gate") == name and MP.gate_pass(v, released):
                    out["passed"][k] = True
        break
    return out


def kill_group(proc):
    """SIGTERM the session's process group, then SIGKILL what is left; the leader is reaped as it goes."""
    for sig, grace in ((signal.SIGTERM, 3.0), (signal.SIGKILL, 1.0)):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        end = time.time() + grace
        while time.time() < end:
            proc.poll()
            try:
                os.killpg(proc.pid, 0)
            except (ProcessLookupError, PermissionError):
                return
            time.sleep(0.05)


def contained(c, argv, timeout):
    """The repo's own code, run as a session runs it: the scrubbed env with the loop's pid and the crontab shim
    first on PATH, no bytecode written and none read from the repo, its own process group (killed whole when it
    ends or times out, 124). (rc, stdout, stderr)."""
    first = c.plan.get("session_path_first")
    env = MP.session_env(os.environ, c.run.get("driver_pid") or os.getpid(), first) if first else clean_env()
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")     # no plugin a session installed
    cache = tempfile.mkdtemp(prefix="aloop-e2e-pycache-")
    env["PYTHONPYCACHEPREFIX"] = cache      # bytecode is read from (and written to) an empty dir: never a __pycache__
    try:                                    # entry a session planted, the source's mtime and size in its header
        return _contained(c, argv, timeout, env)
    finally:
        shutil.rmtree(cache, ignore_errors=True)


def _contained(c, argv, timeout, env):
    with tempfile.TemporaryFile() as so, tempfile.TemporaryFile() as se:
        proc = None
        try:
            proc = subprocess.Popen(argv, cwd=c.repo, stdin=subprocess.DEVNULL, stdout=so, stderr=se, env=env,
                                    start_new_session=True)
            try:
                rc = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                rc = 124
        except OSError as e:
            return 127, "", "%s: %s" % (type(e).__name__, e)
        finally:
            if proc is not None:
                kill_group(proc)
                proc.poll()
        so.seek(0)
        se.seek(0)
        return rc, so.read().decode("utf-8", "replace"), se.read().decode("utf-8", "replace")


def walk(top):
    """Every file under top that pytest would collect from (its norecursedirs and virtualenvs left out), sorted."""
    out = []
    for d, dirs, files in os.walk(top):
        dirs[:] = sorted(x for x in dirs if not SKIP_DIRS.match(x)
                         and not os.path.isfile(os.path.join(d, x, "pyvenv.cfg")))
        out += [os.path.join(d, f) for f in sorted(files)]
    return out


def scanned(top):
    """Every file under top but git's own and the sessions' worktrees (.aladdin/worktrees …), sorted: a dir pytest
    never collects (a dot dir, build/, a virtualenv) can still hold what a conftest.py or a test imports."""
    out = []
    for d, dirs, files in os.walk(top):
        rel = os.path.relpath(d, top)
        dirs[:] = sorted(x for x in dirs if x not in NOT_SCANNED and not (rel in WORKTREE_HOMES and x == "worktrees"))
        out += [os.path.join(d, f) for f in sorted(files)]
    return out


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def is_test_file(path):
    b = os.path.basename(path)
    return b.endswith(".py") and (b.startswith("test_") or b.endswith("_test.py"))


TEST_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(test\w*)\s*\(", re.M)


def tests_of(repo, seed):
    """(file, name) of every test function the repo's test files define, and the seed's own tests too (a test
    deleted since the seed still has to run and pass)."""
    out = set()
    for p in walk(repo):
        if is_test_file(p):
            out |= {(os.path.relpath(p, repo), n) for n in TEST_DEF.findall(read(p))}
    if seed:
        src = git(repo, "show", "%s:tests/test_todo.py" % seed)
        if src.returncode == 0:
            out |= {("tests/test_todo.py", n) for n in TEST_DEF.findall(src.stdout)}
    return sorted(out)


PASSED = re.compile(r"^PASSED (\S+?)::(?:\w+::)*(\w+)(?:\[.*\])?\s*$")


def pytest_problems(out, tests):
    """What pytest -rA's output says beyond a pass: a skipped, xfailed, xpassed, deselected, failed or errored
    test (in the counts or a summary line), or a test with no PASSED line of its own; [] when every test ran
    and passed."""
    probs = []
    for m in re.finditer(r"\b(\d+) (skipped|xfailed|xpassed|deselected|failed|errors?)\b", out):
        if int(m.group(1)) > 0:
            probs.append("%s %s" % (m.group(1), m.group(2)))
    lines = out.splitlines()
    probs += [ln.strip()[:160] for ln in lines if re.match(r"(?:SKIPPED|XFAIL|XPASS|ERROR|FAILED)\b", ln)]
    passed = {m.groups() for m in (PASSED.match(ln) for ln in lines) if m}
    missing = ["%s::%s" % t for t in tests if tuple(t) not in passed]
    if missing:
        probs.append("no PASSED line for %s%s" % (", ".join(missing[:5]), " …" if len(missing) > 5 else ""))
    if not tests:
        probs.append("no test function found")
    return probs


def config_drops(path):
    """The keys of a pytest config file's pytest section that drop or change tests ([] for none, or no section)."""
    text, out, sect = read(path), [], None
    for ln in text.splitlines():
        m = re.match(r"^\s*\[([^\]]+)\]\s*$", ln)
        if m:
            sect = m.group(1).strip()
            continue
        if sect in PYTEST_SECTIONS and PYTEST_DROPS.match(ln):
            out.append(ln.split("=")[0].strip())
    return out


def has_pytest_section(path):
    if os.path.basename(path) in ("pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini"):
        return True      # pytest takes it as the inifile whatever it holds
    return any(re.match(r"^\s*\[(%s)\]\s*$" % "|".join(re.escape(x) for x in PYTEST_SECTIONS), ln)
               for ln in read(path).splitlines())


def path_expr(node):
    """A path built from names, strings, attributes and os.path/pathlib/str calls (no other call runs)."""
    if isinstance(node, (ast.Constant, ast.Name)):
        return isinstance(node, ast.Name) or isinstance(node.value, (str, int))
    if isinstance(node, ast.Attribute):
        return path_expr(node.value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Div)):
        return path_expr(node.left) and path_expr(node.right)
    if isinstance(node, ast.Call) and not node.keywords:
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
        return name in PATH_CALLS and (not isinstance(f, ast.Attribute) or path_expr(f.value)) \
            and all(path_expr(a) for a in node.args)
    return False


def conftest_code(path):
    """Why a conftest.py could change what a test runs, or None. pytest runs it before any test, so it may hold only a
    docstring, imports of os, sys and pathlib, and sys.path.insert/append of a path (path_expr): a hook, a swapped
    module or an import of anything else can re-mark, re-report or replace a test in ways no pattern lists."""
    try:
        tree = ast.parse(read(path))
    except (SyntaxError, ValueError):
        return "it does not parse"
    for i, n in enumerate(tree.body):
        if i == 0 and isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
            continue
        if isinstance(n, ast.Import) and all(a.name in CONFTEST_IMPORTS and not a.asname for a in n.names):
            continue
        if isinstance(n, ast.ImportFrom) and not n.level and (n.module, tuple(a.name for a in n.names)) in (
                ("os", ("path",)), ("pathlib", ("Path",))) and not n.names[0].asname:
            continue
        f = n.value.func if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) else None
        if isinstance(f, ast.Attribute) and f.attr in ("insert", "append") and isinstance(f.value, ast.Attribute) \
                and f.value.attr == "path" and isinstance(f.value.value, ast.Name) and f.value.value.id == "sys" \
                and not n.value.keywords and all(path_expr(a) for a in n.value.args):
            continue
        return "code that runs before every test, line %d: %s" % (n.lineno, type(n).__name__)
    return None


def product_modules(repo, seed):
    """{name: package|module} of the seed's top-level packages and modules (the product the tests import: todo)."""
    out = {}
    for p in git(repo, "ls-tree", "-r", "--name-only", seed).stdout.splitlines() if seed else []:
        parts = p.split("/")
        if len(parts) == 2 and parts[1] == "__init__.py":
            out[parts[0]] = "package"
        elif len(parts) == 1 and p.endswith(".py") and p not in ("conftest.py", "setup.py"):
            out.setdefault(p[:-3], "module")
    return out


def suppressions(repo, seed):
    """(file, why) for each file in the repo that can drop, skip, re-mark or fake a test: a mark or hook in any .py
    file (outside git's own dir and the worktrees), a conftest.py with code, a module that shadows the product (pytest
    puts a test's dir first on sys.path), a module no scan can read, a pytest config at the root that drops a test."""
    hits, products = [], product_modules(repo, seed)
    for p in scanned(repo):
        rp, b = os.path.relpath(p, repo), os.path.basename(p)
        parts = rp.split(os.sep)
        if "__pycache__" in parts:
            continue        # read from nowhere: the checks run python with an empty pycache prefix
        m = SUPPRESS.search(read(p)) if b.endswith(".py") else None
        if m:
            hits.append((rp, m.group(0)))
        why = conftest_code(p) if b == "conftest.py" else None
        if why:
            hits.append((rp, why))
        mod = IMPORTABLE.match(b)
        name = (parts[-2] if len(parts) > 1 else None) if b == "__init__.py" else mod.group(1) if mod else None
        own = (len(parts) > 1 and parts[0] in products) or (rp == "%s.py" % name and products.get(name) == "module")
        if name in products and not own:      # the product's own package, or its module at the root, is not a shadow
            hits.append((rp, "shadows the product module %s" % name))
        if OPAQUE.search(b):
            hits.append((rp, "a module no scan can read"))
        if len(parts) == 1 and b in PYTEST_CONFIGS and config_drops(p):
            hits.append((rp, ", ".join(config_drops(p))))
    return hits


# ── the run ───────────────────────────────────────────────────────────────────────────────────────
SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


class Interrupted(BaseException):
    """A SIGTERM, SIGINT or SIGHUP: raised once, where the run waits (a session, a sleep, a peek)."""

    def __init__(self, sig):
        BaseException.__init__(self, sig)
        self.sig = sig

    def __str__(self):
        return "signal %s" % signal.Signals(self.sig).name


class Run:
    def __init__(self, plan, plan_path):
        self.plan, self.plan_path = plan, plan_path
        self.repo, self.dir = plan["repo"], plan["run_dir"]
        self.sdir = plan.get("sessions_dir") or os.path.join(self.dir, "sessions")
        self.caps = plan["caps"]
        self.pid = os.getpid()
        self.t0 = time.time()
        self.records, self.relays, self.surfaced, self.waits = [], [], set(), set()
        self.texts = {}                  # session n -> its final result text (in memory only)
        self.no_progress, self.stop, self.tick_usd = 0, None, 0.0
        self.goal_met_s = None
        self.signalled, self.delivered, self.interruptible = None, False, False

    # signals: the first one stops the run; it is raised where the run waits, else at the next safe point
    def on_signal(self, sig, frame):
        again = self.signalled is not None
        if not again:
            self.signalled = sig
        print("driver: %s%s" % (signal.Signals(sig).name, " again: the run is already stopping" if again else
                                 ": stopping the run (the session's group is killed, its record written)"), flush=True)
        if self.interruptible:
            self.deliver()

    def deliver(self):
        """Raise the first signal, once."""
        if self.signalled is not None and not self.delivered:
            self.delivered, self.interruptible = True, False
            raise Interrupted(self.signalled)

    def waiting(self, fn, *a, **k):
        """fn(*a, **k) with a signal raised in it (the waits a signal ends: a session, a sleep, a peek)."""
        self.interruptible = True
        try:
            self.deliver()
            return fn(*a, **k)
        finally:
            self.interruptible = False

    def sleep(self, secs):
        self.waiting(time.sleep, secs)

    # facts
    def host_usd(self):
        return round(sum(r["cost_usd"] or 0 for r in self.records), 4)

    def spent(self):
        return max(self.host_usd(), self.tick_usd)

    def goal_met(self):
        return any(r["event"] == "goal.met" for r in M.ledger_rows(self.repo)[0])

    def tick_ids(self):
        return [(e.get("event_id"), (e.get("data") or {}).get("action")) for e in events(self.repo)
                if e.get("event") == "mission.tick"]

    def progress(self):
        """What a session's work changes: the ledger, the mission artifacts, Aladdin's plan/commit/merge
        events, the sprint closes and HEAD (never a mission.tick or spend)."""
        rows = M.ledger_rows(self.repo)[0]
        arts = [(k, M.rel(self.repo, p), M.digest(d)) for k in sorted(M.LAYOUT) for p, d in M.artifacts(self.repo, k)]
        evs = sum(1 for e in events(self.repo) if e.get("event") in PROGRESS_EVENTS)
        closed = 0
        for h in (".aladdin", ".agentic"):
            st = load(os.path.join(self.repo, h, "state", "sprints.json")) or {}
            if isinstance(st.get("closed"), list):
                closed = len(st["closed"])
                break
        head = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        return sha(json.dumps([len(rows), rows[-1]["hash"] if rows else "", arts, evs, closed, head]))

    def halt(self, code, reason):
        if not self.stop:
            self.stop = {"code": code, "reason": reason, "at_s": round(time.time() - self.t0, 1)}
            print("stop: %s" % reason, flush=True)

    # caps, checked before every session
    def cap_stop(self):
        c, n = self.caps, len(self.records)
        if n >= c["sessions"]:
            return "sessions_cap", "the session cap: %d sessions ran (E2E_MAX_SESSIONS)" % n
        if time.time() - self.t0 >= c["total_timeout_s"]:
            return "total_timeout", "the total wall time cap: %ds (E2E_TIMEOUT)" % c["total_timeout_s"]
        left = c["usd"] - self.spent()
        if left < MIN_SESSION_USD:
            return "usd_cap", ("the outer USD cap: max(host $%.2f, tick $%.2f) = $%.2f of $%.2f spent, $%.2f left (under "
                               "$%.2f a session)" % (self.host_usd(), self.tick_usd, self.spent(), c["usd"], left,
                                                     MIN_SESSION_USD))
        if self.no_progress >= c["no_progress_sessions"]:
            return "no_progress", "no progress in %d sessions in a row (no ledger row, artifact, plan, commit or " \
                                  "sprint close)" % self.no_progress
        return None

    def lock(self, kind):
        """The mission loop lock, held while a session runs (as `mission cron --fire` holds it)."""
        fd = os.open(DUE.lock_path(self.repo, make=True), os.O_RDWR | os.O_CREAT, 0o644)
        if DUE.fcntl is not None:
            try:
                DUE.fcntl.flock(fd, DUE.fcntl.LOCK_EX | DUE.fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                return None
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps({"pid": self.pid, "host": "claude", "action": kind, "at": iso()}).encode())
        return fd

    def kill_reason(self):
        """After goal.met, a tick that raises the bar (RESEARCH, DEFINE_JOURNEYS, BASELINE) ends the session."""
        if not self.goal_met():
            return None
        if self.goal_met_s is None:
            self.goal_met_s = round(time.time() - self.t0, 1)
        after = False
        for e in events(self.repo):
            if e.get("event") == "goal.met":
                after = True
            elif after and e.get("event") == "mission.tick":
                a = (e.get("data") or {}).get("action")
                if a in self.plan["kill_after_goal_met"]:
                    return "after goal.met: a %s tick (%s)" % (a, e.get("ts"))
        return None

    def watch(self, proc, deadline):
        timed_out, killed = False, None
        while True:
            left = deadline - time.time()
            try:
                rc = self.waiting(proc.wait, timeout=max(0.05, min(float(self.caps["poll_s"]), left)))
                break
            except subprocess.TimeoutExpired:
                pass
            if time.time() >= deadline:
                timed_out = True
                kill_group(proc)
                rc = proc.wait()
                break
            k = self.kill_reason()
            if k:
                killed = k
                kill_group(proc)
                rc = proc.wait()
                break
        kill_group(proc)            # what the session left behind in its group
        if rc in TIMEOUT_RC:
            timed_out = True
        return rc, timed_out, killed

    def session(self, kind, prompt, line=None, meta=None):
        self.deliver()
        stop = self.cap_stop()
        if stop:
            self.halt(*stop)
            return None
        n, sid = len(self.records) + 1, str(uuid.uuid4())
        budget = min(float(self.caps["per_session_usd"]), self.caps["usd"] - self.spent())
        subs = {"<PROMPT>": prompt, "<SESSION_ID>": sid, "<MAX_BUDGET_USD>": "%.2f" % budget}
        argv = [subs.get(a, a) for a in self.plan["sessions"][kind]["argv"]]
        timeout = min(self.caps["session_timeout_s"], self.caps["total_timeout_s"] - (time.time() - self.t0))
        env = MP.session_env(os.environ, self.pid, self.plan["session_path_first"])
        before, fp0 = {i for i, _ in self.tick_ids()}, self.progress()
        fd = self.lock(kind)
        if fd is None:
            self.halt("lock_held", "the mission loop lock is held by another session (%s)" % DUE.holder(self.repo))
            return None
        out, err = os.path.join(self.sdir, "%02d.jsonl" % n), os.path.join(self.sdir, "%02d.err" % n)
        start, rc, timed_out, killed, error, proc, stopped = time.time(), None, False, None, None, None, None
        try:
            with open(out, "w") as so, open(err, "w") as se:
                proc = subprocess.Popen(argv, cwd=self.repo, stdin=subprocess.DEVNULL, stdout=so, stderr=se, env=env,
                                        start_new_session=True)
                rc, timed_out, killed = self.watch(proc, start + timeout)
        except OSError as e:
            if proc is not None:            # the host started: an OSError while it runs is the driver's own failure
                stopped, killed = e, "the driver failed (%s: %s)" % (type(e).__name__, e)
            else:
                error = "%s: %s" % (type(e).__name__, e)
        except BaseException as e:      # the run is stopping (a signal) or the driver failed: the session goes first,
            stopped = e                 # whole, and its record is written before the run stops
            killed = "interrupted (%s)" % (e if isinstance(e, Interrupted) else "Ctrl-C") \
                if isinstance(e, (Interrupted, KeyboardInterrupt)) else "the driver failed (%s: %s)" % (type(e).__name__, e)
        finally:
            self.interruptible = False
            if proc is not None and (stopped is not None or rc is None):
                kill_group(proc)
                rc = proc.poll()
            os.close(fd)
        end = time.time()
        st = stream(out)
        res = st["result"] or {}
        acts = [a for i, a in self.tick_ids() if i not in before]
        fp1 = self.progress()
        rec = {"n": n, "kind": kind, "session_id": sid, "prompt_sha256": sha(line if line else prompt),
               "full_prompt_sha256": sha(prompt), "rc": rc, "secs": round(end - start, 1),
               "cost_usd": res.get("total_cost_usd") if isinstance(res.get("total_cost_usd"), (int, float)) else None,
               "turns": res.get("num_turns") or 0, "models": res.get("modelUsage") if isinstance(res.get("modelUsage"), dict) else {},
               "actions": acts, "cost_complete": bool(st["result"]) and not killed and not timed_out,
               "has_result": bool(st["result"]), "subtype": res.get("subtype"), "is_error": res.get("is_error"),
               "timed_out": timed_out, "killed": killed, "error": error, "max_budget_usd": round(budget, 2),
               "line": line, "progress": fp1 != fp0, "started_at": iso(start), "ended_at": iso(end)}
        self.texts[n] = st["text"]
        self.records.append(rec)
        with open(os.path.join(self.dir, "sessions.jsonl"), "a") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
        if line:
            self.relays.append(dict({"action": rule_kind(self.plan, line), "line": line, "n": n, "session_id": sid},
                                    **(meta or {})))
        self.no_progress = 0 if rec["progress"] else self.no_progress + 1
        print("session %d (%s): exit %s · %ss · %s · %s turns · %s%s" % (
            n, kind, rc, rec["secs"], "$%.2f" % rec["cost_usd"] if rec["cost_usd"] is not None else "no cost line",
            rec["turns"], " → ".join(acts) or "no tick", (" · killed: %s" % killed) if killed else
            (" · timed out" if timed_out else "")), flush=True)
        if stopped is not None:
            raise stopped
        self.deliver()
        return rec

    def after(self, rec):
        """True when the run ends with this session."""
        if self.goal_met():
            self.halt("goal_met", "goal.met is recorded (session %d)" % rec["n"])
            return True
        if rec["timed_out"]:
            self.halt("session_timeout", "session %d timed out (exit %s, cap %ss): no retry" % (
                rec["n"], rec["rc"], self.caps["session_timeout_s"]))
            return True
        if rec["error"] or rec["rc"] != 0 or not rec["has_result"] or rec["subtype"] != "success" or rec["is_error"]:
            self.halt("host_failure", "session %d failed on the host (exit %s, result %s, subtype %s, is_error %s%s): "
                                      "no retry" % (rec["n"], rec["rc"], "yes" if rec["has_result"] else "none",
                                                    rec["subtype"], rec["is_error"],
                                                    ", %s" % rec["error"] if rec["error"] else ""))
            return True
        return False

    def peek(self):
        now = int(time.time())
        r = self.waiting(mission, self.repo, "tick", "--peek", "--json", "--now", iso(now))
        try:
            p = json.loads(r.stdout)
        except ValueError:
            self.halt("peek_failed", "mission tick --peek --json printed no JSON (exit %d): %s" % (r.returncode,
                                                                                                 r.stderr[-300:]))
            return None, now * 1000
        b = p.get("budget") if isinstance(p.get("budget"), dict) else {}
        if isinstance(b.get("usd"), (int, float)):
            self.tick_usd = round(float(b["usd"]), 4)
        print("peek: %s (exit %d) — %s" % (p.get("action"), r.returncode, (p.get("reason") or "")[:160]), flush=True)
        return p, now * 1000

    def relay(self, peek):
        """The next step for a pending grant on the allowlist: a reply, one surface session, or a stop."""
        action = peek["action"]
        line, why = disk_line(self.repo, peek, self.plan)
        if not line:
            self.halt("line_mismatch", "nothing typed: the %s line does not check out: %s" % (action, why))
            return None
        done = [x for x in self.relays if x["line"] == line]
        if len(done) >= self.caps["same_line_relays"]:
            caught = [q for q in M.prompts(self.repo) if line in [x.strip() for x in q["text"].splitlines()]]
            if not caught:
                self.halt("hook_not_captured", "hook did not capture: `%s`, typed in session %d (%s), is not in the "
                                               "prompt log: the UserPromptSubmit hook never recorded it"
                          % (line, done[-1]["n"], done[-1]["session_id"]))
            else:
                then = reason_line(peek.get("reason"))[1]
                self.halt("captured_not_granted", "captured, not granted: `%s` was captured at %s (session %s), yet %s "
                                                  "is still pending, so no grant cites it: the session did not run "
                                                  "`aloop mission %s`, or the line went stale (%s)"
                          % (line, caught[-1]["ts"], caught[-1]["session"], action, then, peek.get("reason")))
            return None
        if len(self.relays) >= self.caps["grants"]:
            self.halt("grants_cap", "the grant cap: %d lines typed (E2E_MAX_GRANTS)" % len(self.relays))
            return None
        probs = pregrant(self.repo, self.plan, action, line.split()[1])
        if probs:
            self.halt("pregrant", "the pre-grant check failed, nothing typed (%s): %s" % (action, "; ".join(probs)))
            return None
        last = self.records[-1]
        if shown(self.texts.get(last["n"]), line):
            subject, digest = granted_subject(self.repo, action, line.split()[1])
            print("relay: `%s` (shown in session %d; the line on disk; the pre-grant checks hold on %s @ %s)" % (
                line, last["n"], subject, (digest or "")[:12]), flush=True)
            return ("reply", LEAD + "\n" + line, line, {"subject": subject, "sha256": digest})
        if line not in self.surfaced:
            self.surfaced.add(line)
            print("surface: session %d never showed `%s`%s; one surface session" % (
                last["n"], line, " (it is only in a tool's output)" if line in (stream(os.path.join(
                    self.sdir, "%02d.jsonl" % last["n"]))["tools"]) else ""), flush=True)
            return ("surface", LEAD, None)
        self.halt("never_shown", "the session never showed the human the line `%s` (sessions %s): nothing typed" % (
            line, ", ".join(str(r["n"]) for r in self.records[-2:])))
        return None

    def decide(self, peek, now_ms):
        a = peek.get("action")
        if rule_of(self.plan, a):
            return self.relay(peek)
        if a == "WAIT":
            step = wait_step(self.waits, peek, now_ms, self.caps["wait_max_s"])
            if step[0] == "stop":
                self.halt(step[1], step[2])
                return None
            left = self.caps["total_timeout_s"] - (time.time() - self.t0)
            if step[1] > left:
                self.halt("total_timeout", "a WAIT of %ds would pass the total wall time cap (%ds left)" % (step[1], left))
                return None
            return ("sleep", step[1])
        if peek.get("who") in ("role", "supervisor"):
            try:
                d = DUE.due(self.repo, now=now_ms, record=False)
            except Exception as e:      # the facts could not be read: stop, never guess
                self.halt("due_failed", "due could not read the facts (%s: %s)" % (type(e).__name__, e))
                return None
            if d.get("due"):
                return ("cron", LEAD, None)
            self.halt("not_due", "%s is not due: %s" % (a, d.get("reason")))
            return None
        self.halt("human", self.plan["stops"].get(a) or "%s is a human action the run never takes" % a)
        return None

    def loop(self):
        rec = self.session("first", self.plan["first_prompt"])
        while rec is not None and not self.after(rec):
            rec = None
            while rec is None:          # peek, then a session, a sleep (a WAIT, once per key) or a stop
                peek, now_ms = self.peek()
                nxt = self.decide(peek, now_ms) if peek is not None else None
                if nxt is None:
                    return
                if nxt[0] == "sleep":
                    print("wait: sleeping %ss (until %s)" % (round(nxt[1], 1), peek.get("next_check")), flush=True)
                    self.sleep(nxt[1])
                    continue
                rec = self.session(nxt[0], nxt[1], line=nxt[2], meta=nxt[3] if len(nxt) > 3 else None)
                if rec is None:
                    return


def rule_kind(plan, line):
    verb = line.split()[0]
    return next((x["action"] for x in plan["allowlist"] if x["verb"] == verb), None)


# ── the post-run checks: each a function of the context, each printed whatever the others say ───────
class Ctx:
    def __init__(self, plan, sessions, run):
        self.plan, self.sessions, self.run = plan, sessions, run
        self.repo = plan["repo"]
        self._c = {}

    def get(self, key, fn):
        if key not in self._c:
            self._c[key] = fn()
        return self._c[key]

    def prime(self):
        """Read the ledger, the run log and the gate log as the run left them, before a check runs the repo's
        own code (a test the sessions wrote may write to the repo: the checks judge the run, not that)."""
        return self.rows, self.events, self.gates

    @property
    def rows(self):
        return self.get("rows", lambda: M.ledger_rows(self.repo)[0])

    @property
    def events(self):
        return self.get("events", lambda: events(self.repo))

    @property
    def gates(self):
        return self.get("gates", lambda: gate_facts(self.repo))

    def human(self, event=None):
        return [r for r in self.rows if r.get("role") == "human" and (event is None or r["event"] == event)]

    def relay(self, action):
        return [x for x in self.run.get("relays") or [] if x["action"] == action]

    def seed(self):
        return (self.plan.get("seed") or {}).get("sha") or ""


def rule_event(plan, action):
    return (rule_of(plan, action) or {}).get("event")


def relay_problems(c, x):
    """Why one relayed line is not granted exactly as it was checked: exactly one human row cites it, that row
    is the allowlist's event for it, cites it from its reply session, by BY, with no data.action, and covers
    the subject and sha256 the pre-grant checks read just before it was typed, still the content on disk."""
    want, line = rule_event(c.plan, x.get("action")), x.get("line")
    rows = [r for r in c.human() if (r["data"].get("prompt") or {}).get("line") == line]
    probs = [] if len(rows) == 1 else ["%d ledger rows cite `%s`, not exactly 1 (%s)" % (
        len(rows), line, ", ".join("%s %s" % (r["event"], r["data"].get("subject")) for r in rows) or "none")]
    for r in rows:
        d = r["data"]
        if r["event"] != want:
            probs.append("`%s` is cited by a %s, not %s" % (line, r["event"], want))
        if (d.get("subject"), d.get("sha256")) != (x.get("subject"), x.get("sha256")):
            probs.append("the %s citing `%s` covers %s @ %s; the line was checked against %s @ %s" % (
                r["event"], line, d.get("subject"), (d.get("sha256") or "")[:12], x.get("subject"),
                (x.get("sha256") or "")[:12]))
        if (d.get("prompt") or {}).get("session") != x.get("session_id"):
            probs.append("the %s cites `%s` from session %s, not its reply session %s" % (
                r["event"], line, (d.get("prompt") or {}).get("session"), x.get("session_id")))
        if d.get("by") != BY or not M.cites(r) or d.get("action"):
            probs.append("the %s citing `%s` is by %r, cites it %s, action %r" % (
                r["event"], line, d.get("by"), "exactly" if M.cites(r) else "not", d.get("action")))
    doc = load(os.path.join(c.repo, x["subject"])) if x.get("subject") else None
    if doc is None or M.digest(doc) != x.get("sha256"):
        probs.append("%s on disk is not the content checked before `%s` was typed" % (x.get("subject"), line))
    return probs


def check_sessions(c):
    if not c.sessions:
        return False, "no session ran"
    bad = [s["n"] for s in c.sessions if not (s.get("rc") == 0 and s.get("has_result") and s.get("subtype") == "success"
                                             and not s.get("is_error")) and not (s.get("killed") or "").startswith("after goal.met")]
    killed = [s["n"] for s in c.sessions if (s.get("killed") or "").startswith("after goal.met")]
    return not bad, "%d session(s)%s%s" % (len(c.sessions), "; failed: %s" % bad if bad else "",
                                          "; killed by the run after goal.met: %s" % killed if killed else "")


def check_mission_typed(c):
    p = pregrant(c.repo, c.plan, "CONFIRM_MISSION")
    return not p, "; ".join(p) or "statement, mode, budget, deadline, cadence, constraints, the default veto window " \
        "as typed, nothing else; set by %s" % BY


def check_mission_approved(c):
    relays = c.relay("CONFIRM_MISSION")
    if not relays:
        return False, "no approve line was relayed"
    probs = [p for x in relays for p in relay_problems(c, x)]
    cur = M.mission(c.repo)[2]
    last = [r for r in c.human("mission.approved") if (r["data"].get("prompt") or {}).get("line") == relays[-1]["line"]]
    if cur is None or not last or cur.get("event_id") != last[-1].get("event_id"):
        probs.append("the mission's current approval (%s) is not the row citing `%s`" % (
            (cur or {}).get("event_id"), relays[-1]["line"]))
    return not probs, "; ".join(probs) or "`%s` from session %s approves exactly %s @ %s, the mission's current " \
        "approval" % (relays[-1]["line"], relays[-1]["session_id"], relays[-1].get("subject"),
                      (relays[-1].get("sha256") or "")[:12])


def confirmed_baseline(c):
    """(row, doc) of the confirmation of the last relayed confirm line, when that baseline is the current one
    and intact (its file is the content the confirmation covers); else (None, None)."""
    relays = c.relay("CONFIRM_BASELINE")
    if not relays:
        return None, None
    x = relays[-1]
    rows = [r for r in c.human("baseline.confirmed") if (r["data"].get("prompt") or {}).get("line") == x["line"]]
    path, doc, bad = B.current_intact(c.repo)
    if len(rows) != 1 or path is None or bad or M.rel(c.repo, path) != x.get("subject") \
            or rows[0]["data"].get("subject") != x.get("subject"):
        return None, None
    return rows[0], doc


def check_baseline_confirmed(c):
    relays = c.relay("CONFIRM_BASELINE")
    if not relays:
        return False, "no confirm line was relayed"
    probs = [p for x in relays for p in relay_problems(c, x)]
    path, doc, bad = B.current_intact(c.repo)
    probs += bad
    if path is None or M.rel(c.repo, path) != relays[-1].get("subject"):
        probs.append("the current baseline is %s, not %s (the one `%s` confirmed)" % (
            M.rel(c.repo, path) if path else "none", relays[-1].get("subject"), relays[-1]["line"]))
    ms = (doc or {}).get("measurements") or {}
    runs = ms.get("runs") if isinstance(ms.get("runs"), list) else []
    if len(runs) != 2 or ms.get("agreed") is not True:
        probs.append("measured %d time(s), agreed %r" % (len(runs), ms.get("agreed")))
    return not probs, "; ".join(probs) or "measured twice and agreed; `%s` from session %s confirms exactly %s @ %s, " \
        "the current baseline, intact" % (relays[-1]["line"], relays[-1]["session_id"], relays[-1].get("subject"),
                                         (relays[-1].get("sha256") or "")[:12])


def check_grants_relayed(c):
    relays = c.run.get("relays") or []
    if not relays:
        return False, "no grant line was relayed"
    probs = [p for x in relays for p in relay_problems(c, x)]
    relayed = {x["line"] for x in relays}
    rows = [r for r in c.human() if r["event"] in M.GRANT_VERBS]
    stray = [r["event"] for r in rows if ((r["data"].get("prompt") or {}).get("line") not in relayed)]
    qs = M.prompts(c.repo)
    extra = sorted({ln.strip() for q in qs for ln in q["text"].splitlines() if GRANTISH.match(ln.strip())} - relayed)
    mine = sorted(s["full_prompt_sha256"] for s in c.sessions if s.get("full_prompt_sha256"))
    exact = sorted(q["sha256"] for q in qs) == mine
    return not stray and not extra and not probs, "%d grant(s) for %d relayed line(s)%s%s%s; prompt log: %d captured, " \
        "%s the run's %d" % (len(rows), len(relays), "; " + "; ".join(probs) if probs else "",
                             "; not relayed: %s" % stray if stray else "",
                             "; other grant lines captured: %s" % extra if extra else "", len(qs),
                             "exactly" if exact else "not exactly", len(mine))


def check_allowlist(c):
    allowed = {x["event"] for x in c.plan["allowlist"]}
    other = sorted({"%s (%s)" % (r["event"], r["data"]["action"]) if r["data"].get("action") else r["event"]
                    for r in c.human() if r["event"] not in allowed or r["data"].get("action")})
    force = [e for e in c.events if e.get("event") == "mission.tick" and (e.get("data") or {}).get("action") == "FORCE"]
    stop = [e for e in c.events if e.get("event") == "mission.budget_exhausted"]
    many = len(c.run.get("relays") or []) > c.plan["caps"]["grants"]
    return not (other or force or stop or many), "human records outside the allowlist (or a restore or retire): %s; " \
        "--force: %d; budget stops: %d; lines typed: %d" % (other or "none", len(force), len(stop),
                                                           len(c.run.get("relays") or []))


def check_human_by(c):
    bad = [r["event"] for r in c.human() if r["data"].get("by") != BY]
    sets = [e for e in c.events if e.get("event") == "mission.set"]
    bad += ["mission.set" for e in sets if "by" in (e.get("data") or {}) and e["data"]["by"] != BY]
    doc = load(M.path_of(c.repo, "mission"))
    cur = M.digest(doc) if doc is not None else None
    other = [e.get("ts") for e in sets if (e.get("data") or {}).get("sha256") != cur]
    set_by = ((doc or {}).get("provenance") or {}).get("set_by")
    ok = not bad and not other and bool(c.human()) and bool(sets) and set_by == BY
    return ok, "%d human record(s), %d mission.set (mission.json set_by %r)%s%s" % (
        len(c.human()), len(sets), set_by, "; not by %s: %s" % (BY, bad) if bad else "",
        "; mission.set of other content than mission.json (it carries no by): %s" % other if other else "")


def check_ledger_intact(c):
    probs = list(M.ledger_problems(c.repo))
    path, _, bad = B.current_intact(c.repo)
    probs += bad
    r = mission(c.repo, "check", "--json")
    if r.returncode != 0:
        probs.append("mission check exit %d: %s" % (r.returncode, (r.stderr.strip().splitlines() or [""])[-1][:200]))
    return not probs, "; ".join(probs[:8]) or "every row's hash verifies and every human grant cites its line; the " \
        "confirmed baseline %s is the content its confirmation covers; mission check passes" % (
            M.rel(c.repo, path) if path else "(none)")


def check_runner(c):
    want, seed = c.plan["seed"]["fixture_runner_sha256"], c.seed()
    js = [d for _, d in M.artifacts(c.repo, "journey")]
    pins = [((d.get("runner") or {}).get("files")) for d in js]
    same = git(c.repo, "diff", "--quiet", seed, "--", RUNNER).returncode == 0 if seed else False
    tracked = git(c.repo, "cat-file", "-e", "%s:%s" % (seed, RUNNER)).returncode == 0 if seed else False
    extras = runner_extras(c.repo)
    ok = bool(js) and all(p == {RUNNER: want} for p in pins) and same and tracked \
        and sha_file(os.path.join(c.repo, RUNNER)) == want and not extras
    return ok, "%d journey(s) pinning %s; unchanged since seed %s: %s; sha256 the fixture's: %s; other files " \
        "beside it: %s" % (len(js), "the seeded runner" if js and all(p == {RUNNER: want} for p in pins) else pins,
                           seed[:12], same, sha_file(os.path.join(c.repo, RUNNER)) == want, extras or "none")


def check_failing_at_baseline(c):
    _, doc = confirmed_baseline(c)
    if doc is None:
        return False, "no confirmed baseline that is the relayed one, current and intact"
    ms = doc.get("measurements") or {}
    runs = ms.get("runs") or []
    np_ = ms.get("not_passing") or []
    ok = bool(np_) and bool(runs) and all(all((v or {}).get("passed") is False for k, v in (r.get("journeys") or {}).items()
                                              if k in np_) for r in runs)
    return ok, "not passing at baseline: %s" % (np_ or "none")


def check_goal(c):
    der = [r for r in c.rows if r["event"] == "goal.derived"]
    if len(der) != 1:
        return False, "%d goal.derived record(s), not 1" % len(der)
    d = der[0]["data"]
    rev = [r for r in c.rows if r["event"] == "ethics.reviewed" and r["data"].get("subject") == d.get("subject")
           and r["data"].get("sha256") == d.get("sha256")]
    intents = [x for x in (load(p) for p in glob.glob(os.path.join(c.repo, "docs", "agents", "intents", "*.json"))) if x
               and (x.get("provenance") or {}).get("goal_id") == d.get("goal_id")]
    ok = bool(rev) and len(intents) == 1 and all(x.get("source") == "mission" for x in intents)
    return ok, "goal %s: %d ethics review(s), %d intent(s) (%s)" % (d.get("goal_id"), len(rev), len(intents),
                                                                     ", ".join(str(x.get("source")) for x in intents))


NOT_A_PASS = "exit 0 by the host's own run: not a self-check, a stuck release or a budget release"


def check_red_green(c):
    p = c.gates["passed"]
    return p["red"] and p["green"], "RED %s, GREEN %s (%s; budget releases: %d)" % (
        "passed" if p["red"] else "no pass", "passed" if p["green"] else "no pass", NOT_A_PASS, c.gates["budget_releases"])


def check_host_gates(c):
    return c.gates["selfcheck_false"], "%d gate line(s); selfcheck false: %s" % (c.gates["lines"], c.gates["selfcheck_false"])


def check_final_gate(c):
    return c.gates["passed"]["final"], "final gate %s (%s)" % ("passed" if c.gates["passed"]["final"] else "no pass",
                                                               NOT_A_PASS)


def check_goal_met(c):
    met = [r for r in c.rows if r["event"] == "goal.met"]
    ev = mission(c.repo, "evaluate", "check").returncode if met else None
    return bool(met) and ev == 0, "%d goal.met record(s); mission evaluate check exit %s" % (len(met), ev)


def check_no_suppressed(c):
    """Nothing in the repo (or above it) that drops, skips, re-marks or fakes a test (suppressions), and no pytest
    config or conftest.py in a dir above the repo (pytest would take it)."""
    hits = ["%s (%s)" % h for h in suppressions(c.repo, c.seed())]
    d = os.path.dirname(os.path.realpath(c.repo))
    while True:
        for n in PYTEST_CONFIGS + ("conftest.py",):
            p = os.path.join(d, n)
            if os.path.isfile(p) and (n == "conftest.py" or has_pytest_section(p)):
                hits.append("%s (above the repo)" % p)
        up = os.path.dirname(d)
        if up == d:
            break
        d = up
    return not hits, "suppressed: %s" % (hits or "none")


def check_trace(c):
    seed = c.seed()
    shas = git(c.repo, "rev-list", "--no-merges", "%s..HEAD" % seed).stdout.split() if seed else []
    if not shas:
        return False, "no commit after the seed"
    untraced, product, traced_to_mission = [], [], []
    for s in shas:
        r = run([os.path.join(PLUGIN, "bin", "events"), "trace", s], c.repo)
        if r.returncode != 0:
            untraced.append(s[:12])
        files = git(c.repo, "diff-tree", "--no-commit-id", "--name-only", "-r", s).stdout.split()
        if any(f.startswith("todo/") for f in files):
            product.append(s[:12])
            if TRACE.search(r.stdout):
                traced_to_mission.append(s[:12])
    ok = not untraced and bool(product) and traced_to_mission == product
    return ok, "%d commit(s); untraced: %s; product commits %s, traced to the mission %s approved: %s" % (
        len(shas), untraced or "none", product or "none", BY, traced_to_mission or "none")


def check_authors(c):
    """Every commit on main, the seed's too, is authored and committed by the author defaults.md names (the
    seed's own author: a session that changed defaults.md, or an inherited git identity, shows here)."""
    st = _paths.settings(c.repo)
    want = "%s <%s>" % (st.get("COMMIT_AUTHOR_NAME") or "", st.get("COMMIT_AUTHOR_EMAIL") or "")
    seed = c.seed()
    seed_by = git(c.repo, "log", "-1", "--format=%an <%ae>", seed).stdout.strip() if seed else ""
    rows = [ln.split("\t") for ln in git(c.repo, "log", "--format=%H\t%an <%ae>\t%cn <%ce>", "HEAD").stdout.splitlines()]
    bad = sorted({"%s (%s / %s)" % (h[:12], a, m) for h, a, m in (r_ for r_ in rows if len(r_) == 3) if a != want or m != want})
    ok = bool(st.get("COMMIT_AUTHOR_NAME")) and want == seed_by and bool(rows) and not bad
    return ok, "%d commit(s); defaults.md's author %s (the seed's: %s); by another: %s" % (
        len(rows), want, seed_by or "none", bad[:5] or "none")


def check_no_crash(c):
    sd = c.plan.get("sessions_dir") or ""
    paths = glob.glob(os.path.join(sd, "*.jsonl")) + glob.glob(os.path.join(sd, "*.err")) + \
        glob.glob(os.path.join(c.repo, ".aladdin", "transcripts", "*", "events.jsonl")) + \
        ([c.gates["log"]] if c.gates["log"] else [])
    n = 0
    for p in paths:
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                n += f.read().count(CRASH)
        except OSError:
            continue
    return n == 0 and c.gates["stuck_releases"] == 0, "%d hook crash(es) (%r) in %d file(s); stuck releases: %d" % (
        n, CRASH, len(paths), c.gates["stuck_releases"])


def check_product(c):
    head = git(c.repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    extras = runner_extras(c.repo)
    rc, out, err = contained(c, ["python3", RUNNER], 120)
    try:
        steps = json.loads((out.strip().splitlines() or ["{}"])[-1]).get("steps") or {}
    except (ValueError, AttributeError):
        steps = {}
    calls = [p for p in glob.glob(os.path.join(c.repo, "tests", "**", "*.py"), recursive=True) if "add(" in read(p)]
    seed_test = git(c.repo, "diff", "--quiet", c.seed(), "--", "tests/test_todo.py").returncode == 0 if c.seed() else False
    ok = head == "main" and steps.get("see") is True and bool(calls) and seed_test and not extras
    return ok, "on %s: the runner (exit %s, run as a session) sees %r; tests calling add(: %d; the seed's test " \
        "unchanged: %s; other files beside the runner: %s" % (head, rc, steps.get("see"), len(calls), seed_test,
                                                               extras or "none")


def check_pytest(c):
    rc, out, err = contained(c, ["python3", "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider",
                                 "--rootdir", c.repo, "--confcutdir", c.repo], 600)
    probs = pytest_problems(out, tests_of(c.repo, c.seed()))
    tail = (out + err).strip().splitlines()[-1:] or [""]
    return rc == 0 and not probs, "exit %d: %s%s" % (rc, tail[0][:160], "; " + "; ".join(probs[:6]) if probs else "")


def check_crontab(c):
    before, now = c.plan.get("crontab_before"), MP.crontab_before()
    return before == now, "crontab -l before %s, after %s" % (before, now)


def check_cost(c):
    host = round(sum(s.get("cost_usd") or 0 for s in c.sessions), 2)
    tick = c.run.get("tick_usd") or 0.0
    cap = c.plan["caps"]["usd"]
    return max(host, tick) <= cap, "max(host $%.2f%s, tick $%.2f) of the $%.2f cap" % (
        host, "" if all(s.get("cost_complete") for s in c.sessions) else " (incomplete)", tick, cap)


def check_driver_wrote_nothing(c):
    pid = str(c.run.get("driver_pid"))
    ticks = [e for e in c.events if e.get("event") == "mission.tick"]
    foreign = [e.get("ts") for e in ticks if (e.get("data") or {}).get("loop") != pid]
    counted = sum(len(s.get("actions") or []) for s in c.sessions)
    wins = [((ms_of(s["started_at"]) or 0) / 1000.0 - 1, (ms_of(s["ended_at"]) or 0) / 1000.0 + 2) for s in c.sessions
            if s.get("started_at") and s.get("ended_at")]
    outside = [r["event"] for r in c.rows if not any(a <= (ms_of(r["ts"]) or 0) / 1000.0 <= b for a, b in wins)]
    ok = not foreign and counted == len(ticks) and not outside
    return ok, "%d mission.tick (each a session's, loop %s: %s; counted in sessions: %d); ledger rows outside a " \
               "session: %s" % (len(ticks), pid, "yes" if not foreign else "no, %d not" % len(foreign), counted,
                                outside or "none")


# In this order: what reads the run (the ledger, the artifacts, the gate log, git) first; then the two that run
# the repo's own code (the runner, pytest), as a session would; then what reads only what the run recorded.
CHECKS = [
    ("every session ended with a success result line (or the run killed it after goal.met)", check_sessions),
    ("mission.json is the typed answers, whole, set by %s" % BY, check_mission_typed),
    ("mission.approved cites the relayed approve line from its reply session and covers exactly what was checked",
     check_mission_approved),
    ("the baseline was measured twice and agreed; its one confirmation cites the relayed confirm line and covers "
     "exactly what was checked, the current baseline, intact", check_baseline_confirmed),
    ("every relayed line is cited by exactly one grant of the content checked before it was typed; no captured "
     "line grants anything else", check_grants_relayed),
    ("no grant outside the allowlist (no restore or retire), no tick --force, no budget stop", check_allowlist),
    ("every human record is by %s; every mission.set is of the mission.json checked" % BY, check_human_by),
    ("the ledger verifies (every row's hash, every human grant cites its line), the confirmed baseline is the "
     "content its confirmation covers, mission check passes", check_ledger_intact),
    ("the journey runs the seeded runner, unchanged since the seed, nothing beside it", check_runner),
    ("the journey failed at baseline (something to earn)", check_failing_at_baseline),
    ("one goal: derived, ethics-reviewed, one intent with source mission", check_goal),
    ("RED and GREEN passed (the host's run, exit 0, not a self-check, stuck or budget release)", check_red_green),
    ("the host's SubagentStop gates ran (selfcheck false in the gate log)", check_host_gates),
    ("the final gate passed (the host's run, exit 0, not a self-check, stuck or budget release)", check_final_gate),
    ("goal.met is recorded and mission evaluate check passes", check_goal_met),
    ("no suppressed tests (no skip or xfail mark, pytest hook or plugin, conftest.py with code, swapped or shadowed "
     "product module, unreadable module, or pytest config that drops a test, in the repo or above it)",
     check_no_suppressed),
    ("every commit after the seed traces; the product commit to the mission %s approved" % BY, check_trace),
    ("every commit after the seed is by the run's author (defaults.md's, the seed's own), committer too",
     check_authors),
    ("no hook crashed and no gate was stuck-released", check_no_crash),
    ("on main the runner sees the task, a test calls add(, the seed's test is unchanged", check_product),
    ("pytest passes on main: every test ran and passed (none skipped, xfailed, xpassed or deselected)", check_pytest),
    ("crontab -l is unchanged", check_crontab),
    ("the cost stayed within the outer cap", check_cost),
    ("the driver wrote no mission.tick and no ledger row", check_driver_wrote_nothing),
]


def run_checks(ctx):
    out = []
    for name, fn in CHECKS:
        try:
            ok, detail = fn(ctx)
        except Exception as e:      # a check that cannot run fails, and the rest still run
            ok, detail = False, "the check could not run (%s: %s)" % (type(e).__name__, e)
        out.append({"name": name, "ok": bool(ok), "detail": detail})
        print("%s - %s: %s" % ("ok" if ok else "FAIL", name, detail), flush=True)
    return out


# ── refusals, then the run ────────────────────────────────────────────────────────────────────────
def refuse(msg):
    print("mission_driver: %s" % msg, file=sys.stderr)
    return 2


def argv_ok(plan):
    for kind in ("first", "reply", "surface", "cron"):
        a = ((plan.get("sessions") or {}).get(kind) or {}).get("argv") or []
        if len(a) < 6 or a[1] != "-p" or a[2] != "<PROMPT>" or a[-2] != "--allowedTools":
            return "%s argv is not cron.host_cmd's shape: %r" % (kind, a)
        for need in (["--setting-sources", "project"], ["--output-format", "stream-json"], ["--max-turns", "400"],
                     ["--session-id", "<SESSION_ID>"], ["--max-budget-usd", "<MAX_BUDGET_USD>"]):
            if need[0] not in a or a[a.index(need[0]) + 1] != need[1]:
                return "%s argv lacks %s" % (kind, " ".join(need))
        if "--strict-mcp-config" not in a or "--verbose" not in a:
            return "%s argv lacks --strict-mcp-config or --verbose" % kind
        if [x for x in a if re.search(r"resume|continue|bypass|skip-permission|dangerous", x, re.I)]:
            return "%s argv resumes or bypasses: %r" % (kind, a)
    return None


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if argv else 64
    if argv[0] != "--plan" or len(argv) != 2:
        return refuse("usage: mission_driver.py --plan RUN/plan.json")
    for k in list(os.environ):          # no gate or loop override, worker identity or parent session, even run by hand
        if MP.scrubbed(k) or k in GIT_VARS:
            del os.environ[k]
    plan = load(argv[1])
    if not plan or plan.get("kind") != "mission-e2e-plan":
        return refuse("%s is not a mission-e2e plan" % argv[1])
    if plan.get("dry_run") or not plan.get("live") or plan.get("host") != "claude":
        return refuse("a dry-run plan (or another host's) never runs a host: the live run is claude only")
    bad = argv_ok(plan)
    if bad:
        return refuse(bad)
    repo, sdir, rdir = plan["repo"], plan.get("sessions_dir"), plan["run_dir"]
    if not os.path.isdir(repo) or not sdir or not os.path.isdir(sdir) or os.listdir(sdir):
        return refuse("the run dir is not a fresh one (repo %s, sessions dir %s empty?)" % (repo, sdir))
    if os.path.exists(os.path.join(rdir, "driver.json")) or os.path.exists(os.path.join(rdir, "sessions.jsonl")):
        return refuse("%s already ran: a run dir runs once" % rdir)
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    if head != (plan.get("seed") or {}).get("sha") or git(repo, "status", "--porcelain").stdout.strip():
        return refuse("the repo is not the clean seed %s (HEAD %s)" % ((plan.get("seed") or {}).get("sha"), head))
    pk = mission(repo, "tick", "--peek", "--json")
    try:
        first = json.loads(pk.stdout).get("action")
    except ValueError:
        first = None
    if pk.returncode != 3 or first != "SET_MISSION":
        return refuse("the seed's peek is %s (exit %d), not SET_MISSION" % (first, pk.returncode))

    R = Run(plan, argv[1])
    print("driver: pid %s · caps %s" % (R.pid, json.dumps(plan["caps"], sort_keys=True)), flush=True)
    for sig in SIGNALS:                 # SIGINT too: a background job of e2e-mission.sh starts with it ignored
        signal.signal(sig, R.on_signal)
    try:
        R.loop()
        R.deliver()                     # a signal that came as the loop ended
    except (Interrupted, KeyboardInterrupt) as e:
        R.halt("interrupted", "interrupted (%s): the running session's group was killed and its record written; "
                              "no retry" % (e if isinstance(e, Interrupted) else "Ctrl-C"))
    except Exception as e:              # a driver bug stops the run; the checks and the record still follow
        R.halt("driver_error", "the driver failed (%s: %s)" % (type(e).__name__, e))

    def calm(sig, frame):               # the checks are bounded: they finish, then driver.json is written
        print("driver: %s during the post-run checks: they finish first" % signal.Signals(sig).name, flush=True)
    for sig in SIGNALS:
        signal.signal(sig, calm)
    wall = round(time.time() - R.t0, 1)
    # the outcome, the grants and the run's own records: read before any check runs the repo's code
    met = R.goal_met()
    after = []
    seen = False
    for e in events(repo):
        if e.get("event") == "goal.met":
            seen = True
        elif seen and e.get("event") == "mission.tick":
            after.append((e.get("data") or {}).get("action"))
    cited = {}
    for r in M.ledger_rows(repo)[0]:
        if r.get("role") == "human" and isinstance((r.get("data") or {}).get("prompt"), dict):
            cited[r["data"]["prompt"].get("line")] = (r["event"], r["data"]["prompt"].get("session"))
    grants = [dict(g, event=(cited.get(g["line"]) or (None, None))[0], cited_session=(cited.get(g["line"]) or (None, None))[1])
              for g in R.relays]
    captured = len(M.prompts(repo))
    run_info = {"driver_pid": R.pid, "relays": R.relays, "tick_usd": R.tick_usd, "stop": R.stop}
    ctx = Ctx(plan, R.records, run_info)
    ctx.prime()
    print("checks:", flush=True)
    results = run_checks(ctx)
    passed = sum(1 for x in results if x["ok"])
    doc = {"kind": "mission-driver", "version": 1, "plan": argv[1], "driver_pid": R.pid,
           "checks": "%d/%d" % (passed, len(results)), "check_results": results,
           "outcome": "GOAL MET" if met else "stopped: %s" % (R.stop or {}).get("code"), "stop": R.stop,
           "wall_s": wall, "time_to_goal_met_s": R.goal_met_s if R.goal_met_s is not None else
           ((R.stop or {}).get("at_s") if met else None),
           "sessions": len(R.records), "grants": grants,
           "path": " | ".join(" → ".join(r["actions"]) or "—" for r in R.records),
           "cost_usd": round(R.host_usd(), 2), "tick_usd": round(R.tick_usd, 2), "spent_usd": round(R.spent(), 2),
           "cost_complete": bool(R.records) and all(r["cost_complete"] for r in R.records),
           "actions_after_goal_met": after, "deviations": (plan.get("env") or {}).get("deviations") or [],
           "seed_sha": (plan.get("seed") or {}).get("sha"), "by": BY,
           "captured_prompts": captured, "finished_at": iso()}
    with open(os.path.join(rdir, "driver.json"), "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write("\n")
    print("checks: %s · outcome: %s · stop: %s" % (doc["checks"], doc["outcome"], (R.stop or {}).get("reason")), flush=True)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
