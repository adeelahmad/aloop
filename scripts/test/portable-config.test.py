#!/usr/bin/env python3
"""ROADMAP §4.9: the portable project config. `.aladdin/config.yaml` replaces
docs/agents/defaults.md with a migration path: `config migrate` writes it from the current
settings, it is authoritative when present (Python readers and, through `config sync`, the
bash gates that parse defaults.md), `config check` validates it, and `aloop-init --apply`
keeps it true. Per-role budget profiles live in budgets.roles.
    python3 scripts/test/portable-config.test.py
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
T = tempfile.mkdtemp(prefix="agentic-config-")
ENV = dict(os.environ, HOME=os.path.join(T, "home"), AGENTIC_NO_AUTOINSTALL="1")
ENV.pop("AGENTIC_HOST", None)
PASS = [0]
C = os.path.join(BIN, "config")
YAML = os.path.join(T, ".aladdin", "config.yaml")
DEF = os.path.join(T, "docs", "agents", "defaults.md")
sys.path.insert(0, BIN)
_paths = __import__("_paths")


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def run(*args, inp=None, **env):
    return subprocess.run(args, cwd=T, env=dict(ENV, **env), capture_output=True, text=True, input=inp)


def setting(key):
    """What the bash gates see (defaults.md through _paths.sh)."""
    return run("bash", "-c", '. "%s/_paths.sh"; agentic_setting %s' % (BIN, key)).stdout.strip()


subprocess.run(["git", "init", "-q"], cwd=T, check=True)
os.makedirs(os.path.join(T, "docs", "agents"))

# 1) migrate: config.yaml from defaults.md (incl. a role profile), defaults.md re-rendered from it
open(DEF, "w").write("AGENTIC_TOKENS_HARD=300000\nAGENTIC_MODEL_WORKER=economical\n"
                     'AGENTIC_BUDGET_PLANNER="tokens_hard=800000 time_hard=90"\n')
r = run(C, "migrate")
assert r.returncode == 0 and "wrote .aladdin/config.yaml" in r.stdout and "rendered docs/agents/defaults.md" in r.stdout, r
y = open(YAML).read()
assert "schema: aladdin.config/1" in y and "  tokens_hard: 300000" in y and "  worker: economical" in y, y
assert "  roles:\n    planner:\n      tokens_hard: 800000\n      time_hard: 90" in y, y
assert "Rendered from `.aladdin/config.yaml`" in open(DEF).read()
assert setting("TOKENS_HARD") == "300000" and setting("MODEL_WORKER") == "economical" and setting("TIME_MODE") == "fixed"
assert 'AGENTIC_BUDGET_PLANNER="time_hard=90 tokens_hard=800000"' in open(DEF).read()
assert run(C, "migrate").returncode == 2
ok("config migrate: yaml from defaults.md (role profiles too); defaults.md rendered from it; no double migrate")

# 2) the yaml is authoritative: edit it, every reader follows after sync (SessionStart runs it)
edited = open(YAML).read().replace("  tokens_hard: 300000", "  tokens_hard: 210000") \
    .replace("  boundary: rotate", "  boundary: compact").replace("      time_hard: 90", "      time_hard: 45")
open(YAML, "w").write(edited)
assert _paths.settings(T)["TOKENS_HARD"] == 210000 and _paths.settings(T)["CONTEXT_BOUNDARY"] == "compact"
assert setting("TOKENS_HARD") == "300000"                       # bash readers wait for the render
run(os.path.join(BIN, "session-start"), inp=json.dumps({"hook_event_name": "SessionStart", "cwd": T, "source": "startup"}))
assert setting("TOKENS_HARD") == "210000" and setting("CONTEXT_BOUNDARY") == "compact"
b = run(os.path.join(BIN, "budget"), "resolve").stdout
assert "BUDGET_TOKENS_HARD=210000" in b and "BUDGET_TOKENS_SOFT=150000" in b, b
assert 'AGENTIC_BUDGET_PLANNER="time_hard=45 tokens_hard=800000"' in open(DEF).read()
assert run(C, "sync").returncode == 0 and run(C, "sync").stdout == ""   # idempotent
ok("config.yaml wins: Python readers at once, bash readers after `config sync` (SessionStart); idempotent")

# 3) aloop-init --apply keeps the yaml true instead of forking defaults.md
r = run(os.path.join(BIN, "aloop-init"), "--apply", "TIME_HARD_MIN=40")
assert r.returncode == 0 and "wrote .aladdin/config.yaml" in r.stdout, r
assert "  time_hard_min: 40" in open(YAML).read() and "  tokens_hard: 210000" in open(YAML).read()
assert "      time_hard: 45" in open(YAML).read()                    # role profiles survive
assert setting("TIME_HARD_MIN") == "40"
ok("aloop-init --apply writes the change into config.yaml and re-renders defaults.md")

# 4) check: unknown sections/keys, bad numbers and a bad schema are refused; sync refuses too
good = open(YAML).read()
open(YAML, "w").write(good.replace("schema: aladdin.config/1", "schema: aladdin.config/9") + "misc:\n  foo: 1\n")
r = run(C, "check")
assert r.returncode == 2 and "schema must be aladdin.config/1" in r.stderr and "unknown section 'misc'" in r.stderr, r.stderr
open(YAML, "w").write(good.replace("  tokens_hard: 210000", "  tokens_hard: lots"))
r = run(C, "check")
assert r.returncode == 2 and "budgets.tokens_hard: must be a whole number" in r.stderr, r.stderr
assert run(C, "sync").returncode == 2 and setting("TOKENS_HARD") == "210000"   # the rendered file is untouched
open(YAML, "w").write(good)
assert run(C, "check").returncode == 0
ok("config check refuses unknown sections/keys, bad numbers and schema; a bad yaml never reaches the gates")

# 5) show names the source of every setting
rows = {r["key"]: r for r in json.loads(run(C, "show", "--json").stdout)}
assert rows["AGENTIC_TOKENS_HARD"]["source"] == ".aladdin/config.yaml" and rows["AGENTIC_TOKENS_HARD"]["value"] == 210000
assert rows["AGENTIC_BUDGET_PLANNER"]["source"] == ".aladdin/config.yaml"
ok("config show: effective value and source per setting")

print("\n%d checks passed" % PASS[0])
