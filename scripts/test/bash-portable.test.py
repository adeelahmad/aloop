#!/usr/bin/env python3
"""Gates run on the bash a host finds first, and on a stock Mac that is bash 3.2 (/bin/bash).
A bash-4 builtin there is "command not found", and the gate quietly takes another path: with
`mapfile`, the standards.md gate matrix came back empty and the default Rust matrix ran instead.

1. no plugin shell script uses a bash-4+ construct;
2. the gate matrix runs the commands standards.md declares (on the bash on PATH; set
   BASH32=/path/to/bash-3.2 to also run it there).
    python3 scripts/test/bash-portable.test.py
"""
import atexit
import os
import re
import shutil
import subprocess
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
BIN = os.path.join(ROOT, "plugin", "bin")
T = os.path.realpath(tempfile.mkdtemp(prefix="aloop-bash-portable-"))
atexit.register(shutil.rmtree, T, ignore_errors=True)
n = 0


def ok(msg):
    global n
    n += 1
    print("ok - " + msg)


# 1) bash-4+ constructs, by what they look like in a script
BASH4 = [
    (r"\b(mapfile|readarray|coproc)\b", "mapfile/readarray/coproc"),
    (r"\b(declare|local|typeset)\s+-[a-zA-Z]*[An]", "associative array or nameref"),
    (r"\$\{[A-Za-z_][A-Za-z0-9_]*(\[[^]]*\])?(,,?|\^\^?)\}", "case modification ${x,,} / ${x^^}"),
    (r"\$\{[A-Za-z_][A-Za-z0-9_]*@[QEPAa]\}", "${x@Q} transformation"),
    (r"&>>|\|&", "&>> or |&"),
    (r"\bwait\s+-n\b", "wait -n"),
    (r"\$\{[A-Za-z_][A-Za-z0-9_]*\[-[0-9]+\]\}", "negative array index"),
]
scripts = []
for d in (BIN, os.path.join(ROOT, "plugin", "hooks"), os.path.join(ROOT, "scripts", "smoke")):
    for f in sorted(os.listdir(d)):
        p = os.path.join(d, f)
        if os.path.isfile(p):
            head = open(p, errors="replace").readline()
            if f.endswith(".sh") or re.match(r"#!.*\bbash\b", head):
                scripts.append(p)
hits = []
for p in scripts:
    for i, line in enumerate(open(p, errors="replace"), 1):
        code = line.split(" #", 1)[0] if not line.lstrip().startswith("#") else ""
        for rx, what in BASH4:
            if re.search(rx, code):
                hits.append("%s:%d: %s: %s" % (os.path.relpath(p, ROOT), i, what, line.strip()))
assert len(scripts) > 20 and not hits, "\n".join(hits)
ok("no bash-4+ construct in %d plugin shell scripts (macOS /bin/bash is 3.2)" % len(scripts))

# 2) the matrix in standards.md is the one that runs
subprocess.run(["git", "init", "-q"], cwd=T, check=True)
open(os.path.join(T, "standards.md"), "w").write(
    "# standards\n\n## Gate matrix\n\n```\ntouch ran-first\ntouch ran-second\n```\n\n## Other\n\n```\ntouch never\n```\n")
probe = 'GATE_NAME=t; . "%s/_gatelib.sh"; GATE_LOG=0; run_standards_matrix; echo "rc=$?"' % BIN
shells = ["bash"] + ([os.environ["BASH32"]] if os.environ.get("BASH32") else [])
for sh in shells:
    for f in ("ran-first", "ran-second", "never"):
        if os.path.exists(os.path.join(T, f)):
            os.remove(os.path.join(T, f))
    env = {k: v for k, v in os.environ.items() if k not in ("STANDARDS_FILE", "GATE_RUN_MATRIX", "SKIP_HOOKS")}
    env.update(HOME=os.path.join(T, "home"), REPO_DIR=T)
    r = subprocess.run([sh, "-c", probe], cwd=T, env=env, capture_output=True, text=True)
    ver = subprocess.run([sh, "-c", "echo $BASH_VERSION"], capture_output=True, text=True).stdout.strip()
    assert "rc=0" in r.stdout and "DEFAULT Rust matrix" not in r.stderr, (ver, r.stdout, r.stderr)
    assert os.path.exists(os.path.join(T, "ran-first")) and os.path.exists(os.path.join(T, "ran-second")), (ver, r.stderr)
    assert not os.path.exists(os.path.join(T, "never")), (ver, r.stderr)
    ok("bash %s: the gate matrix runs standards.md's own commands, and only the matrix section's" % ver)

print("\n%d checks passed" % n)
