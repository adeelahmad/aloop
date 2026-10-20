#!/usr/bin/env python3
"""ROADMAP §4.11 (M8): the north-star run is reproducible on every host, not only Claude Code.
`e2e-live.sh --host codex|opencode|dsh` installs the plugin into the seeded repo with `aloop install`
and drives that host's headless CLI (`codex exec --json`, `opencode run --format json`,
`dsh --profile aloop --json`) with
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
for name in ("claude", "codex", "opencode", "dsh", "cargo"):
    p = os.path.join(STUBS, name)
    open(p, "w").write("#!/bin/sh\nexit 0\n")
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
ENV = dict({k: v for k, v in os.environ.items() if k not in ("DEEPSEEK_API_KEY", "DSH_HOME")},
           HOME=os.path.join(T, "home"), PATH=STUBS + ":/usr/bin:/bin",
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

# 2b) dsh: the profile rendered into the run's own DSH_HOME, then `dsh --profile aloop --json`,
#     unattended and with the supervisor's model in ALOOP_DSH_MODEL (dsh has no such flags)
r, d = run("--host", "dsh")
assert r.returncode == 0, (r.stdout, r.stderr)
cmd = r.stdout[r.stdout.index("cmd: ") + 5:]
assert cmd.startswith("env DSH_PERMISSION_MODE=danger-full-access DSH_TELEMETRY_DISABLED=1 ALOOP_DSH_MODEL=deepseek-v4-pro "
                      "dsh --profile aloop --json "), cmd
assert "E2E-DONE" in cmd and "host: dsh · supervisor model: deepseek-v4-pro · worker model: deepseek-flash" in r.stdout, r.stdout
patch = open(os.path.join(d, "dsh-home", "profiles", "aloop", "cordis.patch.yml")).read()
assert "toolName: subagent, allowedAgentPresets: [aloop-final-gate" in patch and 'model: "deepseek-flash"' in patch, patch[:1500]
assert os.path.isfile(os.path.join(d, "repo", ".dsh", "skills", "aloop", "SKILL.md"))
assert not os.path.exists(os.path.join(ENV["HOME"], ".dsh")), "the user's own DSH_HOME is untouched"
r = subprocess.run([SH, "--yes", "--host", "dsh"], cwd=T, env=dict(ENV, E2E_DIR=os.path.join(T, "nokey")), capture_output=True, text=True)
assert r.returncode == 2 and "export DEEPSEEK_API_KEY" in r.stdout and not os.path.exists(os.path.join(T, "nokey")), (r.returncode, r.stdout)
ok("--host dsh: aloop install dsh into the run's own DSH_HOME, then dsh --profile aloop --json; no key → exit 2 before seeding")

# 2c) the user's own dsh profile never changes, on any host: aloop-init re-renders an installed
#     (global) profile, so the run isolates DSH_HOME before init. Their .env is read as dsh reads it.
UDSH = os.path.join(ENV["HOME"], ".dsh")
os.makedirs(os.path.join(UDSH, "profiles", "aloop"))
USER_PROFILE = "# aloop (generated by aloop-install; re-run it instead of editing)\n# aloop-project: (global)\nUSER-PROFILE\n"
open(os.path.join(UDSH, "profiles", "aloop", "cordis.patch.yml"), "w").write(USER_PROFILE)
UCODEX = os.path.join(ENV["HOME"], ".codex", "agents", "planner.toml")
os.makedirs(os.path.dirname(UCODEX))
USER_AGENT = '# aloop (generated by aloop-install; re-run it instead of editing)\nname = "planner"\nmodel = "gpt-5.5"\n'
open(UCODEX, "w").write(USER_AGENT)
for host in ("claude", "dsh", "codex"):
    r, d = run("--host", host)
    assert r.returncode == 0, (host, r.stdout, r.stderr)
    assert open(os.path.join(UDSH, "profiles", "aloop", "cordis.patch.yml")).read() == USER_PROFILE, host
    assert open(UCODEX).read() == USER_AGENT, host
NODE_ENV = dict(ENV, PATH=ENV["PATH"] + ":" + os.path.dirname(shutil.which("node")))
for i, (dotenv, has) in enumerate((("export DEEPSEEK_API_KEY=sk-x\n", True), ("DEEPSEEK_API_KEY = 'sk-x'\n", True),
                                   ('DEEPSEEK_API_KEY=""\n', False), ("# DEEPSEEK_API_KEY=sk-x\n", False))):
    open(os.path.join(UDSH, ".env"), "w").write(dotenv)
    r = subprocess.run([SH, "--yes", "--host", "dsh"], cwd=T, env=dict(NODE_ENV, E2E_DIR=os.path.join(T, "key%d" % i)),
                       capture_output=True, text=True)
    assert ("no DeepSeek API key" not in r.stdout) == has and (has or r.returncode == 2), (dotenv, r.returncode, r.stdout[-800:])
assert open(os.path.join(UDSH, "profiles", "aloop", "cordis.patch.yml")).read() == USER_PROFILE
ok("the user's own dsh profile and global Codex agents are never re-rendered by a run (any host); a key in their .env counts as dsh reads it")

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

# 3b) the approval given up front is the human's typed line (ROADMAP M0.9): the prompt carries the line that
#     grants sprint 1 autonomy, and that line, captured as the human's words, is what the recorded grant cites
r, d = run("--host", "opencode")
grant = re.search(r"^autonomous S1 [0-9a-f]{8}$", r.stdout, re.M)
assert grant and "--mode autonomous" in r.stdout and "--sprint 1" in r.stdout, r.stdout
repo = os.path.join(d, "repo")
AUTH = [os.path.join(BIN, "events"), "authority", "--initiated-by", "e2e human", "--mode", "autonomous", "--scope", "the e2e run",
        "--sprint", "1"]
assert subprocess.run(AUTH, cwd=repo, env=ENV, capture_output=True, text=True).returncode == 3     # not before the human's words
subprocess.run([os.path.join(BIN, "transcripts"), "prompt"], cwd=repo, env=ENV, check=True, capture_output=True, text=True,
               input=json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "e2e", "prompt": "Use the aloop skill.\n"
                                 + grant.group(0) + "\nRun the whole loop."}))
r = subprocess.run(AUTH, cwd=repo, env=ENV, capture_output=True, text=True)
assert r.returncode == 0, r.stderr
ok("the e2e prompt carries the human's line granting sprint 1 autonomy; captured, it records that grant")

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

# 5a) dsh prints no cost line, but its plugin logs each call's exact usage per role (.aladdin/host/dsh):
#     run-summary prices every call from the dated DeepSeek table (cache hits apart, the peak or
#     off-peak band of its UTC time); a model with no published price keeps its tokens and gets no
#     dollars, and the run's cost is then unmeasured (—), never a guess or a partial sum
rd = os.path.join(T, "rs-dsh"); hd = os.path.join(rd, "repo", ".aladdin", "host", "dsh")
os.makedirs(os.path.join(hd, "root", "subagents"))
subprocess.run(["git", "init", "-q", os.path.join(rd, "repo")], check=True, env=ENV)
open(os.path.join(rd, "out.jsonl"), "w").write(json.dumps({"type": "status", "phase": "turn_end"}) + "\n")


def calls(path, *rows):
    with open(path, "a") as f:
        for ts, model, i, cr, cw, o in rows:
            f.write(json.dumps({"timestamp": ts, "message": {"id": "%s@%s" % (model, ts), "model": model, "usage": {
                "input_tokens": i, "output_tokens": o, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw}}}) + "\n")


#   supervisor on deepseek-v4-pro: Tue 07:30 UTC is peak, Sat is off-peak
calls(os.path.join(hd, "root.jsonl"), ("2026-10-20T07:30:00.000Z", "deepseek-v4-pro", 100000, 2000000, 0, 50000),
      ("2026-10-24T07:30:00.000Z", "deepseek-v4-pro", 100000, 1000000, 0, 10000))
#   a worker on deepseek-flash: Tue 02:00 is peak, Tue 12:00 off-peak (a cache write bills as a miss)
calls(os.path.join(hd, "root", "subagents", "agent-w1.jsonl"), ("2026-10-20T02:00:00.000Z", "deepseek-flash", 200000, 4000000, 0, 100000),
      ("2026-10-20T12:00:00.000Z", "deepseek-flash", 900000, 10000000, 100000, 200000))
#   the legacy id is billed at the Flash price (Wed 05:00, between the two peak bands)
calls(os.path.join(hd, "root", "subagents", "agent-w2.jsonl"), ("2026-10-21T05:00:00.000Z", "deepseek-v4-flash", 1000000, 0, 0, 0))
PRO = 0.1 * 1.32 + 2 * 0.044 + 0.05 * 3.96 + 0.1 * 0.66 + 1 * 0.022 + 0.01 * 1.98      # 0.5258
FLASH = 0.2 * 0.30 + 4 * 0.006 + 0.1 * 1.20 + 1.0 * 0.15 + 10 * 0.003 + 0.2 * 0.60    # 0.504
LEGACY = 1.0 * 0.15
r = subprocess.run([os.path.join(BIN, "run-summary"), rd, "--intent", "semver", "--host", "dsh", "--checks", "12/12", "--secs", "600"],
                   capture_output=True, text=True, env=ENV)
assert r.returncode == 0, r.stderr
s = json.load(open(os.path.join(rd, "summary.json")))
by = s.get("cost_by_model") or {}
assert set(by) == {"deepseek-v4-pro", "deepseek-flash", "deepseek-v4-flash"}, s
assert abs(by["deepseek-v4-pro"]["usd"] - PRO) < 1e-6 and abs(by["deepseek-flash"]["usd"] - FLASH) < 1e-6, by
assert abs(by["deepseek-v4-flash"]["usd"] - LEGACY) < 1e-6, by
assert by["deepseek-v4-pro"]["in"] == 200000 and by["deepseek-v4-pro"]["cache_read"] == 3000000 and by["deepseek-v4-pro"]["out"] == 60000, by
assert by["deepseek-flash"]["cache_write"] == 100000 and by["deepseek-flash"]["calls"] == 2, by
assert s["cost_usd"] == round(PRO + FLASH + LEGACY, 6) == 1.1798 and "api-docs.deepseek.com" in (s.get("prices") or ""), s
row = [l for l in r.stdout.splitlines() if l.startswith("| ")][-1]
assert "| semver (dsh) |" in row and "| $1.1798 |" in row and "HOST=dsh" in row, row
calls(os.path.join(hd, "root", "subagents", "agent-w3.jsonl"), ("2026-10-20T12:00:00.000Z", "my-gateway-model", 5000, 0, 0, 700))
r = subprocess.run([os.path.join(BIN, "run-summary"), rd, "--intent", "semver", "--host", "dsh"], capture_output=True, text=True, env=ENV)
s = json.load(open(os.path.join(rd, "summary.json")))
by = s["cost_by_model"]
assert s["cost_usd"] is None and by["my-gateway-model"]["usd"] is None and by["my-gateway-model"]["in"] == 5000, s
assert by["my-gateway-model"]["out"] == 700 and abs(by["deepseek-flash"]["usd"] - FLASH) < 1e-6, by
row = [l for l in r.stdout.splitlines() if l.startswith("| ")][-1]
assert "| — |" in row and "my-gateway-model" in r.stdout and "no published price" in r.stdout, r.stdout
shutil.rmtree(os.path.join(rd, "repo", ".aladdin"))
r = subprocess.run([os.path.join(BIN, "run-summary"), rd, "--host", "dsh"], capture_output=True, text=True, env=ENV)
assert json.load(open(os.path.join(rd, "summary.json")))["cost_usd"] is None and "$0.00" not in r.stdout, r.stdout
ok("run-summary --host dsh: every call priced from the dated DeepSeek table (model, cache hits, peak band); an unpriced model keeps its tokens and the cost is unmeasured")

# 5a') DeepSeek bills 20-100x below Claude, so a short dsh run costs a fraction of a cent: cost_usd keeps
#      the micro-dollars and the row cell four places, never $0.00 (the "unmeasured shown as free" cell); the
#      cell is read from the unrounded total, so a call under half a micro-dollar (cost_usd 0.0) is <$0.0001 too
for name, tokens, cost, cell in (("rs-dsh-cent", 10000, 0.0015, "$0.0015"), ("rs-dsh-micro", 100, 0.000015, "<$0.0001"),
                                 ("rs-dsh-nano", 1, round(0.00000015, 6), "<$0.0001")):
    rd = os.path.join(T, name); hd = os.path.join(rd, "repo", ".aladdin", "host", "dsh")
    os.makedirs(hd)
    subprocess.run(["git", "init", "-q", os.path.join(rd, "repo")], check=True, env=ENV)
    open(os.path.join(rd, "out.jsonl"), "w").write("")
    calls(os.path.join(hd, "root.jsonl"), ("2026-10-24T12:00:00.000Z", "deepseek-flash", tokens, 0, 0, 0))   # Sat: off-peak
    r = subprocess.run([os.path.join(BIN, "run-summary"), rd, "--host", "dsh"], capture_output=True, text=True, env=ENV)
    s = json.load(open(os.path.join(rd, "summary.json")))
    row = [l for l in r.stdout.splitlines() if l.startswith("| ")][-1]
    assert s["cost_usd"] == cost == s["cost_by_model"]["deepseek-flash"]["usd"] and "| %s |" % cell in row and "| $0.00 |" not in row, (s, row)
ok("run-summary --host dsh keeps a sub-cent cost (cost_usd to the micro-dollar, the row to four places, <$0.0001 below), never $0.00")

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

# 5c) the Go intent seeds a Go module (no cargo) and needs go, not cargo
nogo = os.path.join(T, "nogo"); os.makedirs(nogo)   # /usr/bin and /bin without go, node, npm (CI runners ship them)
for b in ("/usr/bin", "/bin"):
    for f in os.listdir(b):
        if f not in ("go", "gofmt", "node", "npm") and not os.path.lexists(os.path.join(nogo, f)):
            os.symlink(os.path.join(b, f), os.path.join(nogo, f))
r, d = run("--intent", "roman", env=dict(ENV, PATH=STUBS + ":" + nogo))
assert r.returncode == 2 and "go needed" in r.stdout, r.stdout
gostub = os.path.join(STUBS, "go")
open(gostub, "w").write("#!/bin/sh\nexit 0\n"); os.chmod(gostub, 0o755)
r, d = run("--intent", "roman")
assert r.returncode == 0, (r.stdout, r.stderr)
repo = os.path.join(d, "repo")
assert open(os.path.join(repo, "go.mod")).read().startswith("module "), os.listdir(repo)
assert os.path.isfile(os.path.join(repo, "roman", "roman.go")) and not os.path.exists(os.path.join(repo, "Cargo.toml")), os.listdir(repo)
ok("--intent roman: a Go module is seeded with no cargo, and the run needs go")

# 5d) the JS intent seeds an npm package with a node --test script (no cargo) and needs node + npm
r, d = run("--intent", "camel", env=dict(ENV, PATH=STUBS + ":" + nogo))
assert r.returncode == 2 and "node and npm needed" in r.stdout, r.stdout
for name in ("node", "npm"):
    open(os.path.join(STUBS, name), "w").write("#!/bin/sh\nexit 0\n"); os.chmod(os.path.join(STUBS, name), 0o755)
r, d = run("--intent", "camel")
assert r.returncode == 0, (r.stdout, r.stderr)
repo = os.path.join(d, "repo")
pkg = json.load(open(os.path.join(repo, "package.json")))
assert pkg["scripts"]["test"] == "node --test" and pkg["type"] == "module", pkg
assert os.path.isfile(os.path.join(repo, "src", "camel.js")) and not os.path.exists(os.path.join(repo, "Cargo.toml")), os.listdir(repo)
ok("--intent camel: an npm package (node --test) is seeded with no cargo, and the run needs node and npm")

# 5e) the brownfield intent seeds a working crate with its own tests and asks for a change to it:
#     every other fixture starts from an empty library, but most real intents land on existing code
r, d = run("--intent", "extend")
assert r.returncode == 0, (r.stdout, r.stderr)
repo = os.path.join(d, "repo")
lib = open(os.path.join(repo, "src", "lib.rs")).read()
assert "pub fn parse(" in lib and "compare" not in lib, lib
seed = open(os.path.join(repo, "tests", "parse.rs")).read()
assert seed.count("#[test]") >= 3, seed
assert 'name = "semver_lite"' in open(os.path.join(repo, "Cargo.toml")).read()
tracked = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True, text=True).stdout.split()
assert "src/lib.rs" in tracked and "tests/parse.rs" in tracked, tracked
r, d = run("--intent", "modify")
assert r.returncode == 0, (r.stdout, r.stderr)
assert "pub fn parse(" in open(os.path.join(d, "repo", "src", "lib.rs")).read()
assert open(os.path.join(d, "repo", "tests", "parse.rs")).read() == seed
ok("--intent extend|modify: a working crate with its own passing tests is seeded and committed before the run")

# 5f) the application intent seeds a binary next to the library: its tests run the binary
r, d = run("--intent", "cli")
assert r.returncode == 0, (r.stdout, r.stderr)
assert open(os.path.join(d, "repo", "src", "main.rs")).read().strip() == "fn main() {}"
ok("--intent cli: a stub binary (src/main.rs) is seeded for an application intent")

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

# 7) every intent's post-run checks run to the end (live run 37: the slugify branch never set
#    SUPPRESS_RE, so the checks died on an unbound variable after a successful session)
for intent in ("semver", "wordfreq", "slugify", "roman", "camel", "extend", "modify", "cli"):
    d = tempfile.mkdtemp(prefix="run-", dir=T)
    r = subprocess.run([SH, "--yes", "--intent", intent], cwd=T, capture_output=True, text=True,
                       env=dict(ENV, E2E_DIR=d, PATH=spy + ":" + ENV["PATH"], TIMEOUT="30"))
    out = r.stdout + r.stderr
    assert "unbound variable" not in out and "no suppressed tests on main" in out and "every merged commit traces" in out, (intent, out[-2000:])
    assert intent != "cli" or "the minwc binary counts stdin" in out, out[-2000:]
    assert intent not in ("extend", "modify") or "the seed's own tests are still on main" in out, out[-2000:]
ok("the post-run checks of every intent (semver, wordfreq, slugify, roman, camel, extend, modify, cli) run to the end")

print("\n%d checks passed" % PASS[0])
