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
    python3 scripts/test/mission-e2e-live.test.py
"""
import hashlib
import json
import os
import re
import shutil
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
for name, real in (("python3", sys.executable), ("git", shutil.which("git")), ("make", shutil.which("make"))):
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

shutil.rmtree(T, ignore_errors=True)       # kept when a check fails, for the post-mortem
print("\nmission-e2e-live: %d checks passed" % PASS[0])
