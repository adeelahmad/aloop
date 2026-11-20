#!/usr/bin/env python3
"""ROADMAP M12: the loop's user space. A project ejects the plugin's opinions (agents, skills, a kernel
reference) into git on its first run, changes them freely, keeps them across plugin upgrades, and can
never use them to weaken the kernel (the gates, the audit log).

Each check runs the real tools on a fresh git repo (no network, no tokens):
  eject on first session / first init, idempotent, opt-out, nothing outside an aloop project;
  status states; home > repo > plugin precedence; the Claude Code render and its log event;
  a role name the kernel has no gates for is never loaded; reference edits change nothing;
  a plugin upgrade (an edited copy of the plugin): untouched files follow, an edit is kept with
  <rel>.plugin-new, a clean three-way merge, a conflicting one, accept, reset;
  dispatch is steered to the customized copy; no worker, sub-agent or live-sprint supervisor writes
  user space (Write gate, diffscope, task-worktree merge); `.gitignore` keeps user space in git;
  `aloop install opencode` renders the customized copy.
    python3 scripts/test/userspace.test.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

PLUGIN = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(PLUGIN, "bin")
TMP = tempfile.mkdtemp(prefix="aloop-userspace-")
HOME = os.path.join(TMP, "home")
ENV = dict(os.environ, PATH=BIN + ":/usr/bin:/bin", AGENTIC_NO_AUTOINSTALL="1", HOME=HOME,
           ALOOP_HOME=os.path.join(HOME, ".aloop"),
           GIT_AUTHOR_NAME="A", GIT_AUTHOR_EMAIL="a@a", GIT_COMMITTER_NAME="A", GIT_COMMITTER_EMAIL="a@a")
for k in ("AGENTIC_HOST", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA", "AGENTIC_NO_EJECT", "ALADDIN_NO_EJECT",
          "SKIP_HOOKS", "AGENTIC_CWD"):
    ENV.pop(k, None)
FAILS = []
N = [0]


def ok(cond, what, detail=""):
    N[0] += 1
    print(("ok   " if cond else "FAIL ") + what + ("" if cond else "  :: " + str(detail)[:1500]))
    if not cond:
        FAILS.append(what)


def run(args, cwd, stdin=None, **env):
    return subprocess.run(args, cwd=cwd, env=dict(ENV, **env), input=stdin, capture_output=True, text=True)


def write(p, t):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(t)


def read(p):
    try:
        return open(p).read()
    except OSError:
        return None


def project(name, docs=True):
    d = os.path.join(TMP, name)
    os.makedirs(d)
    run(["git", "init", "-q", "-b", "main"], d)
    write(os.path.join(d, "README"), "x\n")
    if docs:
        os.makedirs(os.path.join(d, "docs", "agents"))
    run(["git", "add", "-A"], d)
    run(["git", "commit", "-qm", "init"], d)
    return d


def us(d, *args, bin_dir=BIN, **env):
    return run([os.path.join(bin_dir, "userspace")] + list(args), d, **env)


def status(d, bin_dir=BIN):
    r = us(d, "status", "--json", bin_dir=bin_dir)
    return {x["rel"]: x for x in json.loads(r.stdout)}


def session_start(d, bin_dir=BIN, **env):
    return run([os.path.join(bin_dir, "session-start")], d, stdin=json.dumps({"cwd": d, "source": "startup"}), **env)


ROLES = sorted(p[:-3] for p in os.listdir(os.path.join(PLUGIN, "agents")) if p.endswith(".md"))
GATES = sorted(p for p in os.listdir(BIN) if p.startswith("gate-"))

# 1) the first session of an aloop project ejects; a second one writes nothing; outside one, nothing
P = project("p1")
r = session_start(P)
U = os.path.join(P, "docs", "agents", "userspace")
m = json.load(open(os.path.join(U, "manifest.json")))
ok(r.returncode == 0 and all(os.path.isfile(os.path.join(U, "agents", x + ".md")) for x in ROLES),
   "first session ejects every role's agent (%d)" % len(ROLES), r.stderr)
ok(os.path.isfile(os.path.join(U, "skills", "aloop", "SKILL.md")) and
   os.path.isfile(os.path.join(U, "reference", "hooks", "hooks.json")) and
   all(os.path.isfile(os.path.join(U, "reference", "bin", g)) for g in GATES) and
   os.path.isfile(os.path.join(U, "reference", "pipeline", "execution", "00-red", "persona.md")),
   "it ejects the skills and a kernel reference (hooks.json, %d gates, the pipeline)" % len(GATES))
ok(m["plugin_version"] == json.load(open(os.path.join(PLUGIN, ".claude-plugin", "plugin.json")))["version"]
   and all(os.path.isfile(os.path.join(U, ".base", rel)) for rel in m["files"]),
   "the manifest pins the plugin version and every file has its base copy")
ok(read(os.path.join(U, "agents", "planner.md")) == read(os.path.join(PLUGIN, "agents", "planner.md")),
   "an ejected copy is the plugin's text")
before = {rel: os.path.getmtime(os.path.join(U, rel)) for rel in m["files"]}
session_start(P)
ok(all(os.path.getmtime(os.path.join(U, rel)) == t for rel, t in before.items()), "a second session rewrites nothing")
ok(all(x["state"] == "default" for x in status(P).values()), "every file reads default")
ok(not os.path.exists(os.path.join(P, ".claude", "agents")), "nothing customized: nothing rendered for Claude Code")
Q = project("p-optout")
session_start(Q, AGENTIC_NO_EJECT="1")
ok(not os.path.exists(os.path.join(Q, "docs", "agents", "userspace")), "AGENTIC_NO_EJECT=1 skips the eject")
X = project("not-aloop", docs=False)
session_start(X)
ok(not os.path.exists(os.path.join(X, "docs")), "a repo that is not an aloop project gets nothing")

# 1b) the first init ejects too
I = project("p-init", docs=False)
r = run([os.path.join(BIN, "aloop-init"), "--apply", "GITIGNORE=yes"], I)
ok(r.returncode == 0 and os.path.isfile(os.path.join(I, "docs", "agents", "userspace", "manifest.json")),
   "aloop init --apply ejects the user space", r.stdout + r.stderr)

# 2) customizing a role: status, the Claude Code render (name forced, marker), the log
pl = os.path.join(U, "agents", "planner.md")
write(pl, read(pl).replace("name: planner", "name: my-architect") + "\n## House rule\nPlan in Spanish.\n")
st = status(P)["agents/planner.md"]
ok(st["state"] == "customized" and st["layer"] == "repo", "an edited agent reads customized, repo layer", st)
r = us(P, "apply")
ca = read(os.path.join(P, ".claude", "agents", "planner.md")) or ""
ok("name: planner\n" in ca and "my-architect" not in ca and "Plan in Spanish." in ca and "aloop-userspace (rendered by" in ca,
   "the customized planner is rendered for Claude Code under the kernel's role name", ca[:400])
ok(sorted(os.listdir(os.path.join(P, ".claude", "agents"))) == ["planner.md"], "only the customized role is rendered")
ev = [json.loads(l) for l in open(os.path.join(P, ".aladdin", "logs", "events.jsonl"))]
ap = [e for e in ev if e["event"] == "userspace.applied"]
ok(ap and any(o.startswith("agents/planner.md=repo:") for o in ap[-1]["data"]["overrides"]),
   "apply logs which copy was used (userspace.applied names agents/planner.md=repo:<sha>)", ap[-1:] if ap else ev[-3:])
r = run([os.path.join(BIN, "events"), "emit", "userspace.applied", "--role", "x"], P)
ok(r.returncode != 0, "a forged userspace.applied is refused by `events emit`", r.stdout + r.stderr)
r = session_start(P)
ok("customizes the role(s) planner (repo)" in r.stdout, "the session context names the customized role", r.stdout[-600:])

# 3) home > repo > plugin
hd = us(P, "path", "--home").stdout.strip()
ok(hd.startswith(os.path.join(HOME, ".aloop", "projects", "p1-")), "the home layer is ~/.aloop/projects/<name>-<hash>", hd)
write(os.path.join(hd, "agents", "planner.md"), "---\nname: planner\ndescription: home copy\nmodel: x\n---\nHOME PLANNER\n")
ok(us(P, "which", "agents/planner.md").stdout.startswith("home "), "the home copy wins over the repo's")
us(P, "apply")
ca = read(os.path.join(P, ".claude", "agents", "planner.md")) or ""
ok("HOME PLANNER" in ca and "Plan in Spanish." not in ca, "the render uses the home copy")
ev = [json.loads(l) for l in open(os.path.join(P, ".aladdin", "logs", "events.jsonl")) if '"userspace.applied"' in l]
ok(any(o.startswith("agents/planner.md=home:") for o in ev[-1]["data"]["overrides"]), "and the log says home")
os.remove(os.path.join(hd, "agents", "planner.md"))
ok(us(P, "which", "agents/scaffolder.md").stdout.startswith("repo "), "an ejected, unedited file resolves to the repo copy")
os.remove(os.path.join(U, "agents", "scaffolder.md"))
ok(us(P, "which", "agents/scaffolder.md").stdout.startswith("plugin ") and
   status(P)["agents/scaffolder.md"]["state"] == "removed", "a deleted copy falls back to the plugin's (removed)")

# 4) a new role name is never loaded; a reference edit changes nothing
write(os.path.join(U, "agents", "yolo-worker.md"), "---\nname: yolo-worker\ndescription: d\n---\nno gates\n")
r = us(P, "apply")
ok(not os.path.exists(os.path.join(P, ".claude", "agents", "yolo-worker.md")) and "names no kernel role" in r.stdout,
   "an agent file for a role the kernel has no gates for is not loaded (and says so)", r.stdout)
gf = os.path.join(U, "reference", "bin", "gate-final")
write(gf, "#!/usr/bin/env bash\nexit 0\n")
st = status(P)["reference/bin/gate-final"]
ok(st["kind"] == "reference" and st["layer"] == "plugin", "an edited gate reference still resolves to the plugin's gate", st)
hooks = read(os.path.join(PLUGIN, "hooks", "hooks.json"))
ok("docs/agents/userspace" not in hooks and "userspace" not in hooks.replace('"${CLAUDE_PLUGIN_ROOT}/bin/session-start"', ""),
   "the hooks never run a project copy of a gate")
us(P, "reset", "reference/bin/gate-final")

# 5) plugin upgrades: an edited copy of the plugin stands in for the new release
NP = os.path.join(TMP, "plugin-next")
shutil.copytree(PLUGIN, NP, ignore=shutil.ignore_patterns("tools", "evals", "target", "node_modules"))
NB = os.path.join(NP, "bin")
U2P = project("p-upgrade")
session_start(U2P, bin_dir=BIN)
U2 = os.path.join(U2P, "docs", "agents", "userspace")
# the user edits red-worker's last line region and green-worker's first lines; leaves standards alone
red = os.path.join(U2, "agents", "red-worker.md")
write(red, read(red) + "\n## Ours\nAlways name tests after the story.\n")
grn = os.path.join(U2, "agents", "green-worker.md")
g = read(grn).split("\n")
g[1] = "name: green-worker   # ours"
write(grn, "\n".join(g))
# the new release changes standards (untouched here), red-worker far from the edit, green-worker on the edited line
def edit_plugin(rel, fn):
    p = os.path.join(NP, rel)
    write(p, fn(read(p)))
edit_plugin("agents/standards.md", lambda t: t + "\nNEW STANDARDS LINE\n")
edit_plugin("agents/red-worker.md", lambda t: t.replace("\n", "\nNEW RED TOP LINE\n", 6).replace("NEW RED TOP LINE\n", "", 5))
edit_plugin("agents/green-worker.md", lambda t: t.replace("name: green-worker", "name: green-worker   # theirs", 1))
write(os.path.join(NP, "skills", "brand-new", "SKILL.md"), "---\nname: brand-new\ndescription: d\n---\nnew\n")
r = us(U2P, "upgrade", bin_dir=NB)
st = status(U2P, bin_dir=NB)
ok("NEW STANDARDS LINE" in read(os.path.join(U2, "agents", "standards.md")) and st["agents/standards.md"]["state"] == "default",
   "an untouched file follows the plugin upgrade", r.stdout)
ok(os.path.isfile(os.path.join(U2, "skills", "brand-new", "SKILL.md")), "a file new in the release is ejected")
ok("Always name tests after the story." in read(red) and "NEW RED TOP LINE" not in read(red) and
   os.path.isfile(red + ".plugin-new") and st["agents/red-worker.md"]["state"] == "conflict",
   "an edited file the release changed is kept, with <rel>.plugin-new beside it", st["agents/red-worker.md"])
dd = us(U2P, "diff", "agents/red-worker.md", bin_dir=NB).stdout
ok("+NEW RED TOP LINE" in dd and "yours/agents/red-worker.md" in dd, "diff shows the plugin's change and yours against it")
r = us(U2P, "merge", "agents/red-worker.md", bin_dir=NB)
t = read(red)
ok(r.returncode == 0 and "NEW RED TOP LINE" in t and "Always name tests after the story." in t and
   not os.path.exists(red + ".plugin-new") and status(U2P, bin_dir=NB)["agents/red-worker.md"]["state"] == "customized",
   "a clean three-way merge keeps both changes and moves the base on", r.stdout + r.stderr)
r = us(U2P, "merge", "agents/green-worker.md", bin_dir=NB)
ok(r.returncode == 1 and "# ours" in read(grn) and "<<<<<<<" in (read(grn + ".merge") or ""),
   "a conflicting merge leaves your copy and writes <rel>.merge with the markers", r.stdout + r.stderr)
us(U2P, "accept", "agents/green-worker.md", bin_dir=NB)
ok(status(U2P, bin_dir=NB)["agents/green-worker.md"]["state"] == "customized" and "# ours" in read(grn)
   and not os.path.exists(grn + ".merge"), "accept keeps your copy, resolved against the new text")
us(U2P, "reset", "agents/green-worker.md", bin_dir=NB)
ok(read(grn) == read(os.path.join(NP, "agents", "green-worker.md")) and
   status(U2P, bin_dir=NB)["agents/green-worker.md"]["state"] == "default", "reset restores the plugin's copy")
r = session_start(U2P, bin_dir=NB)
ok(r.returncode == 0, "a session on the new release runs clean after the merge", r.stderr)

# 6) dispatch is steered to the customized copy
os.makedirs(os.path.join(P, ".aladdin"), exist_ok=True)
def dispatch(sub, d=P):
    return run([os.path.join(BIN, "gate-dispatch")], d, stdin=json.dumps(
        {"tool_name": "Agent", "session_id": "s", "cwd": d,
         "tool_input": {"subagent_type": sub, "prompt": "plan", "model": "opus"}}))
r = dispatch("aloop:planner")
ok(r.returncode == 2 and "dispatch subagent_type `planner`" in r.stderr, "dispatching aloop:<role> of a customized role is refused", r.stderr)
r = dispatch("planner")
ok("this project customizes" not in r.stderr, "dispatching the bare, customized role passes that check", r.stderr)
r = dispatch("aloop:intake")
ok("this project customizes" not in r.stderr, "an uncustomized role dispatches as aloop:<role>", r.stderr)

# 7) nobody in a run writes user space
def scope(file, agent_type="", d=P, session="s1"):
    p = {"tool_name": "Write", "session_id": session, "cwd": d, "tool_input": {"file_path": file, "content": "x"}}
    if agent_type:
        p["agent_type"] = agent_type
    return run([os.path.join(BIN, "gate-supervisor-scope")], d, stdin=json.dumps(p))
r = scope(os.path.join(U, "agents", "red-worker.md"), "red-worker")
ok(r.returncode == 2 and "user space" in r.stderr, "a dispatched agent cannot write user space", r.stderr)
r = scope(os.path.join(hd, "agents", "red-worker.md"), "planner")
ok(r.returncode == 2, "nor the personal home layer", r.stderr)
r = scope(os.path.join(U, "agents", "red-worker.md"))
ok(r.returncode == 0, "the main session (the human's hands) may, between runs", r.stderr)
lock = os.path.join(P, ".aladdin", "state", "sprint.lock")
write(lock, "s1\n")
r = scope(os.path.join(U, "agents", "red-worker.md"))
ok(r.returncode == 2 and "sprint is live" in r.stderr, "the supervisor may not while a sprint is live", r.stderr)
os.remove(lock)

# 7b) a worker's diff and a task branch merge
W = project("p-worker")
run(["git", "add", "-A"], W)
session_start(W)
write(os.path.join(W, ".gitignore"), ".aladdin/\n.agentic/\ndocs/agents/*\n!docs/agents/userspace/\n")
run(["git", "add", "-A"], W)
run(["git", "commit", "-qm", "userspace"], W)
base = run(["git", "rev-parse", "HEAD"], W).stdout.strip()
sd = os.path.join(W, "docs", "agents", "sprint1", "s1-01-x")
write(os.path.join(sd, "targets.json"), json.dumps({"tasks": [{"id": "T1", "title": "t", "tests": [
    {"path": "tests/test_a.py", "fn": "test_a", "assert": "a"}]}], "targets": [
    {"change_ref": "C1", "task": "T1", "operation": "create", "path": "src/a.py", "planned_symbol": "a",
     "reason": "r", "evidence_refs": [], "constraints": []}]}))
write(os.path.join(W, "src", "a.py"), "def a():\n    return 1\n")
write(os.path.join(W, "docs", "agents", "userspace", "agents", "green-worker.md"), "---\nname: green-worker\n---\nskip tests\n")
r = run([os.path.join(BIN, "targets"), "diffscope", "T1", sd, "--base", base, "--root", W, "--role", "green"], W)
ok(r.returncode == 2 and "docs/agents/userspace/agents/green-worker.md" in r.stderr and "user space" in r.stderr,
   "a worker's diff that touches user space is blocked (not hidden as run data)", r.stdout + r.stderr)
run(["git", "checkout", "-qb", "task"], W)
run(["git", "add", "-A", "src", "docs/agents/userspace"], W)
run(["git", "commit", "-qm", "worker"], W)
run(["git", "checkout", "-q", "main"], W)
r = run([os.path.join(BIN, "task-worktree"), "merge", "S1-01-T1", "task"], W)
ok(r.returncode == 2 and "user space" in r.stderr and
   run(["git", "rev-parse", "HEAD"], W).stdout.strip() == base, "a task branch that touches user space is never merged", r.stderr)

# 8) .gitignore keeps the run's docs out and user space in
G = project("p-gi", docs=False)
write(os.path.join(G, ".gitignore"), "node_modules/\n# aloop (managed by aloop-init)\n.agentic/\n.transcripts/\ndocs/agents/\n")
r = run([os.path.join(BIN, "aloop-init"), "--apply", "GITIGNORE=yes"], G)
gi = read(os.path.join(G, ".gitignore"))
ok("docs/agents/*\n" in gi and "!docs/agents/userspace/\n" in gi and "\ndocs/agents/\n" not in gi and "node_modules/" in gi,
   "the managed pre-M12 docs/agents/ line becomes docs/agents/* + !docs/agents/userspace/", gi)
ci = lambda p: run(["git", "check-ignore", "-q", p], G).returncode == 0
ok(ci("docs/agents/sprint1/plan.md") and ci("docs/agents/defaults.md") and not ci("docs/agents/userspace/agents/planner.md"),
   "run docs stay ignored; user space is tracked")
run([os.path.join(BIN, "aloop-init"), "--ensure-gitignore"], G)
ok(read(os.path.join(G, ".gitignore")) == gi, "ensure-gitignore is idempotent")

# 9) other hosts render the customized copy
r = run([os.path.join(BIN, "install"), "opencode", "--project", P], P)
oc = read(os.path.join(P, ".opencode", "agents", "planner.md")) or ""
ok(r.returncode == 0 and "Plan in Spanish." in oc, "aloop install opencode renders the project's customized planner", r.stderr + oc[:300])
oi = read(os.path.join(P, ".opencode", "agents", "intake.md")) or ""
ok("Plan in Spanish." not in oi and oi, "and the plugin's copy for an uncustomized role")

if not FAILS: shutil.rmtree(TMP, ignore_errors=True)
else: print("kept", TMP)
print("\n%d/%d userspace checks passed" % (N[0] - len(FAILS), N[0]))
sys.exit(1 if FAILS else 0)
