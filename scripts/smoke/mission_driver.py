#!/usr/bin/env python3
"""mission_driver — the live loop of one mission run (ROADMAP M9.13): e2e-mission.sh hands it plan.json.

    mission_driver.py --plan RUN/plan.json

It plays the human in three ways only, then checks the run:
  first    the first message (plan.json's first_prompt: the SET_MISSION answers, set now)
  reply    a grant line, typed in a NEW session (its own --session-id, never --resume) whose prompt is
           "/aloop:mission\\n<line>". Only mission approve and baseline confirm, and only a line that
           (1) is the one `mission tick --peek --json`'s reason names, (2) equals the line recomputed on
           disk (the code: the mission's sha256 from `mission check --json`, or the baseline's digest),
           (3) passes the pre-grant checks (the mission is the typed answers; one journey on the seeded
           runner, measured twice and agreeing, failing at baseline), and (4) the previous session showed
           the human: its final result text holds the exact line. A line not shown gets ONE surface session
           (prompt "/aloop:mission"); a line relayed once and still pending stops the run, as "hook did
           not capture" (its prompt never reached the prompt log) or "captured, not granted"
  cron     "/aloop:mission" when the peek names a role or the supervisor and `due` says due (called in
           process with record=False and the peek's own --now, so it records nothing)

Everything else a human does stops the run with its reason (a gray ethics verdict needs a real human);
the driver never runs a writing mission command, never writes a mission.tick or a ledger row, never
uses --force. Every session: the env scrubbed by prefix with ALOOP_MISSION_LOOP=<this pid> (a recorded
deviation), the failing crontab shim first on PATH, its own process group (a timeout or a kill takes the
whole group), the mission loop lock held as `mission cron --fire` holds it. While a session runs it is
polled: after goal.met, a RESEARCH, DEFINE_JOURNEYS or BASELINE tick kills its group (cost_complete
false). Caps, from plan.json: sessions, grants, the same line relayed once, the outer USD cap on
max(the host's summed cost, tick's own estimate) with --max-budget-usd min(10, what is left), the
session and total timeouts, no progress in 2 sessions in a row, a WAIT slept once per key (at most
15 min). No retries.

Writes RUN/sessions/NN.jsonl (stream) and NN.err, RUN/sessions.jsonl (one record a session) and
RUN/driver.json (the checks, the stop, the grants, the path), which mission_summary.py (e2e-mission.sh runs
it next) folds into RUN/summary.json (kind mission) and summary.md. Prints every post-run check (each one
runs, whatever the others say).
Exit 0 every check passed · 1 a check failed · 2 the plan or the run dir was refused.
"""
import glob
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
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
SUPPRESS = re.compile(r"pytest\.mark\.(?:skip|skipif|xfail)|pytest\.(?:skip|xfail|importorskip)\(|unittest\.skip"
                      r"|@skip\b|expectedFailure")
CRASH = "the gate itself failed"
GIT_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_COMMON_DIR", "GIT_NAMESPACE")
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


def pregrant(repo, plan, action, ident=None):
    """What a careful human checks before typing the line; [] when it holds."""
    a, probs = plan["answers"], []
    if action == "CONFIRM_MISSION":
        doc = load(M.path_of(repo, "mission"))
        if doc is None:
            return ["no mission.json"]
        for k in ("statement", "mode", "cadence", "constraints"):
            if doc.get(k) != a[k]:
                probs.append("mission.json %s is %r, the typed answer %r" % (k, doc.get(k), a[k]))
        want = {"usd": a["budget_usd"], "deadline": a["deadline"]}
        if doc.get("budget") != want:
            probs.append("mission.json budget (and deadline) is %r, the typed answers %r" % (doc.get("budget"), want))
        set_by = (doc.get("provenance") or {}).get("set_by")
        if set_by != BY:
            probs.append("mission.json was set by %r, not %s" % (set_by, BY))
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
    if journeys and bj:
        j, want = journeys[0], plan["seed"]["fixture_runner_sha256"]
        if bj[0].get("journey_id") != j.get("journey_id") or bj[0].get("sha256") != M.digest(j):
            probs.append("the baseline's journey is not the journey on disk")
        rn = j.get("runner") if isinstance(j.get("runner"), dict) else {}
        if rn.get("files") != {RUNNER: want}:
            probs.append("the journey's runner pins %r, not the seeded runner %s at %s" % (rn.get("files"), RUNNER, want[:12]))
        if RUNNER not in (rn.get("command") or []):
            probs.append("the journey's runner command %r does not run %s" % (rn.get("command"), RUNNER))
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
    """The gate log, parsed as JSON: a stuck release (either spelling) is counted and is never a pass."""
    out = {"lines": 0, "stuck_releases": 0, "selfcheck_false": False, "passed": {k: False for k in GATES}, "log": None}
    for h in (".aladdin", ".agentic"):
        p = os.path.join(repo, h, "logs", "gates.jsonl")
        if not os.path.isfile(p):
            continue
        out["log"] = p
        for v in jsonl(p):
            out["lines"] += 1
            if v.get("released") == "stuck":
                out["stuck_releases"] += 1
            if v.get("selfcheck") is False:
                out["selfcheck_false"] = True
            for k, name in GATES.items():
                if v.get("gate") == name and v.get("exit") == 0 and "released" not in v:
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


# ── the run ───────────────────────────────────────────────────────────────────────────────────────
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
                rc = proc.wait(timeout=max(0.05, min(float(self.caps["poll_s"]), left)))
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

    def session(self, kind, prompt, line=None):
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
        start, rc, timed_out, killed, error = time.time(), None, False, None, None
        try:
            with open(out, "w") as so, open(err, "w") as se:
                proc = subprocess.Popen(argv, cwd=self.repo, stdin=subprocess.DEVNULL, stdout=so, stderr=se, env=env,
                                        start_new_session=True)
                rc, timed_out, killed = self.watch(proc, start + timeout)
        except OSError as e:
            error = "%s: %s" % (type(e).__name__, e)
        finally:
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
               "line": line, "progress": fp1 != fp0, "started_at": iso(start), "ended_at": iso(end),
               "start_s": start, "end_s": end}
        self.texts[n] = st["text"]
        self.records.append(rec)
        with open(os.path.join(self.dir, "sessions.jsonl"), "a") as f:
            f.write(json.dumps({k: v for k, v in rec.items() if k not in ("start_s", "end_s")}, sort_keys=True) + "\n")
        if line:
            self.relays.append({"action": rule_kind(self.plan, line), "line": line, "n": n, "session_id": sid})
        self.no_progress = 0 if rec["progress"] else self.no_progress + 1
        print("session %d (%s): exit %s · %ss · %s · %s turns · %s%s" % (
            n, kind, rc, rec["secs"], "$%.2f" % rec["cost_usd"] if rec["cost_usd"] is not None else "no cost line",
            rec["turns"], " → ".join(acts) or "no tick", (" · killed: %s" % killed) if killed else
            (" · timed out" if timed_out else "")), flush=True)
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
        r = mission(self.repo, "tick", "--peek", "--json", "--now", iso(now))
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
            print("relay: `%s` (shown in session %d; the line on disk; the pre-grant checks hold)" % (line, last["n"]),
                  flush=True)
            return ("reply", LEAD + "\n" + line, line)
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
                    time.sleep(nxt[1])
                    continue
                rec = self.session(nxt[0], nxt[1], line=nxt[2])
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
    return not p, "; ".join(p) or "statement, mode, budget, deadline, cadence, constraints as typed; set by %s" % BY


def check_mission_approved(c):
    rows = c.human("mission.approved")
    if not rows:
        return False, "no mission.approved record"
    r, relays = rows[-1], c.relay("CONFIRM_MISSION")
    p = r["data"].get("prompt") or {}
    hit = [x for x in relays if x["line"] == p.get("line")]
    ok = bool(hit) and p.get("session") == hit[-1]["session_id"] and r["data"].get("by") == BY and M.cites(r) \
        and M.mission(c.repo)[2] is not None
    return ok, "cites `%s` from session %s (the reply session: %s)" % (p.get("line"), p.get("session"),
                                                                        hit[-1]["session_id"] if hit else "none relayed")


def confirmed_baseline(c):
    rows = M.records(c.repo, "baseline.confirmed", role="human")
    if not rows:
        return None, None
    ref = rows[-1]["data"].get("subject") or ""
    return rows[-1], load(os.path.join(c.repo, ref))


def check_baseline_confirmed(c):
    r, doc = confirmed_baseline(c)
    if r is None or doc is None:
        return False, "no baseline.confirmed record"
    ms = doc.get("measurements") or {}
    runs = ms.get("runs") if isinstance(ms.get("runs"), list) else []
    p = r["data"].get("prompt") or {}
    hit = [x for x in c.relay("CONFIRM_BASELINE") if x["line"] == p.get("line")]
    ok = len(runs) == 2 and ms.get("agreed") is True and bool(hit) and p.get("session") == hit[-1]["session_id"] \
        and r["data"].get("by") == BY and M.cites(r)
    return ok, "measured %d time(s), agreed %r; cites `%s` from session %s" % (len(runs), ms.get("agreed"), p.get("line"),
                                                                              p.get("session"))


def check_grants_relayed(c):
    relayed = {x["line"] for x in c.run.get("relays") or []}
    if not relayed:
        return False, "no grant line was relayed"
    rows = [r for r in c.human() if r["event"] in M.GRANT_VERBS]
    stray = [r["event"] for r in rows if ((r["data"].get("prompt") or {}).get("line") not in relayed)]
    qs = M.prompts(c.repo)
    extra = sorted({ln.strip() for q in qs for ln in q["text"].splitlines() if GRANTISH.match(ln.strip())} - relayed)
    mine = sorted(s["full_prompt_sha256"] for s in c.sessions if s.get("full_prompt_sha256"))
    exact = sorted(q["sha256"] for q in qs) == mine
    return not stray and not extra, "%d grant(s), each citing a relayed line%s%s; prompt log: %d captured, %s the run's %d" % (
        len(rows), "; not relayed: %s" % stray if stray else "", "; other grant lines captured: %s" % extra if extra else "",
        len(qs), "exactly" if exact else "not exactly", len(mine))


def check_allowlist(c):
    allowed = {x["event"] for x in c.plan["allowlist"]}
    other = sorted({r["event"] for r in c.human()} - allowed)
    force = [e for e in c.events if e.get("event") == "mission.tick" and (e.get("data") or {}).get("action") == "FORCE"]
    stop = [e for e in c.events if e.get("event") == "mission.budget_exhausted"]
    many = len(c.run.get("relays") or []) > c.plan["caps"]["grants"]
    return not (other or force or stop or many), "human records outside the allowlist: %s; --force: %d; budget stops: " \
        "%d; lines typed: %d" % (other or "none", len(force), len(stop), len(c.run.get("relays") or []))


def check_human_by(c):
    bad = [r["event"] for r in c.human() if r["data"].get("by") != BY]
    sets = [e for e in c.events if e.get("event") == "mission.set"]
    bad += ["mission.set" for e in sets if "by" in (e.get("data") or {}) and e["data"]["by"] != BY]
    doc = load(M.path_of(c.repo, "mission"))
    set_by = ((doc or {}).get("provenance") or {}).get("set_by")
    ok = not bad and bool(c.human()) and bool(sets) and set_by == BY
    return ok, "%d human record(s), %d mission.set (mission.json set_by %r)%s" % (
        len(c.human()), len(sets), set_by, "; not by %s: %s" % (BY, bad) if bad else "")


def check_runner(c):
    want, seed = c.plan["seed"]["fixture_runner_sha256"], c.seed()
    js = [d for _, d in M.artifacts(c.repo, "journey")]
    pins = [((d.get("runner") or {}).get("files")) for d in js]
    same = git(c.repo, "diff", "--quiet", seed, "--", RUNNER).returncode == 0 if seed else False
    tracked = git(c.repo, "cat-file", "-e", "%s:%s" % (seed, RUNNER)).returncode == 0 if seed else False
    ok = bool(js) and all(p == {RUNNER: want} for p in pins) and same and tracked \
        and sha_file(os.path.join(c.repo, RUNNER)) == want
    return ok, "%d journey(s) pinning %s; unchanged since seed %s: %s; sha256 the fixture's: %s" % (
        len(js), "the seeded runner" if js and all(p == {RUNNER: want} for p in pins) else pins, seed[:12], same,
        sha_file(os.path.join(c.repo, RUNNER)) == want)


def check_failing_at_baseline(c):
    _, doc = confirmed_baseline(c)
    if doc is None:
        return False, "no confirmed baseline"
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


def check_red_green(c):
    p = c.gates["passed"]
    return p["red"] and p["green"], "RED %s, GREEN %s (exit 0 with no `released` field)" % (
        "passed" if p["red"] else "no pass", "passed" if p["green"] else "no pass")


def check_host_gates(c):
    return c.gates["selfcheck_false"], "%d gate line(s); selfcheck false: %s" % (c.gates["lines"], c.gates["selfcheck_false"])


def check_final_gate(c):
    return c.gates["passed"]["final"], "final gate %s" % ("passed" if c.gates["passed"]["final"] else "no pass")


def check_goal_met(c):
    met = [r for r in c.rows if r["event"] == "goal.met"]
    ev = mission(c.repo, "evaluate", "check").returncode if met else None
    return bool(met) and ev == 0, "%d goal.met record(s); mission evaluate check exit %s" % (len(met), ev)


def check_product(c):
    head = git(c.repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    r = run(["python3", RUNNER], c.repo, timeout=120, env=dict(clean_env(), PYTHONDONTWRITEBYTECODE="1"))
    try:
        steps = json.loads((r.stdout.strip().splitlines() or ["{}"])[-1]).get("steps") or {}
    except ValueError:
        steps = {}
    calls = [p for p in glob.glob(os.path.join(c.repo, "tests", "**", "*.py"), recursive=True)
             if "add(" in (open(p, encoding="utf-8", errors="replace").read())]
    seed_test = git(c.repo, "diff", "--quiet", c.seed(), "--", "tests/test_todo.py").returncode == 0 if c.seed() else False
    ok = head == "main" and steps.get("see") is True and bool(calls) and seed_test
    return ok, "on %s: the runner's see %r; tests calling add(: %d; the seed's test unchanged: %s" % (
        head, steps.get("see"), len(calls), seed_test)


def check_pytest(c):
    r = run(["python3", "-m", "pytest", "-q", "-p", "no:cacheprovider"], c.repo, timeout=600,
            env=dict(clean_env(), PYTHONDONTWRITEBYTECODE="1"))
    tail = (r.stdout + r.stderr).strip().splitlines()[-1:] or [""]
    return r.returncode == 0, "exit %d: %s" % (r.returncode, tail[0][:160])


def check_no_suppressed(c):
    hits = []
    for p in glob.glob(os.path.join(c.repo, "tests", "**", "*.py"), recursive=True) + \
            glob.glob(os.path.join(c.repo, "todo", "**", "*.py"), recursive=True):
        if SUPPRESS.search(open(p, encoding="utf-8", errors="replace").read()):
            hits.append(os.path.relpath(p, c.repo))
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
    wins = [(int(s["start_s"]) - 1, int(s["end_s"]) + 2) for s in c.sessions if "start_s" in s]
    outside = [r["event"] for r in c.rows if not any(a <= (ms_of(r["ts"]) or 0) / 1000.0 <= b for a, b in wins)]
    ok = not foreign and counted == len(ticks) and not outside
    return ok, "%d mission.tick (each a session's, loop %s: %s; counted in sessions: %d); ledger rows outside a " \
               "session: %s" % (len(ticks), pid, "yes" if not foreign else "no, %d not" % len(foreign), counted,
                                outside or "none")


CHECKS = [
    ("every session ended with a success result line (or the run killed it after goal.met)", check_sessions),
    ("mission.json is the typed answers, set by %s" % BY, check_mission_typed),
    ("mission.approved cites the relayed approve line and its reply session", check_mission_approved),
    ("the baseline was measured twice and agreed; baseline.confirmed cites the relayed confirm line", check_baseline_confirmed),
    ("every grant cites a line the run relayed; no captured line grants anything else", check_grants_relayed),
    ("no grant outside the allowlist, no tick --force, no budget stop", check_allowlist),
    ("every human record is by %s" % BY, check_human_by),
    ("the journey runs the seeded runner, unchanged since the seed", check_runner),
    ("the journey failed at baseline (something to earn)", check_failing_at_baseline),
    ("one goal: derived, ethics-reviewed, one intent with source mission", check_goal),
    ("RED and GREEN passed (exit 0, not a stuck release)", check_red_green),
    ("the host's SubagentStop gates ran (selfcheck false in the gate log)", check_host_gates),
    ("the final gate passed (exit 0, not a stuck release)", check_final_gate),
    ("goal.met is recorded and mission evaluate check passes", check_goal_met),
    ("on main the runner sees the task, a test calls add(, the seed's test is unchanged", check_product),
    ("pytest passes on main", check_pytest),
    ("no suppressed tests", check_no_suppressed),
    ("every commit after the seed traces; the product commit to the mission %s approved" % BY, check_trace),
    ("no hook crashed and no gate was stuck-released", check_no_crash),
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
    try:
        R.loop()
    except Exception as e:              # a driver bug stops the run; the checks and the record still follow
        R.halt("driver_error", "the driver failed (%s: %s)" % (type(e).__name__, e))
    wall = round(time.time() - R.t0, 1)
    after = []
    seen = False
    for e in events(repo):
        if e.get("event") == "goal.met":
            seen = True
        elif seen and e.get("event") == "mission.tick":
            after.append((e.get("data") or {}).get("action"))
    run_info = {"driver_pid": R.pid, "relays": R.relays, "tick_usd": R.tick_usd, "stop": R.stop}
    print("checks:", flush=True)
    results = run_checks(Ctx(plan, R.records, run_info))
    passed = sum(1 for x in results if x["ok"])
    met = R.goal_met()
    cited = {}
    for r in M.ledger_rows(repo)[0]:
        if r.get("role") == "human" and isinstance((r.get("data") or {}).get("prompt"), dict):
            cited[r["data"]["prompt"].get("line")] = (r["event"], r["data"]["prompt"].get("session"))
    grants = [dict(g, event=(cited.get(g["line"]) or (None, None))[0], cited_session=(cited.get(g["line"]) or (None, None))[1])
              for g in R.relays]
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
           "captured_prompts": len(M.prompts(repo)), "finished_at": iso()}
    with open(os.path.join(rdir, "driver.json"), "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write("\n")
    print("checks: %s · outcome: %s · stop: %s" % (doc["checks"], doc["outcome"], (R.stop or {}).get("reason")), flush=True)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
