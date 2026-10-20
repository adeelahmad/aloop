#!/usr/bin/env python3
"""ROADMAP §4.12 (M9) M9.9: the six fixed mission roles and their wiring. Each role is a persona with
its eval suite, a model route, hook matchers on every host, a role path guard (gate-mission-scope:
a builder never writes docs/agents/mission/, a mission role never writes source or another role's
artifacts) and an artifact check at stop (gate-mission-artifact).

The gate checks run against a copy of plugin/bin + plugin/mission with a test schema and a fake
area module, so the other M9 tracks' schemas and areas never change what this test sees.
    python3 scripts/test/mission-roles.test.py
"""
import atexit
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
ROOT = os.path.join(REPO, "plugin")
BIN = os.path.join(ROOT, "bin")
T = os.path.realpath(tempfile.mkdtemp(prefix="aloop-roles-"))   # macOS: /var is a symlink to /private/var
atexit.register(shutil.rmtree, T, ignore_errors=True)
ENV = {k: v for k, v in os.environ.items() if k not in ("SKIP_HOOKS", "AGENTIC_HOST", "AGENT_ROLE", "AGENTIC_SELFCHECK")}
ENV.update(HOME=os.path.join(T, "home"), AGENTIC_NO_AUTOINSTALL="1")
PASS = [0]
TABLE = json.load(open(os.path.join(ROOT, "mission", "roles.json")))
ROLES = list(TABLE["roles"])
OPUS, SONNET = "claude-opus-5-5", "sonnet"


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def sh(*args, cwd, inp="", env=None):
    return subprocess.run(args, cwd=cwd, env=env or ENV, capture_output=True, text=True, input=inp)


def git_repo(d):
    os.makedirs(d, exist_ok=True)
    for c in (["init", "-q"], ["config", "user.email", "a@a"], ["config", "user.name", "A"],
              ["commit", "-q", "--allow-empty", "-m", "init"]):
        subprocess.run(["git"] + c, cwd=d, check=True, capture_output=True)
    return d


def matches(matcher, name):
    """Claude Code's rule: '' or '*' matches all; a plain name list is exact; else a regex search."""
    if matcher in ("", "*"):
        return True
    if re.fullmatch(r"[A-Za-z0-9_|:-]+", matcher):
        return name in matcher.split("|")
    return re.search(matcher, name) is not None


def frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    fm = {}
    for line in m.group(1).splitlines():
        mm = re.match(r"^([a-z]+):\s*(.*?)\s*(?:#.*)?$", line)
        if mm:
            fm[mm.group(1)] = mm.group(2).strip('"')
    return fm, m.group(2)


# ── A. the six roles and their wiring (the real plugin) ─────────────────────────────
sys.path.insert(0, BIN)
import _mission as RealM  # noqa: E402
import _routes  # noqa: E402

assert ROLES == ["market-researcher", "product-tester", "state-assessor", "evaluator", "mission-planner",
                 "ethics-reviewer"], ROLES
dirs = {loc for k, loc in RealM.LAYOUT.items() if not loc.endswith(".json")}
owned = set()
for r, spec in TABLE["roles"].items():
    assert spec["group"] in ("planning", "worker") and spec["writes"] and spec["persona"] and spec["job"], r
    for w in spec["writes"]:
        assert w.endswith("/") and (w == "docs/agents/intents/" or w[len("docs/agents/mission/"):-1] in dirs), (r, w)
        owned.add(w)
assert {"docs/agents/mission/%s/" % d for d in dirs} <= owned, "every artifact dir has an owner role"
assert set(TABLE["kind_areas"]) == {k for k in RealM.LAYOUT if k != "mission"}
ok("roles.json: the six fixed roles; every artifact dir has an owner; every kind maps to its area")

for r in ROLES:
    fm, body = frontmatter(open(os.path.join(ROOT, "agents", r + ".md")).read())
    assert fm["name"] == r and len(fm["description"]) > 40, (r, fm)
    assert fm["model"] == (OPUS if TABLE["roles"][r]["group"] == "planning" else SONNET), (r, fm["model"])
    tools = [t.strip() for t in fm["tools"].split(",")]
    assert {"Read", "Bash", "Write"} <= set(tools), (r, tools)
    for h in ("# Persona — ", "## Mandate", "## Writes", "## Tools", "## Hard limits", "## Report"):
        assert h in body, (r, h)
    assert body.index("## Mandate") < body.index("## Writes") < body.index("## Hard limits") < body.index("## Report"), r
    for w in TABLE["roles"][r]["writes"]:
        assert w.rstrip("/") in body, (r, w)
    assert "selfcheck %s" % r in body and "gate-mission-artifact" in body and "gate-mission-scope" in body, r
ok("six persona files: name, model by route group, tools, the fixed sections, their own paths and gates")

hooks = json.load(open(os.path.join(ROOT, "hooks", "hooks.json")))["hooks"]


def cmds(event, name):
    return [h["command"] for g in hooks[event] if matches(g.get("matcher", ""), name) for h in g["hooks"]]


for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"):
    assert any("gate-mission-scope" in c for c in cmds("PreToolUse", tool)), tool
for r in ROLES:
    for name in (r, "aloop:" + r):
        assert any(c.endswith('gate-mission-artifact" start') for c in cmds("SubagentStart", name)), name
        stop = cmds("SubagentStop", name)
        assert any(c.endswith('gate-mission-artifact"') for c in stop) and any("budget\" hook stop" in c for c in stop), name
for other in ("red-worker", "aloop:planner", "general-purpose"):
    assert not any("gate-mission-artifact" in c for c in cmds("SubagentStop", other)), other
ok("hooks.json: gate-mission-scope on every write tool; start record + artifact gate + budget at each role's start/stop")

for r in ROLES:
    assert _routes.group_of(r) == TABLE["roles"][r]["group"], r
P0 = git_repo(os.path.join(T, "routes"))
os.makedirs(os.path.join(P0, "docs", "agents"))
for r in ROLES:
    want = _routes.table()["hosts"]["claude"]["strongest" if TABLE["roles"][r]["group"] == "planning" else "strong"]
    out = sh(os.path.join(BIN, "models"), "resolve", r, cwd=P0)
    assert out.returncode == 0 and out.stdout.strip() == want, (r, out.stdout, out.stderr)
ok("model routes: planning-group roles resolve to the strongest route, the two measuring roles to strong")

r = sh(os.path.join(BIN, "install"), "codex", "--project", P0, cwd=P0)
assert r.returncode == 0, r.stderr
r2 = sh(os.path.join(BIN, "install"), "opencode", "--project", P0, cwd=P0)
assert r2.returncode == 0, r2.stderr
for r in ROLES:
    toml = open(os.path.join(P0, ".codex", "agents", r + ".toml")).read()
    effort = "high" if TABLE["roles"][r]["group"] == "planning" else "medium"
    assert 'name = "%s"' % r in toml and 'model_reasoning_effort = "%s"' % effort in toml, (r, toml[:300])
    oc = open(os.path.join(P0, ".opencode", "agents", r + ".md")).read()
    model = "anthropic/claude-opus-5-5" if TABLE["roles"][r]["group"] == "planning" else "anthropic/claude-sonnet-5"
    assert "mode: subagent" in oc and "model: %s" % model in oc and "## On OpenCode" in oc, (r, oc[:300])
js = open(os.path.join(P0, ".opencode", "plugins", "aloop.js")).read()
for r in ROLES:
    assert re.search(r'"%s":\s*"gate-mission-artifact"' % re.escape(r), js), r
assert '"gate-mission-scope"' in js and '"gate-mission-artifact", ["start"]' in js
ok("host install: Codex and OpenCode agents rendered per route group; the OpenCode plugin wires both gates")

# the mission skill (M9.12, plugin/skills/mission/) reaches OpenCode as `aloop-mission` once it ships;
# until then the install skips it (checked on the real plugin above)
IP = os.path.join(T, "install-plugin")
for d in ("bin", "agents", "skills", "hosts", "mission"):
    shutil.copytree(os.path.join(ROOT, d), os.path.join(IP, d), symlinks=True)
if not os.path.isfile(os.path.join(ROOT, "skills", "mission", "SKILL.md")):
    assert not os.path.exists(os.path.join(P0, ".opencode", "skills", "aloop-mission"))
    os.makedirs(os.path.join(IP, "skills", "mission"))
    open(os.path.join(IP, "skills", "mission", "SKILL.md"), "w").write(
        "---\nname: mission\ndescription: Run the mission loop\n---\n# Mission\n")
P1 = git_repo(os.path.join(T, "p1"))
r = sh(os.path.join(IP, "bin", "install"), "opencode", "--project", P1, cwd=P1)
assert r.returncode == 0, r.stderr
sk = os.path.join(P1, ".opencode", "skills", "aloop-mission", "SKILL.md")
assert os.path.isfile(sk) and "name: aloop-mission" in open(sk).read(), r.stdout
r = sh(os.path.join(IP, "bin", "install"), "opencode", "--uninstall", "--project", P1, cwd=P1)
assert r.returncode == 0 and not os.path.exists(os.path.dirname(sk)), (r.stdout, r.stderr)
ok("host install: the mission skill reaches OpenCode as aloop-mission, and uninstall removes it")

for r in ROLES:
    out = sh(os.path.join(BIN, "selfcheck"), r, cwd=P0)
    assert "running gate-mission-artifact" in out.stdout, (r, out.stdout, out.stderr)
ok("selfcheck <role> runs the role's stop gate (gate-mission-artifact)")

open(os.path.join(P0, "docs", "agents", "defaults.md"), "w").write(
    "---\ntype: agentic-defaults\n---\n```conf\nAGENTIC_MODEL_PLANNING=opus\nAGENTIC_MODEL_WORKER=haiku\n```\n")
os.makedirs(os.path.join(P0, ".aladdin", "state"), exist_ok=True)


def dispatch(role, model):
    p = {"session_id": "s", "cwd": P0, "tool_name": "Agent",
         "tool_input": {"subagent_type": "aloop:" + role, "prompt": "Run the baseline research.", "model": model}}
    return sh(os.path.join(BIN, "gate-dispatch"), cwd=P0, inp=json.dumps(p))


assert dispatch("evaluator", "haiku").returncode == 2 and "aloop models resolve evaluator" in dispatch("evaluator", "haiku").stderr
assert dispatch("evaluator", "opus").returncode == 0, dispatch("evaluator", "opus").stderr
assert dispatch("state-assessor", "opus").returncode == 2 and dispatch("state-assessor", "haiku").returncode == 0
ok("gate-dispatch: a mission role runs on the project's route for its group, and needs no TASK_ID")

loader = importlib.machinery.SourceFileLoader("budget_mod", os.path.join(BIN, "budget"))
spec = importlib.util.spec_from_loader("budget_mod", loader)
budget = importlib.util.module_from_spec(spec)
loader.exec_module(budget)
assert all(r in budget.ROLE_PROFILES for r in ROLES) and not any(r in budget.WORKERS for r in ROLES)
ok("budget: every mission role is metered against its own profile, observe-only (never a worker kill)")

for r in ROLES:
    s = json.load(open(os.path.join(ROOT, "evals", "agents", r + ".json")))
    assert s["agent_role"] == r and s["gate"] == "gate-mission-artifact", r
    kinds = [e["kind"] for e in s["evals"]]
    assert "happy" in kinds and "edge" in kinds and kinds.count("negative") >= 2, (r, kinds)
assert all("`agents/%s.json`" % r in open(os.path.join(ROOT, "evals", "README.md")).read() for r in ROLES)
v = sh(sys.executable, os.path.join(REPO, "scripts", "eval", "validate-suites.py"), cwd=REPO)
assert v.returncode == 0, v.stderr
ok("eval suites: one per role (happy, edge, two negatives) on gate-mission-artifact; validate-suites passes")

# ── B. gate-mission-scope: the role path guards ───────────────────────────────────
P = git_repo(os.path.join(T, "proj"))
for d in ("research", "eval", "state", "goals"):
    os.makedirs(os.path.join(P, "docs", "agents", "mission", d))
os.makedirs(os.path.join(P, "src"))
WT = os.path.join(P, ".aladdin", "worktrees", "S1-01-T1")
subprocess.run(["git", "worktree", "add", "-q", "-b", "agentic/S1-01-T1", WT], cwd=P, check=True, capture_output=True)
SCOPE = os.path.join(BIN, "gate-mission-scope")
MD = os.path.join(P, "docs", "agents", "mission")


def scope(tool, agent_type=None, env=None, cwd=None, **ti):
    p = {"session_id": "s1", "cwd": cwd or P, "hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": ti}
    if agent_type:
        p["agent_type"], p["agent_id"] = agent_type, "a-" + agent_type.split(":")[-1]
    return sh(SCOPE, cwd=cwd or P, inp=json.dumps(p), env=env)


def allowed(r):
    return r.returncode == 0 and not r.stderr


def blocked(r, *words):
    return r.returncode == 2 and "BLOCK[mission-scope]" in r.stderr and all(w in r.stderr for w in words)


E = "aloop:evaluator"
assert allowed(scope("Write", E, file_path=os.path.join(MD, "eval", "evl_x.json")))
assert allowed(scope("Write", "evaluator", file_path=os.path.join(MD, "judges", "rub_x.json")))
assert blocked(scope("Write", E, file_path=os.path.join(P, "src", "app.py")), "source", "src/app.py")
assert blocked(scope("Edit", E, file_path=os.path.join(MD, "research", "rsr_x.json")), "market-researcher")
assert blocked(scope("Write", E, file_path=os.path.join(MD, "mission.json")), "the mission itself")
assert blocked(scope("Write", E, file_path=os.path.join(WT, "src", "a.py")), "a task worktree")
assert blocked(scope("Write", E, file_path=os.path.join(WT, "docs", "agents", "mission", "eval", "e.json")), "a task worktree")
assert allowed(scope("Write", E, file_path=os.path.join(P, ".aladdin", "scratch", "out.json")))
assert allowed(scope("Write", E, file_path=os.path.join(T, "outside", "rival.json")))
assert allowed(scope("Write", "aloop:mission-planner", file_path=os.path.join(P, "docs", "agents", "intents", "int_x.json")))
assert blocked(scope("Write", "aloop:mission-planner", file_path=os.path.join(P, "docs", "agents", "sprint1", "plan.md")),
               "Aladdin's planning docs")
ok("a mission role writes only its own paths in the main tree (plus run data and scratch outside the project)")

G = "aloop:green-worker"
assert blocked(scope("Write", G, file_path=os.path.join(MD, "eval", "evl_x.json")), "builder never grades")
assert blocked(scope("Write", G, file_path=os.path.join(WT, "docs", "agents", "mission", "eval", "e.json")))
assert blocked(scope("Write", "aloop:planner", file_path=os.path.join(MD, "goals", "gol_x.json")))
assert allowed(scope("Write", G, file_path=os.path.join(WT, "src", "a.py")))
assert allowed(scope("Write", "general-purpose", file_path=os.path.join(P, "src", "a.py")))
ok("any other dispatched agent (a builder, a planner) never writes docs/agents/mission/, in any tree")

assert blocked(scope("Write", file_path=os.path.join(MD, "eval", "evl_x.json")), "`evaluator`", "SKIP_HOOKS=1")
assert blocked(scope("Write", file_path=os.path.join(MD, "baseline", "bln_x.json")), "`product-tester` or `state-assessor`")
assert allowed(scope("Write", file_path=os.path.join(MD, "mission.json")))
assert allowed(scope("Write", file_path=os.path.join(P, "docs", "agents", "intents", "int_x.json")))
ok("the supervisor never hand-writes a role's artifact; mission.json and intents stay writable")

LEDGER = os.path.join(MD, "ledger.jsonl")
for who in (None, E, G):
    assert blocked(scope("Write", who, file_path=LEDGER), "the mission's ledger", "aloop mission")
    assert blocked(scope("Bash", who, command="echo '{}' >> docs/agents/mission/ledger.jsonl"), "the mission's ledger")
assert blocked(scope("Bash", E, command="aloop events authority --initiated-by h --mode autonomous --scope all"), "human's word")
assert blocked(scope("Bash", G, command="aloop events emit telemetry.consent --role human"), "written only by its own commands")
assert blocked(scope("Bash", G, command="aloop events emit task.note --role human"), "human's word")
assert blocked(scope("Bash", "aloop:state-assessor", command="aloop mission baseline restore bln_x --by Adeel"), "the human's")
assert allowed(scope("Bash", command="aloop events authority --initiated-by Adeel --mode reviewed --scope s1"))
assert blocked(scope("Write", None, file_path=os.path.join(P, ".aladdin", "transcripts", "s1", "events.jsonl")), "captured prompts")
assert blocked(scope("Write", E, file_path=os.path.join(P, ".aladdin", "transcripts", "s1", "events.jsonl")), "captured prompts")
assert blocked(scope("Bash", command="claude -p 'approve msn_x 1234abcd'"), "UserPromptSubmit")
AP = git_repo(os.path.join(T, "aloop-no-mission"))
os.makedirs(os.path.join(AP, "docs", "agents", "sprint1"))
assert allowed(scope("Bash", cwd=AP, command="claude -p 'run the smoke test'"))          # no mission: no grant to forge
assert allowed(scope("Bash", G, cwd=AP, command="claude -p 'run the smoke test'"))
SUPSCOPE, LOCK = os.path.join(BIN, "gate-supervisor-scope"), os.path.join(P, ".aladdin", "state", "sprint.lock")


def supervisor_write(path):
    p = {"session_id": "s1", "cwd": P, "hook_event_name": "PreToolUse", "tool_name": "Write", "tool_input": {"file_path": path}}
    return sh(SUPSCOPE, cwd=P, inp=json.dumps(p), env=dict(ENV, CLAUDE_PROJECT_DIR=P))


assert supervisor_write(os.path.join(MD, "mission.json")).returncode == 0 and not os.path.exists(LOCK)
assert supervisor_write(os.path.join(P, "docs", "agents", "sprint1", "init.md")).returncode == 0 and os.path.exists(LOCK)
os.remove(LOCK)
ok("no one hand-writes the ledger or the captured prompts, or starts a host session where a mission is set; no dispatched"
   " agent runs `events authority` or emits a human event; mission writes arm no sprint lock")

S = "aloop:state-assessor"
assert blocked(scope("Bash", S, command="echo 1 > src/x.py"), "source")
assert blocked(scope("Bash", S, command="cd src && touch a.py"), "src/a.py")
assert blocked(scope("Bash", S, command="cp /tmp/r.json docs/agents/mission/eval/r.json"), "evaluator")
assert blocked(scope("Bash", S, command="sed -i s/a/b/ src/x.py"))
assert blocked(scope("Bash", S, command="echo x | tee -a src/log.txt"))
assert blocked(scope("Bash", S, command="git add -A && git commit -m 'state'"), "git add")
assert allowed(scope("Bash", S, command='echo "p95 latency > 200ms" | aloop mission assess new --stdin'))
assert allowed(scope("Bash", S, command="cat > docs/agents/mission/state/sta_x.json <<'EOF'\nrm -rf src\n> src/x\nEOF\nls"))
assert allowed(scope("Bash", S, command="ls > /dev/null 2>&1; git status; git log -1; git stash list"))
assert allowed(scope("Bash", S, command="git -C %s/rival checkout v1.2 && npm test > %s/rival/out.txt" % (T, T)))
assert allowed(scope("Bash", S, command="mkdir -p docs/agents/mission/state && touch docs/agents/mission/state/.keep"))
assert blocked(scope("Bash", S, command="echo x > %s/src/b.py" % WT), "a task worktree")
ok("Bash: redirects, tee, cp/sed/touch targets, cd and git writes are judged; quotes and heredoc bodies are data")

assert blocked(scope("Bash", G, command="aloop mission evaluate new --goal g1"), "read-only mission commands")
assert blocked(scope("Bash", G, command="cd %s && mission research claim x" % P))
assert allowed(scope("Bash", G, command="aloop mission status && aloop mission journeys diff && mission check"))
assert allowed(scope("Bash", G, command="aloop mission research --help"))
assert blocked(scope("Bash", E, command="aloop mission approve --by Adeel"), "the human's")
assert blocked(scope("Bash", E, command="aloop mission baseline confirm bln_x --by Adeel"), "the human's")
assert blocked(scope("Bash", "aloop:ethics-reviewer", command="aloop mission ethics consent gol_x"), "the human's")
assert blocked(scope("Bash", E, command="aloop mission tick"), "supervisor's")
assert allowed(scope("Bash", "aloop:market-researcher", command="aloop mission research claim --text 'x' --source s1"))
assert allowed(scope("Bash", command="aloop mission approve --by Adeel --reason ok"))   # the supervisor relays the human
ok("the mission CLI: builders run only read-only commands; no dispatched agent approves, confirms, consents or ticks")

patch = "*** Begin Patch\n*** Update File: %s\n@@\n-a\n+b\n*** End Patch" % os.path.join(P, "src", "x.py")
assert blocked(scope("apply_patch", E, command=patch), "source")
assert allowed(scope("apply_patch", E, command=patch.replace(os.path.join(P, "src", "x.py"), os.path.join(MD, "eval", "e.json"))))
assert blocked(scope("write", E, filePath="src/x.py"), "source")                        # OpenCode-shaped
assert blocked(scope("NotebookEdit", E, notebook_path=os.path.join(P, "src", "n.ipynb")))
assert allowed(scope("Write", E, env=dict(ENV, SKIP_HOOKS="1"), file_path=os.path.join(P, "src", "x.py")))
NP = git_repo(os.path.join(T, "plain"))
assert allowed(scope("Write", E, cwd=NP, file_path=os.path.join(NP, "src", "x.py")))
ok("Codex apply_patch, OpenCode filePath and NotebookEdit are judged; SKIP_HOOKS=1 and a non-aloop repo pass")

# ── C. gate-mission-artifact: the artifact check at stop (an isolated plugin copy) ──
PL = os.path.join(T, "plugin")
for d in ("bin", "mission", "hosts"):
    shutil.copytree(os.path.join(ROOT, d), os.path.join(PL, d), symlinks=True)
for f in os.listdir(os.path.join(PL, "mission")):
    if f.endswith(".py"):
        os.remove(os.path.join(PL, "mission", f))             # other tracks' areas: not this test's subject
json.dump({"type": "object", "required": ["eval_id", "status", "created_at", "gaps"],
           "properties": {"gaps": {"type": "array", "minItems": 1}, "goal_met": {"type": "boolean"}}},
          open(os.path.join(PL, "mission", "schemas", "eval.json"), "w"))
for k in ("state", "research", "goal"):
    p = os.path.join(PL, "mission", "schemas", k + ".json")
    if os.path.exists(p):
        os.remove(p)
open(os.path.join(PL, "mission", "evaluate.py"), "w").write(
    '"""evaluate: a fake area for the roles test."""\nimport os\n\n\ndef main(argv, cwd):\n'
    '    rc = int(open(os.path.join(cwd, ".area-rc")).read()) if os.path.exists(os.path.join(cwd, ".area-rc")) else 0\n'
    '    if rc == 2:\n        print("BLOCK[evaluate]: gap g2 cites no journey", file=__import__("sys").stderr)\n'
    '    return rc\n')
sys.path.insert(0, os.path.join(PL, "bin"))
del sys.modules["_mission"]
import _mission as M  # noqa: E402
assert M.SCHEMAS_DIR.startswith(PL)
GATE = os.path.join(PL, "bin", "gate-mission-artifact")
Q = git_repo(os.path.join(T, "mission-proj"))
QD = os.path.join(Q, "docs", "agents", "mission")
os.makedirs(QD)


def grant(event, rel, sha, by="Adeel"):
    """The human's approval in Q: M9.17 records it only once the human typed the line it names."""
    sys.path.insert(0, os.path.join(REPO, "scripts", "test", "fixtures"))
    from mission_fixture import ASKED, say
    try:
        return M.approve(Q, event, rel, sha, by=by)
    except M.NeedsHuman as e:
        say(Q, ENV, ASKED.search(str(e)).group(1))
        return M.approve(Q, event, rel, sha, by=by)


def hook(agent_type, agent_id, *args, env=None):
    p = {"session_id": "s1", "cwd": Q, "agent_type": agent_type, "agent_id": agent_id,
         "hook_event_name": "SubagentStart" if args else "SubagentStop"}
    return sh(GATE, *args, cwd=Q, inp=json.dumps(p), env=env)


def artifact(kind, **fields):
    d = {kind + "_id": M.new_id(kind), "status": M.STATUSES[kind][0], "created_at": M.now()}
    d.update(fields)
    M.seal(d)
    return M.save(M.path_of(Q, kind, d[kind + "_id"]), d), d


def events():
    p = os.path.join(Q, ".aladdin", "logs", "events.jsonl")
    return [json.loads(x) for x in open(p)] if os.path.exists(p) else []


def gblocked(r, *words):
    return r.returncode == 2 and "BLOCK[mission-artifact]" in r.stderr and all(w in r.stderr for w in words)


assert hook("", "oc-child", "start").returncode == 0 and not os.path.exists(
    os.path.join(Q, ".aladdin", "state", "mission-runs", "oc-child.json"))        # a role-less record would excuse anything
assert hook(E, "ev1", "start").returncode == 0
rec = json.load(open(os.path.join(Q, ".aladdin", "state", "mission-runs", "ev1.json")))
assert rec["role"] == "evaluator" and rec["files"] == {}
r = hook(E, "ev1")
assert gblocked(r, "you wrote no artifact", "`docs/agents/mission/eval/`", "selfcheck evaluator"), r.stderr
ok("start records what exists; a role that wrote no artifact in its own paths is blocked at stop")

path, doc = artifact("eval", gaps=[{"gap": "g1"}], goal_met=False)
assert hook(E, "ev1").returncode == 0
ev = [e for e in events() if e["event"] == "artifact.created"]
assert len(ev) == 1 and ev[0]["role"] == "evaluator" and ev[0]["agent_id"] == "ev1", ev
assert ev[0]["refs"]["artifact"] == "docs/agents/mission/eval/%s.json" % doc["eval_id"] and ev[0]["data"]["sha256"] == M.digest(doc)
assert json.load(open(os.path.join(Q, ".aladdin", "state", "mission-runs", "ev1.json")))["stopped"]
assert [e["data"].get("gate") for e in events() if e["event"] == "gate.passed"] == ["mission-artifact"]
ok("a valid artifact passes; artifact.created records its role, agent, path and sha256 (provenance); the run closes")

hook(E, "ev2", "start")
bad = {"eval_id": "evl_made_up", "status": "recorded", "created_at": M.now(), "gaps": [{"gap": "g"}]}
M.save(os.path.join(QD, "eval", "evl_made_up.json"), bad)
r = hook(E, "ev2")
assert gblocked(r, "evl_made_up.json", "was not issued by the harness"), r.stderr
os.remove(os.path.join(QD, "eval", "evl_made_up.json"))
p2, d2 = artifact("eval", gaps=[])
r = hook(E, "ev2")
assert gblocked(r, "gaps needs at least 1 item"), r.stderr
d2["gaps"] = [{"gap": "g3"}]
json.dump(d2, open(p2, "w"))                                 # edited after it was sealed
r = hook(E, "ev2")
assert gblocked(r, "sha256 does not match"), r.stderr
M.seal(d2)
M.save(p2, d2)
assert hook(E, "ev2").returncode == 0
ok("the core contract is checked at stop: harness-issued id, the kind's schema, a sha256 matching the content")

hook(E, "ev3", "start")
artifact("eval", gaps=[{"gap": "g4"}])
open(os.path.join(Q, ".area-rc"), "w").write("2")
r = hook(E, "ev3")
assert gblocked(r, "aloop mission evaluate check", "gap g2 cites no journey"), r.stderr
open(os.path.join(Q, ".area-rc"), "w").write("3")
r = hook(E, "ev3")
assert r.returncode == 0 and "needs the human" in r.stderr, r.stderr
os.remove(os.path.join(Q, ".area-rc"))
hook(S, "sa1", "start")
artifact("state", numbers={"journeys_passed": 3})
r = hook(S, "sa1")
assert r.returncode == 0 and "no state schema yet" in r.stderr and "area `assess` is not built yet" in r.stderr, r.stderr
ok("the area's own check runs when its module exists (exit 2 blocks, 3 needs the human); a missing schema checks the core only")

hook(S, "sa2", "start")
artifact("state", numbers={"journeys_passed": 4})
fp, fd = artifact("eval", gaps=[{"gap": "written by the assessor"}])  # as if through another role's command
r = hook(S, "sa2")
assert gblocked(r, "eval/%s.json changed during your run, and it is evaluator's artifact" % fd["eval_id"]), r.stderr
os.remove(fp)
hook(S, "sa3", "start")
artifact("state", numbers={"journeys_passed": 5})
hook(E, "ev4", "start")                                      # an evaluator runs at the same time
fp, fd = artifact("eval", gaps=[{"gap": "the evaluator's"}])
assert hook(S, "sa3").returncode == 0
hook(S, "sa5", "start")
artifact("state", numbers={"journeys_passed": 6})
assert hook(E, "ev4").returncode == 0                        # it finishes first: it still overlapped sa5
assert hook(S, "sa5").returncode == 0
ok("a change to another role's artifact blocks at stop, unless a run of that role overlapped (open, or stopped since)")

hook(S, "sa4", "start")
artifact("state", numbers={"journeys_passed": 7})
was = open(fp).read()
M.save(fp, dict(fd, approval={"event": "goal.approved", "by": "the assessor"}))   # bookkeeping only, no approval event
r = hook(S, "sa4")
assert gblocked(r, "the status of docs/agents/mission/eval/%s.json changed during your run" % fd["eval_id"]), r.stderr
open(fp, "w").write(was)
assert hook(S, "sa4").returncode == 0
hook(S, "sa6", "start")
artifact("state", numbers={"journeys_passed": 8})
rel = "docs/agents/mission/eval/%s.json" % fd["eval_id"]
grant("journey.approved", rel, M.digest(fd))
fd["approval"] = {"event": "journey.approved"}
M.save(fp, fd)
r = hook(S, "sa6")
assert r.returncode == 0 and "was recorded while you ran" in r.stderr, r.stderr
assert [e for e in events() if e["event"] == "gate.passed"][-1]["data"]["human_events"][0]["by"] == "Adeel"
hook(E, "ev5", "start")
os.remove(fp)
artifact("eval", gaps=[{"gap": "g9"}])
r = hook(E, "ev5")
assert gblocked(r, "was deleted", "retire it"), r.stderr
ok("a status-only change to another role's artifact blocks unless the human approved it; deleting one blocks")

hook("aloop:mission-planner", "mp1", "start")
os.makedirs(os.path.join(Q, "docs", "agents", "intents"), exist_ok=True)
json.dump({"intent_id": "int_human", "source": "prompt"}, open(os.path.join(Q, "docs", "agents", "intents", "int_human.json"), "w"))
r = hook("aloop:mission-planner", "mp1")
assert gblocked(r, "you wrote no artifact") and "int_human" not in r.stderr, r.stderr   # the human's intent is not its work
json.dump({"intent_id": "int_bogus", "source": "mission"}, open(os.path.join(Q, "docs", "agents", "intents", "int_bogus.json"), "w"))
r = hook("aloop:mission-planner", "mp1")
assert gblocked(r, "int_bogus.json fails `intent check`"), r.stderr
hook(E, "ev12", "start")
artifact("eval", gaps=[{"gap": "g13"}])
json.dump({"intent_id": "int_human2", "source": "prompt"}, open(os.path.join(Q, "docs", "agents", "intents", "int_human2.json"), "w"))
os.remove(os.path.join(Q, "docs", "agents", "intents", "int_human.json"))
assert hook(E, "ev12").returncode == 0                       # intake or the human wrote it: not the evaluator's doing
ok("a Mission Planner's intents (source mission) must pass `intent check`; the human's intents are no role's")

hook(E, "ev6", "start")
sc = dict(ENV, AGENT_ROLE="evaluator")
r = sh(os.path.join(PL, "bin", "selfcheck"), "evaluator", cwd=Q, env=sc)
assert r.returncode == 2 and "you wrote no artifact" in r.stderr, r.stderr
n_ev = len(events())
artifact("eval", gaps=[{"gap": "g10"}])
r = sh(os.path.join(PL, "bin", "selfcheck"), "evaluator", cwd=Q, env=sc)
assert r.returncode == 0 and "SELF-CHECK PASS" in r.stdout, (r.stdout, r.stderr)
assert len(events()) == n_ev and not json.load(open(os.path.join(Q, ".aladdin", "state", "mission-runs", "ev6.json"))).get("stopped")
ok("selfcheck <role> judges the role's open run the same way, with no events and without closing the run")

hook(E, "ev10", "start")
artifact("eval", gaps=[{"gap": "g11"}])
grant("goal.approved", "docs/agents/mission/goals/gol_x.json", "0" * 64)   # however it was spelled
r = hook(E, "ev10")
assert r.returncode == 0 and "`goal.approved` (docs/agents/mission/goals/gol_x.json, by Adeel) was recorded while you ran" in r.stderr, r.stderr
assert [e for e in events() if e["event"] == "gate.passed"][-1]["data"]["human_events"] == [
    {"event": "goal.approved", "subject": "docs/agents/mission/goals/gol_x.json", "by": "Adeel"}]
hook("aloop:mission-planner", "mp2", "start")
gp, gd = artifact("goal", eval_id="evl_x", gaps=["g1"])
assert hook("aloop:mission-planner", "mp2").returncode == 0
hook("aloop:mission-planner", "mp3", "start")
grant("goal.approved", "docs/agents/mission/goals/%s.json" % gd["goal_id"], M.digest(gd))
gd["status"], gd["approval"] = "approved", {"event": "goal.approved"}
M.save(gp, gd)
assert gblocked(hook("aloop:mission-planner", "mp3"), "you wrote no artifact")   # the human's approval is not its write
ok("a human event during a role's run is named (note, gate.passed human_events); the human's approval is no role's write")

hook(E, "ev7", "start")
for i in range(2):
    assert hook(E, "ev7").returncode == 2
r = hook(E, "ev7")
assert r.returncode == 0 and "STUCK" in r.stderr and "NOT verified" in r.stderr, r.stderr
assert events()[-1]["event"] == "gate.failed" and events()[-1]["data"]["released"] == "stuck"
ok("the same block three times releases the stop UNVERIFIED (gate.failed, released=stuck), never a pass")

assert hook("aloop:red-worker", "rw1").returncode == 0
assert hook(E, "ev8", env=dict(ENV, SKIP_HOOKS="1")).returncode == 0
artifact("eval", gaps=[{"gap": "g14"}])
r = hook(E, "ev-no-start")
assert gblocked(r, "there is no start record for this run (agent ev-no-start)", "accepts nothing"), r.stderr
ok("not a mission role, or SKIP_HOOKS=1: no opinion; a run with an agent id but no start record is blocked")

st7 = json.load(open(os.path.join(Q, ".aladdin", "state", "mission-runs", "ev7.json")))
assert st7.get("stopped") and st7.get("released") == "stuck", st7          # a released run owns nothing any more
hook(E, "ev13", "start")
artifact("eval", gaps=[{"gap": "g15"}])
assert hook(E, "ev13").returncode == 0
assert gblocked(hook(E, "ev13"), "you wrote no artifact")   # the same agent's next task starts from its last pass
with open(os.path.join(Q, ".aladdin", "logs", "gates.jsonl"), "a") as f:
    f.write("{ not json\n")
assert hook(E, "ev13").returncode == 2
assert "STUCK" in hook(E, "ev13").stderr                    # one bad line never turns the release off
import time  # noqa: E402
loader = importlib.machinery.SourceFileLoader("gate_artifact", GATE)
spec = importlib.util.spec_from_loader("gate_artifact", loader)
ga = importlib.util.module_from_spec(spec)
loader.exec_module(ga)
n_ev = len(events())
iid = "int_%012x%s" % (int(time.time() * 1000), "0a1b2c3d")
ok_intent = {"intent_id": iid, "source": "prompt", "actor": "a user", "want": "dark mode", "why": "eyes",
             "constraints": [], "success_scenarios": ["the toggle works"], "failure_scenarios": ["no toggle"],
             "connections": [], "done_looks_like": "a test passes", "open_questions": [], "assumptions": [],
             "provenance": {"received_at": M.now(), "raw_ref": "", "raw_sha256": ""}}
json.dump(ok_intent, open(os.path.join(T, iid + ".json"), "w"))
assert ga.intent_problems(os.path.join(T, iid + ".json")) == ([], []), ga.intent_problems(os.path.join(T, iid + ".json"))
assert ga.intent_problems(os.path.join(Q, "docs", "agents", "intents", "int_bogus.json"))[0]
assert len(events()) == n_ev                                 # checking an intent emits nothing (intent.normalized is intake's)
t0 = time.time()
rc, out, _ = ga.run_check(["sleep", "5"], Q, time.time() + 1)
assert rc == 1 and time.time() - t0 < 4, (rc, out)
assert ga.run_check(["true"], Q, time.time() - 1)[0] == 1          # nothing runs once the deadline is spent
ok("a stuck release closes the run; a pass re-baselines it; bad log lines, intent checks and slow checks are handled")

open(os.path.join(PL, "mission", "roles.json"), "w").write("{ not json")          # a broken role table: a gate bug
r = hook(E, "ev9")
assert r.returncode == 2 and "failing closed" in r.stderr, r.stderr
p = {"session_id": "s1", "cwd": Q, "agent_type": E, "tool_name": "Write", "tool_input": {"file_path": os.path.join(Q, "src", "x")}}
r = sh(os.path.join(PL, "bin", "gate-mission-scope"), cwd=Q, inp=json.dumps(p))
assert r.returncode == 2 and "failing closed" in r.stderr, r.stderr
p["cwd"] = NP
assert sh(os.path.join(PL, "bin", "gate-mission-scope"), cwd=NP, inp=json.dumps(p)).returncode == 0
ok("both gates fail closed on a crash once the event is theirs (a non-aloop repo still passes)")

# ── D. the real areas on master (journeys, baseline, assess, evaluate): each role's run passes its stop ──
sys.path.insert(0, os.path.join(REPO, "scripts", "test", "fixtures"))
from mission_fixture import Project  # noqa: E402
REAL = os.path.join(BIN, "gate-mission-artifact")
X = Project(veto_window_h=0)
atexit.register(shutil.rmtree, X.dir, ignore_errors=True)
XENV = {k: v for k, v in X.env.items() if k not in ("SKIP_HOOKS", "AGENT_ROLE", "AGENTIC_SELFCHECK")}
assert M.SCHEMAS_DIR.startswith(PL)


def role_run(agent_type, aid, work):
    p = {"session_id": "s1", "cwd": X.dir, "agent_type": agent_type, "agent_id": aid}
    assert sh(REAL, "start", cwd=X.dir, inp=json.dumps(dict(p, hook_event_name="SubagentStart")), env=XENV).returncode == 0
    out = work()
    return sh(REAL, cwd=X.dir, inp=json.dumps(dict(p, hook_event_name="SubagentStop")), env=XENV), out


def made_path(r):
    assert r.returncode == 0, r.stderr
    return r.stdout.strip().splitlines()[-1]


def made(r):
    return json.load(open(made_path(r)))


def researched():
    """The Scout's real report: `research new`, a page it saved in its own dir archived, a quoted claim."""
    rid = researched.rid = os.path.basename(made_path(X.run("research", "new", "Rivals", "--question",
                                                           "How fast do rivals show a first task?")))[:-5]
    page = os.path.join(X.dir, "docs", "agents", "mission", "research", "rival.html")   # --file reads only inside the project
    with open(page, "w") as f:
        f.write("<html><body><p>Rival A shows your first task in 3 seconds.</p></body></html>")
    assert X.run("research", "archive", rid, "https://rival.example/a", "--file", os.path.relpath(page, X.dir)).returncode == 0
    os.unlink(page)
    r = X.run("research", "claim", rid, "rivals show a first task in 3s", "--source", "s1", "--quote", "first task in 3 seconds")
    assert r.returncode == 0, r.stderr
    return X.journey(X.draft(rid))


r, j1 = role_run("aloop:market-researcher", "mr1", researched)
assert r.returncode == 0, r.stderr
r, b = role_run("aloop:state-assessor", "sa1", lambda: made(X.run("baseline", "new", "--definition", "a first task in 5s")))
assert r.returncode == 0, r.stderr
r, _ = role_run("aloop:state-assessor", "sa2", lambda: X.run("baseline", "measure", b["baseline_id"]))
assert r.returncode == 0 and "baseline.measured" in open(M.ledger(X.dir)).read(), r.stderr   # a CLI append is no tamper
assert X.run("baseline", "confirm", b["baseline_id"], "--by", "Adeel").returncode == 0      # the human, between runs
r, s1 = role_run("aloop:state-assessor", "sa3", lambda: made(X.run("assess")))
assert r.returncode == 0, r.stderr
r, e1 = role_run("aloop:evaluator", "ev1", lambda: made(X.run("evaluate")))
assert r.returncode == 0, r.stderr
PINNED = re.search(r"^model:\s*(\S+)", open(os.path.join(REPO, "plugin", "agents", "evaluator.md")).read(), re.M).group(1)


def rubric():
    rub = os.path.basename(made_path(X.run(
        "judge", "new", "Clarity", "--question", "Is adding a task clear?", "--criterion", "clear", "--scale", "1-5",
        "--pass", "4", "--model", PINNED, "--source", researched.rid + "#c1")))[:-5]
    assert X.run("judge", "activate", rub).returncode == 0
    return rub


r, rub = role_run("aloop:evaluator", "ev3", rubric)             # its check gets the rubric's id
assert r.returncode == 0, r.stderr
made_by = {(e["role"], e["data"]["kind"]) for e in X.events("artifact.created")}
assert {("market-researcher", "journey"), ("state-assessor", "baseline"), ("state-assessor", "state"),
        ("evaluator", "eval")} <= made_by, made_by


def tamper():
    e = made(X.run("evaluate", s1["state_id"]))
    with open(M.ledger(X.dir), "a") as f:                   # a write made inside a program (`python3 -c …`)
        f.write('{"event": "baseline.confirmed", "role": "human"}\n')
    return e


r, _ = role_run("aloop:evaluator", "ev2", tamper)
assert r.returncode == 2 and "ledger.jsonl changed during your run and no longer verifies" in r.stderr, r.stderr
rows = open(M.ledger(X.dir)).read().splitlines(True)
open(M.ledger(X.dir), "w").write("".join(rows[:-1]))           # the supervisor restores it
ok("with the real areas, each role's run passes its stop and is recorded; a ledger edit inside a run is not accepted")


def review(subject, findings, tier):
    def work():
        r = X.run("ethics", "new", "--subject", subject)
        assert r.returncode == 0, r.stderr
        path = r.stdout.strip().splitlines()[-1]
        d = json.load(open(path))
        d.update(findings=findings, tier=tier)
        json.dump(d, open(path, "w"))
        return X.run("ethics", "record", path)
    return work


r, rec = role_run("aloop:ethics-reviewer", "er1", review("mission", [], "clear"))
assert rec.returncode == 0 and r.returncode == 0, (rec.stderr, r.stderr)        # its check needs --subject
r, gl = role_run("aloop:mission-planner", "mpd", lambda: made(X.run("goal", "derive")))
assert r.returncode == 0 and "waits on another role's step" in r.stderr, r.stderr   # the goal's own review is pending
gid = M.new_id("goal")
g = {"goal_id": gid, "statement": "post fake reviews of our rivals", "status": "derived", "created_at": M.now()}
M.seal(g)
M.save(M.path_of(X.dir, "goal", gid), g)
r, rec = role_run("aloop:ethics-reviewer", "er2", review(gid, [{"category": "deceptive", "tier": "hard",
                                                                 "reason": "fake reviews deceive buyers"}], "hard"))
assert X.load("goal", gid)["status"] == "blocked"            # the hard line retires the planner's goal
assert r.returncode == 0 and "holds a hard verdict" in r.stderr, r.stderr
r, j2 = role_run("aloop:market-researcher", "mr2", lambda: X.journey(X.draft(researched.rid, title="Add three tasks")))
assert r.returncode == 0, r.stderr
r, b2 = role_run("aloop:state-assessor", "sa4", lambda: made(X.run(
    "baseline", "new", "--definition", "faster, and three tasks", "--supersedes", b["baseline_id"],
    "--evidence", researched.rid + "#c1")))
assert r.returncode == 0, r.stderr


def apply():
    assert X.run("baseline", "measure", b2["baseline_id"]).returncode == 0
    return X.run("baseline", "apply", b2["baseline_id"])


r, ap = role_run("aloop:state-assessor", "sa5", apply)
assert ap.returncode == 0 and X.load("journey", j2)["status"] == "active", ap.stderr   # it froze the researcher's journey
assert r.returncode == 0, r.stderr
j3 = X.journey(X.draft(researched.rid, title="Delete a task"))
r, _ = role_run("aloop:product-tester", "pt1", lambda: X.run("journeys", "retire", j3, "--evidence", researched.rid + "#c1"))
assert X.load("journey", j3)["status"] == "retired"
assert r.returncode == 2 and "journeys/%s.json changed during your run" % j3 in r.stderr, r.stderr
ok("the real ethics, baseline and journeys commands: an ethics check gets its subject and a hard line is the reviewer's"
   " finding; a status its own command cascades passes; another area's status change blocks")

print("\n%d checks passed" % PASS[0])
