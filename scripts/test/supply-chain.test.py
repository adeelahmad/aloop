#!/usr/bin/env python3
"""ROADMAP M10.2: GREEN refuses a dependency added to a manifest that the approved plan does not name
(only plan text the human's grant pins counts, M0.9: the plan they approved, or under their autonomy
grant the plan as the planner's gate passed it), and a credential committed to the project blocks
GREEN (in the lines it added) and the final gate (anywhere). Package installs are the usual
supply-chain entry, and acceptance proved behaviour, not where the code came from. `deps added`
lists the new names per manifest (Cargo.toml, package.json, requirements*.txt, pyproject.toml,
go.mod); gate-green-verify blocks on any the story's plan (tasks.md, targets.json, plan.md,
validate.md, stories.md) leaves out. `secrets scan` finds fixed-shape credentials and prints where,
never the text.
    python3 scripts/test/supply-chain.test.py
"""
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures"))
from plan_fixture import approve_plan  # noqa: E402

BIN = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugin", "bin"))
T = os.path.realpath(tempfile.mkdtemp(prefix="aloop-deps-"))
atexit.register(shutil.rmtree, T, ignore_errors=True)
MAIN = os.path.join(T, "repo")
STORY = os.path.join(MAIN, "docs", "agents", "sprint1", "s1-01-fetch")
FAKE = os.path.join(T, "fakebin")
ENV = {k: v for k, v in os.environ.items()
       if k not in ("AGENTIC_HOST", "SKIP_HOOKS", "STORY_DIR", "AGENT_ROLE", "REPO_DIR", "BASE_REF", "SCOPE_GLOBS", "TEST_GLOBS")}
ENV.update(HOME=os.path.join(T, "home"), PYTHONDONTWRITEBYTECODE="1", PATH=FAKE + os.pathsep + os.environ["PATH"])
n = [0]


def ok(msg):
    n[0] += 1
    print("ok - " + msg)


def sh(*args, cwd=MAIN):
    r = subprocess.run(args, cwd=cwd, env=ENV, capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(text)


os.makedirs(FAKE)
write(os.path.join(FAKE, "pytest"), "#!/bin/sh\nexit 0\n")
os.chmod(os.path.join(FAKE, "pytest"), 0o755)
os.makedirs(MAIN)
sh("git", "init", "-q", "-b", "main")
sh("git", "config", "user.email", "ada@x.io")
sh("git", "config", "user.name", "Ada")
write(os.path.join(MAIN, "Cargo.toml"), '[package]\nname = "x"\n\n[dependencies]\nserde = "1"\n\n[dev-dependencies]\ntempfile = "3"\n')
write(os.path.join(MAIN, "package.json"), '{"name": "x", "dependencies": {"left-pad": "1"}}\n')
write(os.path.join(MAIN, "requirements.txt"), "# pinned\nrequests==2.31\n")
write(os.path.join(MAIN, "pyproject.toml"), '[project]\nname = "x"\ndependencies = [\n  "click>=8",\n]\n\n[tool.poetry.dependencies]\npython = "^3.9"\n')
write(os.path.join(MAIN, "go.mod"), "module x\n\nrequire (\n\tgithub.com/a/b v1.0.0\n)\n")
write(os.path.join(MAIN, "app.py"), "x = 1\n")
write(os.path.join(MAIN, ".gitignore"), ".aladdin/\n.agentic/\ndocs/agents/\n")
sh("git", "add", "-A")
sh("git", "commit", "-qm", "init")
DEPS = os.path.join(BIN, "deps")

# 1) `deps added` lists only what is new in each manifest
write(os.path.join(MAIN, "Cargo.toml"), '[package]\nname = "x"\n\n[dependencies]\nserde = "1"\nclap = { version = "4" }\n\n'
      '[dependencies.regex]\nversion = "1"\n\n[target.\'cfg(unix)\'.dependencies]\nlibc = "0.2"\n\n[dev-dependencies]\ntempfile = "3"\n')
write(os.path.join(MAIN, "package.json"), '{"name": "x", "dependencies": {"left-pad": "1"}, "devDependencies": {"evil-pkg": "0.0.1"}}\n')
write(os.path.join(MAIN, "requirements.txt"), "# pinned\nrequests==2.31\nPyYAML>=6  # config\n")
write(os.path.join(MAIN, "pyproject.toml"), '[project]\nname = "x"\ndependencies = [\n  "click>=8",\n  "rich",\n]\n\n'
      '[tool.poetry.dependencies]\npython = "^3.9"\nhttpx = "^0.27"\n')
write(os.path.join(MAIN, "go.mod"), "module x\n\nrequire (\n\tgithub.com/a/b v1.0.0\n\tgithub.com/c/d v0.1.0\n)\nrequire github.com/e/f v2.0.0\n")
r = sh(DEPS, "added", "--base", "HEAD")
got = sorted(r.stdout.split("\n")[:-1])
want = sorted(["Cargo.toml\tclap", "Cargo.toml\tregex", "Cargo.toml\tlibc", "package.json\tevil-pkg",
               "requirements.txt\tpyyaml", "pyproject.toml\trich", "pyproject.toml\thttpx",
               "go.mod\tgithub.com/c/d", "go.mod\tgithub.com/e/f"])
assert got == want, got
sh("git", "checkout", "-q", "--", ".")
assert sh(DEPS, "added", "--base", "HEAD").stdout == ""
ok("deps added lists the new names per manifest (Cargo, npm, pip, pyproject, go) and nothing on an unchanged tree")

# 2) GREEN: a dependency the plan does not name is refused; once the plan names it, it passes
write(os.path.join(STORY, "init.md"), "---\ntype: init\n---\n# init\n\n## S1-01-T1 · attempt 1 · supervisor · 2026-10-20T00:00:00Z\ncontract\n")
write(os.path.join(os.path.dirname(STORY), "standards.md"), "# standards\n\n## Gate matrix\n\n```\npytest -q\n```\n")
write(os.path.join(STORY, "tasks.md"), "---\ntype: tasks\nstory: S1-01\n---\n## T1 — fetch a page\nUse the standard library only.\n")
wt = os.path.join(T, "green")
sh("git", "worktree", "add", "-q", "-b", "agentic/green", wt)
write(os.path.join(wt, ".aladdin", "task.env"),
      "TASK_ID=S1-01-T1\nATTEMPT=1\nAGENT_ROLE=green-worker\nSTORY_DIR=%s\nBASE_REF=main\nSCOPE_GLOBS=app.py requirements.txt\n" % STORY)
write(os.path.join(wt, "app.py"), "import requests\nimport leftpadx\n")
write(os.path.join(wt, "requirements.txt"), "# pinned\nrequests==2.31\nleftpadx==0.1\n")
sh("git", "add", "-A", cwd=wt)
sh("git", "commit", "-qm", "green", cwd=wt)
write(os.path.join(STORY, "output.md"),
      "---\ntype: output\n---\n# output\n\n## S1-01-T1 · attempt 1 · green-worker · 2026-10-20T00:02:00Z\n"
      "### Summary\nwork\n### Result\n| Check | Status |\n|---|---|\n| tests/test_app.py::test_fetch | PASS |\nregressed=0\n### Next\nnext\n")


def green():
    return subprocess.run([os.path.join(BIN, "gate-green-verify")], cwd=wt, env=dict(ENV, REPO_DIR=wt),
                          capture_output=True, text=True, input="")


TASKS = "---\ntype: tasks\nstory: S1-01\n---\n## T1 — fetch a page\n%s\n"
r = green()
assert r.returncode == 2 and "leftpadx" in r.stderr and "requirements.txt" in r.stderr and "plan" in r.stderr, r.stderr
approve_plan(MAIN, ENV)  # the human approves the plan as it is: it does not name leftpadx
r = green()
assert r.returncode == 2 and "leftpadx" in r.stderr, r.stderr
ok("GREEN refuses a dependency the approved plan does not name")

# 2b) plan text counts only as the human approved it: naming the dependency after their approval does
#     not cover it (the changed file is named); their approval of the plan that names it does
write(os.path.join(STORY, "tasks.md"), TASKS % "Adds `leftpadx` for padding.")
r = green()
assert r.returncode == 2 and "leftpadx" in r.stderr and "s1-01-fetch/tasks.md" in r.stderr and "changed since" in r.stderr, r.stderr
approve_plan(MAIN, ENV)
assert green().returncode == 0
ok("a dependency named after the human's approval is refused; their approval of the plan that names it admits it")

# 2c) a re-plan after the approval leaves the approved text standing: another story's change, or this story's
#     tasks.md growing a task, does not undo the approved dependency; a dependency only the new text names is refused
write(os.path.join(os.path.dirname(STORY), "s1-02-other", "tasks.md"), "---\ntype: tasks\nstory: S1-02\n---\n## T1 — other\nlater\n")
assert green().returncode == 0
write(os.path.join(STORY, "tasks.md"), TASKS % "Adds `leftpadx` for padding.\n\n## T2 — more\nAdds `padx9` too.")
r = green()
assert r.returncode == 0, r.stderr
write(os.path.join(wt, "requirements.txt"), "# pinned\nrequests==2.31\nleftpadx==0.1\npadx9==1.0\n")
sh("git", "commit", "-qam", "green: padx9", cwd=wt)
r = green()
assert r.returncode == 2 and "padx9" in r.stderr and "s1-01-fetch/tasks.md" in r.stderr and "changed since" in r.stderr, r.stderr
sh("git", "reset", "-q", "--hard", "HEAD~1", cwd=wt)
write(os.path.join(STORY, "tasks.md"), TASKS % "Adds `leftpadx` for padding.")
ok("a re-plan after the approval keeps the approved text: its dependency stands, one only the new text names does not")

# 2d) only the files the approval pins are plan text: a STORY_DIR that is the sprint dir reads none of them
write(os.path.join(os.path.dirname(STORY), "tasks.md"), TASKS % "Adds `leftpadx` for padding.")
r = subprocess.run([DEPS, "unplanned", "--base", "main", "--story", os.path.dirname(STORY), "--root", wt],
                   env=ENV, capture_output=True, text=True)
assert r.returncode == 2 and "requirements.txt\tleftpadx" in r.stdout, r
os.remove(os.path.join(os.path.dirname(STORY), "tasks.md"))
ok("text outside the approved plan's files names no dependency (a sprint-dir STORY_DIR, an unpinned tasks.md)")

# 2e) under the human's autonomy grant the plan counts as the planner's gate passed it (gate-stage2-complete
#     records it on the planner's stop): before a pass nothing counts, and says so; a dependency named after the
#     pass is refused; neither a bare `ids gated`, a self-check nor `events emit` records the plan; the planner's
#     next pass (here naming it in stories.md) admits it
SPRINT = os.path.dirname(STORY)
PLANNER_STOP = json.dumps({"hook_event_name": "SubagentStop", "agent_type": "aloop:planner", "agent_id": "planner-1"})


def stage2(selfcheck=False):
    env = dict(ENV, AGENTIC_SELFCHECK="1") if selfcheck else ENV
    r = subprocess.run([os.path.join(BIN, "gate-stage2-complete"), SPRINT], cwd=MAIN, env=env,
                       capture_output=True, text=True, input=PLANNER_STOP)
    assert r.returncode == 0, r.stderr


write(os.path.join(SPRINT, "stories.md"), "---\ntype: stories\nsprint: 1\n---\n# Sprint 1\n\n- S1-01 (s1-01-fetch)\n- S1-02 (s1-02-other)\n")
write(os.path.join(SPRINT, "plan.md"), "---\ntype: sprint-plan\nsprint: 1\nstage: \"2\"\n---\n# Waves\n")
for sid, k in (("s1-01-fetch", 1), ("s1-02-other", 2)):
    write(os.path.join(SPRINT, sid, "validate.md"), "---\ntype: validate\nstory: S1-0%d\n---\n## T1\npytest -q\n" % k)
    write(os.path.join(SPRINT, sid, "plan.md"), "---\ntype: plan\nstory: S1-0%d\nstage: \"2\"\n---\n# Plan\n" % k)
approve_plan(MAIN, ENV, mode="autonomous")
r = green()
assert r.returncode == 2 and "leftpadx" in r.stderr and "S1 is autonomous, but no plan of it has passed the planner's gate yet" in r.stderr, r.stderr
stage2()
assert green().returncode == 0
write(os.path.join(wt, "requirements.txt"), "# pinned\nrequests==2.31\nleftpadx==0.1\npadx2==1.0\n")
sh("git", "commit", "-qam", "green: padx2", cwd=wt)
write(os.path.join(STORY, "tasks.md"), TASKS % "Adds `leftpadx` for padding and `padx2` for more.")
r = green()
assert r.returncode == 2 and "padx2" in r.stderr and "s1-01-fetch/tasks.md" in r.stderr and "changed since" in r.stderr, r.stderr
IDS = os.path.join(BIN, "ids")
for args, inp in ((["gated", SPRINT], ""), (["gated", SPRINT], json.dumps({"agent_type": "aloop:green-worker"}))):
    assert subprocess.run([IDS] + args, cwd=MAIN, env=ENV, input=inp, capture_output=True, text=True).returncode == 64
stage2(selfcheck=True)                                             # the supervisor's self-check is no verdict
forged = subprocess.run([os.path.join(BIN, "events"), "emit", "plan.gated", "--data", "sprint=S1"], cwd=MAIN, env=ENV,
                        capture_output=True, text=True)
assert forged.returncode != 0 and green().returncode == 2, forged
write(os.path.join(STORY, "tasks.md"), TASKS % "Adds `leftpadx` for padding.")
write(os.path.join(SPRINT, "stories.md"), "---\ntype: stories\nsprint: 1\n---\n# Sprint 1\n\n- S1-01 (s1-01-fetch), "
      "with `padx2`\n- S1-02 (s1-02-other)\n")
stage2()
r = green()
assert r.returncode == 0, r.stderr
ok("under an autonomy grant a dependency counts once the planner's gate passed the plan that names it, and only then")

# Fake credentials are assembled at run time so this file never holds one (push protection).
SECRETS = os.path.join(BIN, "secrets")
FAKE_KEYS = {
    "private key": "-----BEGIN RSA " + "PRIVATE KEY-----",
    "AWS access key id": "AK" + "IA" + "Q3EGRZ7XKPLMNB2Y",
    "GitHub token": "gh" + "p_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
    "Slack token": "xo" + "xb-" + "1234567890-abcdefghij",
    "Anthropic API key": "sk-" + "ant-" + "api03-" + "x" * 40,
}

# 3) `secrets scan`: each shape is found with its file and line, the text never printed; a marked
#    fixture line and an ordinary line pass; with --base only added lines count
write(os.path.join(MAIN, "conf.py"), "".join('k%d = "%s"\n' % (i, v) for i, v in enumerate(FAKE_KEYS.values())))
r = subprocess.run([SECRETS, "scan", "--root", MAIN], env=ENV, capture_output=True, text=True)
assert r.returncode == 2, r
assert sorted(r.stdout.split("\n")[:-1]) == sorted("conf.py:%d: %s" % (i + 1, k) for i, k in enumerate(FAKE_KEYS)), r.stdout
assert not any(v[8:] in r.stdout + r.stderr for v in FAKE_KEYS.values()), "the scan printed a secret"
write(os.path.join(MAIN, "conf.py"), 'k = "%s"  # aloop: allow-secret\ntoken = os.environ["API_TOKEN"]\nsk = "sk-learn"\n' % FAKE_KEYS["GitHub token"])
assert sh(SECRETS, "scan", "--root", MAIN).stdout == ""
write(os.path.join(MAIN, "conf.py"), 'k = "%s"\n' % FAKE_KEYS["AWS access key id"])
sh("git", "add", "conf.py")
sh("git", "commit", "-qm", "an old key")
sh("git", "tag", "before")
write(os.path.join(MAIN, "app.py"), "x = 1\ny = 2\n")
assert sh(SECRETS, "scan", "--base", "before", "--root", MAIN).stdout == ""
write(os.path.join(MAIN, "app.py"), "x = 1\ny = 2\nkey = '%s'\n" % FAKE_KEYS["Slack token"])
r = subprocess.run([SECRETS, "scan", "--base", "before", "--root", MAIN], env=ENV, capture_output=True, text=True)
assert r.returncode == 2 and r.stdout == "app.py:3: Slack token\n", r
ok("secrets scan finds each credential shape by file and line without printing it, honours the allow marker, and --base sees only added lines")

# 4) GREEN blocks a credential its own diff adds; the final gate blocks one anywhere in the project
sh("git", "checkout", "-q", "--", "app.py")
sh("git", "merge", "-q", "--no-edit", "main", cwd=wt)  # main moved in case 3: the old key is not GREEN's
write(os.path.join(wt, "app.py"), "import requests\nimport leftpadx\nKEY = '%s'\n" % FAKE_KEYS["Anthropic API key"])
sh("git", "commit", "-qam", "green: key", cwd=wt)
r = green()
assert r.returncode == 2 and "app.py:3: Anthropic API key" in r.stderr and "environment" in r.stderr, r.stderr
assert FAKE_KEYS["Anthropic API key"][8:] not in r.stderr
write(os.path.join(wt, "app.py"), "import os\nimport requests\nimport leftpadx\nKEY = os.environ['ANTHROPIC_API_KEY']\n")
sh("git", "commit", "-qam", "green: env", cwd=wt)
assert green().returncode == 0
final = subprocess.run([os.path.join(BIN, "gate-final")], cwd=MAIN, input="", capture_output=True, text=True,
                       env=dict(ENV, REPO_DIR=MAIN, AGENTIC_SELFCHECK="1", GATE_RUN_MATRIX="0", SPRINT_DIR=os.path.dirname(STORY)))
assert final.returncode == 2 and "conf.py:1: AWS access key id" in final.stderr, final.stderr
ok("GREEN blocks a credential its diff adds and passes once it reads the environment; the final gate blocks one anywhere")

print("\n%d checks passed" % n[0])
