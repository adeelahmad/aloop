#!/usr/bin/env python3
"""ROADMAP M10.2 (first half): GREEN refuses a dependency added to a manifest that the approved
plan does not name. Package installs are the usual supply-chain entry, and acceptance proved
behaviour, not where the code came from. `deps added` lists the new names per manifest
(Cargo.toml, package.json, requirements*.txt, pyproject.toml, go.mod); gate-green-verify blocks
on any the story's plan (tasks.md, targets.json, plan.md, validate.md, stories.md) leaves out.
    python3 scripts/test/supply-chain.test.py
"""
import atexit
import os
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
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


r = green()
assert r.returncode == 2 and "leftpadx" in r.stderr and "requirements.txt" in r.stderr and "plan" in r.stderr, r.stderr
write(os.path.join(STORY, "tasks.md"), "---\ntype: tasks\nstory: S1-01\n---\n## T1 — fetch a page\nAdds `leftpadx` for padding.\n")
r = green()
assert r.returncode == 0, r.stderr
ok("GREEN refuses a dependency the approved plan does not name, and accepts it once the plan names it")

print("\n%d checks passed" % n[0])
