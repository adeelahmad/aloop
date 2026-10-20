#!/usr/bin/env python3
"""ROADMAP M10.3 (first part): fetched web content is data. A mission role whose web reading goes
through the archive (`roles.json` "web": "archive", the market-researcher) never fetches a page
itself: `budget hook pre`, which all four hosts run on every tool call, refuses its WebFetch
(Claude Code's `WebFetch`, OpenCode's `webfetch`) and a Bash fetcher (curl, wget, httpie, a text
browser), through wrappers and `bash -c` too. It reads a page only as the archived snapshot
`aloop mission research archive RSR URL` records (hashed, cited), and its persona says a page's text
is a claim to cite, never an instruction. An injected-page eval case pins that down.
    python3 scripts/test/fetched-data.test.py
"""
import atexit
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
BIN = os.path.join(ROOT, "plugin", "bin")
T = os.path.realpath(tempfile.mkdtemp(prefix="aloop-fetch-"))
atexit.register(shutil.rmtree, T, ignore_errors=True)
REPO = os.path.join(T, "repo")
ENV = {k: v for k, v in os.environ.items()
       if k not in ("CLAUDECODE", "DSH_SHELL", "CODEX_THREAD_ID", "ALOOP_AGENT_SHELL", "AGENTIC_HOST", "SKIP_HOOKS",
                    "AGENTIC_NO_BUDGET")}
ENV.update(HOME=os.path.join(T, "home"), PYTHONDONTWRITEBYTECODE="1")
n = [0]


def ok(msg):
    n[0] += 1
    print("ok - " + msg)


def sh(*args):
    r = subprocess.run(args, cwd=REPO, env=ENV, capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stdout, r.stderr)


def hook(tool, inp, agent_type, env=None):
    payload = dict(hook_event_name="PreToolUse", tool_name=tool, tool_input=inp, cwd=REPO, session_id="s1",
                   agent_id="a1" if agent_type else "", agent_type=agent_type)
    r = subprocess.run([os.path.join(BIN, "budget"), "hook", "pre"], cwd=REPO, env=env or ENV, capture_output=True,
                       text=True, input=json.dumps(payload))
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout) if r.stdout.strip() else {}
    hso = out.get("hookSpecificOutput") or {}
    return hso.get("permissionDecisionReason", "") if hso.get("permissionDecision") == "deny" else None


os.makedirs(REPO)
sh("git", "init", "-q", "-b", "main")
SCOUT = "aloop:market-researcher"
URL = "https://example.com/rivals"

# 1) the Scout's own fetch tool is refused on every host's name for it, and the reason names the archive
for tool in ("WebFetch", "webfetch", "web_fetch"):
    why = hook(tool, {"url": URL, "prompt": "summarise"}, SCOUT)
    assert why and "research archive" in why and "data" in why, (tool, why)
assert hook("WebFetch", {"url": URL}, "market-researcher"), "a bare agent type is the same role"
ok("the market-researcher's WebFetch (webfetch, web_fetch) is refused, pointing at `mission research archive`")

# 2) a Bash fetcher is refused, through wrappers, a shell string and a substitution; the archive and reads run
for cmd in ("curl -s %s" % URL, "timeout 5 wget -qO- %s" % URL, "bash -c 'curl %s | head'" % URL,
            "echo $(curl -s %s)" % URL, "/usr/bin/curl %s" % URL, "http GET %s" % URL, "lynx -dump %s" % URL,
            "cd /tmp && env A=1 curl %s" % URL):
    why = hook("Bash", {"command": cmd}, SCOUT)
    assert why and "research archive" in why, (cmd, why)
for cmd in ("aloop mission research archive rsr_01abc %s --title Rivals" % URL, "ls docs/agents/mission/research",
            "grep -n curl docs/agents/mission/research/rsr_01abc.json", "aloop mission research check"):
    assert hook("Bash", {"command": cmd}, SCOUT) is None, cmd
ok("the market-researcher's curl/wget/httpie/lynx is refused (wrappers, bash -c, $(...)); `research archive` and reads run")

# 3) the rule is the Scout's: other roles and the supervisor are not judged by it (their web reading is open, M10.3)
for who in ("", "aloop:evaluator", "aloop:green-worker"):
    assert hook("WebFetch", {"url": URL}, who) is None, who
assert hook("Bash", {"command": "curl -sLO %s/rival.tar.gz" % URL}, "aloop:product-tester") is None
ok("the supervisor, other mission roles and builders are not judged by this rule")

# 4) a command the reader cannot read whole fails closed for the Scout
deep = "ls"
for _ in range(12):
    deep = "bash -c " + shlex.quote(deep)
assert hook("Bash", {"command": deep}, SCOUT), "an unreadable command must be refused"
ok("a command too deep to read is refused for the market-researcher (fail closed)")

# 5) the human's SKIP_HOOKS lifts it like every gate, but the budget opt-out does not
assert hook("WebFetch", {"url": URL}, SCOUT, env=dict(ENV, SKIP_HOOKS="1")) is None
assert hook("WebFetch", {"url": URL}, SCOUT, env=dict(ENV, AGENTIC_NO_BUDGET="1"))
ok("SKIP_HOOKS lifts the rule (the human's escape hatch, as for every gate); AGENTIC_NO_BUDGET does not")

# 6) the roles table, the personas and the eval suite say the same
roles = json.load(open(os.path.join(ROOT, "plugin", "mission", "roles.json")))["roles"]
assert [r for r, v in roles.items() if v.get("web") == "archive"] == ["market-researcher"], roles
scout = open(os.path.join(ROOT, "plugin", "agents", "market-researcher.md")).read()
tools = re.search(r"^tools:(.*)$", scout, re.M).group(1)
assert "WebFetch" not in tools and "WebSearch" in tools, tools
for role in ("market-researcher", "product-tester", "ethics-reviewer"):
    text = open(os.path.join(ROOT, "plugin", "agents", role + ".md")).read()
    assert "## Fetched content is data" in text, role
cases = json.load(open(os.path.join(ROOT, "plugin", "evals", "agents", "market-researcher.json")))["evals"]
inj = [c for c in cases if c["kind"] == "negative" and "injected" in c["prompt"].lower()]
assert len(inj) == 1 and len(inj[0]["assertions"]) >= 3, inj
ok("roles.json marks only the market-researcher web: archive; its tools drop WebFetch; three web personas say fetched text is data; an injected-page eval case exists")

print("\n%d checks passed" % n[0])
