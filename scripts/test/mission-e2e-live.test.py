#!/usr/bin/env python3
"""ROADMAP §4.12 (M9.13, the live part's harness, offline at zero tokens): `scripts/smoke/e2e-mission.sh`
seeds a to-do repo for one real mission cycle on the claude host and plays the human only by typing the
first message and the grant lines. No host binary runs here.
  1) the fixture through the real mission CLI, the way mission-e2e.test.py drives it: 1 journey and
     1 gap at baseline; after fix.diff, GOAL MET with 1 earned (one sprint, the seeded runner unchanged)
  2) guards: --yes, make without YES, unknown arg or host, missing CLI, an E2E_DIR that is not empty,
     inside a git repo or under a CLAUDE.md, codex/opencode live, the live path with no driver
  3) --dry-run: seed, zero-token self-checks, plan.json (first prompt and sha, seed sha, argv for the
     first/reply/surface/cron sessions from cron.host_cmd plus the measured flags, caps, allowlist);
     prints the lines; the stub host's marker is never created; make e2e-mission DRY=1
  4) the environment is scrubbed by prefix (an env spy on every git the seed runs, and session_env)
  5) the mission summary (`--sessions DIR --kind mission`): costs summed per session, gate lines parsed
     as JSON, a stuck release is never a pass, and the ledger row
  6) the live loop (mission_driver.py) on a stub claude that behaves like a host (fixtures/mission_host.py:
     its prompt captured by the plugin's UserPromptSubmit hooks, the mission CLI run as the playbook's
     supervisor would, stream-json with a result line): the whole cycle through e2e-mission.sh with every
     post-run check passing, each reply a new session citing its grant; a run where everything fails;
     forged, wrong and unshown lines never typed; a hook that did not capture; the session, USD, timeout
     and no-progress caps; a gray verdict; the pre-grant checks; stuck releases; a repeated WAIT
  and, each a block of its own: every pre-grant check alone; the runner's import isolation; the gate contract,
  git identity and host settings scrubbed; each post-run check broken alone (a forged or uncited ledger row, a
  second confirmation, a grant of other content than was checked, a mission.set of other content, a restore, a
  skip or a pytest config, in the repo or above it …); gate passes that are a self-check or a budget release; the
  loop's WAIT, the grant, total-time and cut session caps; host failures; the driver's refusals and the live
  preflight; children and SIGKILL; the loop lock; a planted test that records goal.met; SIGTERM, SIGINT and
  SIGHUP to the driver or the script; a failure of the driver itself during a session; nan and inf caps
    python3 scripts/test/mission-e2e-live.test.py
    MISSION_E2E_LIVE_KEEP_GOING=1 python3 scripts/test/mission-e2e-live.test.py    (every block, then the failures)
"""
import contextlib
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid

sys.dont_write_bytecode = True
ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
SMOKE = os.path.join(ROOT, "scripts", "smoke")
SH = os.path.join(SMOKE, "e2e-mission.sh")
DRIVER = os.path.join(SMOKE, "mission_driver.py")
SUMMARY = os.path.join(SMOKE, "mission_summary.py")
FIX = os.path.join(SMOKE, "fixtures", "todo-mission")
RUNNER = "docs/agents/mission/journeys/runners/todo_journey.py"
BIN = os.path.join(ROOT, "plugin", "bin")
sys.path.insert(0, BIN)
sys.path.insert(0, SMOKE)
import _mission as M  # noqa: E402

PASS = [0]
BY = "scripted-human (e2e)"
T = tempfile.mkdtemp(prefix="aloop-e2e-mission-")
STUBS = os.path.join(T, "stubs")
TOOLS = os.path.join(T, "tools")         # python3 and git only: a PATH with no host CLI on it
MARKER = os.path.join(T, "host-ran")
os.makedirs(STUBS)
os.makedirs(TOOLS)
for name in ("claude", "codex", "opencode"):   # a host that ran leaves the marker: a dry run never runs one
    p = os.path.join(STUBS, name)
    with open(p, "w") as f:
        f.write('#!/bin/sh\necho "%s $*" >> "%s"\nexit 0\n' % (name, MARKER))
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
with open(os.path.join(TOOLS, "python3"), "w") as f:      # this interpreter (a symlink would lose its venv)
    f.write('#!/bin/sh\nexec "%s" "$@"\n' % sys.executable)
os.chmod(os.path.join(TOOLS, "python3"), 0o755)
for name, real in (("git", shutil.which("git")), ("make", shutil.which("make"))):
    if real:
        os.symlink(real, os.path.join(TOOLS, name))
FAMILIES = re.compile(r"^(AGENTIC_|ALADDIN_|ALOOP_|GATE_)|^(SKIP_HOOKS|TASK_ID|ATTEMPT|AGENT_ROLE|YES|DRY|HOST|MODEL)$"
                      r"|_SPRINT_LOCK_TTL$|^(CLAUDE_CODE_|CLAUDE_COWORK_|CLAUDE_MEMORY_)")
BASE = {k: v for k, v in os.environ.items()
        if not FAMILIES.search(k) and not k.startswith(("E2E_", "GIT_")) and k not in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL")}
ENV = dict(BASE, HOME=os.path.join(T, "home"), PATH=":".join([STUBS, TOOLS, "/usr/bin", "/bin"]),
           GIT_AUTHOR_NAME="A", GIT_AUTHOR_EMAIL="a@a", GIT_COMMITTER_NAME="A", GIT_COMMITTER_EMAIL="a@a")
os.makedirs(ENV["HOME"])


def ok(name):
    PASS[0] += 1
    print("ok -", name)


KEEP_GOING = os.environ.get("MISSION_E2E_LIVE_KEEP_GOING") == "1"
FAILED = []


def block(name, fn):
    """One named check: ok when fn returns. With MISSION_E2E_LIVE_KEEP_GOING=1 a failing block is printed and the
    rest still run (the suite then exits 1), else the failure stops the suite as an assert does."""
    try:
        fn()
    except Exception as e:      # noqa: BLE001 - every failure is reported under its block's name
        if not KEEP_GOING:
            raise
        FAILED.append(name)
        print("FAIL - %s: %s: %s" % (name, type(e).__name__, str(e)[:1500]), flush=True)
        return
    ok(name)


@contextlib.contextmanager
def kept(*paths):
    """Every path as it was before the block, byte for byte, after it (a file the block made is removed)."""
    saved = {p: (open(p, "rb").read() if os.path.exists(p) else None) for p in paths}
    try:
        yield
    finally:
        for p, raw in saved.items():
            if raw is None:
                if os.path.exists(p):
                    os.remove(p)
            else:
                with open(p, "wb") as f:
                    f.write(raw)


@contextlib.contextmanager
def doctored(path, fn=None, text=None):
    """path changed for the block (fn applied to its JSON doc, or text appended: a new file when there was
    none), then put back."""
    with kept(path):
        if fn is not None:
            doc = json.load(open(path))
            fn(doc)
            with open(path, "w") as f:
                json.dump(doc, f)
        else:
            with open(path, "a") as f:
                f.write(text or "")
        yield path


@contextlib.contextmanager
def environ(env):
    """This process's environment is env for the block (the driver's functions read os.environ), then restored."""
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def jsonl_of(path):
    return [json.loads(x) for x in open(path)] if os.path.isfile(path) else []


def iso_in(secs):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + secs))


def sh(*args, env=None, run_dir=None):
    """e2e-mission.sh with these args; E2E_DIR is a new empty dir unless run_dir names one."""
    d = run_dir or tempfile.mkdtemp(prefix="run-", dir=T)
    r = subprocess.run([SH] + list(args), cwd=T, env=dict(env or ENV, E2E_DIR=d), capture_output=True, text=True)
    return r, d


def dry(*args, env=None):
    r, d = sh("--yes", "--dry-run", *args, env=env)
    assert r.returncode == 0, (args, r.returncode, r.stdout[-3000:], r.stderr[-2000:])
    return r, d, json.load(open(os.path.join(d, "plan.json")))


def git(repo, *args):
    return subprocess.run(["git"] + list(args), cwd=repo, env=ENV, capture_output=True, text=True)


def sha_file(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def gone():
    return not os.path.exists(MARKER)


# ── 1) the fixture through the real mission CLI ───────────────────────────────────────────────────
r, d, plan = dry()
repo = os.path.join(d, "repo")
STEPS = []


def mission(*args, rc=0):
    r = subprocess.run([os.path.join(BIN, "mission")] + list(args), cwd=repo, env=ENV, capture_output=True, text=True)
    assert rc is None or r.returncode == rc, (args, r.returncode, r.stdout[-400:], r.stderr[-800:])
    return r


def tool(name, *args, rc=0):
    r = subprocess.run([os.path.join(BIN, name)] + list(args), cwd=repo, env=ENV, capture_output=True, text=True)
    assert r.returncode == rc, (name, args, r.returncode, r.stdout[-400:], r.stderr[-800:])
    return r


def tick(want, rc=0):
    r = subprocess.run([os.path.join(BIN, "mission"), "tick", "--json"], cwd=repo, env=ENV, capture_output=True, text=True)
    t = json.loads(r.stdout)
    assert (t["action"], r.returncode) == (want, rc), (want, rc, t, r.stderr[-400:])
    STEPS.append(want)
    return t


def say(text, session):
    """The human types `text` into a session: the UserPromptSubmit hook captures it, as a host runs it."""
    r = subprocess.run([os.path.join(BIN, "transcripts"), "prompt"], cwd=repo, env=ENV, capture_output=True, text=True,
                       input=json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": session, "prompt": text}))
    assert r.returncode == 0, r.stderr


LINE = re.compile(plan["line_re"])


def typed(t, verb, prefix, code):
    """The line the reason names: the plan's pattern, the verb that fits the action, the code recomputed on disk."""
    m = re.search(r"repl(?:ies|ying) `([^`]+)`, then `aloop mission ([^`]+)`", t["reason"])
    assert m, t["reason"]
    line = m.group(1)
    g = LINE.match(line)
    assert g and line.split()[0] == verb and g.group(3) == prefix and line.split()[2] == code, (line, verb, code)
    return line, m.group(2)


def review_clear(subject="mission"):
    path = mission("ethics", "new", "--subject", subject).stdout.strip()
    doc = json.load(open(path))
    doc.update(findings=[], tier="clear")                     # the reviewer's judgment: nothing to flag
    with open(path, "w") as f:
        json.dump(doc, f)
    mission("ethics", "record", path)


a = plan["answers"]
tick("SET_MISSION", 3)
init = ["init", a["statement"], "--mode", a["mode"], "--budget-usd", str(a["budget_usd"]), "--deadline", a["deadline"],
        "--cadence", a["cadence"], "--by", BY]
for c in a["constraints"]:
    init += ["--constraint", c]
mission(*init)
t = tick("CONFIRM_MISSION", 3)
chk = json.loads(mission("check", "--json", rc=3).stdout)
line, then = typed(t, "approve", "msn", chk["sha256"][:8])
assert line == "approve %s %s" % (chk["mission_id"], chk["sha256"][:8]) and then == "approve --by NAME", (line, then)
md = M.load(M.path_of(repo, "mission"))[0]
assert (md["statement"], md["mode"], md["cadence"], md["constraints"]) == (a["statement"], a["mode"], a["cadence"], a["constraints"]), md
assert md["budget"].get("usd") == a["budget_usd"] and md["budget"].get("deadline") == a["deadline"], md["budget"]
# (g) the driver's patterns on the real CLI's CONFIRM_MISSION reason: the line, recomputed on disk, and its checks
import mission_driver as D  # noqa: E402
assert D.reason_line(t["reason"]) == (line, "approve --by NAME"), (D.reason_line(t["reason"]), t["reason"])
assert D.disk_line(repo, t, plan) == (line, None), D.disk_line(repo, t, plan)
assert D.pregrant(repo, plan, "CONFIRM_MISSION") == [], D.pregrant(repo, plan, "CONFIRM_MISSION")
for bad, why in ((t["reason"].replace("`approve ", "`restore "), "verb"), (t["reason"].replace("`approve ", "`consent "), "verb"),
                 (t["reason"].replace(line, line[:-8] + "0badc0de"), "disk"), ("no line at all", "no reply line")):
    got = D.disk_line(repo, dict(t, reason=bad), plan)
    assert got[0] is None and why in got[1], (bad, got)
assert D.disk_line(repo, dict(t, action="CONFIRM_BASELINE"), plan)[0] is None       # approve never confirms a baseline
assert D.disk_line(repo, dict(t, action="ASK_CONSENT"), plan)[0] is None            # consent is never on the allowlist
assert D.shown("Reply with `%s` to approve." % line, line) and not D.shown("reply %s0" % line, line)
assert not D.shown("reply " + line[:-1], line) and not D.shown("", line)
s1 = str(uuid.uuid4())
say("/aloop:mission\n" + line, s1)                        # a reply session's prompt: the slash command, then the line
mission("approve", "--by", BY)
tick("ETHICS_REVIEW")
review_clear()
tick("RESEARCH")
rid = json.load(open(mission("research", "new", "To-do apps: a first task", "--question",
                             "how soon do to-do apps show a task the user just added?").stdout.strip()))["research_id"]
with open(os.path.join(repo, "page.html"), "w") as f:       # a saved page, as the researcher fetched it
    f.write("<html><body><p>A to-do app shows a new task in the list as soon as it is added.</p></body></html>")
src = mission("research", "archive", rid, "https://example.com/todo", "--file", "page.html").stdout.strip()
os.remove(os.path.join(repo, "page.html"))
mission("research", "claim", rid, "A to-do app shows a new task in the list at once", "--source", src, "--quote",
        "shows a new task in the list as soon as it is added", "--kind", "other")
mission("research", "check", rid)
tick("DEFINE_JOURNEYS")
pyv = subprocess.run(["python3", "--version"], env=ENV, capture_output=True, text=True)
pin = (pyv.stdout + pyv.stderr).strip()                   # the runner's docstring: pin exactly what the probe prints
draft = {"title": "Add a task and see it", "persona": "anyone with a to-do list",
         "steps": [{"id": "open", "action": "open a new to-do list", "event": "list_open"},
                   {"id": "add", "action": "add the task \"buy milk\"", "event": "task_add"},
                   {"id": "see", "action": "see \"buy milk\" in the list", "event": "task_seen"}],
         "metrics": [], "pass_criteria": [{"id": "all", "steps": "all"}],
         "runner": {"command": ["python3", RUNNER], "pinned": {"python3": {"version": pin, "probe": ["python3", "--version"]}},
                    "timeout_s": 60},
         "source_refs": [rid + "#c1"]}
with open(os.path.join(T, "draft.json"), "w") as f:
    json.dump(draft, f)
jdoc = json.load(open(mission("journeys", "new", "--from", os.path.join(T, "draft.json")).stdout.strip()))
assert jdoc["runner"]["files"] == {RUNNER: sha_file(os.path.join(FIX, RUNNER))} == {RUNNER: plan["seed"]["runner_sha256"]}, jdoc["runner"]
tick("BASELINE")
bid = json.load(open(mission("baseline", "new", "--definition", "a user adds a task and sees it at once").stdout.strip()))["baseline_id"]
tick("BASELINE")
r = mission("baseline", "measure", bid)
assert "2 runs agree" in r.stderr and "1 of 1 journeys failing" in r.stderr, r.stderr
bdoc = M.load(M.path_of(repo, "baseline", bid))[0]
assert bdoc["measurements"]["agreed"] is True and len(bdoc["measurements"]["runs"]) == 2 and len(bdoc["journeys"]) == 1, bdoc
t = tick("CONFIRM_BASELINE", 3)
line, then = typed(t, "confirm", "bln", M.digest(bdoc)[:8])
assert line.split()[1] == bid and then == "baseline confirm %s --by NAME" % bid, (line, then)
assert D.reason_line(t["reason"]) == (line, then) and D.disk_line(repo, t, plan) == (line, None), (t, D.disk_line(repo, t, plan))
assert D.pregrant(repo, plan, "CONFIRM_BASELINE") == [], D.pregrant(repo, plan, "CONFIRM_BASELINE")
assert D.disk_line(repo, dict(t, reason=t["reason"].replace("`confirm ", "`restore ")), plan)[0] is None
assert D.disk_line(repo, dict(t, action="CONFIRM_MISSION"), plan)[0] is None
s2 = str(uuid.uuid4())
say("/aloop:mission\n" + line, s2)
mission("baseline", "confirm", bid, "--by", BY)
tick("ASSESS")
mission("assess")
tick("EVALUATE")
r = mission("evaluate")
ev = json.load(open(r.stdout.strip().splitlines()[-1]))
assert len(ev["gaps"]) == 1 and ev["goal_met"] is False, (ev, r.stderr)
tick("DERIVE_GOAL")
ok("the fixture through the real mission CLI: the typed answers set the mission, the typed lines grant it and the "
   "baseline; 1 journey, measured twice and agreeing, failing at baseline: 1 gap")

g = json.load(open(mission("goal", "derive").stdout.strip()))
gid = g["goal_id"]
tick("ETHICS_REVIEW")
review_clear(gid)
tick("PLAN_INTENTS")
paths = mission("goal", "intents", gid).stdout.split()
assert len(paths) == len(g["gaps"]) == 1, (paths, g["gaps"])
tick("SPRINT")
mission("goal", "next", gid)
sdir = os.path.join(repo, "docs", "agents", "sprint1")
os.makedirs(os.path.join(sdir, "s1-01-gap"))
with open(os.path.join(sdir, "stories.md"), "w") as f:
    f.write("# Stories\n\n- S1-01: a task the user adds is in their list\n")
for name in ("story.md", "tasks.md"):
    with open(os.path.join(sdir, "s1-01-gap", name), "w") as f:
        f.write("---\nstory: S1-01\n---\n\n## T1 add() keeps the task\n")
tool("ids", "freeze", sdir)
tick("WAIT", 4)
r = git(repo, "apply", os.path.join(FIX, "fix.diff"))                # the sprint's work: the one-line fix
assert r.returncode == 0, r.stderr
tool("stats", "sprint-close", sdir)
tick("ASSESS")
time.sleep(1.05 - time.time() % 1)     # sprint-close stamps whole seconds: a report in the same second is not after it
mission("assess")
tick("EVALUATE")
r = mission("evaluate")
assert "GOAL MET" in r.stderr and "0 gap(s)" in r.stderr and "1 earned" in r.stderr, r.stderr
ev = json.load(open(r.stdout.strip().splitlines()[-1]))
assert ev["goal_met"] is True and ev["goal_id"] == gid, ev
out = subprocess.run(["python3", RUNNER], cwd=repo, env=ENV, capture_output=True, text=True).stdout.strip().splitlines()[-1]
assert json.loads(out)["steps"] == {"open": True, "add": True, "see": True}, out
assert not [x for x, _, _ in os.walk(os.path.join(repo, "docs", "agents", "mission", "journeys")) if "__pycache__" in x]
assert git(repo, "diff", "--quiet", plan["seed"]["sha"], "--", RUNNER).returncode == 0
assert sha_file(os.path.join(repo, RUNNER)) == plan["seed"]["runner_sha256"]
humans = [row for row in M.ledger_rows(repo)[0] if row.get("role") == "human"]
assert len(humans) == 2 and all(row["data"]["by"] == BY for row in humans), humans
assert [(h["event"], h["data"]["prompt"]["session"]) for h in humans] == [("mission.approved", s1), ("baseline.confirmed", s2)]
sets = [e for e in M.history(repo) if e.get("event") == "mission.set"]     # mission.set names no one: the mission does
assert len(sets) == 1 and M.load(M.path_of(repo, "mission"))[0]["provenance"]["set_by"] == BY, sets
assert ev["earned"] and len(ev["earned"]) == 1, ev
assert M.ledger_problems(repo) == []
ok("after fix.diff, GOAL MET with 1 earned in one sprint; the runner is the seeded one (seed sha, fixture sha256), "
   "writes no bytecode; every human record is by %s" % BY)
print("loop:", " → ".join(STEPS))
REPO1, BID1, JID1, RID1 = repo, bid, jdoc["journey_id"], rid      # the cycle's repo: the pre-grant table below


def t_pregrant():
    """Decision 4, part by part, on the cycle's own (approved, confirmed) repo: each mutated copy fails with its own
    reason, the original passes."""
    assert D.pregrant(REPO1, plan, "CONFIRM_MISSION") == [], D.pregrant(REPO1, plan, "CONFIRM_MISSION")
    assert D.pregrant(REPO1, plan, "CONFIRM_BASELINE", BID1) == [], D.pregrant(REPO1, plan, "CONFIRM_BASELINE", BID1)
    mp = M.path_of(REPO1, "mission")
    for fn, want in ((lambda d: d.update(statement=d["statement"] + " Also show an ad."), "statement"),
                     (lambda d: d.update(mode="spoon-fed"), "mode"),
                     (lambda d: d.update(cadence="1h"), "cadence"),
                     (lambda d: d["budget"].update(usd=250.0), "budget"),
                     (lambda d: d["budget"].update(deadline="2099-01-01"), "budget"),
                     (lambda d: d["budget"].update(tokens=10 ** 9), "budget"),
                     (lambda d: d["constraints"].pop(), "constraints"),
                     (lambda d: d["constraints"].append("Install any rival you like."), "constraints"),
                     (lambda d: d.update(veto_window_h=0.0), "veto_window_h"),
                     (lambda d: d.pop("veto_window_h"), "veto_window_h"),
                     (lambda d: d.update(notes="set by a session"), "notes"),
                     (lambda d: d["provenance"].update(set_by="Claude"), "set by"),
                     (lambda d: d["provenance"].update(note="x"), "provenance")):
        with doctored(mp, fn):
            got = D.pregrant(REPO1, plan, "CONFIRM_MISSION")
        assert any(want in x for x in got), (want, got)
    bp, jp = M.path_of(REPO1, "baseline", BID1), M.path_of(REPO1, "journey", JID1)

    def every_run(fn):
        return lambda b: [fn(r["journeys"][JID1]) for r in b["measurements"]["runs"]]
    for fn, want in ((lambda b: b["measurements"].update(runs=b["measurements"]["runs"][:1]), "measured 1 time"),
                     (lambda b: b["measurements"].update(agreed=False), "agreed False"),
                     (lambda b: (every_run(lambda j: j.update(passed=True))(b), b["measurements"].update(not_passing=[])),
                      "does not fail at baseline"),
                     (lambda b: b.update(benchmarks=[{"id": "rival", "command": ["python3", "bench_rival.py"]}]), "benchmark"),
                     (lambda b: b.update(rivals=["%s#c1" % RID1]), "rival"),
                     (lambda b: b.update(tolerance=1.0), "tolerance"),
                     (lambda b: b.update(tolerance="0.05"), "tolerance"),
                     (lambda b: b["journeys"][0].update(sha256="0" * 64), "not the journey on disk"),
                     (lambda b: b["journeys"].append({"journey_id": "jrn_" + "0" * 20, "sha256": "0" * 64}),
                      "covers 2 journeys")):
        with doctored(bp, fn):
            got = D.pregrant(REPO1, plan, "CONFIRM_BASELINE", BID1)
        assert any(want in x for x in got), (want, got)
    for tol in (0.05, 0.01, 0):                 # the default, or tighter: never looser
        with doctored(bp, lambda b: b.update(tolerance=tol)):
            assert D.pregrant(REPO1, plan, "CONFIRM_BASELINE", BID1) == [], tol
    for fn, want in ((lambda j: j.update(steps=j["steps"][:2]), "steps"),
                     (lambda j: j.update(steps=[dict(x, id=x["id"] + "2") for x in j["steps"]]), "steps"),
                     (lambda j: j.update(pass_criteria=[{"id": "fast", "metric": "ms", "op": "<=", "value": 100}]), "criterion"),
                     (lambda j: j.update(metrics=[{"id": "ms", "unit": "ms"}]), "criterion"),
                     (lambda j: j["runner"].update(files={RUNNER: "0" * 64}), "pins"),
                     (lambda j: j["runner"].update(command=["python3", "-c", "import runpy, sys; runpy.run_path(sys.argv[1])",
                                                            RUNNER]), "command"),
                     (lambda j: j["runner"].update(command=["python3", RUNNER, "--fast"]), "command"),
                     (lambda j: j["runner"].update(command=["sh", "-c", "pip install todo-rival; python3 " + RUNNER]), "command")):
        with doctored(jp, fn):      # the baseline re-pinned to the changed journey: only the change itself is judged
            with doctored(bp, lambda b: b["journeys"][0].update(sha256=M.digest(json.load(open(jp))))):
                got = D.pregrant(REPO1, plan, "CONFIRM_BASELINE", BID1)
        assert any(want in x for x in got) and not any("not the journey on disk" in x for x in got), (want, got)
    rd = os.path.join(REPO1, os.path.dirname(RUNNER))
    other = M.path_of(REPO1, "journey", "jrn_" + "f" * 20)
    for path, text, want in ((os.path.join(rd, "json.py"), "x = 1\n", "runners"),
                             (os.path.join(rd, "helper.py"), "", "runners"),
                             (os.path.join(REPO1, RUNNER), "# measured my way\n", "runner on disk"),
                             (other, json.dumps(dict(jdoc, journey_id="jrn_" + "f" * 20)), "2 journeys")):
        with doctored(path, text=text):
            got = D.pregrant(REPO1, plan, "CONFIRM_BASELINE", BID1)
        assert any(want in x for x in got), (want, got)
    assert D.pregrant(REPO1, plan, "CONFIRM_MISSION") == [] and D.pregrant(REPO1, plan, "CONFIRM_BASELINE", BID1) == []


block("the pre-grant checks, each on its own (decision 4): mission.json is the typed answers whole (mode, cadence, "
      "budget, deadline, constraints, the default veto window, no extra key, set by %s); the baseline is one "
      "journey on the seeded runner run as `python3 %s` with no other file beside it, no benchmark, no rival, no "
      "looser tolerance, measured twice and agreeing, failing at baseline" % (BY, RUNNER), t_pregrant)


def t_runner_isolated():
    """A file planted beside the pinned runner (or in the project root) never stands in for the stdlib it imports."""
    proj = tempfile.mkdtemp(prefix="planted-", dir=T)
    shutil.copytree(os.path.join(FIX, "todo"), os.path.join(proj, "todo"))
    rdir = os.path.join(proj, os.path.dirname(RUNNER))
    os.makedirs(rdir)
    shutil.copy(os.path.join(FIX, RUNNER), rdir)
    fake = "def dumps(*a, **k):\n    return '{\"steps\": {\"add\": true, \"open\": true, \"see\": true}}'\n"
    for where in (rdir, proj):
        with doctored(os.path.join(where, "json.py"), text=fake):
            r = subprocess.run(["python3", RUNNER], cwd=proj, env=ENV, capture_output=True, text=True)
        got = json.loads(r.stdout.strip().splitlines()[-1])
        assert got["steps"] == {"open": True, "add": True, "see": False}, (where, r.stdout, r.stderr)
    assert not [x for x, _, _ in os.walk(proj) if "__pycache__" in x]


block("the seeded runner drops its own dir from sys.path before any import, and imports json before it adds the "
      "project root: a json.py planted beside it or in the root does not fake the journey", t_runner_isolated)

# ── 2) guards ─────────────────────────────────────────────────────────────────────────────────────
for args in ((), ("--dry-run",), ("--dry-run", "--yes")):
    r, d = sh(*args)
    assert r.returncode == 2 and "spends tokens" in r.stdout and not os.listdir(d), (args, r.returncode, r.stdout)
r = subprocess.run(["make", "-s", "-C", ROOT, "e2e-mission"], env=ENV, capture_output=True, text=True)
assert r.returncode != 0 and "spends tokens: make e2e-mission YES=1" in r.stdout + r.stderr, (r.returncode, r.stdout, r.stderr)
r, d = sh("--yes", "--dry-run", "--frobnicate")
assert r.returncode == 2 and "unknown arg: --frobnicate" in r.stdout and not os.listdir(d), r.stdout
r, d = sh("--yes", "--dry-run", "--host")
assert r.returncode == 2 and "--host needs a value" in r.stdout and not os.listdir(d), r.stdout
r, d = sh("--yes", "--dry-run", "--host", "cursor")
assert r.returncode == 2 and "unknown host: cursor" in r.stdout and not os.listdir(d), r.stdout
nohost = dict(ENV, PATH=":".join([TOOLS, "/usr/bin", "/bin"]))
if not shutil.which("claude", path=nohost["PATH"]):
    r, d = sh("--yes", "--dry-run", env=nohost)
    assert r.returncode == 2 and "claude CLI not found" in r.stdout and not os.listdir(d), r.stdout
full = tempfile.mkdtemp(prefix="full-", dir=T)
with open(os.path.join(full, "keep.txt"), "w") as f:
    f.write("mine\n")
r, _ = sh("--yes", "--dry-run", run_dir=full)
assert r.returncode == 2 and "not empty" in r.stdout and os.listdir(full) == ["keep.txt"], (r.stdout, os.listdir(full))
afile = os.path.join(T, "a-file")
open(afile, "w").close()
r, _ = sh("--yes", "--dry-run", run_dir=afile)
assert r.returncode == 2 and "not a directory" in r.stdout, r.stdout
outer = os.path.join(T, "a-repo")
os.makedirs(outer)
subprocess.run(["git", "init", "-q", outer], check=True, env=ENV)
inside = os.path.join(outer, "deep", "run")
r, _ = sh("--yes", "--dry-run", run_dir=inside)
assert r.returncode == 2 and "inside a git repo" in r.stdout and not os.path.exists(os.path.join(outer, "deep")), r.stdout
mem = os.path.join(T, "a-project")
os.makedirs(mem)
with open(os.path.join(mem, "CLAUDE.md"), "w") as f:
    f.write("# someone's standing instructions\n")
r, _ = sh("--yes", "--dry-run", run_dir=os.path.join(mem, "sub", "run"))
assert r.returncode == 2 and "CLAUDE.md" in r.stdout and not os.path.exists(os.path.join(mem, "sub")), r.stdout
for host in ("codex", "opencode"):
    r, d = sh("--yes", "--host", host)
    assert r.returncode == 2 and "--dry-run" in r.stdout and not os.listdir(d) and gone(), (host, r.stdout)
if not os.path.exists(DRIVER):            # stage B writes the driver; until then the live path stops before anything
    r, d = sh("--yes")
    assert r.returncode == 2 and "driver not built" in r.stdout and not os.listdir(d) and gone(), r.stdout
ok("guards: exit 2 without --yes (and make without YES), on an unknown arg or host, a missing CLI, an E2E_DIR that is "
   "not empty, inside a git repo or under a CLAUDE.md, and for codex/opencode live; nothing seeded, no host run")

# ── 3) the dry run ────────────────────────────────────────────────────────────────────────────────
r, d, plan = dry()
out = r.stdout
for k in ("cmd: ", "prompt: ", "reply: ", "surface: ", "cron: ", "caps: ", "run dir: "):
    assert ("\n" + k) in ("\n" + out), (k, out)
assert "FAIL" not in out and "run dir: %s" % d in out, out
for want in ("ok - the journey runner run twice gives the same result, see false", "ok - no __pycache__ under journeys/",
             "ok - tick --peek: SET_MISSION (exit 3)", "ok - exactly 2 seed commits",
             "ok - the runner is tracked and docs/agents/ is ignored", "ok - the self-checks left the seed clean"):
    assert want in out, (want, out)
assert ("ok - pytest passes" in out) or ("skip - pytest" in out), out
assert gone(), open(MARKER).read()
repo = os.path.join(d, "repo")
head = git(repo, "rev-parse", "HEAD").stdout.strip()
assert plan["seed"]["sha"] == head and git(repo, "rev-list", "--count", "HEAD").stdout.strip() == "2", plan["seed"]
assert git(repo, "log", "--format=%s").stdout.split("\n")[:2] == ["aloop init", "init"]
assert git(repo, "ls-files", "--error-unmatch", RUNNER).returncode == 0
assert git(repo, "check-ignore", "-q", "docs/agents/mission/mission.json").returncode == 0
assert not os.path.exists(os.path.join(repo, "fix.diff")) and git(repo, "status", "--porcelain").stdout == ""
assert plan["seed"]["runner"] == RUNNER and plan["seed"]["runner_sha256"] == sha_file(os.path.join(FIX, RUNNER))
pk = subprocess.run([os.path.join(BIN, "mission"), "tick", "--peek", "--json"], cwd=repo, env=ENV, capture_output=True, text=True)
assert pk.returncode == 3 and json.loads(pk.stdout)["action"] == "SET_MISSION", pk.stdout
prompt = plan["first_prompt"]
assert plan["first_prompt_sha256"] == hashlib.sha256(prompt.encode()).hexdigest()
lines = prompt.splitlines()
assert lines[0] == "/aloop:mission" and "set the mission now" in prompt.lower(), prompt
for c in plan["answers"]["constraints"]:
    assert c in prompt, c
assert any(c.startswith("Measure the product only with %s as it is (one journey; its docstring names the steps and the pin)" % RUNNER)
           for c in plan["answers"]["constraints"]), plan["answers"]["constraints"]
assert any("No rival product is installed or run" in c and "ruled out by this constraint" in c for c in plan["answers"]["constraints"])
assert any("at most 3 sources" in c for c in plan["answers"]["constraints"])
assert BY in prompt and "Decline the cron" in prompt and plan["by"] == BY
assert not [ln for ln in lines if re.match(r"^\W*(?:(?:yes|ok|okay|sure)\W+)?(?:approve|confirm|consent|restore|retire|veto)\b",
                                           ln.strip().lower())], lines
assert not re.search(r"\b[0-9a-f]{8,64}\b", prompt.lower()), "no code-like token in the first prompt"
it = subprocess.run([os.path.join(BIN, "intent-trigger"), "hook"], cwd=repo, env=ENV, capture_output=True, text=True,
                    input=json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "s", "prompt": prompt}))
assert it.returncode == 0 and it.stdout.strip() == "", it.stdout     # a slash command: intent-trigger stays quiet
ok("--dry-run seeds 2 commits (init, aloop init: the runner tracked, docs/agents/ ignored, the seed sha recorded), "
   "runs the zero-token self-checks, never runs the host; the first prompt sets the mission now with the constraints")

sys.path.insert(0, os.path.join(ROOT, "plugin", "mission"))
import cron  # noqa: E402

S = plan["sessions"]
assert set(S) == {"first", "reply", "surface", "cron"}, set(S)
for kind, s in S.items():
    argv = s["argv"]
    assert argv[0] == os.path.join(STUBS, "claude") and argv[1] == "-p" and argv[2] == "<PROMPT>", (kind, argv)
    assert argv[-2:] == ["--allowedTools", cron.TOOLS] and argv[3:5] == ["--plugin-dir", os.path.join(ROOT, "plugin")], argv
    flags = argv[5:-2]
    for pair in (["--setting-sources", "project"], ["--output-format", "stream-json"], ["--max-turns", "400"],
                 ["--max-budget-usd", "<MAX_BUDGET_USD>"], ["--session-id", "<SESSION_ID>"]):
        i = flags.index(pair[0])
        assert flags[i:i + 2] == pair, (pair, flags)
    assert "--strict-mcp-config" in flags and "--verbose" in flags and "--model" in flags, flags
    assert not [x for x in argv if re.search(r"resume|continue|bypass|skip-permission|dangerous", x, re.I)], argv
    assert s["new_session"] is True
assert S["first"]["prompt"] == prompt and S["reply"]["prompt"] == "/aloop:mission\n<LINE>"
assert S["surface"]["prompt"] == S["cron"]["prompt"] == "/aloop:mission" == cron.PROMPT["claude"]
sup = subprocess.run([os.path.join(BIN, "models"), "routes", "--host", "claude"], env=ENV, capture_output=True,
                     text=True).stdout
want = re.search(r"strong=(\S+)", sup).group(1)
fl = S["first"]["argv"]
assert fl[fl.index("--model") + 1] == want == plan["models"]["supervisor"], (fl, sup)
assert plan["allowlist"] == [
    {"action": "CONFIRM_MISSION", "verb": "approve", "prefix": "msn", "event": "mission.approved"},
    {"action": "CONFIRM_BASELINE", "verb": "confirm", "prefix": "bln", "event": "baseline.confirmed"}], plan["allowlist"]
assert plan["line_re"] == r"^(approve|confirm) ((msn|bln)_[0-9a-f]{20}) [0-9a-f]{8}$"
assert plan["stops"]["ASK_CONSENT"] == "a gray ethics verdict needs a real human", plan["stops"]
assert plan["caps"] == {"sessions": 8, "grants": 4, "same_line_relays": 1, "usd": 30.0, "per_session_usd": 10.0,
                        "session_timeout_s": 5400, "total_timeout_s": 14400, "no_progress_sessions": 2,
                        "max_turns": 400, "wait_max_s": 900, "waits_per_key": 1, "poll_s": 10}, plan["caps"]
assert plan["kill_after_goal_met"] == ["RESEARCH", "DEFINE_JOURNEYS", "BASELINE"]
assert plan["host"] == "claude" and plan["dry_run"] is True and plan["fixture"] == "todo"
shim = plan["crontab_shim"]
assert os.path.dirname(shim) == plan["session_path_first"] and not shim.startswith(repo + os.sep), (shim, plan)
assert os.access(shim, os.X_OK)
for args in (["-l"], ["-"]):
    cr = subprocess.run([shim] + args, input="* * * * * true\n", capture_output=True, text=True)
    assert cr.returncode != 0 and "disabled" in cr.stderr, (args, cr.returncode, cr.stderr)
for k in ("cmd: ", "reply: ", "surface: ", "cron: "):
    ln = next(x for x in out.splitlines() if x.startswith(k))
    assert "--setting-sources project" in ln and "--strict-mcp-config" in ln and "--allowedTools" in ln, ln
caps = next(x for x in out.splitlines() if x.startswith("caps: "))
assert "sessions 8" in caps and "grants 4" in caps and "$30" in caps, caps
ok("plan.json: argv for the first/reply/surface/cron sessions is cron.host_cmd(\"claude\") plus the measured flags "
   "(a new session id each, never --resume), the caps, the allowlist (mission approve, baseline confirm) and a "
   "failing crontab shim first on the sessions' PATH")

r, d2, plan2 = dry("--model", "my-model", "--worker-model", "my-worker")
fl = plan2["sessions"]["first"]["argv"]
assert fl[fl.index("--model") + 1] == "my-model" and plan2["models"] == {"supervisor": "my-model", "worker": "my-worker"}
defaults = open(os.path.join(d2, "repo", "docs", "agents", "defaults.md")).read()
assert re.search(r"MODEL_PLANNING\W+my-worker", defaults) and re.search(r"MODEL_WORKER\W+my-worker", defaults), defaults
for host in ("codex", "opencode"):
    r, dh, ph = dry("--host", host)
    argv = ph["sessions"]["cron"]["argv"]
    assert argv[0] == os.path.join(STUBS, host) and "<PROMPT>" in argv and ph["host"] == host and gone(), (host, argv)
    assert ph["live"] is False and "cmd: " in r.stdout, ph
md = tempfile.mkdtemp(prefix="run-", dir=T)
r = subprocess.run(["make", "-s", "-C", ROOT, "e2e-mission", "YES=1", "DRY=1", "MODEL=my-model"],
                   env=dict(ENV, E2E_DIR=md), capture_output=True, text=True)
assert r.returncode == 0 and os.path.isfile(os.path.join(md, "plan.json")) and gone(), (r.stdout[-2000:], r.stderr[-1000:])
assert json.load(open(os.path.join(md, "plan.json")))["models"]["supervisor"] == "my-model"
ok("--model/--worker-model, --host codex|opencode --dry-run, and make e2e-mission YES=1 DRY=1 [MODEL=] all plan "
   "without running a host")

# ── 4) the environment is scrubbed by prefix ──────────────────────────────────────────────────────
SPY = os.path.join(T, "spy")
os.makedirs(SPY)
SPY_OUT = os.path.join(T, "spy.env")
with open(os.path.join(SPY, "git"), "w") as f:
    f.write('#!/bin/sh\n{ echo "----"; env; } >> "$E2E_SPY_OUT"\nexec "%s" "$@"\n' % shutil.which("git"))
os.chmod(os.path.join(SPY, "git"), 0o755)
POLLUTE = {"AGENTIC_NO_BUDGET": "1", "AGENTIC_SELFCHECK": "1", "AGENTIC_STUCK_BLOCKS": "99", "AGENTIC_AUTONOMY_MODE": "x",
           "AGENTIC_CWD": os.path.join(T, "elsewhere"), "AGENTIC_TRANSCRIPTS": os.path.join(T, "elsewhere"),
           "AGENTIC_SPRINT_LOCK_TTL": "1", "ALADDIN_STUCK_BLOCKS": "99", "ALADDIN_SPRINT_LOCK_TTL": "1",
           "ALOOP_JUDGE_CMD": "true", "ALOOP_JUDGE_TIMEOUT": "1", "ALOOP_MISSION_LOOP": "4242",
           "GATE_RUN_TESTS": "0", "GATE_RUN_MATRIX": "0", "SKIP_HOOKS": "1", "TASK_ID": "T9", "ATTEMPT": "3",
           "AGENT_ROLE": "green-worker", "OTHER_SPRINT_LOCK_TTL": "1",
           "CLAUDE_CODE_SESSION_ID": "parent", "CLAUDE_CODE_REMOTE_SESSION_ID": "cse_parent",
           "CLAUDE_CODE_REMOTE_SDK_URL": "https://example.invalid/v1/code/sessions/cse_parent",
           "CLAUDE_COWORK_MEMORY_PATH_OVERRIDE": "/tmp/parent-mem", "CLAUDE_MEMORY_STORES": "[]",
           "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/s.sock", "CLAUDE_CODE_MESSAGING_TOKEN": "t"}
spy_env = dict(ENV, PATH=SPY + ":" + ENV["PATH"], E2E_SPY_OUT=SPY_OUT, E2E_SPY_KEEP="kept",
               GIT_DIR=os.path.join(outer, ".git"), GIT_WORK_TREE=outer, **POLLUTE)
r, d, plan = dry(env=spy_env)
assert git(outer, "rev-list", "--all").stdout == "" and git(os.path.join(d, "repo"), "rev-list", "--count", "HEAD").stdout.strip() == "2"
blocks = [b for b in open(SPY_OUT).read().split("----\n") if b.strip()]
assert len(blocks) >= 4, len(blocks)            # git init, config, add, commit … every git the seed ran
for b in blocks:
    names = {ln.split("=", 1)[0] for ln in b.splitlines() if "=" in ln}
    assert not (names & set(POLLUTE)) and not (names & {"GIT_DIR", "GIT_WORK_TREE"}), sorted(names & set(POLLUTE))
    assert not [n for n in names if FAMILIES.search(n) and n != "ALOOP_NO_HOST_RERENDER"], sorted(names)
    assert "E2E_SPY_KEEP=kept" in b.splitlines()
assert set(POLLUTE) <= set(plan["env"]["scrubbed"]), sorted(set(POLLUTE) - set(plan["env"]["scrubbed"]))
text = open(os.path.join(d, "plan.json")).read()
assert "elsewhere" not in text and "cse_parent" not in text and "green-worker" not in text, "names only, never values"
assert plan["env"]["set"] == {"ALOOP_MISSION_LOOP": "<driver pid>"} and plan["env"]["deviations"], plan["env"]
import mission_plan as MP  # noqa: E402

se = MP.session_env(dict(POLLUTE, PATH="/usr/bin:/bin", KEEP="1", HOME="/h"), 777, "/run/bin")
assert se["ALOOP_MISSION_LOOP"] == "777" and se["PATH"] == "/run/bin:/usr/bin:/bin" and se["KEEP"] == "1", se
assert not [k for k in se if (k in POLLUTE or FAMILIES.search(k)) and k != "ALOOP_MISSION_LOOP"], sorted(se)
ok("every family is gone from the seed's environment (AGENTIC_*, ALADDIN_*, ALOOP_*, GATE_*, SKIP_HOOKS, TASK_ID, "
   "ATTEMPT, AGENT_ROLE, *_SPRINT_LOCK_TTL, the parent session's ids, a GIT_DIR), names only in plan.json; session_env sets "
   "only ALOOP_MISSION_LOOP and puts the shim first")

# What an operator's shell can carry besides the families: the gate contract (REPO_DIR, SCOPE_GLOBS … sent every
# RED/GREEN/final gate to another tree), BASH_ENV (runs in every bash gate), the git identity and config (a hook,
# the operator's name on every commit) and the host's own settings (model routing, effort, thinking).
EVIL = os.path.join(T, "evil")
os.makedirs(os.path.join(EVIL, "hooks"))
HOOKED = os.path.join(EVIL, "hook-ran")
with open(os.path.join(EVIL, "hooks", "pre-commit"), "w") as f:
    f.write('#!/bin/sh\necho ran >> "%s"\nexit 0\n' % HOOKED)
os.chmod(os.path.join(EVIL, "hooks", "pre-commit"), 0o755)
with open(os.path.join(EVIL, "gitconfig"), "w") as f:
    f.write("[core]\n\thooksPath = %s\n[user]\n\tname = Operator Real\n\temail = op@real.invalid\n" % os.path.join(EVIL, "hooks"))
with open(os.path.join(EVIL, "bashenv"), "w") as f:
    f.write(": sourced\n")
ELSE = os.path.join(T, "elsewhere")
POLLUTE2 = {"REPO_DIR": ELSE, "STORY_DIR": ELSE, "SPRINT_DIR": ELSE, "BASE_REF": "HEAD", "SCOPE_GLOBS": "**",
            "TEST_GLOBS": "*", "SCAFFOLD_SYMBOLS": "x", "STANDARDS_FILE": os.path.join(ELSE, "s.md"),
            "INTAKE_FILE": os.path.join(ELSE, "i.md"), "MEMORY_FILE": os.path.join(ELSE, "m.md"),
            "TASK_CONTRACT": os.path.join(ELSE, "t.env"), "TOOL_FILE_PATH": os.path.join(ELSE, "f"),
            "CLAUDE_ENV_FILE": os.path.join(ELSE, "e"), "CLAUDE_PLUGIN_DATA": ELSE, "CLAUDE_PLUGIN_ROOT": ELSE,
            "BASH_ENV": os.path.join(EVIL, "bashenv"),
            "GIT_AUTHOR_NAME": "Operator Real", "GIT_AUTHOR_EMAIL": "op@real.invalid", "GIT_AUTHOR_DATE": "2001-01-01T00:00:00",
            "GIT_COMMITTER_NAME": "Operator Real", "GIT_COMMITTER_EMAIL": "op@real.invalid",
            "GIT_COMMITTER_DATE": "2001-01-01T00:00:00", "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": os.path.join(EVIL, "hooks"),
            "GIT_CONFIG_PARAMETERS": "'core.hooksPath'='%s'" % os.path.join(EVIL, "hooks"),
            "GIT_CONFIG_GLOBAL": os.path.join(EVIL, "gitconfig"), "GIT_CONFIG_SYSTEM": os.path.join(EVIL, "gitconfig"),
            "ANTHROPIC_MODEL": "m", "ANTHROPIC_SMALL_FAST_MODEL": "m", "ANTHROPIC_DEFAULT_TIER_MODEL": "m",
            "CLAUDE_CODE_SUBAGENT_MODEL": "m", "CLAUDE_EFFORT": "max", "MAX_THINKING_TOKENS": "1",
            "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "1", "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "9",
            "CLAUDE_CODE_COORDINATOR_EXTRA_TOOLS": "x", "CLAUDECODE": "1", "CLAUDE_CODE_CHILD_SESSION": "1",
            "CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD": "1", "CLAUDE_ADDITIONAL_DIRECTORIES": ELSE,
            "PYTEST_ADDOPTS": "--deselect tests/test_todo.py", "PYTEST_PLUGINS": "x"}


def t_env_more():
    out2 = os.path.join(T, "spy2.env")
    r, d, p2 = dry(env=dict(ENV, PATH=SPY + ":" + ENV["PATH"], E2E_SPY_OUT=out2, ANTHROPIC_BASE_URL="https://gateway.invalid",
                            **POLLUTE2))
    blocks = [b for b in open(out2).read().split("----\n") if b.strip()]
    assert len(blocks) >= 4, len(blocks)
    for b in blocks:
        names = {ln.split("=", 1)[0] for ln in b.splitlines() if "=" in ln}
        assert not (names & set(POLLUTE2)), sorted(names & set(POLLUTE2))
    assert set(POLLUTE2) <= set(p2["env"]["scrubbed"]), sorted(set(POLLUTE2) - set(p2["env"]["scrubbed"]))
    assert not os.path.exists(HOOKED), "an inherited git config ran a hook in the seed"
    who = set(git(os.path.join(d, "repo"), "log", "--format=%an <%ae> | %cn <%ce>").stdout.split("\n")) - {""}
    assert who == {"aloop e2e <e2e@example.invalid> | aloop e2e <e2e@example.invalid>"}, who
    text2 = open(os.path.join(d, "plan.json")).read()
    assert "Operator Real" not in text2 and "gateway.invalid" not in text2, "names only, never values"
    assert "ANTHROPIC_BASE_URL" in p2["env"]["host_kept"] and not set(POLLUTE2) & set(p2["env"]["host_kept"]), p2["env"]
    se2 = MP.session_env(dict(POLLUTE2, PATH="/usr/bin", KEEP="1", ANTHROPIC_BASE_URL="u", ANTHROPIC_API_KEY="k",
                              CLAUDE_CODE_OAUTH_TOKEN="t", HTTPS_PROXY="p", GIT_SSL_CAINFO="c"), 1, "/s")
    assert not set(POLLUTE2) & set(se2), sorted(set(POLLUTE2) & set(se2))
    for k in ("KEEP", "ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "HTTPS_PROXY", "GIT_SSL_CAINFO"):
        assert k in se2, k                     # auth, proxy and transport stay
    gl = open(os.path.join(BIN, "_gatelib.sh")).read()
    keys = re.search(r'^TASK_ENV_KEYS="([^"]+)"', gl, re.M).group(1).split()
    assert len(keys) >= 8 and all(MP.scrubbed(k) for k in keys), [k for k in keys if not MP.scrubbed(k)]
    reads, assigned = set(), set()          # every variable a gate reads is scrubbed, or set by the gate itself first
    for name in sorted(os.listdir(BIN)):
        pth = os.path.join(BIN, name)
        if not os.path.isfile(pth) or not (name.startswith("gate-") or name in ("_gatelib.sh", "_paths.sh", "_failclosed.sh")):
            continue
        src = open(pth, errors="replace").read()
        if src.startswith("#!/usr/bin/env python3"):
            reads |= set(re.findall(r"os\.environ(?:\.get)?[\(\[]\s*[\"']([A-Z][A-Z0-9_]*)", src))
        else:
            reads |= set(re.findall(r"\$\{?!?([A-Z][A-Z0-9_]*)", src))
            assigned |= set(re.findall(r"^([A-Z][A-Z0-9_]*)=", src, re.M))
    loose = sorted(n for n in reads - assigned - {"PATH", "PWD", "HOME", "BASH_SOURCE", "NF"} if not MP.scrubbed(n))
    assert not loose, loose


block("the gate contract (REPO_DIR, STORY_DIR, SPRINT_DIR, BASE_REF, SCOPE_GLOBS, TEST_GLOBS … and every "
      "TASK_ENV_KEYS name), BASH_ENV, GIT_AUTHOR_*/GIT_COMMITTER_*/GIT_CONFIG_*, the host's model, effort and thinking "
      "settings and PYTEST_* never reach the seed or a session (no hook ran, the seed is the run's author); every "
      "variable a gate reads is scrubbed or the gate's own; the host variables kept are named in plan.json", t_env_more)

assert MP.preflight_plugins(json.dumps([{"id": "aloop@aloop-marketplace", "enabled": True}])) == 2
assert MP.preflight_plugins(json.dumps([{"id": "agentic-agile@agentic-marketplace", "enabled": True}])) == 2
assert MP.preflight_plugins(json.dumps([{"id": "aloop@aloop-marketplace", "enabled": False},
                                        {"id": "other@synced", "enabled": True}])) == 0
assert MP.preflight_plugins("Installed plugins: none") == 2               # no JSON: refused, never assumed clean
assert MP.preflight_network("http://127.0.0.1:9/") == 2
ok("the live preflight refuses an enabled aloop (or agentic-agile) plugin, unreadable plugin output and no network")

# ── 5) the mission summary ────────────────────────────────────────────────────────────────────────
R = os.path.join(T, "summary-run")
os.makedirs(os.path.join(R, "sessions"))
os.makedirs(os.path.join(R, "repo", ".aladdin", "logs"))
subprocess.run(["git", "init", "-q", os.path.join(R, "repo")], check=True, env=ENV)
seed = "0" * 40
with open(os.path.join(R, "plan.json"), "w") as f:
    json.dump({"host": "claude", "fixture": "todo", "seed": {"sha": seed}, "env": {"deviations": ["x"]}}, f)
for n, rows in ((1, [{"type": "system", "subtype": "init"}, "not json",
                     {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.5, "num_turns": 9},
                     {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 1.1, "num_turns": 30,
                      "modelUsage": {"model-a": {"costUSD": 1.1}}}]),
                (2, [{"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.25, "num_turns": 12,
                      "modelUsage": {"model-b": {"costUSD": 0.25}}}])):
    with open(os.path.join(R, "sessions", "%02d.jsonl" % n), "w") as f:
        for row in rows:
            f.write((row if isinstance(row, str) else json.dumps(row)) + "\n")
with open(os.path.join(R, "repo", ".aladdin", "logs", "gates.jsonl"), "w") as f:
    f.write('{"gate":"gate-red-verify","exit":0,"selfcheck":false,"reason":"1","released":"stuck"}\n')   # _gatelib's spelling
    f.write(json.dumps({"gate": "gate-green-verify", "exit": 0, "selfcheck": False}) + "\n")
    f.write(json.dumps({"gate": "gate-final", "exit": 0, "selfcheck": False, "released": "stuck"}) + "\n")   # json.dumps'
    f.write(json.dumps({"gate": "gate-scaffold-verify", "exit": 2, "selfcheck": True}) + "\n")
    f.write("not json\n")
r = subprocess.run([sys.executable, SUMMARY, R, "--sessions", os.path.join(R, "sessions"), "--kind", "mission",
                    "--checks", "3/4", "--secs", "925", "--run", "7"], env=ENV, capture_output=True, text=True)
assert r.returncode == 0, (r.stdout, r.stderr)
s = json.load(open(os.path.join(R, "summary.json")))
assert s["kind"] == "mission" and s["cost_usd"] == 1.35 and s["turns"] == 42 and s["sessions"] == 2, s
assert s["stuck_releases"] == 2 and s["gate_verdicts"] == 4 and s["gate_blocks"] == 1 and s["selfcheck_blocks"] == 1, s
assert s["passed"] == {"red": False, "green": True, "final": False}, s["passed"]
assert s["seed_sha"] == seed and s["deviations"] == ["x"] and s["cost_complete"] is True and s["wall"] == "15m25s", s
assert [x["models"] for x in s["per_session"]] == [["model-a"], ["model-b"]], s["per_session"]
assert s["tick_usd"] is None and s["reproduce"] == "make e2e-mission YES=1", s
row = [ln for ln in r.stdout.splitlines() if ln.startswith("| mission ")][-1]
cells = [c.strip() for c in row.strip("|").split("|")]
assert cells[0] == "mission 7" and cells[1] == "todo (claude)" and cells[3] == "3/4" and cells[4] == "2 / 0", cells
assert cells[6:9] == ["15m25s", "$1.35", "—"] and cells[-1] == "`make e2e-mission YES=1`" and len(cells) == 11, cells
assert os.path.isfile(os.path.join(R, "summary.md"))
os.remove(os.path.join(R, "sessions", "02.jsonl"))
with open(os.path.join(R, "sessions", "02.jsonl"), "w") as f:      # a killed session prints no result line
    f.write(json.dumps({"type": "assistant", "message": {"content": "x"}}) + "\n")
r = subprocess.run([sys.executable, SUMMARY, R, "--sessions", os.path.join(R, "sessions"), "--kind", "mission"],
                   env=ENV, capture_output=True, text=True)
s = json.load(open(os.path.join(R, "summary.json")))
assert r.returncode == 0 and s["cost_complete"] is False and s["cost_usd"] == 1.1, s
r = subprocess.run([sys.executable, SUMMARY, R, "--sessions", os.path.join(R, "sessions"), "--kind", "intent"],
                   env=ENV, capture_output=True, text=True)
assert r.returncode == 64, r.returncode
ok("the mission summary sums the last result line per session, flags a session with none (cost_complete false), "
   "counts stuck releases in both spellings and never counts one as a pass; one ledger row")


def gate_line(gate, role, task, attempt, wt, rc, selfcheck=False, **extra):
    return json.dumps(dict({"ts": "2001-01-01T00:00:00Z", "gate": gate, "role": role, "task": task, "attempt": attempt,
                            "story_dir": "", "wt": wt, "exit": rc, "selfcheck": selfcheck, "reason": ""}, **extra)) + "\n"


def budget_gates(repo_):
    """A gate log where only the final gate truly passed: RED's pass is a worker's self-check (the host's run
    blocked), GREEN's exit 0 is a budget release (a task.token_hard_limit for that task and attempt), and another
    GREEN's is one with a budget-exceeded marker in its worktree; and the matching events."""
    wt = os.path.join(repo_, "wt-over")
    os.makedirs(os.path.join(wt, ".aladdin"), exist_ok=True)
    with open(os.path.join(wt, ".aladdin", "budget-exceeded"), "w") as f:
        f.write("role=green-worker attempt=2 agent_id=a1 at=1 900k of 800k tokens used\n")
    os.makedirs(os.path.join(repo_, ".aladdin", "logs"), exist_ok=True)
    with open(os.path.join(repo_, ".aladdin", "logs", "gates.jsonl"), "w") as f:
        f.write(gate_line("gate-red-verify", "red-worker", "S1-01-T1", "1", repo_, 0, selfcheck=True))
        f.write(gate_line("gate-red-verify", "red-worker", "S1-01-T1", "1", repo_, 2, reason="123"))
        f.write(gate_line("gate-green-verify", "green-worker", "S1-01-T1", "1", repo_, 0))
        f.write(gate_line("gate-green-verify", "green-worker", "S1-01-T2", "2", wt, 0))
        f.write(gate_line("gate-final", "final-gate", "", "", repo_, 0, selfcheck=True))
        f.write(gate_line("gate-final", "final-gate", "", "", repo_, 0))
    with open(os.path.join(repo_, ".aladdin", "logs", "events.jsonl"), "a") as f:
        f.write(json.dumps({"schema": "aladdin.event/1", "event_id": "b" * 32, "ts": "2001-01-01T00:00:00Z",
                            "event": "task.token_hard_limit", "task": "S1-01-T1", "attempt": "1",
                            "ids": {"task": "S1-01-T1", "attempt": "1"}, "data": {"tokens": 900000}}) + "\n")


def t_summary_more():
    R2 = os.path.join(T, "summary-run-2")
    os.makedirs(os.path.join(R2, "sessions"))
    rp = os.path.join(R2, "repo")
    subprocess.run(["git", "init", "-q", rp], check=True, env=ENV)
    with open(os.path.join(R2, "plan.json"), "w") as f:
        json.dump({"host": "claude", "fixture": "todo", "seed": {"sha": "0" * 40},
                   "env": {"deviations": ["x"], "host_kept": ["ANTHROPIC_BASE_URL"]}}, f)
    with open(os.path.join(R2, "sessions", "01.jsonl"), "w") as f:
        f.write(json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.5,
                            "num_turns": 3}) + "\n")
    budget_gates(rp)
    M.record(rp, "telemetry.consent", "docs/agents/mission/telemetry/tel.json", "human", by="Claude", sha256="ab" * 32)
    rows = open(M.ledger(rp)).read().splitlines()
    last = json.loads(rows[-1])
    with open(M.ledger(rp), "a") as f:          # a human consent whose hash is made up
        f.write(json.dumps({"event_id": "f" * 32, "ts": last["ts"], "event": "ethics.consent", "role": "human",
                            "data": {"by": BY, "subject": "x", "sha256": "0" * 64,
                                     "prompt": {"line": "consent gol_x 00000000", "session": "s"}},
                            "prev": last["hash"], "hash": "0" * 64}, sort_keys=True) + "\n")
    r = subprocess.run([sys.executable, SUMMARY, R2, "--sessions", os.path.join(R2, "sessions"), "--kind", "mission"],
                       env=ENV, capture_output=True, text=True)
    assert r.returncode == 0, (r.stdout, r.stderr)
    s2 = json.load(open(os.path.join(R2, "summary.json")))
    assert s2["passed"] == {"red": False, "green": False, "final": True}, s2["passed"]
    assert s2["grants"] == [] and len(s2["ledger_problems"]) >= 2, (s2["grants"], s2["ledger_problems"])
    assert s2["host_env_kept"] == ["ANTHROPIC_BASE_URL"], s2.get("host_env_kept")
    row2 = [ln for ln in r.stdout.splitlines() if ln.startswith("| mission ")][-1]
    assert [c.strip() for c in row2.strip("|").split("|")][4] == "1 / 0", row2
    assert "ledger" in open(os.path.join(R2, "summary.md")).read().lower()


block("the summary counts a gate pass only when the host ran it (not a self-check) and it was not a budget "
      "release (a hard-limit event or a budget-exceeded marker for that task attempt); its grants are the ledger "
      "rows that verify, and it reports the ledger's problems and the host variables kept", t_summary_more)

# ── 6) the live loop (mission_driver.py) on a stub claude that behaves like a host ──────────────────
# scripts/test/fixtures/mission_host.py is `claude` here: it captures its prompt through the plugin's
# UserPromptSubmit hooks as a host does, runs the mission CLI the way the playbook's supervisor would (or
# idles, or hangs), and prints stream-json with a result line; a scenario file configures each session.
HOSTDIR = os.path.join(T, "host")
os.makedirs(HOSTDIR)
with open(os.path.join(HOSTDIR, "claude"), "w") as f:
    f.write('#!/bin/sh\nexec "%s" -B "%s" "$@"\n' % (sys.executable, os.path.join(ROOT, "scripts", "test", "fixtures",
                                                                                  "mission_host.py")))
os.chmod(os.path.join(HOSTDIR, "claude"), 0o755)
HAVE_PYTEST = subprocess.run([os.path.join(TOOLS, "python3"), "-m", "pytest", "--version"], capture_output=True).returncode == 0
HENV = dict(ENV, PATH=":".join([HOSTDIR, TOOLS, "/usr/bin", "/bin"]), E2E_POLL_S="0.3")
if not HAVE_PYTEST:     # a stand-in that really runs tests/test_*.py's test functions (the preflight and checks need one)
    PYT = os.path.join(T, "pytest-stand-in", "pytest")
    os.makedirs(PYT)
    open(os.path.join(PYT, "__init__.py"), "w").close()
    with open(os.path.join(PYT, "__main__.py"), "w") as f:
        f.write("import glob, importlib.util, os, sys\nsys.dont_write_bytecode = True\n"
                "if '--version' in sys.argv:\n    print('pytest stand-in')\n    sys.exit(0)\n"
                "sys.path.insert(0, os.getcwd())\nfails = n = 0\n"
                "for p in sorted(glob.glob('tests/test_*.py')):\n"
                "    spec = importlib.util.spec_from_file_location(os.path.basename(p)[:-3], p)\n"
                "    m = importlib.util.module_from_spec(spec)\n    spec.loader.exec_module(m)\n"
                "    for k in sorted(dir(m)):\n        if k.startswith('test_') and callable(getattr(m, k)):\n"
                "            n += 1\n            try:\n                getattr(m, k)()\n"
                "                print('PASSED %s::%s' % (p, k))\n"     # pytest -rA's summary line
                "            except Exception as e:\n                fails += 1\n"
                "                print('FAILED %s::%s - %s' % (p, k, e))\n"
                "print('%d passed, %d failed' % (n - fails, fails))\nsys.exit(1 if fails or not n else 0)\n")
    HENV["PYTHONPATH"] = os.path.dirname(PYT)
with open(os.path.join(T, "net.txt"), "w") as f:
    f.write("the web\n")
HENV["E2E_NET_URL"] = "file://" + os.path.join(T, "net.txt")      # the preflight's network check, offline
PLAN_PY = os.path.join(SMOKE, "mission_plan.py")
_, TEMPLATE, TPLAN = dry()                                            # a seeded run dir every driver scenario copies
POLLUTE_RUN = {"GATE_RUN_TESTS": "0", "AGENTIC_NO_BUDGET": "1", "AGENTIC_CWD": os.path.join(T, "elsewhere"),
               "ALOOP_MISSION_LOOP": "4242", "SKIP_HOOKS": "1", "TASK_ID": "T9", "AGENT_ROLE": "green-worker",
               "ALADDIN_SPRINT_LOCK_TTL": "1", "CLAUDE_CODE_SESSION_ID": "parent",
               "REPO_DIR": ELSE, "SCOPE_GLOBS": "**", "BASH_ENV": os.path.join(EVIL, "bashenv"),
               "GIT_AUTHOR_NAME": "Operator Real", "GIT_AUTHOR_EMAIL": "op@real.invalid",
               "GIT_COMMITTER_NAME": "Operator Real", "GIT_COMMITTER_EMAIL": "op@real.invalid", "GIT_CONFIG_COUNT": "1",
               "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": os.path.join(EVIL, "hooks"),
               "ANTHROPIC_MODEL": "m", "CLAUDE_CODE_SUBAGENT_MODEL": "m", "CLAUDE_EFFORT": "max",
               "PYTEST_ADDOPTS": "--deselect tests/test_todo.py"}


class Live:
    def __init__(self, r, run_dir, stub):
        self.r, self.dir, self.stub, self.repo = r, run_dir, stub, os.path.join(run_dir, "repo")
        self.out = r.stdout + r.stderr
        p = os.path.join(run_dir, "driver.json")
        self.drv = json.load(open(p)) if os.path.isfile(p) else {}
        p = os.path.join(stub, "calls.jsonl")
        self.calls = [json.loads(x) for x in open(p)] if os.path.isfile(p) else []
        p = os.path.join(run_dir, "sessions.jsonl")
        self.sessions = [json.loads(x) for x in open(p)] if os.path.isfile(p) else []
        self.stop = self.drv.get("stop") or {}

    def ledger(self, event=None):
        return [x for x in M.ledger_rows(self.repo)[0] if event is None or x["event"] == event]

    def events(self, event):
        return [e for e in M._events.read(self.repo) if e.get("event") == event]

    def checks(self):
        return {c["name"]: c for c in self.drv.get("check_results") or []}


def stub_dir(name, sessions=None, default=None):
    d = os.path.join(T, "stub-" + name)
    os.makedirs(d)
    with open(os.path.join(d, "scenario.json"), "w") as f:
        json.dump({"default": default or {}, "sessions": sessions or {}}, f)
    return d


def mkplan(name, sessions=None, default=None, env=None):
    """A copy of the seeded run dir with a live plan (mission_plan.py plan without --dry-run) and its stub's dir:
    (run dir, stub dir, env)."""
    R = os.path.join(T, "live-" + name)
    shutil.copytree(TEMPLATE, R, symlinks=True)
    os.remove(os.path.join(R, "plan.json"))
    stub = stub_dir(name, sessions, default)
    e = dict(HENV, MISSION_STUB_DIR=stub, **(env or {}))
    p = subprocess.run([sys.executable, "-B", PLAN_PY, "plan", "--run-dir", R, "--repo", os.path.join(R, "repo"),
                        "--host", "claude", "--model", "stub-sup", "--seed-sha", TPLAN["seed"]["sha"],
                        "--shim-dir", os.path.join(R, "bin")], env=e, capture_output=True, text=True)
    assert p.returncode == 0 and json.load(open(os.path.join(R, "plan.json")))["live"] is True, (p.stdout, p.stderr)
    return R, stub, e


def drive(R, stub, e, name, timeout=300):
    r = subprocess.run([sys.executable, "-B", DRIVER, "--plan", os.path.join(R, "plan.json")], cwd=T, env=e,
                       capture_output=True, text=True, timeout=timeout)
    assert "Traceback" not in r.stdout + r.stderr, (name, r.stdout[-3000:], r.stderr[-3000:])
    return Live(r, R, stub)


def live(name, sessions=None, default=None, env=None, timeout=300):
    """The driver on a copy of the seeded run dir, with a live plan."""
    R, stub, e = mkplan(name, sessions, default, env)
    return drive(R, stub, e, name, timeout)


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(st) and not st.startswith("Z")


HEX8 = re.compile(r"\b[0-9a-f]{8}\b")
GRANTISH = re.compile(r"^\s*(approve|confirm|consent|restore|retire|veto|revoke)\b", re.I | re.M)

# 6a) the whole cycle through e2e-mission.sh's live path: the stub sets the mission from the first message,
#     each grant asks and shows its line, the driver types it back in a NEW session, the stub's sprint is the
#     one-line fix; after goal.met the stub raises the bar (a RESEARCH tick) and the driver kills that session
stub = stub_dir("happy", sessions={"4": {"after_goal_met": "raise"}}, default={"cost": 0.75, "turns": 12})
HD = tempfile.mkdtemp(prefix="run-", dir=T)
r = subprocess.run([SH, "--yes", "--model", "stub-sup"], cwd=T, capture_output=True, text=True, timeout=600,
                   env=dict(HENV, MISSION_STUB_DIR=stub, E2E_DIR=HD, **POLLUTE_RUN))
H = Live(r, HD, stub)
assert "Traceback" not in H.out, H.out[-4000:]
assert r.returncode == 0, (r.returncode, H.out[-5000:])
ck = H.checks()
assert len(ck) >= 19 and all(c["ok"] for c in ck.values()) and H.drv["checks"] == "%d/%d" % (len(ck), len(ck)), \
    [c for c in ck.values() if not c["ok"]]
for c in ck.values():
    assert ("ok - %s" % c["name"]) in r.stdout, c["name"]
assert H.drv["outcome"] == "GOAL MET" and H.stop["code"] == "goal_met", H.drv
assert [s["kind"] for s in H.sessions] == ["first", "reply", "reply", "cron"], H.sessions
assert len(H.calls) == 4 and [c["n"] for c in H.calls] == [1, 2, 3, 4], H.calls
plan_h = json.load(open(os.path.join(HD, "plan.json")))
assert H.calls[0]["prompt"] == plan_h["first_prompt"] and H.sessions[0]["prompt_sha256"] == plan_h["first_prompt_sha256"]
appr, conf = H.ledger("mission.approved"), H.ledger("baseline.confirmed")
assert len(appr) == 1 and len(conf) == 1, (appr, conf)
aline, cline = appr[0]["data"]["prompt"]["line"], conf[0]["data"]["prompt"]["line"]
LINE_RE = re.compile(plan_h["line_re"])
assert LINE_RE.match(aline) and aline.startswith("approve msn_") and LINE_RE.match(cline) and cline.startswith("confirm bln_")
# (a) a reply is a new session whose prompt is exactly the slash command and the line, and the grant cites it
assert H.calls[1]["prompt"] == "/aloop:mission\n" + aline and H.calls[2]["prompt"] == "/aloop:mission\n" + cline, H.calls[1:3]
assert H.calls[3]["prompt"] == "/aloop:mission"
assert appr[0]["data"]["prompt"]["session"] == H.calls[1]["session_id"] == H.sessions[1]["session_id"]
assert conf[0]["data"]["prompt"]["session"] == H.calls[2]["session_id"] == H.sessions[2]["session_id"]
ids = [c["session_id"] for c in H.calls]
assert len(set(ids)) == 4 and all(str(uuid.UUID(x, version=4)) == x for x in ids), ids
for c in H.calls:
    assert not [x for x in c["argv"] if re.search(r"resume|continue|bypass|skip-permission|dangerous", x, re.I)], c["argv"]
    i = c["argv"].index("--session-id")
    assert c["argv"][i + 1] == c["session_id"] and "--setting-sources" in c["argv"] and "--strict-mcp-config" in c["argv"]
assert H.sessions[1]["prompt_sha256"] == hashlib.sha256(aline.encode()).hexdigest()        # a reply's: the line itself
assert [g["line"] for g in H.drv["grants"]] == [aline, cline] and all(r_["data"]["by"] == BY for r_ in H.ledger() if r_["role"] == "human")
# (f) the loop's pid on every session, nothing the run scrubs, and the crontab shim first on PATH (it refuses)
for c in H.calls:
    assert c["loop"] == str(H.drv["driver_pid"]) != "4242" and c["leaked"] == [] and c["path0"] == os.path.join(HD, "bin"), c
    assert c["crontab"]["list"][0] not in (0, None) and "disabled" in c["crontab"]["list"][1], c["crontab"]
    assert c["crontab"]["install"][0] not in (0, None) and "disabled" in c["crontab"]["install"][1], c["crontab"]
assert any("ALOOP_MISSION_LOOP" in x for x in H.drv["deviations"]), H.drv["deviations"]
# (e) every mission.tick was a session's (the driver's pid as the loop), none the driver's; the driver's own counts agree
ticks = H.events("mission.tick")
assert ticks and all(e["data"].get("loop") == str(H.drv["driver_pid"]) for e in ticks), [e["data"].get("loop") for e in ticks]
assert len(ticks) == sum(len(s["actions"]) for s in H.sessions), (len(ticks), [s["actions"] for s in H.sessions])
# decision 10: the session that raised the bar after goal.met was killed, its cost flagged incomplete
last = H.sessions[3]
assert last["killed"] and "RESEARCH" in last["killed"] and last["cost_complete"] is False and last["actions"][-1] == "RESEARCH", last
assert not alive(H.calls[3]["pid"]), H.calls[3]
assert all(s["cost_complete"] for s in H.sessions[:3]) and [s["cost_usd"] for s in H.sessions[:3]] == [0.75] * 3
assert [list(s["models"]) for s in H.sessions[:3]] == [["stub-model"]] * 3, H.sessions
sm = json.load(open(os.path.join(HD, "summary.json")))
assert sm["kind"] == "mission" and sm["seed_sha"] == plan_h["seed"]["sha"] and sm["checks"] == H.drv["checks"], sm
assert sm["cost_complete"] is False and sm["outcome"] == "GOAL MET" and sm["driver"]["stop"]["code"] == "goal_met", sm
smd = open(os.path.join(HD, "summary.md")).read()
assert "## Checks (%s)" % H.drv["checks"] in smd and smd.count("\n- ok ") == len(ck) and "≥$" in smd, smd
assert "| mission ? | todo (claude) |" in r.stdout, r.stdout[-1500:]
assert subprocess.run(["git", "diff", "--quiet", plan_h["seed"]["sha"], "--", RUNNER], cwd=H.repo, env=ENV).returncode == 0
ok("the live loop on a stub host through e2e-mission.sh: first, reply, reply, cron; each reply a new session whose "
   "prompt is the slash command and the line, cited by its grant; every check passes (%s); the session that raised "
   "the bar after goal.met was killed with its group, cost flagged incomplete" % H.drv["checks"])
ok("the sessions' env: ALOOP_MISSION_LOOP is the driver's pid (recorded as a deviation), no scrubbed family leaks, "
   "the crontab shim is first on PATH and refuses; every mission.tick was made inside a session")


def check_named(checks, prefix):
    hit = [c for n, c in checks.items() if n.startswith(prefix)]
    assert len(hit) == 1, (prefix, sorted(checks))
    return hit[0]


def t_happy_more():
    pid = H.drv["driver_pid"]
    for c in H.calls:
        assert (c.get("lock") or {}).get("pid") == pid, c.get("lock")       # decision 6: the driver holds the loop lock
        leak = set(c["env_names"]) & (set(POLLUTE_RUN) - {"ALOOP_MISSION_LOOP"})
        assert not leak, sorted(leak)
    g = {x["action"]: x for x in H.drv["grants"]}
    assert g["CONFIRM_MISSION"]["subject"] == "docs/agents/mission/mission.json", g
    assert g["CONFIRM_MISSION"]["sha256"] == appr[0]["data"]["sha256"], g
    assert (g["CONFIRM_BASELINE"]["subject"], g["CONFIRM_BASELINE"]["sha256"]) == (conf[0]["data"]["subject"],
                                                                                    conf[0]["data"]["sha256"]), g
    who = set(subprocess.run(["git", "log", "--format=%an <%ae> | %cn <%ce>"], cwd=H.repo, env=ENV, capture_output=True,
                             text=True).stdout.split("\n")) - {""}
    assert who == {"aloop e2e <e2e@example.invalid> | aloop e2e <e2e@example.invalid>"}, who
    assert not os.path.exists(HOOKED), "an inherited git config ran a hook inside a session"
    for prefix in ("the ledger verifies", "every commit after the seed is by", "every relayed line is cited by exactly one"):
        assert check_named(H.checks(), prefix)["ok"], prefix
    smh = json.load(open(os.path.join(HD, "summary.json")))
    assert smh["ledger_problems"] == [] and [x["event"] for x in smh["grants"]] == ["mission.approved", "baseline.confirmed"]
    assert isinstance(smh["host_env_kept"], list) and not set(smh["host_env_kept"]) & set(POLLUTE_RUN), smh["host_env_kept"]
    assert all(s_.get("started_at") and s_.get("ended_at") for s_ in H.sessions)


block("the happy run: the driver held the mission loop lock in every session; no gate contract, git identity or "
      "config, BASH_ENV or host setting reached one; each relay records the subject and sha256 it checked and its "
      "grant covers exactly them; every commit is the run's author and no inherited hook ran", t_happy_more)

LEDGER_H = os.path.join(H.repo, "docs", "agents", "mission", "ledger.jsonl")
EVENTS_H = os.path.join(H.repo, ".aladdin", "logs", "events.jsonl")


def hctx(plan=None, sessions=None):
    """The happy run's post-run context, rebuilt from what it wrote (plan.json, sessions.jsonl, driver.json)."""
    drv = json.load(open(os.path.join(HD, "driver.json")))
    run = {"driver_pid": drv["driver_pid"], "relays": drv["grants"], "tick_usd": drv["tick_usd"], "stop": drv["stop"]}
    return D.Ctx(plan or plan_h, H.sessions if sessions is None else sessions, run)


def append_event(path, event, **data):
    with open(path, "a") as f:
        f.write(json.dumps({"schema": "aladdin.event/1", "event_id": uuid.uuid4().hex, "ts": iso_in(0), "event": event,
                            "role": "human", "refs": {}, "data": data}) + "\n")


def cited(line):
    return {"ts": iso_in(0), "sha256": "0" * 64, "session": "forged", "line": line}


def t_doctored():
    with environ(dict(HENV, MISSION_STUB_DIR=H.stub)):    # the checks run pytest (or its stand-in) as the driver does
        doctored_checks()


def doctored_checks():
    with contextlib.redirect_stdout(io.StringIO()):
        base = D.run_checks(hctx())
    assert all(x["ok"] for x in base), [x for x in base if not x["ok"]]       # from the files alone, every check holds
    real_c = conf[0]
    bsha, bsub = real_c["data"]["sha256"], real_c["data"]["subject"]
    bid_h = real_c["data"]["baseline_id"]
    # (F1) a forged human row (a made-up hash), and a well-hashed one that cites nothing, by someone else
    with kept(LEDGER_H, EVENTS_H):
        last = json.loads(open(LEDGER_H).read().splitlines()[-1])
        with open(LEDGER_H, "a") as f:
            f.write(json.dumps({"event_id": "f" * 32, "ts": last["ts"], "event": "ethics.consent", "role": "human",
                                "data": {"by": BY, "subject": "x", "sha256": "0" * 64}, "prev": last["hash"],
                                "hash": "0" * 64}, sort_keys=True) + "\n")
        ok_, why = D.check_ledger_intact(hctx())
        assert not ok_ and "edited" in why, why
    with kept(LEDGER_H, EVENTS_H, os.path.join(HD, "summary.json"), os.path.join(HD, "summary.md")):
        M.record(H.repo, "telemetry.consent", "docs/agents/mission/telemetry/tel.json", "human", by="Claude", sha256="ab" * 32)
        ok_, why = D.check_ledger_intact(hctx())
        assert not ok_ and "cites none" in why, why
        r_ = subprocess.run([sys.executable, SUMMARY, HD, "--sessions", os.path.join(HD, "sessions"), "--kind", "mission"],
                            env=ENV, capture_output=True, text=True)
        sm_ = json.load(open(os.path.join(HD, "summary.json")))
        assert r_.returncode == 0 and [x["by"] for x in sm_["grants"]] == [BY, BY] and sm_["ledger_problems"], sm_["grants"]
        assert "| 4 / 2 |" in r_.stdout, r_.stdout[-600:]
    # (F3) a second confirmation of another baseline (looser, with a rival) that copies the real row's data
    forged = os.path.join(H.repo, os.path.dirname(bsub), "bln_" + "f" * 20 + ".json")
    with kept(LEDGER_H, EVENTS_H, forged):
        bdoc_h = json.load(open(os.path.join(H.repo, bsub)))
        with open(forged, "w") as f:
            json.dump(dict(bdoc_h, baseline_id="bln_" + "f" * 20, tolerance=1.0, rivals=["x"]), f)
        M.record(H.repo, "baseline.confirmed", M.rel(H.repo, forged), "human",
                 **{k: v for k, v in real_c["data"].items() if k != "subject"})
        assert M.ledger_problems(H.repo) == []       # the chain is intact: only the content check can see it
        c_ = hctx()
        for fn in (D.check_baseline_confirmed, D.check_grants_relayed, D.check_ledger_intact, D.check_failing_at_baseline):
            assert not fn(c_)[0], (fn.__name__, fn(c_))
    # (F3b) each part of a relayed line's own check, alone: a second grant of the very content checked, citing the
    # line (two grants for one line); the line checked against other content than its one grant covers; and the
    # checked and granted content changed on disk afterwards
    with kept(LEDGER_H, EVENTS_H):
        M.record(H.repo, "baseline.confirmed", bsub, "human", **{k: v for k, v in real_c["data"].items() if k != "subject"})
        assert M.ledger_problems(H.repo) == [], M.ledger_problems(H.repo)
        ok_, why = D.check_grants_relayed(hctx())
        assert not ok_ and "2 ledger rows cite" in why, why
    with kept(forged):
        bdoc_h = json.load(open(os.path.join(H.repo, bsub)))
        fdoc = dict(bdoc_h, baseline_id="bln_" + "f" * 20, tolerance=1.0)
        with open(forged, "w") as f:
            json.dump(fdoc, f)
        c_ = hctx()
        for x in c_.run["relays"]:
            if x["action"] == "CONFIRM_BASELINE":
                x.update(subject=M.rel(H.repo, forged), sha256=M.digest(fdoc))
        ok_, why = D.check_grants_relayed(c_)
        assert not ok_ and "the line was checked against" in why and "not the content checked" not in why, why
    with doctored(os.path.join(H.repo, bsub), lambda b: b.update(tolerance=1.0)):
        ok_, why = D.check_grants_relayed(hctx())
        assert not ok_ and "not the content checked" in why and "the line was checked against" not in why, why
    # (F5) a mission.set of other content than the mission.json whose set_by is checked
    with kept(EVENTS_H):
        append_event(EVENTS_H, "mission.set", mission_id=appr[0]["data"]["mission_id"], mode="autonomous", sha256="0" * 64)
        assert not D.check_human_by(hctx())[0], D.check_human_by(hctx())
    # (F6, F23) a restore and a consent, each citing a line that says it: outside the allowlist
    for event, subject, data in (("baseline.confirmed", bsub, dict(sha256=bsha, baseline_id=bid_h, action="restore",
                                                                   prompt=cited("restore %s %s" % (bid_h, bsha[:8])))),
                                 ("ethics.consent", "docs/agents/mission/goals/gol_x.json",
                                  dict(sha256="ab" * 32, prompt=cited("consent gol_x abababab")))):
        with kept(LEDGER_H, EVENTS_H):
            M.record(H.repo, event, subject, "human", by=BY, **data)
            assert M.ledger_problems(H.repo) == [], M.ledger_problems(H.repo)
            ok_, why = D.check_allowlist(hctx())
            assert not ok_ and event in why, why
    # (F23) each remaining check on a context that breaks only it
    assert not D.check_crontab(hctx(plan=dict(plan_h, crontab_before={"exit": 0, "sha256": "0" * 64})))[0]
    with kept(EVENTS_H):
        append_event(EVENTS_H, "mission.tick", action="SPRINT", loop="1")
        assert not D.check_driver_wrote_nothing(hctx())[0]
    last_end = max(D.ms_of(s_["ended_at"]) for s_ in H.sessions) / 1000.0
    time.sleep(max(0.0, last_end + 3.5 - time.time()))      # the row lands outside every session's window
    with kept(LEDGER_H, EVENTS_H):
        M.record(H.repo, "state.assessed", "docs/agents/mission/state/sta_x.json", "mission")
        ok_, why = D.check_driver_wrote_nothing(hctx())
        assert not ok_ and "state.assessed" in why, why
    assert not D.check_cost(hctx(sessions=[dict(x, cost_usd=100.0) for x in H.sessions]))[0]
    assert D.TRACE.search("mission msn_%s approved by %s at 2001-01-01" % ("0" * 20, BY))
    assert not D.TRACE.search("mission msn_%s approved by Claude" % ("0" * 20)) and not D.TRACE.search("")
    head = git(H.repo, "rev-parse", "HEAD").stdout.strip()
    try:
        with open(os.path.join(H.repo, "todo", "__init__.py"), "a") as f:
            f.write("# tweak\n")
        r_ = subprocess.run(["git", "commit", "-qam", "tweak"], cwd=H.repo, capture_output=True, text=True,
                            env=dict(ENV, GIT_AUTHOR_NAME="Operator Real", GIT_COMMITTER_NAME="Operator Real"))
        assert r_.returncode == 0, r_.stderr
        c_ = hctx()
        assert not D.check_trace(c_)[0] and not D.check_authors(c_)[0], (D.check_trace(c_), D.check_authors(c_))
    finally:
        subprocess.run(["git", "reset", "-q", "--hard", head], cwd=H.repo, env=ENV, check=True)
    for path, text in ((os.path.join(H.repo, "tests", "test_more.py"), "import pytest\n\n\n@pytest.mark.skip\ndef test_x():\n    pass\n"),
                       (os.path.join(H.repo, "conftest.py"), "def pytest_collection_modifyitems(items):\n    pass\n"),
                       (os.path.join(H.repo, "pytest.ini"), "[pytest]\naddopts = --deselect tests/test_todo.py\n"),
                       (os.path.join(H.repo, "setup.cfg"), "[tool:pytest]\naddopts = -p no:x\n"),
                       (os.path.join(H.repo, "pyproject.toml"), "[tool.pytest.ini_options]\naddopts = \"-k add\"\n"),
                       (os.path.join(H.repo, "todo", "x.py"), "import unittest\n@unittest.skip('no')\nclass T: pass\n")):
        with doctored(path, text=text):
            ok_, why = D.check_no_suppressed(hctx())
        assert not ok_ and os.path.relpath(path, H.repo) in why, (path, why)
    for path, text in ((os.path.join(HD, "conftest.py"), "def pytest_collection_modifyitems(items):\n    pass\n"),
                       (os.path.join(HD, "tox.ini"), "[pytest]\naddopts = -k add\n")):     # above the repo: pytest takes it
        with doctored(path, text=text):
            ok_, why = D.check_no_suppressed(hctx())
        assert not ok_ and "%s (above the repo)" % os.path.realpath(path) in why, (path, why)
    with doctored(os.path.join(H.repo, os.path.dirname(RUNNER), "json.py"), text="x = 1\n"):
        c_ = hctx()
        assert not D.check_runner(c_)[0] and not D.check_product(c_)[0], (D.check_runner(c_), D.check_product(c_))
    with contextlib.redirect_stdout(io.StringIO()):
        after_ = D.run_checks(hctx())
    assert all(x["ok"] for x in after_), [x for x in after_ if not x["ok"]]       # every doctoring was put back


def t_pytest_output():
    want = [("tests/test_todo.py", "test_a_new_list_is_empty")]
    good = "PASSED tests/test_todo.py::test_a_new_list_is_empty\n1 passed in 0.01s\n"
    assert D.pytest_problems(good, want) == []
    assert D.pytest_problems("PASSED tests/test_todo.py::TestList::test_a_new_list_is_empty[1]\n1 passed\n", want) == []
    for out in (good.replace("1 passed", "1 passed, 1 xfailed"), good.replace("1 passed", "1 passed, 1 deselected"),
                good + "SKIPPED [1] tests/test_add.py:3: no\n", good.replace("1 passed", "1 passed, 2 skipped"),
                good + "XPASS tests/test_add.py::test_y\n", "1 passed in 0.01s\n", good.replace("PASSED", "FAILED")):
        assert D.pytest_problems(out, want), out


block("each post-run check fails on a context that breaks only it: a forged or uncited human row (the ledger check; "
      "the summary's grants are the rows that verify), a second confirmation of other content citing the relayed "
      "line, a mission.set of other content, a restore or a consent, the crontab, a foreign tick or ledger row, the "
      "cost, an untraced commit by another author, a skip, a collection hook or a pytest config, a file beside the "
      "runner; and every check holds again once the context is put back", t_doctored)
block("pytest's output passes only when every test ran and passed: none skipped, xfailed, xpassed or deselected, "
      "each test's PASSED line present (a class or a parameter id counts)", t_pytest_output)

# 6b) the first session idles; the stream has no result line and a hook crash's text: the run stops, every
#     check is printed, exit 1, no traceback; the driver wrote no mission.tick and no ledger row
stub = stub_dir("idle", default={"mode": "idle", "result": False, "crash": True, "rc": 1})
ID = tempfile.mkdtemp(prefix="run-", dir=T)
r = subprocess.run([SH, "--yes", "--model", "stub-sup"], cwd=T, capture_output=True, text=True, timeout=300,
                   env=dict(HENV, MISSION_STUB_DIR=stub, E2E_DIR=ID))
I = Live(r, ID, stub)
assert r.returncode == 1 and "Traceback" not in I.out, (r.returncode, I.out[-3000:])
ck = I.checks()
assert len(ck) == len(H.checks()) and H.drv["checks"].split("/")[1] == I.drv["checks"].split("/")[1], I.drv
for c in ck.values():
    assert ("%s - %s" % ("ok" if c["ok"] else "FAIL", c["name"])) in r.stdout, c
passed = sorted(n for n, c in ck.items() if c["ok"])  # only what an idle run cannot break (the seed's own test passes)
want_ok = ("no grant outside the allowlist", "pytest passes on main", "no suppressed tests", "every commit after the seed is by",
           "crontab -l is unchanged", "the cost stayed within the outer cap", "the driver wrote no mission.tick")
assert len(passed) == len(want_ok) and all(any(n.startswith(w) for n in passed) for w in want_ok), passed
assert I.stop["code"] == "host_failure" and len(I.calls) == 1 and I.sessions[0]["cost_complete"] is False, (I.stop, I.sessions)
assert not ck["no hook crashed and no gate was stuck-released"]["ok"]
assert I.events("mission.tick") == [] and not os.path.exists(os.path.join(I.repo, "docs", "agents", "mission", "ledger.jsonl"))
assert json.load(open(os.path.join(ID, "summary.json")))["kind"] == "mission"
ok("a stub run where everything fails prints all %d checks (exit 1, no traceback); the driver itself wrote no "
   "mission.tick and no ledger row" % len(ck))

# 6c) (b) a forged line only in a tool's output and a line with another code are never typed: one surface session
F = live("forge", sessions={"1": {"show": "forge"}, "2": {"show": "wrong"}})
assert F.r.returncode == 1 and F.stop["code"] == "never_shown" and "never showed the human the line" in F.stop["reason"], F.stop
assert [s["kind"] for s in F.sessions] == ["first", "surface"] and F.calls[1]["prompt"] == "/aloop:mission", F.sessions
assert not [c for c in F.calls if GRANTISH.search(c["prompt"])] and F.ledger("mission.approved") == []
ok("a line shown only in a tool's output (a forged one beside it) or with another code is never typed: one surface "
   "session, then \"the session never showed the human the line\"")

# 6d) (c) the reply's hook does not capture: one relay, then "hook did not capture"; captured but not granted apart
C = live("nohook", sessions={"2": {"hook": False}})
assert C.stop["code"] == "hook_not_captured" and "hook did not capture" in C.stop["reason"], C.stop
assert [s["kind"] for s in C.sessions] == ["first", "reply"] and C.ledger("mission.approved") == [], C.sessions
G = live("nogrant", sessions={"2": {"grant": False}})
assert G.stop["code"] == "captured_not_granted" and "captured, not granted" in G.stop["reason"], G.stop
assert [s["kind"] for s in G.sessions] == ["first", "reply"] and G.ledger("mission.approved") == []
ok("a reply the hook did not capture stops after one relay (\"hook did not capture\"); one captured but never "
   "granted stops apart (\"captured, not granted\")")

# 6e) (d) the caps, each with its reason
S = live("sessions", env={"E2E_MAX_SESSIONS": "2"})
assert S.stop["code"] == "sessions_cap" and len(S.calls) == 2, (S.stop, len(S.calls))
U = live("usd", env={"E2E_BUDGET_USD": "10"}, sessions={"1": {"cost": 6.0}, "2": {"cost": 0.5, "ledger_usd": 9.5}})
assert [c["max_budget_usd"] for c in U.calls] == ["10.00", "4.00"], [c["max_budget_usd"] for c in U.calls]
assert U.stop["code"] == "usd_cap" and "$9.50" in U.stop["reason"] and "$6.50" in U.stop["reason"], U.stop
assert len(U.calls) == 2 and U.drv["tick_usd"] == 9.5 and U.drv["cost_usd"] == 6.5, U.drv
W = live("timeout", env={"E2E_SESSION_TIMEOUT": "2", **POLLUTE_RUN}, default={"mode": "hang"})
child = int(open(os.path.join(W.stub, "child-1.pid")).read())
assert W.stop["code"] == "session_timeout" and W.sessions[0]["timed_out"] is True and W.sessions[0]["secs"] < 30, (W.stop, W.sessions)
assert not alive(child) and not alive(W.calls[0]["pid"]), (child, W.calls[0]["pid"])
assert W.calls[0]["leaked"] == [] and W.calls[0]["loop"] == str(W.drv["driver_pid"])          # the driver scrubs itself too
P = live("noprogress", default={"mode": "idle"}, sessions={"1": {"mode": "cycle"}, "2": {"mode": "cycle", "max_actions": 1}})
assert P.stop["code"] == "no_progress" and [s["kind"] for s in P.sessions] == ["first", "reply", "cron", "cron"], (P.stop, P.sessions)
assert len(P.ledger("mission.approved")) == 1
ok("caps stop with their reason: sessions (2), USD (--max-budget-usd min(10, left): 10.00 then 4.00; the outer cap "
   "takes tick's $9.50 over the host's $6.50), a session timeout (its group killed, the child it left too), and no "
   "progress in 2 sessions")

# 6f) (h) a gray ethics verdict stops without consenting; (i) a pre-grant check failure grants nothing
E_ = live("gray", sessions={"2": {"ethics": "gray"}})
assert E_.stop["code"] == "human" and E_.stop["reason"].startswith("a gray ethics verdict needs a real human"), E_.stop
assert E_.ledger("ethics.consent") == [] and not [c for c in E_.calls if re.search(r"(?m)^consent ", c["prompt"])]
assert len(E_.calls) == 2
X1 = live("tamper", sessions={"1": {"tamper": "statement"}})
assert X1.stop["code"] == "pregrant" and "statement" in X1.stop["reason"] and len(X1.calls) == 1, X1.stop
assert X1.ledger("mission.approved") == []
X2 = live("twojourneys", sessions={"2": {"journeys": 2}})
assert X2.stop["code"] == "pregrant" and "journey" in X2.stop["reason"] and len(X2.calls) == 2, X2.stop
assert X2.ledger("baseline.confirmed") == [] and len(X2.ledger("mission.approved")) == 1
X3 = live("runner", sessions={"2": {"runner": "changed"}})
assert X3.stop["code"] == "pregrant" and "runner" in X3.stop["reason"] and X3.ledger("baseline.confirmed") == [], X3.stop
ok("a gray ethics verdict stops the run with no consent typed; mission.json unlike the typed answers, 2 journeys, or "
   "a runner that is not the seeded one stops it before the grant is typed")

# 6g) (j) a stuck release in either spelling is never a pass; (k) a WAIT key once, then the run stops
J_ = os.path.join(T, "gates-repo")
os.makedirs(os.path.join(J_, ".aladdin", "logs"))
subprocess.run(["git", "init", "-q", J_], check=True, env=ENV)
gl = os.path.join(J_, ".aladdin", "logs", "gates.jsonl")
ctx = D.Ctx({"repo": J_, "run_dir": T, "sessions_dir": os.path.join(T, "none")}, [], {})
with open(gl, "w") as f:
    f.write('{"gate":"gate-red-verify","exit":0,"selfcheck":false,"reason":"1","released":"stuck"}\n')
    f.write(json.dumps({"gate": "gate-green-verify", "exit": 0, "selfcheck": False, "released": "stuck"}) + "\n")
    f.write(json.dumps({"gate": "gate-final", "exit": 0, "selfcheck": False, "released": "stuck"}) + "\n")
for fn in (D.check_red_green, D.check_final_gate, D.check_no_crash):
    assert fn(ctx)[0] is False, (fn.__name__, fn(ctx))
assert D.gate_facts(J_)["stuck_releases"] == 3
with open(gl, "w") as f:
    for g in ("gate-red-verify", "gate-green-verify", "gate-final"):
        f.write('{"gate":"%s","exit":0,"selfcheck":false}\n' % g)
assert all(fn(D.Ctx(ctx.plan, [], {}))[0] for fn in (D.check_red_green, D.check_final_gate, D.check_no_crash))
waits, now = set(), 1_000_000_000_000
pk = {"action": "WAIT", "subject": "docs/agents/mission/goals/g.json", "next_check": "2001-09-09T01:46:40Z", "reason": "r"}
assert D.ms_of(pk["next_check"]) == now + 0, D.ms_of(pk["next_check"])
assert D.wait_step(waits, dict(pk, next_check="2001-09-09T01:48:40Z"), now, 900) == ("sleep", 120.0)
step = D.wait_step(waits, dict(pk, next_check="2001-09-09T01:48:40Z"), now + 120000, 900)
assert step[0] == "stop" and step[1] == "wait_repeat", step
assert D.wait_step(set(), dict(pk, next_check="2001-09-09T02:01:41Z"), now, 900)[:2] == ("stop", "wait_long")
assert D.wait_step(set(), dict(pk, next_check=None), now, 900)[:2] == ("stop", "wait_long")
ok("a stuck release in either spelling fails the RED/GREEN, final-gate and hook checks; a WAIT key sleeps once "
   "(at most 15 min), a repeat or a longer WAIT stops the run")


def t_gate_facts():
    G2 = os.path.join(T, "gates-repo-2")
    subprocess.run(["git", "init", "-q", G2], check=True, env=ENV)
    budget_gates(G2)
    gf = D.gate_facts(G2)
    assert gf["passed"] == {"red": False, "green": False, "final": True}, gf["passed"]
    c_ = D.Ctx({"repo": G2, "run_dir": T, "sessions_dir": os.path.join(T, "none")}, [], {})
    assert not D.check_red_green(c_)[0] and D.check_final_gate(c_)[0], D.check_red_green(c_)
    with open(os.path.join(G2, ".aladdin", "logs", "gates.jsonl"), "w") as f:    # self-check passes only: no gate passed
        for g_ in ("gate-red-verify", "gate-green-verify", "gate-final"):
            f.write(gate_line(g_, "", "", "", G2, 0, selfcheck=True))
            f.write(gate_line(g_, "", "", "", G2, 2, reason="9"))
    c_ = D.Ctx(c_.plan, [], {})
    assert not any(D.gate_facts(G2)["passed"].values()) and not D.check_red_green(c_)[0] and not D.check_final_gate(c_)[0]


block("a gate counts as passed only when the host ran it (selfcheck false), exit 0, no `released` field, and its "
      "task attempt was not released for its budget (a hard-limit event, or a budget-exceeded marker in its worktree)",
      t_gate_facts)


def inproc(name, peeks, caps=None, default=None, shift=0.0):
    """The driver's loop in this process on a copy of the seed (a live plan, the stub host): the peeks are scripted
    (the loop must stop before it runs out of them), the sleeps recorded, not slept. (run, slept, calls, stdout)"""
    R, stub, e = mkplan(name, default=default or {"mode": "idle"})
    pth = os.path.join(R, "plan.json")
    pl = json.load(open(pth))
    pl["caps"].update(caps or {})
    script, slept, buf = [dict(x) for x in peeks], [], io.StringIO()
    with environ(e):
        run = D.Run(pl, pth)
        run.t0 -= shift

        def peek():
            assert script, "the loop peeked again after the scripted peeks: it never stopped"
            return script.pop(0), int(time.time()) * 1000
        run.peek, run.sleep = peek, slept.append
        with contextlib.redirect_stdout(buf):
            run.loop()
    return run, slept, jsonl_of(os.path.join(stub, "calls.jsonl")), buf.getvalue()


def t_wait_loop():
    W = {"action": "WAIT", "who": "none", "subject": "docs/agents/mission/goals/gol_x.json", "next_check": iso_in(5),
         "reason": "sprint 1 is running"}
    run, slept, calls, out = inproc("wait", [W, W])
    assert len(slept) == 1 and 3 <= slept[0] <= 6 and out.count("wait: sleeping") == 1, (slept, out)
    assert run.stop["code"] == "wait_repeat" and len(run.records) == 1 and len(calls) == 1, (run.stop, len(calls))
    run, slept, calls, out = inproc("wait-long", [dict(W, next_check=iso_in(2000))])
    assert run.stop["code"] == "wait_long" and slept == [] and len(calls) == 1, run.stop
    run, slept, calls, out = inproc("wait-total", [dict(W, next_check=iso_in(100))], caps={"total_timeout_s": 60}, shift=50)
    assert run.stop["code"] == "total_timeout" and "would pass the total wall time" in run.stop["reason"] and slept == [], run.stop
    run, slept, calls, out = inproc("total", [], caps={"total_timeout_s": 60}, shift=61)
    assert run.stop["code"] == "total_timeout" and run.records == [] and calls == [], (run.stop, calls)


block("the loop's WAIT: one sleep per key, then a repeat stops it (wait_repeat) with no extra session; a WAIT past "
      "15 min stops it (wait_long), one past the total wall time too (total_timeout); and the total wall time cap "
      "stops it before a session starts", t_wait_loop)


def t_caps_more():
    Gc = live("grants-cap", env={"E2E_MAX_GRANTS": "1"}, sessions={"2": {"reseal": True}})
    assert Gc.stop["code"] == "grants_cap" and len(Gc.drv["grants"]) == 1 and len(Gc.calls) == 2, (Gc.stop, Gc.drv["grants"])
    Cu = live("cut", env={"E2E_TIMEOUT": "3", "E2E_SESSION_TIMEOUT": "600"}, default={"mode": "hang"}, timeout=120)
    assert Cu.stop["code"] in ("session_timeout", "total_timeout") and Cu.sessions[0]["timed_out"], (Cu.stop, Cu.sessions)
    assert Cu.sessions[0]["secs"] < 15 and len(Cu.calls) == 1, Cu.sessions[0]


block("the grant cap stops a run whose reply re-sealed the mission instead of granting it (a second line, never "
      "typed); the session timeout is cut to what is left of the total wall time", t_caps_more)


def t_host_failures():
    for name, cfg, code in (("turns", {"subtype": "error_max_turns"}, "host_failure"),
                            ("budget", {"subtype": "error_max_budget_usd"}, "host_failure"),
                            ("iserror", {"is_error": True}, "host_failure"),
                            ("noresult", {"result": False}, "host_failure"),
                            ("rc124", {"rc": 124}, "session_timeout"),
                            ("rc137", {"rc": 137}, "session_timeout")):
        L_ = live("hf-" + name, default=dict(cfg, mode="idle"))
        assert L_.stop["code"] == code and len(L_.calls) == 1 and L_.r.returncode == 1, (name, L_.stop, len(L_.calls))


block("a session that exits 0 with a result whose subtype is not success, with is_error, or with no result line is a "
      "host failure, and exit 124/137 a timeout: the run stops after that one session (no retry)", t_host_failures)


def t_refusals():
    stub = stub_dir("refuse-dry")
    r_ = subprocess.run([sys.executable, "-B", DRIVER, "--plan", os.path.join(TEMPLATE, "plan.json")], cwd=T,
                        env=dict(HENV, MISSION_STUB_DIR=stub), capture_output=True, text=True)
    assert r_.returncode == 2 and "dry-run" in r_.stderr and not os.path.exists(os.path.join(stub, "calls.jsonl")), r_.stderr
    for name, change in (("resume", lambda a: a.insert(3, "--resume")), ("nobudget", lambda a: a.remove("--max-budget-usd")),
                         ("bypass", lambda a: a.insert(3, "--dangerously-skip-permissions"))):
        R, stub, e = mkplan("refuse-" + name)
        pth = os.path.join(R, "plan.json")
        pl = json.load(open(pth))
        change(pl["sessions"]["reply"]["argv"])
        with open(pth, "w") as f:
            json.dump(pl, f)
        r_ = subprocess.run([sys.executable, "-B", DRIVER, "--plan", pth], cwd=T, env=e, capture_output=True, text=True)
        assert r_.returncode == 2 and not os.path.exists(os.path.join(stub, "calls.jsonl")), (name, r_.returncode, r_.stderr)
    stub = stub_dir("preflight-plugin", default={"plugins": [{"id": "aloop@aloop-marketplace", "enabled": True}]})
    r_, d_ = sh("--yes", "--model", "stub-sup", env=dict(HENV, MISSION_STUB_DIR=stub))
    assert r_.returncode == 2 and "is installed and enabled" in r_.stdout + r_.stderr and os.listdir(d_) == [], r_.stdout
    stub = stub_dir("preflight-net")
    r_, d_ = sh("--yes", "--model", "stub-sup", env=dict(HENV, MISSION_STUB_DIR=stub,
                                                         E2E_NET_URL="file://" + os.path.join(T, "no-such-page")))
    assert r_.returncode == 2 and "not reachable" in r_.stdout + r_.stderr and os.listdir(d_) == [], r_.stdout
    assert not os.path.exists(os.path.join(stub, "calls.jsonl"))


block("the driver refuses a dry-run plan and an argv that resumes, bypasses or has no --max-budget-usd (exit 2, no "
      "session); e2e-mission.sh's live preflight refuses an enabled aloop and no network before seeding", t_refusals)


def group_alive(pgid):
    out = subprocess.run(["ps", "-A", "-o", "pgid=,stat="], capture_output=True, text=True).stdout
    return any(ln.split()[0] == str(pgid) and not ln.split()[1].startswith("Z") for ln in out.splitlines() if ln.split())


def gone_soon(check, secs=5.0):
    end = time.time() + secs
    while time.time() < end and check():
        time.sleep(0.1)
    return not check()


def t_children():
    for name, cfg, env in (("child", {"mode": "idle", "child": "stubborn"}, None),
                           ("timeout-stubborn", {"mode": "hang", "child": "stubborn"}, {"E2E_SESSION_TIMEOUT": "2"})):
        L_ = live(name, default=cfg, env=env)
        child = int(open(os.path.join(L_.stub, "child-1.pid")).read())
        try:
            assert gone_soon(lambda: alive(child)) and gone_soon(lambda: group_alive(L_.calls[0]["pgid"])), (name, child)
        finally:
            try:
                os.killpg(L_.calls[0]["pgid"], signal.SIGKILL)
            except OSError:
                pass


block("a child the session left behind is killed when the session ends normally, and on a timeout; one that ignores "
      "SIGTERM gets SIGKILL (the whole process group is gone)", t_children)


def t_lock():
    sys.path.insert(0, os.path.join(ROOT, "plugin", "mission"))
    import due as DUE
    R, stub, e = mkplan("locked")
    fd = os.open(DUE.lock_path(os.path.join(R, "repo"), make=True), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        L_ = drive(R, stub, e, "locked")
    finally:
        os.close(fd)
    assert L_.stop["code"] == "lock_held" and L_.calls == [] and L_.sessions == [], (L_.stop, L_.calls)


block("the mission loop lock: held by the driver in every session (the happy run), and a lock another process holds "
      "stops the run before a session starts", t_lock)


def t_plant():
    L_ = live("plant", sessions={"1": {"mode": "idle", "plant": True}})
    probe = json.load(open(os.path.join(L_.stub, "probe.json")))
    assert L_.drv["outcome"] != "GOAL MET" and L_.stop["code"] == "human", (L_.drv["outcome"], L_.stop)
    assert probe["path0"] == os.path.join(L_.dir, "bin") and probe["pgid"] != os.getpgid(0), probe
    assert probe["crontab"][0] not in (0, None) and "disabled" in probe["crontab"][1], probe
    assert check_named(L_.checks(), "the driver wrote no")["ok"]


block("the post-run pytest runs the repo's code as a session would (the crontab shim first on PATH, its own process "
      "group), and the outcome is decided before it: a test that records goal.met does not turn the run into GOAL MET",
      t_plant)


def wait_file(path, secs=60):
    end = time.time() + secs
    while time.time() < end and not os.path.exists(path):
        time.sleep(0.1)
    return os.path.exists(path)


def t_signals():
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        R, stub, e = mkplan("sig-%d" % sig, default={"mode": "hang"})
        pr = subprocess.Popen([sys.executable, "-B", DRIVER, "--plan", os.path.join(R, "plan.json")], cwd=T, env=e,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            assert wait_file(os.path.join(stub, "child-1.pid")), "the session never started"
            pr.send_signal(sig)
            out, err = pr.communicate(timeout=120)
        finally:
            if pr.poll() is None:
                pr.kill()
            calls = jsonl_of(os.path.join(stub, "calls.jsonl"))
        L_ = Live(subprocess.CompletedProcess([], pr.returncode, out, err), R, stub)
        child = int(open(os.path.join(stub, "child-1.pid")).read())
        try:
            assert "Traceback" not in out + err, (out[-2000:], err[-2000:])
            assert gone_soon(lambda: alive(calls[0]["pid"])) and gone_soon(lambda: alive(child)), (sig, calls[0]["pid"], child)
            assert L_.stop.get("code") == "interrupted" and len(L_.sessions) == 1, (sig, L_.stop, L_.sessions)
            assert "interrupted" in (L_.sessions[0]["killed"] or "") and L_.sessions[0]["cost_complete"] is False, L_.sessions
        finally:
            for pid in [calls[0]["pgid"]] if calls else []:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except OSError:
                    pass
    for sig, how in ((signal.SIGINT, "group"), (signal.SIGTERM, "shell")):     # Ctrl-C at the terminal; a CI cancel
        stub = stub_dir("sh-sig-%s" % how, default={"mode": "hang"})
        d_ = tempfile.mkdtemp(prefix="run-", dir=T)
        pr = subprocess.Popen([SH, "--yes", "--model", "stub-sup"], cwd=T, env=dict(HENV, MISSION_STUB_DIR=stub, E2E_DIR=d_),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            assert wait_file(os.path.join(stub, "child-1.pid")), "the session never started"
            time.sleep(0.3)
            if how == "group":
                os.killpg(pr.pid, sig)
            else:
                pr.send_signal(sig)
            out, err = pr.communicate(timeout=180)
        finally:
            if pr.poll() is None:
                os.killpg(pr.pid, signal.SIGKILL)
            calls = jsonl_of(os.path.join(stub, "calls.jsonl"))
        child = int(open(os.path.join(stub, "child-1.pid")).read())
        try:
            assert gone_soon(lambda: alive(calls[0]["pid"])) and gone_soon(lambda: alive(child)), (how, out[-2000:], err[-2000:])
            drv = json.load(open(os.path.join(d_, "driver.json")))
            assert drv["stop"]["code"] == "interrupted" and os.path.isfile(os.path.join(d_, "summary.json")), drv["stop"]
            assert "run dir: %s" % d_ in out, out[-1500:]
        finally:
            for pid in [calls[0]["pgid"]] if calls else []:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except OSError:
                    pass


block("SIGTERM, SIGINT or SIGHUP to the driver during a session, Ctrl-C at e2e-mission.sh's terminal or SIGTERM to "
      "the script: the session's whole group is killed, its record written (killed: interrupted, cost incomplete), "
      "then driver.json and the summary", t_signals)


def t_driver_bug():
    """A bug in the driver while a session runs (here kill_reason raises): the session's group is killed and its
    record written before the run stops (driver_error), so its spend is accounted for; driver.json still follows."""
    R, stub, e = mkplan("driver-bug", default={"mode": "hang"})
    saved_kr, saved_sig = D.Run.kill_reason, {s: signal.getsignal(s) for s in D.SIGNALS}

    def broken(self):
        raise ValueError("an injected driver bug")
    D.Run.kill_reason = broken
    try:
        with environ(e), contextlib.redirect_stdout(io.StringIO()):
            rc = D.main(["--plan", os.path.join(R, "plan.json")])
    finally:
        D.Run.kill_reason = saved_kr
        for s, h in saved_sig.items():
            signal.signal(s, h)
    calls = jsonl_of(os.path.join(stub, "calls.jsonl"))
    child = int(open(os.path.join(stub, "child-1.pid")).read())
    try:
        assert len(calls) == 1 and gone_soon(lambda: alive(calls[0]["pid"])) and gone_soon(lambda: alive(child)), calls
        assert gone_soon(lambda: group_alive(calls[0]["pgid"])), calls[0]["pgid"]
        L_ = Live(subprocess.CompletedProcess([], rc, "", ""), R, stub)
        assert rc == 1 and L_.stop.get("code") == "driver_error" and "injected" in L_.stop.get("reason", ""), (rc, L_.stop)
        assert len(L_.sessions) == 1 and "driver failed" in (L_.sessions[0]["killed"] or ""), L_.sessions
        assert L_.sessions[0]["cost_complete"] is False and L_.drv["sessions"] == 1, (L_.sessions, L_.drv.get("sessions"))
    finally:
        try:
            os.killpg(calls[0]["pgid"], signal.SIGKILL)
        except (OSError, IndexError):
            pass


block("a driver bug while a session runs kills the session's whole group and writes its record (killed: the driver "
      "failed, cost incomplete) before the run stops with driver_error; driver.json still follows", t_driver_bug)


def t_nan_caps():
    for var, v in (("E2E_BUDGET_USD", "nan"), ("E2E_BUDGET_USD", "NaN"), ("E2E_BUDGET_USD", "inf"),
                   ("E2E_BUDGET_USD", "-inf"), ("E2E_POLL_S", "nan"), ("E2E_POLL_S", "inf")):
        r_, d_ = sh("--yes", "--dry-run", env=dict(ENV, **{var: v}))
        assert r_.returncode != 0 and var in r_.stdout + r_.stderr and not os.path.exists(os.path.join(d_, "plan.json")), \
            (var, v, r_.returncode, r_.stdout[-500:], r_.stderr[-500:])


block("a cap that is not a finite number above 0 (nan, inf) is refused: the outer USD cap can never be turned off",
      t_nan_caps)

if FAILED:
    print("\nmission-e2e-live: %d checks passed, %d FAILED: %s (run dir kept: %s)" % (PASS[0], len(FAILED),
                                                                                    "; ".join(FAILED), T))
    sys.exit(1)
shutil.rmtree(T, ignore_errors=True)       # kept when a check fails, for the post-mortem
print("\nmission-e2e-live: %d checks passed" % PASS[0])
