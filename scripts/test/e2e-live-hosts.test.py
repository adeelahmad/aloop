#!/usr/bin/env python3
"""ROADMAP §4.11 (M8): the north-star run is reproducible on every host, not only Claude Code.
`e2e-live.sh --host codex|opencode` installs the plugin into the seeded repo with `aloop install`
and drives that host's headless CLI (`codex exec --json`, `opencode run --format json`) with
the same intent, approval and checks; `--dry-run` seeds and prints the command without
spending tokens; `run-summary --host` records the host and the reproduce command names it.
    python3 scripts/test/e2e-live-hosts.test.py
"""
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
SH = os.path.join(ROOT, "scripts", "smoke", "e2e-live.sh")
BIN = os.path.join(ROOT, "plugin", "bin")
PASS = [0]
T = tempfile.mkdtemp(prefix="agentic-e2e-hosts-")
STUBS = os.path.join(T, "stubs")
os.makedirs(STUBS)
# Stub CLIs: the dry run never calls them, but the script refuses to start without the host's CLI.
for name in ("claude", "codex", "opencode", "cargo"):
    p = os.path.join(STUBS, name)
    open(p, "w").write("#!/bin/sh\nexit 0\n")
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
ENV = dict(os.environ, HOME=os.path.join(T, "home"), PATH=STUBS + ":/usr/bin:/bin",
           GIT_AUTHOR_NAME="A", GIT_AUTHOR_EMAIL="a@a", GIT_COMMITTER_NAME="A", GIT_COMMITTER_EMAIL="a@a")
os.makedirs(ENV["HOME"])


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def run(*args, env=ENV):
    d = tempfile.mkdtemp(prefix="run-", dir=T)
    r = subprocess.run([SH, "--yes", "--dry-run", *args], cwd=T, env=dict(env, E2E_DIR=d), capture_output=True, text=True)
    return r, d


# 1) OpenCode: installed into the seeded repo, then `opencode run --format json` with the host's model
r, d = run("--host", "opencode", "--intent", "wordfreq")
assert r.returncode == 0, (r.stdout, r.stderr)
cmd = r.stdout[r.stdout.index("cmd: ") + 5:]
assert cmd.startswith("opencode run --format json --model anthropic/claude-sonnet-5 "), cmd
assert "Two stories" in cmd and "E2E-DONE" in cmd, cmd
assert os.path.isfile(os.path.join(d, "repo", ".opencode", "plugins", "aloop.js")), os.listdir(os.path.join(d, "repo"))
assert os.path.isdir(os.path.join(d, "repo", ".opencode", "agents"))
assert "host: opencode" in r.stdout
ok("--host opencode: aloop install opencode into the run repo, then opencode run --format json with the opencode route")

# 2) Codex: agents rendered, `codex exec --json --full-auto`; the model is the session's own unless --model is given
r, d = run("--host", "codex")
assert r.returncode == 0, (r.stdout, r.stderr)
cmd = r.stdout[r.stdout.index("cmd: ") + 5:]
assert cmd.startswith("codex exec --json --full-auto ") and "--model" not in cmd, cmd
assert any(f.endswith(".toml") for f in os.listdir(os.path.join(d, "repo", ".codex", "agents"))), "codex agents rendered"
r2, _ = run("--host", "codex", "--model", "gpt-5-codex")
assert "cmd: codex exec --json --full-auto --model gpt-5-codex " in r2.stdout, r2.stdout
ok("--host codex: aloop install codex into the run repo, then codex exec --json --full-auto (model only when asked)")

# 3) Claude Code is the default and unchanged
r, d = run()
assert r.returncode == 0, (r.stdout, r.stderr)
cmd = r.stdout[r.stdout.index("cmd: ") + 5:]
assert cmd.startswith("claude -p --plugin-dir ") and "--model sonnet" in cmd and "--output-format stream-json" in cmd, cmd
assert not os.path.exists(os.path.join(d, "repo", ".opencode")) and "host: claude" in r.stdout
# `--allowedTools` takes every following word, so a prompt after it becomes a tool name and
# `claude -p` gets no prompt at all (run 23 died in 2s: "Input must be provided"). It goes on stdin.
assert "E2E-DONE" not in cmd and "prompt: stdin" in r.stdout, cmd
ok("default host is claude: claude -p --plugin-dir … with the prompt on stdin (no host install)")

# 4) an unknown host or a missing CLI is a usage error before anything is seeded or spent
r, _ = run("--host", "cursor")
assert r.returncode == 2 and "cursor" in r.stdout + r.stderr, (r.returncode, r.stdout, r.stderr)
r, _ = run("--host", "opencode", env=dict(ENV, PATH="/usr/bin:/bin"))
assert r.returncode == 2 and "opencode CLI not found" in r.stdout + r.stderr, (r.returncode, r.stdout, r.stderr)
ok("unknown host and missing host CLI exit 2 with the reason")

# 5) run-summary --host: recorded, the reproduce command names the host, and a transcript with no
#    `result` line (Codex/OpenCode print none) records the cost as unmeasured, not $0.00
rd = os.path.join(T, "rs"); os.makedirs(os.path.join(rd, "repo"))
subprocess.run(["git", "init", "-q", os.path.join(rd, "repo")], check=True, env=ENV)
open(os.path.join(rd, "out.jsonl"), "w").write(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 5}}) + "\n")
r = subprocess.run([os.path.join(BIN, "run-summary"), rd, "--intent", "semver", "--host", "opencode", "--checks", "11/12",
                    "--secs", "700"], capture_output=True, text=True, env=ENV)
assert r.returncode == 0, r.stderr
s = json.load(open(os.path.join(rd, "summary.json")))
assert s["host"] == "opencode" and s["cost_usd"] is None and s["reproduce"] == "make e2e-live YES=1 INTENT=semver HOST=opencode", s
row = [l for l in r.stdout.splitlines() if l.startswith("| ")][-1]
assert "| semver (opencode) |" in row and "| — |" in row and "HOST=opencode" in row, row
r = subprocess.run([os.path.join(BIN, "run-summary"), rd, "--intent", "semver"], capture_output=True, text=True, env=ENV)
assert json.load(open(os.path.join(rd, "summary.json")))["host"] == "claude" and "HOST=" not in r.stdout
ok("run-summary --host records the host, names it in the reproduce command, and never prints $0.00 for an unmeasured cost")

# 5b) the Python intent seeds a Python package (no cargo) and needs python3 + pytest, not cargo
py = os.path.join(STUBS, "python3")      # the real python3, except `-m pytest` answers $HAS_PYTEST
open(py, "w").write('#!/bin/sh\n[ "$1 $2" = "-m pytest" ] && exit "${HAS_PYTEST:-0}"\nexec %s "$@"\n' % shutil.which("python3"))
os.chmod(py, 0o755)
r, d = run("--intent", "slugify")
assert r.returncode == 0, (r.stdout, r.stderr)
repo = os.path.join(d, "repo")
assert os.path.isfile(os.path.join(repo, "slugify", "__init__.py")) and not os.path.exists(os.path.join(repo, "Cargo.toml")), os.listdir(repo)
r, d = run("--intent", "slugify", env=dict(ENV, HAS_PYTEST="1"))
assert r.returncode == 2 and "pytest" in r.stdout, r.stdout
ok("--intent slugify: a Python package is seeded with no cargo, and the run needs python3 with pytest")

# 6) a run started from inside a Claude Code session does not inherit that session: live runs 32-34
#    reported the parent's session id and recalled the parent's team memory (memory_recall of this
#    repo's own agent notes), so the measured supervisor was not a clean one.
spy = os.path.join(T, "spy"); os.makedirs(spy)
p = os.path.join(spy, "claude")
open(p, "w").write("#!/bin/sh\nenv > \"$E2E_DIR/child.env\"\nexit 0\n")
os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
PARENT = {"CLAUDE_CODE_SESSION_ID": "parent", "CLAUDE_CODE_REMOTE_SESSION_ID": "cse_parent",
          "CLAUDE_COWORK_MEMORY_PATH_OVERRIDE": "/tmp/parent-mem", "CLAUDE_MEMORY_STORES": "[]",
          "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/s.sock", "CLAUDE_CODE_MESSAGING_TOKEN": "t",
          "CLAUDE_CODE_REMOTE_SDK_URL": "https://example.invalid/v1/code/sessions/cse_parent"}
d = tempfile.mkdtemp(prefix="run-", dir=T)
subprocess.run([SH, "--yes"], cwd=T, env=dict(ENV, E2E_DIR=d, PATH=spy + ":" + ENV["PATH"], TIMEOUT="30", **PARENT),
               capture_output=True, text=True)
child = open(os.path.join(d, "child.env")).read()
for k in PARENT:
    assert ("\n%s=" % k) not in "\n" + child, (k, child)
assert "\nE2E_DIR=" in "\n" + child
ok("the supervisor CLI runs without the parent session's id, memory store or messaging socket")

print("\n%d checks passed" % PASS[0])
