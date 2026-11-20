#!/usr/bin/env python3
"""ROADMAP M12.7, M12.8: kernel extensions and the profile.

A kernel extension (~/.aloop/kernel/<name>/) adds checks to the kernel's events; a project loads it only by
pinning its sha256 in its user-space profile (in git). Checks, on the real `kernel hook` with hook payloads:
  no profile: nothing runs (the default `software` profile); an unpinned module never runs;
  a pinned module runs on its event and role only, its exit 2 blocks with its reason, exit 0 passes;
  a module changed since its pin, a missing one, a crash and a timeout all block (declared policy runs);
  every run is logged (kernel.extension_run, gate-owned); the hook is wired beside the core gates and
  never replaces one; no dispatched agent may write the kernel dir; profile get/set; a bad profile blocks.
    python3 scripts/test/kernel-extensions.test.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

PLUGIN = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(PLUGIN, "bin")
TMP = tempfile.mkdtemp(prefix="aloop-kernel-")
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


def kernel(*a, **env):
    return run([os.path.join(BIN, "kernel")] + list(a), P, **env)


def hook(agent_type="", event="SubagentStop", **env):
    p = {"hook_event_name": event, "session_id": "s", "cwd": P}
    if agent_type:
        p["agent_type"] = agent_type
    return run([os.path.join(BIN, "kernel"), "hook"], P, stdin=json.dumps(p), **env)


def ext(name, on, body, timeout=None):
    d = os.path.join(AH, "kernel", name)
    m = {"name": name, "description": "test", "on": on, "command": "./check"}
    if timeout:
        m["timeout"] = timeout
    write(os.path.join(d, "extension.json"), json.dumps(m))
    write(os.path.join(d, "check"), "#!/usr/bin/env bash\n" + body, 0o755)


def events():
    try:
        return [json.loads(l) for l in open(os.path.join(P, ".aladdin", "logs", "events.jsonl"))
                if '"kernel.extension_run"' in l]
    except OSError:
        return []


# 1) the default: no profile, nothing runs; an installed but unpinned module never runs
r = hook("green-worker")
ok(r.returncode == 0 and kernel("profile").stdout.strip() == "software", "no profile: the software profile, nothing runs", r.stderr)
ext("brand-voice", ["SubagentStop:green-worker"],
    'grep -q "synergy" "$ALOOP_PROJECT/copy.md" && { echo "copy says synergy (banned word)" >&2; exit 2; }; exit 0\n')
write(os.path.join(P, "copy.md"), "we love synergy\n")
r = hook("green-worker")
ok(r.returncode == 0, "an installed, unpinned extension never runs", r.stderr)

# 2) pinned: runs on its event and role, blocks with its reason, passes, logs
r = kernel("pin", "brand-voice")
prof = json.load(open(os.path.join(P, "docs", "agents", "userspace", "profile.json")))
ok(r.returncode == 0 and len(prof["extensions"]["brand-voice"]) == 64, "pin writes the module's sha256 into the profile", r.stderr)
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
ok([e["data"]["verdict"] for e in ev] == ["block", "pass"] and ev[0]["data"]["extension"] == "brand-voice"
   and ev[0]["data"]["sha256"] == prof["extensions"]["brand-voice"], "each run is logged with its sha and verdict", ev)
r = run([os.path.join(BIN, "events"), "emit", "kernel.extension_run"], P)
ok(r.returncode != 0, "a forged kernel.extension_run is refused", r.stdout + r.stderr)

# 3) declared policy runs, or the run stops
write(os.path.join(AH, "kernel", "brand-voice", "check"), "#!/usr/bin/env bash\nexit 0\n", 0o755)
r = hook("green-worker")
ok(r.returncode == 2 and "changed since it was pinned" in r.stderr, "a module changed since its pin is refused", r.stderr)
kernel("pin", "brand-voice")
ok(hook("green-worker").returncode == 0, "re-pinned, it runs again")
ext("crashy", ["SubagentStop"], "exit 7\n")
kernel("pin", "crashy")
r = hook("planner")
ok(r.returncode == 2 and "crashy failed (exit 7), which blocks" in r.stderr, "a crash blocks (any role: `SubagentStop`)", r.stderr)
kernel("unpin", "crashy")
ext("slow", ["Stop"], "sleep 5\n", timeout=1)
kernel("pin", "slow")
r = hook(event="Stop")
ok(r.returncode == 2 and "timed out" in r.stderr, "a timeout blocks", r.stderr)
kernel("unpin", "slow")
shutil.rmtree(os.path.join(AH, "kernel", "brand-voice"))
r = hook("green-worker")
ok(r.returncode == 2 and "not installed" in r.stderr, "a pinned extension that is not installed blocks", r.stderr)
r = kernel("list")
ok("brand-voice" in r.stdout and "missing" in r.stdout, "list shows it missing", r.stdout)
kernel("unpin", "brand-voice")
ok(hook("green-worker").returncode == 0, "unpinned, nothing runs")
r = kernel("pin", "../escape")
ok(r.returncode == 64, "a module name cannot leave the kernel dir", r.stderr)
write(os.path.join(AH, "kernel", "bad", "extension.json"), json.dumps({"on": ["PreToolUse"], "command": "x"}))
r = kernel("pin", "bad")
ok(r.returncode == 64 and "\"on\"" in r.stderr, "an extension on an unsupported event is refused at pin", r.stderr)
r = hook("green-worker", SKIP_HOOKS="1")
ok(r.returncode == 0, "SKIP_HOOKS=1 skips it like every core gate")

# 4) the profile
r = kernel("profile", "marketing")
ok(r.stdout.strip() == "marketing" and json.load(open(os.path.join(P, "docs", "agents", "userspace", "profile.json")))["profile"] == "marketing",
   "the profile is set in user space (in git)")
write(os.path.join(P, "docs", "agents", "userspace", "profile.json"), "{broken")
r = hook("green-worker")
ok(r.returncode == 2 and "not JSON" in r.stderr, "an unreadable profile blocks (a declared policy that cannot be read)", r.stderr)

# 5) wiring: beside the core gates, never instead of one; no agent writes the kernel dir
hooks = json.load(open(os.path.join(PLUGIN, "hooks", "hooks.json")))["hooks"]
for evn in ("SubagentStop", "Stop"):
    star = [h["command"] for m in hooks[evn] if m["matcher"] == "*" for h in m["hooks"]]
    ok(any(c.endswith('/bin/kernel" hook') for c in star), "%s runs `kernel hook` on every agent" % evn)
gates = [h["command"] for m in hooks["SubagentStop"] for h in m["hooks"] if "/bin/gate-" in h["command"]]
ok(len(gates) == 11, "every core SubagentStop gate is still wired (11)", gates)
p = {"tool_name": "Write", "session_id": "s", "cwd": P, "agent_type": "green-worker",
     "tool_input": {"file_path": os.path.join(AH, "kernel", "x", "check"), "content": "exit 0"}}
r = run([os.path.join(BIN, "gate-supervisor-scope")], P, stdin=json.dumps(p))
ok(r.returncode == 2, "a dispatched agent cannot write a kernel extension", r.stderr)

if not FAILS:
    shutil.rmtree(TMP, ignore_errors=True)
print("\n%d/%d kernel-extension checks passed" % (N[0] - len(FAILS), N[0]))
sys.exit(1 if FAILS else 0)
