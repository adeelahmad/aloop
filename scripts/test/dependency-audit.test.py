#!/usr/bin/env python3
"""ROADMAP M10.2b: a sprint does not sign off while its dependencies carry a known vulnerability.
`audit run` runs the stack's own audit tool for each manifest that declares a dependency (cargo audit,
npm audit, pip-audit, govulncheck) and blocks on a finding, and on a missing tool (a dependency audit
that cannot run is no audit: the block names the install command). A manifest with no dependency
needs none. gate-final runs it after the secret scan. The tools are stubbed here (no network).
    python3 scripts/test/dependency-audit.test.py
"""
import atexit
import os
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
BIN = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugin", "bin"))
T = os.path.realpath(tempfile.mkdtemp(prefix="aloop-audit-"))
atexit.register(shutil.rmtree, T, ignore_errors=True)
FAKE = os.path.join(T, "fakebin")
LOG = os.path.join(T, "tools.log")
ENV = {k: v for k, v in os.environ.items() if k not in ("AGENTIC_HOST", "SKIP_HOOKS", "STORY_DIR", "AGENT_ROLE", "REPO_DIR")}
ENV.update(HOME=os.path.join(T, "home"), PYTHONDONTWRITEBYTECODE="1", AUDIT_T=T,
           PATH=FAKE + os.pathsep + os.environ["PATH"])
n = [0]


def ok(msg):
    n[0] += 1
    print("ok - " + msg)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(text)


def sh(*args, cwd):
    r = subprocess.run(args, cwd=cwd, env=ENV, capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stdout, r.stderr)


# one stub per tool: logs its arguments; `missing-<tool>` makes it absent (a version probe fails),
# `vuln-<tool>` makes it report a finding
STUB = """#!/bin/sh
tool=%s
echo "$tool $*" >> "$AUDIT_T/tools.log"
case "$1" in --version|-V) [ -e "$AUDIT_T/missing-$tool" ] && exit 127; echo "$tool 1.0"; exit 0;; esac
[ "$tool" = cargo ] && [ "$1" = audit ] && [ "$2" = --version ] && { [ -e "$AUDIT_T/missing-$tool" ] && exit 101; exit 0; }
if [ -e "$AUDIT_T/vuln-$tool" ]; then echo "Vulnerability found in $tool run: RUSTSEC-0000-0000 / GHSA-xxxx"; exit 1; fi
echo "no vulnerabilities"; exit 0
"""
for tool in ("cargo", "npm", "pip-audit", "govulncheck"):
    write(os.path.join(FAKE, tool), STUB % tool)
    os.chmod(os.path.join(FAKE, tool), 0o755)


def reset():
    for f in os.listdir(T):
        if f.startswith(("missing-", "vuln-")) or f == "tools.log":
            os.remove(os.path.join(T, f))


def mark(kind, tool):
    open(os.path.join(T, "%s-%s" % (kind, tool)), "w").close()


def ran():
    return open(LOG).read().splitlines() if os.path.exists(LOG) else []


def audit(root):
    return subprocess.run([os.path.join(BIN, "audit"), "run", "--root", root], capture_output=True, text=True, env=ENV)


def project(name, files):
    root = os.path.join(T, name)
    os.makedirs(root)
    sh("git", "init", "-q", "-b", "main", cwd=root)
    sh("git", "config", "user.email", "ada@x.io", cwd=root)
    sh("git", "config", "user.name", "Ada", cwd=root)
    for p, text in files.items():
        write(os.path.join(root, p), text)
    sh("git", "add", "-A", cwd=root)
    sh("git", "commit", "-qm", "init", cwd=root)
    return root


CARGO = '[package]\nname = "x"\n\n[dependencies]\nserde = "1"\n'
NPM = '{"name": "x", "dependencies": {"left-pad": "1.3.0"}}\n'
GO = "module example.com/x\n\ngo 1.22\n\nrequire github.com/google/uuid v1.6.0\n"

# 1) no dependency, no audit: a manifest that declares none runs no tool, and no tool is needed
root = project("none", {"Cargo.toml": '[package]\nname = "x"\n\n[dependencies]\n', "package.json": '{"name": "x"}\n',
                        "requirements.txt": "# nothing\n", "go.mod": "module example.com/x\n\ngo 1.22\n", "main.py": "x = 1\n"})
for t in ("cargo", "pip-audit", "govulncheck"):
    mark("missing", t)
r = audit(root)
assert r.returncode == 0 and not ran(), (r, ran())
ok("a project whose manifests declare no dependency needs no audit and runs no tool")

# 2) each stack runs its own tool, on its own manifest, and a clean report passes
reset()
root = project("all", {"Cargo.toml": CARGO, "Cargo.lock": "# lock\n", "package.json": NPM, "package-lock.json": "{}\n",
                       "requirements.txt": "requests==2.32.0\n", "go.mod": GO})
r = audit(root)
assert r.returncode == 0, r.stderr
log = ran()
assert any(l.startswith("cargo audit") and "--version" not in l for l in log), log
assert any(l.startswith("npm audit") and "--audit-level=high" in l for l in log), log
assert "pip-audit -r requirements.txt" in log, log
assert "govulncheck ./..." in log, log
ok("a clean polyglot project runs cargo audit, npm audit --audit-level=high, pip-audit -r requirements.txt and govulncheck ./...")

# 3) a finding blocks, names the tool and shows its report; the other stacks still ran
reset()
mark("vuln", "npm")
r = audit(root)
assert r.returncode == 2 and "npm audit" in r.stderr and "GHSA-xxxx" in r.stderr, r
assert any(l.startswith("govulncheck") for l in ran()), ran()
ok("a vulnerability reported by one stack's tool blocks with its output, after every stack was audited")

# 4) a missing tool blocks with the command that installs it (an audit that cannot run is no audit)
reset()
mark("missing", "pip-audit")
mark("missing", "govulncheck")
mark("missing", "cargo")
r = audit(root)
assert r.returncode == 2, r
for hint in ("pip install pip-audit", "go install golang.org/x/vuln/cmd/govulncheck@latest", "cargo install cargo-audit"):
    assert hint in r.stderr, (hint, r.stderr)
ok("a missing audit tool blocks and the message names the command that installs it")

# 5) pyproject dependencies are audited too, a requirements file per file, a dev-only package.json counts
reset()
root = project("py", {"pyproject.toml": '[project]\nname = "x"\nversion = "1"\ndependencies = ["requests>=2"]\n',
                      "requirements-dev.txt": "pytest\n", "package.json": '{"devDependencies": {"jest": "29"}}\n'})
r = audit(root)
log = ran()
assert r.returncode == 0, r.stderr
assert "pip-audit -r requirements-dev.txt" in log and "pip-audit ." in log, log
assert any(l.startswith("npm audit") for l in log), log
ok("pyproject.toml and each requirements*.txt are audited, and a package.json with only devDependencies counts")

# 6) vendored and installed trees are not the project's manifests
reset()
root = project("vend", {"node_modules/x/package.json": NPM, "vendor/y/go.mod": GO, "target/z/Cargo.toml": CARGO,
                        "main.py": "x = 1\n"})
r = audit(root)
assert r.returncode == 0 and not ran(), (r, ran())
ok("node_modules, vendor and target manifests are skipped")

# 7) gate-final runs it: a vulnerable dependency blocks sign-off, a clean one passes
reset()
root = project("gate", {"requirements.txt": "requests==2.0.0\n", "app.py": "import requests\n"})
env = dict(ENV, REPO_DIR=root, AGENTIC_SELFCHECK="1", GATE_RUN_MATRIX="0")
mark("vuln", "pip-audit")
r = subprocess.run([os.path.join(BIN, "gate-final")], cwd=root, input="", capture_output=True, text=True, env=env)
assert r.returncode == 2 and "dependency audit" in r.stderr and "pip-audit" in r.stderr, r.stderr
os.remove(os.path.join(T, "vuln-pip-audit"))
r = subprocess.run([os.path.join(BIN, "gate-final")], cwd=root, input="", capture_output=True, text=True, env=env)
assert r.returncode == 0, r.stderr
ok("gate-final blocks sign-off on a vulnerable dependency and passes once the audit is clean")

print("\n%d checks passed" % n[0])
