#!/usr/bin/env python3
"""ROADMAP M12.7, M12.8: modules and distros.

The host harness is the hardware, aloop is the kernel, and a module (~/.aloop/modules/<name>/) plugs into
the kernel's events; a project runs it only by adding it (its sha256) to its distro.json (user space, in
git). Checks, on the real `module hook` with hook payloads:
  no distro.json: nothing runs (the default `software` distro); an installed module not added never runs;
  an added module runs on its event and role only, its exit 2 blocks with its reason, exit 0 passes;
  a module changed since it was added, a missing one, a crash and a timeout all block (declared policy runs);
  every run is logged (module.run, gate-owned); the hook is wired beside the core gates and
  never replaces one; no dispatched agent may write a module; distro get/set; a bad distro.json blocks;
  how others build their own: `module new`, `distro new|check|install`.
    python3 scripts/test/modules.test.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

PLUGIN = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(PLUGIN, "bin")
TMP = tempfile.mkdtemp(prefix="aloop-modules-")
HOME = os.path.join(TMP, "home")
AH = os.path.join(HOME, ".aloop")
ENV = dict(os.environ, PATH=BIN + ":/usr/bin:/bin", AGENTIC_NO_AUTOINSTALL="1", HOME=HOME, ALOOP_HOME=AH,
           GIT_AUTHOR_NAME="A", GIT_AUTHOR_EMAIL="a@a", GIT_COMMITTER_NAME="A", GIT_COMMITTER_EMAIL="a@a")
for k in ("AGENTIC_HOST", "CLAUDE_PLUGIN_ROOT", "SKIP_HOOKS", "AGENTIC_CWD"):
    ENV.pop(k, None)
FAILS, N = [], [0]


def ok(cond, what, detail=""):
    N[0] += 1
    print(("ok   " if cond else "FAIL ") + what + ("" if cond else "  :: " + str(detail)[:1200]))
    if not cond:
        FAILS.append(what)


def run(args, cwd, stdin=None, **env):
    return subprocess.run(args, cwd=cwd, env=dict(ENV, **env), input=stdin, capture_output=True, text=True)


def write(p, t, mode=None):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(t)
    if mode:
        os.chmod(p, mode)


P = os.path.join(TMP, "proj")
os.makedirs(os.path.join(P, "docs", "agents"))
run(["git", "init", "-q", "-b", "main"], P)
os.makedirs(os.path.join(P, ".aladdin"))


def module(*a, **env):
    return run([os.path.join(BIN, "module")] + list(a), P, **env)


def hook(agent_type="", event="SubagentStop", **env):
    p = {"hook_event_name": event, "session_id": "s", "cwd": P}
    if agent_type:
        p["agent_type"] = agent_type
    return run([os.path.join(BIN, "module"), "hook"], P, stdin=json.dumps(p), **env)


def ext(name, on, body, timeout=None):
    d = os.path.join(AH, "modules", name)
    m = {"name": name, "description": "test", "on": on, "command": "./check"}
    if timeout:
        m["timeout"] = timeout
    write(os.path.join(d, "module.json"), json.dumps(m))
    write(os.path.join(d, "check"), "#!/usr/bin/env bash\n" + body, 0o755)


def events():
    try:
        return [json.loads(l) for l in open(os.path.join(P, ".aladdin", "logs", "events.jsonl"))
                if '"module.run"' in l]
    except OSError:
        return []


# 1) the default: no distro.json, nothing runs; an installed module that is not added never runs
r = hook("green-worker")
ok(r.returncode == 0 and run([os.path.join(BIN, "distro")], P).stdout.strip() == "software", "no distro.json: the software distro, nothing runs", r.stderr)
ext("brand-voice", ["SubagentStop:green-worker"],
    'grep -q "synergy" "$ALOOP_PROJECT/copy.md" && { echo "copy says synergy (banned word)" >&2; exit 2; }; exit 0\n')
write(os.path.join(P, "copy.md"), "we love synergy\n")
r = hook("green-worker")
ok(r.returncode == 0, "an installed module that is not added never runs", r.stderr)

# 2) added: runs on its event and role, blocks with its reason, passes, logs
r = module("add", "brand-voice")
prof = json.load(open(os.path.join(P, "docs", "agents", "userspace", "distro.json")))
ok(r.returncode == 0 and len(prof["modules"]["brand-voice"]) == 64, "add writes the module's sha256 into distro.json", r.stderr)
r = hook("aloop:green-worker")
ok(r.returncode == 2 and "brand-voice blocked: copy says synergy" in r.stderr, "its exit 2 blocks with its reason", r.stderr)
r = hook("red-worker")
ok(r.returncode == 0, "it does not run for another role", r.stderr)
r = hook("green-worker", event="Stop")
ok(r.returncode == 0, "nor on another event", r.stderr)
write(os.path.join(P, "copy.md"), "we love clarity\n")
r = hook("green-worker")
ok(r.returncode == 0, "exit 0 passes", r.stderr)
ev = events()
ok([e["data"]["verdict"] for e in ev] == ["block", "pass"] and ev[0]["data"]["module"] == "brand-voice"
   and ev[0]["data"]["sha256"] == prof["modules"]["brand-voice"], "each run is logged with its sha and verdict", ev)
r = run([os.path.join(BIN, "events"), "emit", "module.run"], P)
ok(r.returncode != 0, "a forged module.run is refused", r.stdout + r.stderr)

# 3) declared policy runs, or the run stops
write(os.path.join(AH, "modules", "brand-voice", "check"), "#!/usr/bin/env bash\nexit 0\n", 0o755)
r = hook("green-worker")
ok(r.returncode == 2 and "changed since it was added" in r.stderr, "a module changed since it was added is refused", r.stderr)
module("add", "brand-voice")
ok(hook("green-worker").returncode == 0, "added again, it runs again")
ext("crashy", ["SubagentStop"], "exit 7\n")
module("add", "crashy")
r = hook("planner")
ok(r.returncode == 2 and "crashy failed (exit 7), which blocks" in r.stderr, "a crash blocks (any role: `SubagentStop`)", r.stderr)
module("remove", "crashy")
ext("slow", ["Stop"], "sleep 5\n", timeout=1)
module("add", "slow")
r = hook(event="Stop")
ok(r.returncode == 2 and "timed out" in r.stderr, "a timeout blocks", r.stderr)
module("remove", "slow")
shutil.rmtree(os.path.join(AH, "modules", "brand-voice"))
r = hook("green-worker")
ok(r.returncode == 2 and "not installed" in r.stderr, "an added module that is not installed blocks", r.stderr)
r = module("list")
ok("brand-voice" in r.stdout and "missing" in r.stdout, "list shows it missing", r.stdout)
module("remove", "brand-voice")
ok(hook("green-worker").returncode == 0, "removed, nothing runs")
r = module("add", "../escape")
ok(r.returncode == 64, "a module name cannot leave the modules dir", r.stderr)
write(os.path.join(AH, "modules", "bad", "module.json"), json.dumps({"on": ["PreToolUse"], "command": "x"}))
r = module("add", "bad")
ok(r.returncode == 64 and "\"on\"" in r.stderr, "a module on an unsupported event is refused at add", r.stderr)
r = hook("green-worker", SKIP_HOOKS="1")
ok(r.returncode == 0, "SKIP_HOOKS=1 skips it like every core gate")

# 4) the distro
r = run([os.path.join(BIN, "distro"), "use", "marketing"], P)
ok(r.stdout.strip() == "marketing" and json.load(open(os.path.join(P, "docs", "agents", "userspace", "distro.json")))["distro"] == "marketing",
   "the distro is set in user space (in git)")
write(os.path.join(P, "docs", "agents", "userspace", "distro.json"), "{broken")
r = hook("green-worker")
ok(r.returncode == 2 and "not JSON" in r.stderr, "an unreadable distro.json blocks (a declared policy that cannot be read)", r.stderr)

# 5) wiring: beside the core gates, never instead of one; no agent writes the kernel dir
hooks = json.load(open(os.path.join(PLUGIN, "hooks", "hooks.json")))["hooks"]
for evn in ("SubagentStop", "Stop"):
    star = [h["command"] for m in hooks[evn] if m["matcher"] == "*" for h in m["hooks"]]
    ok(any(c.endswith('/bin/module" hook') for c in star), "%s runs `module hook` on every agent" % evn)
gates = [h["command"] for m in hooks["SubagentStop"] for h in m["hooks"] if "/bin/gate-" in h["command"]]
ok(len(gates) == 11, "every core SubagentStop gate is still wired (11)", gates)
p = {"tool_name": "Write", "session_id": "s", "cwd": P, "agent_type": "green-worker",
     "tool_input": {"file_path": os.path.join(AH, "modules", "x", "check"), "content": "exit 0"}}
r = run([os.path.join(BIN, "gate-supervisor-scope")], P, stdin=json.dumps(p))
ok(r.returncode == 2, "a dispatched agent cannot write a module", r.stderr)

# 6) how others build their own: a module scaffold and a distro, end to end
write(os.path.join(P, "docs", "agents", "userspace", "distro.json"), "{}")
r = module("new", "no-todo", "--on", "Stop")
m = json.load(open(os.path.join(AH, "modules", "no-todo", "module.json")))
ok(r.returncode == 0 and m["on"] == ["Stop"] and os.access(os.path.join(AH, "modules", "no-todo", "check"), os.X_OK),
   "module new scaffolds a runnable, commented module", r.stdout + r.stderr)
ok(module("new", "no-todo").returncode == 64, "it never overwrites one")
run(["git", "add", "-A"], P)
run(["git", "commit", "-qm", "base"], P)
module("add", "no-todo")
write(os.path.join(P, "notes.md"), "TODO write this\n")
run(["git", "add", "notes.md"], P)
r = hook(event="Stop")
ok(r.returncode == 2 and "still say TODO" in r.stderr, "the scaffold's example check works as written", r.stderr)
module("remove", "no-todo")
PK = os.path.join(TMP, "distros")
r = run([os.path.join(BIN, "distro"), "new", "marketing", "--dir", PK], P)
pd = os.path.join(PK, "marketing")
ok(r.returncode == 0 and os.path.isfile(os.path.join(pd, "distro.json")) and
   os.path.isfile(os.path.join(pd, "modules", "marketing-check", "check")) and os.path.isfile(os.path.join(pd, "README.md")),
   "distro new scaffolds a distro: distro.json, a working example module, a README", r.stdout + r.stderr)
write(os.path.join(pd, "userspace", "agents", "planner.md"), "---\nname: planner\ndescription: campaign planner\n---\nPlan campaigns.\n")
r = run([os.path.join(BIN, "distro"), "check", pd], P)
ok(r.returncode == 0 and r.stdout.strip().endswith("ok"), "distro check passes a valid distro", r.stdout)
write(os.path.join(pd, "userspace", "agents", "copywriter.md"), "---\nname: copywriter\n---\nx\n")
r = run([os.path.join(BIN, "distro"), "check", pd], P)
ok(r.returncode == 1 and "names no kernel role" in r.stdout, "a distro cannot add a role (only re-shape one)", r.stdout)
os.remove(os.path.join(pd, "userspace", "agents", "copywriter.md"))
r = run([os.path.join(BIN, "distro"), "install", pd], P)
prof = json.load(open(os.path.join(P, "docs", "agents", "userspace", "distro.json")))
ok(r.returncode == 0 and prof["distro"] == "marketing" and "marketing-check" in prof["modules"] and
   os.path.isfile(os.path.join(AH, "modules", "marketing-check", "check")) and
   "Plan campaigns." in open(os.path.join(P, "docs", "agents", "userspace", "agents", "planner.md")).read(),
   "distro install: modules into ~/.aloop/modules and added, roles into user space, distro set", r.stdout + r.stderr)
r = run([os.path.join(BIN, "distro"), "install", pd], P)
ok(r.returncode == 0, "re-installing the same distro is a no-op", r.stderr)
write(os.path.join(AH, "modules", "marketing-check", "check"), "#!/usr/bin/env bash\nexit 0\n", 0o755)
r = run([os.path.join(BIN, "distro"), "install", pd], P)
ok(r.returncode == 64 and "--force" in r.stderr, "installing over a different installed module needs --force", r.stderr)

if not FAILS:
    shutil.rmtree(TMP, ignore_errors=True)
print("\n%d/%d module checks passed" % (N[0] - len(FAILS), N[0]))
sys.exit(1 if FAILS else 0)
