#!/usr/bin/env python3
"""log-execution: the supervisor's one-line ledger append must be usable from its --help alone.
Regression (live e2e runs 13–14): the script had no usage, so the supervisor read its source
(3 reads per run) to learn that the last argument is the file. Now: `--help` prints the usage,
a wrong call prints it and exits 64, the ledger file may be omitted (latest sprint's
execution.log) and the line keeps the shape gate-ledger-format enforces.
    python3 scripts/test/log-execution.test.py
"""
import atexit
import os
import re
import shutil
import subprocess
import tempfile

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugin", "bin", "log-execution")
GATE = os.path.join(os.path.dirname(BIN), "gate-ledger-format")
T = tempfile.mkdtemp(prefix="aloop-ledger-")
atexit.register(shutil.rmtree, T, ignore_errors=True)
n = 0


def ok(msg):
    global n
    n += 1
    print("ok - " + msg)


def run(*args):
    return subprocess.run([BIN, *args], cwd=T, capture_output=True, text=True)


r = run("--help")
assert r.returncode == 0 and "wave-K" in r.stdout and "execution.log" in r.stdout, r
ok("--help prints the usage (wave, activity, story, transition, detail, ledger file)")

r = run("wave-1", "red")
assert r.returncode == 64 and "usage" in r.stderr.lower(), r
ok("too few arguments: usage on stderr, exit 64, nothing written")

log = os.path.join(T, "docs/agents/sprint1/execution.log")
os.makedirs(os.path.dirname(log))
r = run("wave-1", "red", "S1-01", "start", "T1", log)
assert r.returncode == 0, r
line = open(log).read().splitlines()[-1]
assert re.match(r"^\d{4}-\d{2}-\d{2}T\S+Z wave-1 red S1-01 start T1$", line), line
ok("explicit ledger path: `<ISO8601> wave-K activity SN-NN transition detail`")

r = run("wave-1", "budget", "S1-01", "kill", "T1", "hard_time", log)
assert r.returncode == 0 and open(log).read().splitlines()[-1].endswith(" wave-1 budget S1-01 kill T1 hard_time"), r
ok("several detail words are kept")

os.makedirs(os.path.join(T, "docs/agents/sprint2"))
r = run("wave-2", "green", "S2-01", "complete", "T1")
assert r.returncode == 0, r
assert open(os.path.join(T, "docs/agents/sprint2/execution.log")).read().endswith(" wave-2 green S2-01 complete T1\n")
assert len(open(log).read().splitlines()) == 2
ok("no ledger path: appends to the latest sprint's execution.log")

r = run("wave-1", "red", "S1-01", "start", "T1", os.path.join(T, "docs/agents/sprint9/execution.log"))
assert r.returncode == 64 and "sprint9" in r.stderr, r
ok("a ledger path in a sprint dir that does not exist is refused")

for f in (log, os.path.join(T, "docs/agents/sprint2/execution.log")):
    g = subprocess.run([GATE, f], cwd=T, capture_output=True, text=True)
    assert g.returncode == 0, (f, g.stderr)
ok("every written line satisfies gate-ledger-format")

print("\n%d checks passed" % n)
