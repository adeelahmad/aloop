#!/usr/bin/env python3
"""A gate that crashes must BLOCK, not allow (ROADMAP M0.6).

Claude Code treats a hook exit other than 2 as a non-blocking error, so a gate that dies
on an unbound variable lets the action through. Gates map any unexpected exit to 2.

    python3 scripts/test/fail-closed.test.py
"""
import json
import atexit
import os
import shutil
import re
import stat
import subprocess
import tempfile
import time

BIN = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugin", "bin"))
HOOKS = os.path.join(BIN, "..", "hooks", "hooks.json")
T = tempfile.mkdtemp(prefix="aloop-failclosed-")
atexit.register(shutil.rmtree, T, ignore_errors=True)
ENV = {k: v for k, v in os.environ.items() if k not in ("SKIP_HOOKS", "AGENTIC_SPRINT_LOCK_TTL", "STORY_DIR", "AGENT_ROLE")}
ENV["HOME"] = os.path.join(T, "home")
n = 0


def ok(msg):
    global n
    n += 1
    print("ok - " + msg)


def script(name, body):
    p = os.path.join(T, name)
    open(p, "w").write("#!/usr/bin/env bash\n" + body)
    os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
    return p


def run(path, env=None, inp=""):
    return subprocess.run([path], cwd=T, env=env or ENV, input=inp, capture_output=True, text=True)


subprocess.run(["git", "init", "-q"], cwd=T, check=True)

# 1. the gate library: a crash after sourcing it is a BLOCK with the reason
for lib in ("_gatelib.sh", "_failclosed.sh"):
    crash = script("crash-" + lib, 'GATE_NAME=t\nset -u\nsource "%s/%s"\n: "$NOT_SET"\necho unreachable\n' % (BIN, lib))
    r = run(crash)
    assert r.returncode == 2 and "BLOCK[t]" in r.stderr and "failing closed" in r.stderr, r
    for code in (0, 2):
        r = run(script("exit%d-%s" % (code, lib), 'GATE_NAME=t\nsource "%s/%s"\nexit %d\n' % (BIN, lib, code)))
        assert r.returncode == code and "failing closed" not in r.stderr, r
    ok("%s: a crash (exit 1) becomes exit 2 with the reason; 0 and 2 pass through" % lib)

# 2. every gate wired to a SubagentStart/SubagentStop hook is fail-closed
hooks = json.load(open(HOOKS))["hooks"]
wired = set()
for ev in ("SubagentStart", "SubagentStop"):
    for entry in hooks.get(ev, []):
        for h in entry["hooks"]:
            m = re.search(r"bin/(gate-[a-z0-9-]+)", h["command"])
            if m:
                wired.add(m.group(1))


def fail_closed(g):
    src = open(os.path.join(BIN, g)).read()
    if src.startswith("#!/usr/bin/env python3"):   # its own top-level handler (mission-roles.test.py crashes it)
        return "failing closed" in src and "rc = 2" in src
    return re.search(r"_gatelib\.sh|_failclosed\.sh", src)


missing = [g for g in sorted(wired) if not fail_closed(g)]
assert wired and not missing, missing
ok("all %d sub-agent gates load the fail-closed trap (%s)" % (len(wired), ", ".join(sorted(wired))))

# 3. supervisor-scope: a crash inside a live sprint blocks the write (the bug the smoke run found)
fake = os.path.join(T, "fakebin")
os.makedirs(fake)
open(os.path.join(fake, "date"), "w").write("#!/bin/sh\necho not-a-number\n")
os.chmod(os.path.join(fake, "date"), 0o755)
lock = os.path.join(T, ".aladdin", "state", "sprint.lock")
os.makedirs(os.path.dirname(lock))
open(lock, "w").write("other-session\n")
payload = json.dumps({"session_id": "me", "tool_name": "Write", "tool_input": {"file_path": os.path.join(T, "src", "a.rs")}})
r = run(os.path.join(BIN, "gate-supervisor-scope"), env=dict(ENV, PATH=fake + os.pathsep + ENV["PATH"]), inp=payload)
assert r.returncode == 2 and "failing closed" in r.stderr, r
ok("supervisor-scope: a crash while a sprint lock exists blocks the write")
os.remove(lock)
r = run(os.path.join(BIN, "gate-supervisor-scope"), env=dict(ENV, PATH=fake + os.pathsep + ENV["PATH"]), inp=payload)
assert r.returncode == 0, r
ok("supervisor-scope: with no sprint in this repo the gate stays out of the way")

print("\n%d checks passed" % n)
