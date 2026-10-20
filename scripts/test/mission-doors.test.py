#!/usr/bin/env python3
"""ROADMAP §4.12 (M9) M9.30: the one-way / two-way door rule, enforced. A two-way door (a
branch, a local edit, a test, a draft PR) is the agent's to decide: no question. A one-way door
(plugin/mission/doors.json: a push to the default branch, a release, a deploy, a secret, …) is
crossed only under the human's recorded authorization (`mission doors authorize`, a hash-chained
ledger row); without one the PreToolUse gate stops it with ASK[door] <id>. A wall (skipping the
hooks, forging a human record) is never crossed by an agent. While a run is live, a question to
the human must name a one-way door the gate stopped and the human has not authorized.
    python3 scripts/test/mission-doors.test.py
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "fixtures"))
from mission_fixture import Project, M, BIN  # noqa: E402

sys.path.insert(0, M.AREAS_DIR)
import _events  # noqa: E402
import doors as D  # noqa: E402

EVENTS = os.path.join(BIN, "events")
PASS = [0]
GENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def classify(p, cmd):
    return p.run("doors", "classify", "--bash", cmd)


def hook(p, tool, inp, session="s1", cwd=None, env=None):
    payload = {"tool_name": tool, "tool_input": inp, "cwd": cwd or p.dir, "session_id": session,
               "hook_event_name": "PreToolUse"}
    return subprocess.run([os.path.join(BIN, "mission"), "doors", "hook"], input=json.dumps(payload), cwd=cwd or p.dir,
                          env=dict(p.env, **(env or {})), capture_output=True, text=True)


def denied(r):
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny", out
    return out["permissionDecisionReason"]


p = Project(mode="autonomous")
subprocess.run(["git", "add", "-A"], cwd=p.dir, check=True)
subprocess.run(["git", "commit", "-qm", "seed"], cwd=p.dir, env=dict(p.env, **GENV), check=True)

# 1) the catalogue checks itself: every example classifies to its own door, every two-way example to none
r = p.run("doors", "check")
assert r.returncode == 0, r.stderr
cat = json.load(open(D.SOURCE))
draft = os.path.join(p.dir, "doors-draft.json")
bad = json.loads(json.dumps(cat))
bad["doors"][0]["examples"]["bash"] = ["git status"]
json.dump(bad, open(draft, "w"))
r = p.run("doors", "check", "--file", draft)
assert r.returncode == 2 and "does not classify to its door" in r.stderr, r.stderr
bad = json.loads(json.dumps(cat))
bad["doors"][0]["bash"][0]["args"] = "^push(("
json.dump(bad, open(draft, "w"))
r = p.run("doors", "check", "--file", draft)
assert r.returncode == 2 and "does not compile" in r.stderr, r.stderr
bad = json.loads(json.dumps(cat))
bad["two_way_examples"]["bash"].append("git push origin main")
json.dump(bad, open(draft, "w"))
assert "a two-way example" in p.run("doors", "check", "--file", draft).stderr
ok("doors check: the catalogue's own examples prove each door matches and each two-way example does not")

# 2) two-way: the agent decides, no question
for cmd in ("git switch -c f && pytest -q", "git push -u origin claude/x", "git push --dry-run origin main",
            'grep -rn "git push origin main" docs', 'git commit -m "never git push origin main"',
            "echo SKIP_HOOKS=1", "npm test", "gh pr create --draft --title t --body b"):
    r = classify(p, cmd)
    assert r.returncode == 0 and "two-way" in r.stderr, (cmd, r.returncode, r.stderr)
assert p.run("doors", "classify", "--tool", "Read").returncode == 0
assert p.run("doors", "classify", "--tool", "Write", "--path", "src/a.py").returncode == 0
ok("two-way doors pass with no question: branches, tests, draft PRs, and text that only mentions a door")

# 3) one-way: stopped with ASK[door] <id> and the exact line the human runs to authorize it
for cmd, door in (("git push origin main", "push-default-branch"), ("sudo env X=1 git push origin master", "push-default-branch"),
                  ('bash -c "git push origin main"', "push-default-branch"), ("ls && git push --force origin f", "force-push-or-delete-remote"),
                  ("git -C sub push origin HEAD:main", "push-default-branch"), ("gh pr merge 12 --squash", "push-default-branch"),
                  ("npm publish", "publish-release"), ("terraform apply -auto-approve", "production-deploy"),
                  ("gh secret set TOKEN", "credentials-and-secrets"), ("psql -c 'DROP TABLE users'", "destructive-db")):
    r = classify(p, cmd)
    assert r.returncode == 3 and "ASK[door] %s" % door in r.stderr, (cmd, r.returncode, r.stderr)
    assert "aloop mission doors authorize %s --by NAME --reason" % door in r.stderr, r.stderr
r = classify(p, "gh secret set TOKEN")
assert "--once %s" % D.action_sha("Bash", {"command": "gh secret set TOKEN"}) in r.stderr, r.stderr
r = p.run("doors", "classify", "--tool", "mcp__github__merge_pull_request", "--input", '{"pullNumber": 3}')
assert r.returncode == 3 and "ASK[door] push-default-branch" in r.stderr, r.stderr
r = p.run("doors", "classify", "--tool", "Write", "--path", ".claude/settings.json")
assert r.returncode == 3 and "ASK[door] change-agent-settings" in r.stderr, r.stderr
subprocess.run(["git", "branch", "-M", "main"], cwd=p.dir, check=True)
assert classify(p, "git push").returncode == 3                      # a bare push on the default branch
subprocess.run(["git", "switch", "-qc", "feature"], cwd=p.dir, check=True)
assert classify(p, "git push").returncode == 0                      # the same on a branch: two-way
subprocess.run(["git", "switch", "-q", "main"], cwd=p.dir, check=True)
ok("one-way doors stop with ASK[door] <id> and the authorize line (a once-door names the action's digest)")

# 4) walls: never crossed by an agent, and never authorized
for cmd, door in (("SKIP_HOOKS=1 aloop task-worktree merge S1-1-T1", "bypass-gate"), ("git commit --no-verify -m x", "bypass-gate"),
                  ("AGENTIC_NO_BUDGET=1 make test", "bypass-gate"),
                  ("aloop mission doors authorize push-default-branch --by me --reason r", "forge-human-record"),
                  ("echo '{}' >> docs/agents/mission/ledger.jsonl", "forge-human-record")):
    r = classify(p, cmd)
    assert r.returncode == 2 and "BLOCK[door] %s" % door in r.stderr, (cmd, r.returncode, r.stderr)
r = p.run("doors", "authorize", "bypass-gate", "--by", "Adeel", "--reason", "r")
assert r.returncode == 2 and "a wall" in r.stderr, r.stderr
r = p.run("doors", "classify", "--tool", "Edit", "--path", "docs/agents/mission/ledger.jsonl")
assert r.returncode == 2 and "forge-human-record" in r.stderr, r.stderr
ok("walls (skipping the gates, forging a human record) are blocked and cannot be authorized")

# 5) a standing authorization: the human's ledger row opens the door until it is revoked or expires
r = p.run("doors", "authorize", "push-default-branch", "--by", "Adeel", "--reason", "release train")
assert r.returncode == 0, r.stderr
rows = M.records(p.dir, "door.authorized", "door:push-default-branch", role="human")
assert len(rows) == 1 and rows[0]["data"]["by"] == "Adeel" and rows[0]["data"]["grant"] == "standing", rows
assert M.ledger_rows(p.dir)[1] == [] and rows[0]["prev"] == M.ledger_rows(p.dir)[0][-2]["hash"]
r = classify(p, "git push origin main")
assert r.returncode == 0 and "authorized by Adeel" in r.stderr, r.stderr
assert hook(p, "Bash", {"command": "git push origin main"}).stdout == ""        # crossed silently
assert p.run("doors", "revoke", "push-default-branch", "--by", "Adeel", "--reason", "train left").returncode == 0
assert classify(p, "git push origin main").returncode == 3
assert p.run("doors", "authorize", "push-default-branch", "--by", "Adeel", "--reason", "r",
             "--until", "2030-01-01").returncode == 0
sha = D.action_sha("Bash", {"command": "git push origin main"})
assert D.authorization(p.dir, D.door(p.dir, "push-default-branch"), sha, now="2029-06-01T00:00:00Z")
assert D.authorization(p.dir, D.door(p.dir, "push-default-branch"), sha, now="2099-01-01T00:00:00Z") is None
assert p.run("doors", "authorize", "push-default-branch", "--by", "Adeel", "--reason", "r", "--until", "soon").returncode == 64
ok("standing authorization: a human ledger row opens the door; revoke or expiry closes it again")

# 6) a once-authorization covers one exact action, once
r = p.run("doors", "authorize", "credentials-and-secrets", "--by", "Adeel", "--reason", "r")
assert r.returncode == 64 and "--once" in r.stderr, r.stderr
x = D.action_sha("Bash", {"command": "gh secret set X"})
assert p.run("doors", "authorize", "credentials-and-secrets", "--by", "Adeel", "--reason", "rotate", "--once", x).returncode == 0
assert hook(p, "Bash", {"command": "gh secret set X"}).stdout == ""
crossed = [e for e in p.events("door.crossed") if e["data"]["door"] == "credentials-and-secrets"]
assert len(crossed) == 1 and crossed[0]["data"]["ledger"] is True, crossed      # the standing push above is audited too
assert len(M.records(p.dir, "door.crossed", "door:credentials-and-secrets")) == 1
assert "ASK[door] credentials-and-secrets" in denied(hook(p, "Bash", {"command": "gh secret set X"}))   # used up
assert "ASK[door] credentials-and-secrets" in denied(hook(p, "Bash", {"command": "gh secret set Y"}))   # another action
ok("once authorization: exactly one action, consumed by its first crossing (door.crossed in the ledger)")

# 7) tamper and forgery: an edited ledger row, an emitted or stream-only authorization authorize nothing
lp = M.ledger(p.dir)
text = open(lp).read()
lines = text.splitlines(True)
k = next(i for i, l in enumerate(lines) if '"door:push-default-branch"' in l and '"until": "2030-01-01' in l)
row = json.loads(lines[k])
row["data"]["by"] = "Eve"                                         # an edited row keeps its old hash
lines[k] = json.dumps(row, sort_keys=True) + "\n"
open(lp, "w").write("".join(lines))
assert open(lp).read() != text
assert classify(p, "git push origin main").returncode == 3
r = p.run("doors", "check")
assert r.returncode == 2 and "edited" in r.stderr, r.stderr
open(lp, "w").write(text)
assert p.run("doors", "revoke", "publish-release", "--by", "Adeel", "--reason", "r").returncode == 0
assert p.run("doors", "authorize", "publish-release", "--by", "Adeel", "--reason", "r").returncode == 0
assert classify(p, "npm publish").returncode == 0
assert p.run("doors", "revoke", "publish-release", "--by", "Adeel", "--reason", "closed again").returncode == 0
assert classify(p, "npm publish").returncode == 3
cut = open(lp).read()
open(lp, "w").write("".join(cut.splitlines(True)[:-1]))     # the newest row (the revoke) cut off: the chain is intact
assert classify(p, "npm publish").returncode == 3, "a ledger that lost its revoke (the run log has it) opens nothing"
open(lp, "w").write(cut)
for ev in ("door.authorized", "door.revoked", "door.denied", "door.crossed", "baseline.vetoed"):
    r = subprocess.run([EVENTS, "emit", ev, "--data", "subject=door:publish-release"], cwd=p.dir, env=p.env,
                       capture_output=True, text=True)
    assert r.returncode == 64 and "only its own command" in r.stderr, (ev, r.stderr)
_events.emit(p.dir, "door.authorized", role="human", subject="door:publish-release", by="Adeel", grant="standing")
assert classify(p, "npm publish").returncode == 3
ok("an edited ledger row, a lost revoke, `events emit` and a stream-only record authorize nothing")

# 8) the hook: deny JSON for a one-way door, silence for two-way, fail closed on a broken payload
reason = denied(hook(p, "Bash", {"command": "npm publish"}))
assert "ASK[door] publish-release" in reason and "[door:publish-release]" in reason, reason
assert p.events("door.denied")[-1]["data"]["door"] == "publish-release"
assert hook(p, "Bash", {"command": "pytest -q"}).stdout == ""
assert hook(p, "Read", {"file_path": "docs/agents/mission/ledger.jsonl"}).stdout == ""
assert "forge-human-record" in denied(hook(p, "Write", {"file_path": os.path.join(p.dir, "docs/agents/mission/ledger.jsonl"), "content": "x"}))
assert "BLOCK[door] forge-human-record" in denied(hook(p, "Bash", {"command": "aloop mission doors authorize publish-release --by me --reason r"}))
assert "send-external-message" in denied(hook(p, "mcp__Gmail__send_message", {"to": "a@b.c"}))
assert hook(p, "mcp__github__merge_pull_request", {"pullNumber": 1}).stdout == ""   # authorized above (until 2030)
outside = os.path.join(p.dir, "home", "plain")
os.makedirs(outside)
subprocess.run(["git", "init", "-q"], cwd=outside, check=True)
assert hook(p, "Bash", {"command": "git push origin main"}, cwd=outside).stdout == ""   # not an aloop project
r = subprocess.run([os.path.join(BIN, "mission"), "doors", "hook"], input="not json", cwd=p.dir, env=p.env,
                   capture_output=True, text=True)
assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)                                # fails closed
wt = os.path.join(p.dir, "home", "wt")
subprocess.run(["git", "worktree", "add", "-q", wt, "-b", "task"], cwd=p.dir, check=True)
assert hook(p, "Bash", {"command": "git reset --hard"}, cwd=wt).stdout == ""             # a task worktree: two-way
assert "discard-local-work" in denied(hook(p, "Bash", {"command": "git reset --hard"}))   # the main tree: one-way
ok("hook: deny JSON for a one-way door (door.denied), silent for two-way and outside aloop, fails closed")

# 9) questions while a run is live (an approved autonomous mission): only at a one-way door the gate
#    stopped and the human has not authorized; a two-way question is refused (decide it)
def ask(text, session="s1"):
    return hook(p, "AskUserQuestion", {"questions": [{"question": text, "header": "h", "options": [], "multiSelect": False}]},
                session=session)


assert "two-way" in denied(ask("Should I name the helper parse_row or parse_line?"))
assert "no door.denied" in denied(ask("[door:production-deploy] May I deploy?"))         # the gate never stopped it
assert ask("[door:publish-release] May I publish 1.2.0? Recommended: yes").stdout == ""   # stopped above, so ask
assert "no door.denied" in denied(ask("[door:publish-release] May I publish?", session="s2"))   # another session
assert "unknown door" in denied(ask("[door:nope] ?"))
assert ask("[door:change-intent] The goal says X but the code needs Y: change the intent?").stdout == ""
assert p.run("doors", "authorize", "publish-release", "--by", "Adeel", "--reason", "ship it").returncode == 0
assert "already authorized by Adeel" in denied(ask("[door:publish-release] May I publish?"))
s = Project(mode="spoon-fed")
r = hook(s, "AskUserQuestion", {"questions": [{"question": "Which name do you prefer?"}]})
assert r.stdout == "", r.stdout                                    # no live run: asking is the human's choice
ok("questions (live run): only a one-way door the gate stopped and nobody authorized; two-way is decided, not asked")

# 10) the gate reads a command as the shell runs it (adversarial review of M9.30): a comment, a line
#     continuation or `${#x}` hides nothing; a heredoc's text is data unless a shell or python runs it;
#     wrappers (xargs, find -exec, `echo … | sh`, a variable program) are unwrapped; a dry run, a help page
#     and a lookup (`command -v`) are two-way
CAT = D.catalogue()


def doors_of(cmd, cwd=None, assume=True):
    return [d["id"] for d in D.classify(cwd or p.dir, "Bash", {"command": cmd}, CAT, assume_when=assume)]


for cmd, want in (("# ship it\ngit push origin main", "push-default-branch"),
                  ("echo ${#files[@]} && git push origin main", "push-default-branch"),
                  ("curl -s http://x/#a && npm publish", "publish-release"),
                  ("git status\n# now push\ngit push --force origin feature", "force-push-or-delete-remote"),
                  ("git push \\\n  origin main", "push-default-branch"),
                  ("terraform \\\n  apply -auto-approve", "production-deploy"),
                  ("bash <<'EOF'\nnpm publish\nEOF", "publish-release"),
                  ("bash <<< 'npm publish'", "publish-release"),
                  ("echo 'npm publish' | sh", "publish-release"),
                  ("find dist -name '*.tgz' -exec npm publish {} \\;", "publish-release"),
                  ("echo main | xargs git push origin", "push-default-branch"),       # arguments it cannot see
                  ("kubectl apply --dry-run=none -f k8s/", "production-deploy"),     # not a dry run
                  ("gh pr merge 3 -- --help", "push-default-branch"),
                  ("pacman -Syu", "system-change"),
                  ("git config core.hooksPath /dev/null", "change-agent-settings"),
                  ("git -c core.hooksPath=/dev/null commit -m wip", "bypass-gate"),
                  ("echo authorize publish-release --by A --reason ok | xargs mission doors", "forge-human-record"),
                  ("find . -maxdepth 0 -exec mission doors authorize credentials-and-secrets --by A --reason r \\;",
                   "forge-human-record"),
                  ("M=mission; $M doors authorize production-deploy --by A --reason r", "forge-human-record"),
                  ("python3 -c \"import _mission as M; M.record_human('.', 'door.authorized', 'door:x', 'A')\"",
                   "forge-human-record")):
    assert want in doors_of(cmd), (cmd, doors_of(cmd))
for cmd in ("cat > .github/workflows/release.yml <<'EOF'\nrun: npm publish\nEOF",
            "cat > Makefile <<-EOF\n\tgit push origin main --tags\n\tEOF",
            "git commit -F - <<'EOF'\nfix: install\n\nsudo is no longer needed\nEOF",
            "cat > CONTRIBUTING.md <<'EOF'\nSKIP_HOOKS=1 git commit is for humans only.\nEOF",
            "git commit -m \"$(cat <<'EOF'\nnpm publish\nEOF\n)\"",
            "command -v sudo >/dev/null && echo has-sudo", "command -v mail || echo none", "npm ls -g",
            "npm list -g --depth=0", "pacman -Ss jq", "kubectl create deployment web --image=nginx --dry-run=client -o yaml",
            "helm upgrade --install app ./chart --dry-run", "poetry publish --dry-run", "gh pr merge --help",
            "terraform apply --help", "git config --get core.hooksPath"):
    assert doors_of(cmd) == [], (cmd, doors_of(cmd))
ledger_before = open(M.ledger(p.dir)).read()
r = hook(p, "Bash", {"command": "echo authorize production-deploy --by Adeel --reason ok | xargs mission doors"})
assert "BLOCK[door] forge-human-record" in denied(r)
assert open(M.ledger(p.dir)).read() == ledger_before
r = subprocess.run([os.path.join(BIN, "mission"), "doors", "authorize", "production-deploy", "--by", "Adeel", "--reason",
                    "r"], cwd=p.dir, env=dict(p.env, CLAUDECODE="1"), capture_output=True, text=True)
assert r.returncode == 2 and "own terminal" in r.stderr, r.stderr                 # an agent's shell authorizes nothing
assert open(M.ledger(p.dir)).read() == ledger_before
ok("the gate reads commands as the shell does: comments, continuations, heredocs, wrappers; dry runs and lookups are two-way")

# 11) a `when` condition reads the directory the command acts on (`git -C DIR`, `cd DIR &&`), not the
#     hook's: a task worktree's own reset is two-way from anywhere; the main checkout's is one-way from
#     a worktree; a directory the gate cannot resolve counts as the main checkout
assert hook(p, "Bash", {"command": "git -C home/wt reset --hard"}).stdout == ""
assert hook(p, "Bash", {"command": "cd home/wt && git reset --hard HEAD~1"}).stdout == ""
assert "discard-local-work" in denied(hook(p, "Bash", {"command": "git -C %s reset --hard" % p.dir}, cwd=wt))
assert "discard-local-work" in denied(hook(p, "Bash", {"command": "cd %s && git reset --hard" % p.dir}, cwd=wt))
assert "discard-local-work" in denied(hook(p, "Bash", {"command": "cd \"$TOP\" && git reset --hard"}, cwd=wt))
assert hook(p, "Bash", {"command": "(cd %s && ls) && git reset --hard" % p.dir}, cwd=wt).stdout == ""   # a subshell's cd
assert "push-default-branch" not in doors_of("git -C home/wt push", assume=False)
assert "push-default-branch" in doors_of("git -C %s push" % p.dir, cwd=wt, assume=False)
assert "push-default-branch" not in doors_of("git push", cwd=wt, assume=False)
ok("when-conditions read the directory a command acts on (git -C, cd); an unresolved one counts")

# 12) the agent's settings are one-way wherever they live: ~/.claude, a worktree's own, a linked file
assert "change-agent-settings" in denied(hook(p, "Edit", {"file_path": "~/.claude/settings.json", "old_string": "a",
                                                           "new_string": "b"}))
assert "change-agent-settings" in denied(hook(p, "Write", {"file_path": os.path.join(wt, ".claude", "settings.local.json"),
                                                           "content": "{}"}, cwd=wt))
assert "forge-human-record" in denied(hook(p, "Write", {"file_path": os.path.join(wt, "docs/agents/mission/ledger.jsonl"),
                                                        "content": "{}"}, cwd=wt))
assert "send-external-message" in denied(hook(p, "mcp__slack__slack_post_message", {"channel_id": "C1", "text": "hi"}))
assert hook(p, "mcp__slack__slack_list_channels", {}).stdout == ""
ok("settings and the ledger are guarded by suffix (home, worktrees); real Slack send tools are one-way")

# 13) the newest decision is the newest by time, on any branch: a revoke recorded on main closes the door
#     on another checkout of the same run log, and a union merge that puts an older authorization last
#     does not reopen it
q = Project(mode="spoon-fed")
qenv = dict(q.env, **GENV)
subprocess.run(["git", "branch", "-M", "main"], cwd=q.dir, check=True)
subprocess.run(["git", "add", "-A"], cwd=q.dir, check=True)
subprocess.run(["git", "commit", "-qm", "seed"], cwd=q.dir, env=qenv, check=True)
assert q.run("doors", "authorize", "publish-release", "--by", "Adeel", "--reason", "ship").returncode == 0
subprocess.run(["git", "add", "-f", M.ledger(q.dir)], cwd=q.dir, check=True)
subprocess.run(["git", "commit", "-qm", "authorized"], cwd=q.dir, env=qenv, check=True)
assert q.run("doors", "revoke", "publish-release", "--by", "Adeel", "--reason", "freeze").returncode == 0
subprocess.run(["git", "commit", "-qam", "revoked"], cwd=q.dir, env=qenv, check=True)
assert classify(q, "npm publish").returncode == 3
subprocess.run(["git", "checkout", "-q", "HEAD~1"], cwd=q.dir, check=True)     # the ledger without the revoke
assert M.ledger_problems(q.dir) == [] and classify(q, "npm publish").returncode == 3
subprocess.run(["git", "checkout", "-q", "main"], cwd=q.dir, check=True)
lp = M.ledger(q.dir)
base = M.ledger_rows(q.dir)[0][-1]


def human_row(event, ts):
    row = {"event_id": event + ts, "ts": ts, "event": event, "role": "human", "prev": base["hash"],
           "data": {"subject": "door:production-deploy", "by": "Adeel", "reason": "r", "grant": "standing", "until": "",
                    "action_sha256": ""}}
    row["hash"] = M._row_hash(row)
    return json.dumps(row, sort_keys=True) + "\n"


merged = open(lp).read() + human_row("door.revoked", "2099-01-01T00:00:01Z") + human_row("door.authorized", "2099-01-01T00:00:00Z")
open(lp, "w").write(merged)                       # two branches' rows after a union merge: the revoke is newer
assert M.ledger_problems(q.dir) == [] and classify(q, "terraform apply").returncode == 3
ok("a revoke closes the door by time, on any branch of the checkout and after a union merge")

# 14) a broken ledger: the ASK says so, `doors check` fails, and the human's authorize says how to restore it
assert q.run("doors", "check").returncode == 0                    # the merged ledger itself is sound
q2 = Project(mode="spoon-fed")
assert q2.run("doors", "authorize", "publish-release", "--by", "Adeel", "--reason", "ship").returncode == 0
lp2 = M.ledger(q2.dir)
whole = open(lp2).read()
open(lp2, "w").write("".join(whole.splitlines(True)[:-1]))    # the authorization lost; the run log has it
r = classify(q2, "npm publish")
assert r.returncode == 3 and "ledger is broken" in r.stderr and "git checkout --" in r.stderr, r.stderr
r = q2.run("doors", "authorize", "publish-release", "--by", "Adeel", "--reason", "again")
assert r.returncode == 2 and "BLOCK[doors]" in r.stderr and "git checkout --" in r.stderr and "Traceback" not in r.stderr, r.stderr
r = q2.run("doors", "check")
assert r.returncode == 2 and "lost" in r.stderr, r.stderr
open(lp2, "w").write(whole)
ok("a broken ledger: the ASK names it, check fails, authorize blocks with the restore line (no traceback)")

# 15) a once-authorization is crossed once, even by two gates at the same moment
for i in range(4):
    cmd = "gh secret set K%d" % i
    assert q2.run("doors", "authorize", "credentials-and-secrets", "--by", "Adeel", "--reason", "rotate",
                  "--once", D.action_sha("Bash", {"command": cmd})).returncode == 0
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": q2.dir, "session_id": "s%d" % i})
    procs = [subprocess.Popen([os.path.join(BIN, "mission"), "doors", "hook"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, cwd=q2.dir, env=q2.env, text=True) for _ in range(2)]
    outs = [x.communicate(payload) for x in procs]
    assert [x.returncode for x in procs] == [0, 0], outs
    assert sorted(o for o, _ in outs)[0] == "" and "ASK[door]" in sorted(o for o, _ in outs)[1], outs
assert len(M.records(q2.dir, "door.crossed", "door:credentials-and-secrets")) == 4 and M.ledger_problems(q2.dir) == []
ok("a once-authorization is crossed once under concurrent gates (serialized), with no transient broken ledger")

# 16) questions: intake and planning are the human's (the plan's approval is asked); a sprint is live only
#     once its plan is approved, for the session holding its lock
s1 = Project(mode="spoon-fed")
plan_q = "Sprint 1 plan is ready (3 stories, 9 tasks). Approve and start execution?"


def ask_in(x, text, session="S1", env=None):
    return hook(x, "AskUserQuestion", {"questions": [{"question": text, "header": "plan", "options": []}]},
                session=session, env=env)


r = subprocess.run([os.path.join(BIN, "gate-supervisor-scope")], cwd=s1.dir, env=s1.env, capture_output=True, text=True,
                   input=json.dumps({"tool_name": "Write", "session_id": "S1", "cwd": s1.dir, "hook_event_name": "PreToolUse",
                                     "tool_input": {"file_path": os.path.join(s1.dir, "docs/agents/memory.md")}}))
assert r.returncode == 0, r.stderr
assert open(os.path.join(s1.dir, ".aladdin", "state", "sprint.lock")).readline().strip() == "S1"   # the lock is armed
assert ask_in(s1, plan_q).stdout == ""                                      # planning: the approval is asked
_events.emit(s1.dir, "plan.created", role="planner", sprint="S1")      # as plugin/bin/ids names it
_events.record_authority(s1.dir, "Adeel", "reviewed", "sprint1")            # the human approved the plan
assert "two-way" in denied(ask_in(s1, "parse_row or parse_line?"))           # executing: decide it
assert ask_in(s1, "parse_row or parse_line?", session="S2").stdout == ""      # another session's sprint
assert "two-way" in denied(ask_in(Project(mode="spoon-fed"), "parse_row or parse_line?",
                                  env={"AGENTIC_AUTONOMY_MODE": "autonomous"}))   # an autonomous run
ok("questions: planning and the plan approval are asked; live once the plan is approved, for the lock's session")

# 17) round 2 (M9.38): one shell reader (plugin/bin/_shellread.py, the scope gate's) reads the line, so an
#     operator that touches a word, a keyword, a group, a function body, a here-string, an IO-number
#     redirect, a quoted operator or a second find -exec hides nothing; a heredoc or a quote inside
#     "$(cat <<'EOF' … EOF)" is data; stdin reaches only the command it feeds; the directory follows
#     pushd/popd, sh -c, eval and a fed shell; and a pathological line is read in linear time
for cmd, want in (("V=$(cat VERSION); git push origin main", "push-default-branch"),
                  ("echo $(date);git push origin main", "push-default-branch"),
                  ("ls;(git push origin main)", "push-default-branch"),
                  ("ls&&(git push origin main)", "push-default-branch"),
                  ("(make test)&& git push origin main", "push-default-branch"),
                  ('for f in dist/*.tgz; do npm publish "$f"; done', "publish-release"),
                  ("if git diff --quiet; then git push origin main; fi", "push-default-branch"),
                  ("make test && { npm publish; }", "publish-release"),
                  ("f() { git push origin main; }; f", "push-default-branch"),
                  ("until git push origin main; do sleep 1; done", "push-default-branch"),
                  ("if true; then :; else git push origin main; fi", "push-default-branch"),
                  ("! git push origin main", "push-default-branch"),
                  ('cat <<< "hello"\nnpm publish', "publish-release"),
                  ('read -r a b <<< "$line"\nnpm publish', "publish-release"),
                  ("wc -l <<<hello\ngit push origin main", "push-default-branch"),
                  ("find dist \\( -name '*.tgz' -o -name '*.tar.gz' \\) -exec npm publish {} \\;", "publish-release"),
                  ("find dist -name '*.tgz' -exec echo {} \\; -exec npm publish {} \\;", "publish-release"),
                  ("find dist '(' -name '*.tgz' ')' -exec npm publish {} \\;", "publish-release"),
                  ("find . -maxdepth 0 \\( -name . \\) -exec aloop mission doors authorize x --by me --reason r \\;",
                   "forge-human-record"),
                  ("bash -e <<'EOF'\nnpm publish\nEOF", "publish-release"),
                  ("bash -s <<'EOF'\ngit push origin main\nEOF", "push-default-branch"),
                  ('echo "npm publish" | bash -s', "publish-release"),
                  ('bash -s <<< "npm publish"', "publish-release"),
                  ("echo 'npm publish' | sh -", "publish-release"),
                  ("cat <<'EOF' | bash\nnpm publish\nEOF", "publish-release"),
                  ("printf '%s\\n' 'npm publish' | sh", "publish-release"),
                  ("git checkout -- . 2>/dev/null", "discard-local-work"),
                  ("git restore . 2>&1", "discard-local-work"),
                  ("SKIP_HOOKS=1 bash <<'EOF'\nmake\nEOF", "bypass-gate"),
                  ("x=( $(npm publish) )", "publish-release"), ('local x=( "$(npm publish)" )', "publish-release"),
                  ("declare -a x=( `npm publish` ); echo ok", "publish-release"),
                  ("time -p -- npm publish", "publish-release"), ("sudo -u root -- git push origin main", "push-default-branch"),
                  ("sh -c -e 'npm publish'", "publish-release"), ("bash -o pipefail -c 'npm publish'", "publish-release"),
                  ("env -S 'npm publish'", "publish-release"), ("exec -a foo npm publish", "publish-release"),
                  ("sudo -s npm publish", "publish-release"), ("/usr/bin/time -f %e npm publish", "publish-release")):
    assert want in doors_of(cmd), (cmd, doors_of(cmd))
for cmd in ("git commit -m \"$(cat <<'EOF'\ndocs: say \"cd x; SKIP_HOOKS=1 make\" is a wall\nEOF\n)\"",
            "git commit -m \"$(cat <<'EOF'\nfeat: classify \"a | git push --force origin x\"\nEOF\n)\"",
            "gh pr create --draft --title t --body \"$(cat <<'EOF'\nThe gate now reads \"echo x; sudo apt-get install jq\" as a door\nEOF\n)\"",
            "git commit -m \"$(cat <<'EOF'\nfix: run \"make | npm publish\" no more\nEOF\n)\"",
            "BODY=$(cat <<'EOF'\n## Summary\n| cmd | door |\n| npm publish | publish-release |\nEOF\n)\ngh pr create --draft --title t --body \"$BODY\"",
            "git commit -m \"$(cat <<'EOF'\nDoor gate: \"git commit -n -m x\" is a wall\nEOF\n)\"",
            "NOTES=$(cat <<'EOF'\n1) npm publish happens in CI\nEOF\n)",
            "python3 -m pytest -q 2>&1 | tail -3; cat >> \"$STORY_DIR/output.md\" <<'EOF'\n## attempt 1\n- tests: record_human\nEOF",
            "python3 -m pytest -q; cat >> out.md <<'EOF'\nchecked door.revoked handling\nEOF",
            "cat >> \"$STORY_DIR/output.md\" <<'EOF' && python3 -m pytest -q\n## T1\nThe door.authorized row is checked.\nEOF",
            "cat > a.md <<'EOF' && bash\nsudo rm -rf /opt\nEOF",
            "x=$(( 1 << 2 ))\necho $x", "echo '(;)' && echo \"a && b\""):
    assert doors_of(cmd) == [], (cmd, doors_of(cmd))
assert "push-default-branch" in doors_of("git push origin 2>&1 | tail -3", assume=False)        # p is on main
assert "push-default-branch" in doors_of("git push origin >/dev/null 2>&1", assume=False)
assert "discard-local-work" in denied(hook(p, "Bash", {"command": "pushd home/wt; make; popd; git reset --hard"}))
assert "push-default-branch" in doors_of("pushd home/wt >/dev/null && popd >/dev/null && git push", assume=False)   # authorized in p
assert hook(p, "Bash", {"command": "pushd home/wt && git reset --hard"}).stdout == ""
for cmd in ("cd %s && sh -c 'git reset --hard'", "cd %s && eval 'git reset --hard'", "cd %s && bash <<'EOF'\ngit reset --hard\nEOF",
            "cd %s && bash <<< 'git reset --hard'", "cd %s && echo 'git reset --hard' | sh"):
    assert "discard-local-work" in denied(hook(p, "Bash", {"command": cmd % p.dir}, cwd=wt)), cmd
assert "push-default-branch" in doors_of("cd %s && bash -c 'git push'" % p.dir, cwd=wt, assume=False)
for cmd in ("cat " + "<<A " * 1000 + "\n" + "x\nA\n" * 1000 + "npm publish", "find -exec " * 20 + "npm publish \\;",
            "eval " * 30 + "npm publish", "echo " + "$(" * 400 + "x" + ")" * 400 + "; npm publish",
            "echo a" + "[" * 20000 + " b" + "{" * 20000 + "; npm publish"):
    t0 = __import__("time").time()
    try:
        got = doors_of(cmd)
    except ValueError:              # too deep to read: the hook fails closed (exit 2)
        got = ["unreadable"]
    assert __import__("time").time() - t0 < 3 and got, (len(cmd), got)
try:
    with D.deadline(0.2):                   # the hook's read deadline: past it the gate fails closed (exit 2)
        while True:
            pass
except TimeoutError:
    pass
else:
    raise AssertionError("no deadline")
ok("one shell reader: operators, keywords, here-strings, IO numbers, quoted operators, stdin, cd/pushd/popd; linear")

# 18) round 2: the catalogue names what crosses a door, not what mentions one: tests and reads that name
#     record_human, lint pipelines through xargs, test-runner -g, an app's own mission/ledger.jsonl, a
#     read-only `xargs … doors classify`, a dry run or help page of a secret command are two-way; a push of
#     HEAD on the default branch and erasing the run log (which closes doors) are not
for cmd in ("python3 -m pytest -q -k record_human", "python3 -m pytest scripts/test/mission-doors.test.py::test_record_human",
            "python -m pytest -k 'door.authorized or door.revoked'", "python3 -c \"import _mission as M; help(M.record_human)\"",
            "python3 -c \"import json,sys; [print(e['ts']) for e in map(json.loads, sys.stdin) if e['event'] == 'door.authorized']\" < docs/agents/mission/ledger.jsonl",
            "aloop events list --json | python3 -c \"import json,sys; print([e for e in json.load(sys.stdin) if e['event']=='door.revoked'])\"",
            "python3 <<< 'print(\"door.authorized\")'",
            "python3 - <<'EOF'\nimport json\nfor l in open('docs/agents/mission/ledger.jsonl'):\n    e = json.loads(l)\n    if e['event'] == 'door.authorized': print(e['data'])\nEOF",
            "git diff --name-only --diff-filter=d | xargs pnpm prettier --write", "git ls-files '*.ts' | xargs yarn eslint --fix",
            "git diff --name-only | xargs npm run lint --", "find src -name '*.ts' | xargs npm exec -- eslint",
            "ls packages | xargs -I{} pnpm --filter {} build", "parallel npm run test:{} ::: unit e2e",
            "npm test -- -g 'adds a task'", "npm run test:e2e -- -g login", "pnpm playwright test -g login",
            "yarn playwright test -g 'login works'", "yarn mocha -g parser", "npm exec -- mocha -g parser",
            "pnpm -r --filter app exec mocha -g x",
            "aloop mission doors list --json | jq -r '.doors[].id' | xargs -n1 aloop mission doors classify --tool",
            "echo 'npm publish' | xargs -0 aloop mission doors classify --bash",
            "printf '%s\\n' 'npm publish' 'git push origin main' | xargs -I{} aloop mission doors classify --bash {}",
            "kubectl create secret generic app --from-env-file=.env --dry-run=client -o yaml > k8s/secret.yaml",
            "gh secret set --help", "vault kv put --help", "op item get --help", "aws secretsmanager get-secret-value --help"):
    assert doors_of(cmd) == [], (cmd, doors_of(cmd))
for cmd, want in (("python3 - <<'EOF'\nimport _events\n_events.emit('.', 'door.authorized', role='human', subject='door:x')\nEOF",
                   "forge-human-record"),
                  ("python3 -c \"open('docs/agents/mission/ledger.jsonl','a').write('{\\\"event\\\": \\\"door.authorized\\\"}')\"",
                   "forge-human-record"),
                  ("printf 'authorize\\n' | xargs -I{} aloop mission doors {} x --by A --reason r", "forge-human-record"),
                  ("xargs -I % mission doors % publish-release --by A --reason r < f", "forge-human-record"),
                  ("npm install -g pnpm", "system-change"), ("npm -g install pnpm", "system-change"),
                  ("pnpm add -g pnpm", "system-change"), ("npm i -g", "system-change"),
                  ("kubectl create secret generic app --from-literal=a=b", "credentials-and-secrets"),
                  ("sed -i /door.revoked/d .aladdin/logs/events.jsonl", "forge-human-record"),
                  ("rm .aladdin/logs/events.jsonl", "forge-human-record"),
                  (": > .aladdin/logs/events.jsonl", "forge-human-record"),
                  ("rm -rf .aladdin/logs", "forge-human-record")):
    assert want in doors_of(cmd), (cmd, doors_of(cmd))
for path, want in (("src/mission/ledger.jsonl", []), (".aladdin/logs/events.jsonl", ["forge-human-record"])):
    assert [d["id"] for d in D.classify(p.dir, "Write", {"file_path": path}, CAT)] == want, path
assert "push-default-branch" in doors_of("git push origin HEAD", assume=False)          # p is on main
assert "push-default-branch" in doors_of("git push -u origin HEAD", assume=False)
assert "push-default-branch" in doors_of("git push origin @", assume=False)
subprocess.run(["git", "switch", "-q", "-c", "feat2"], cwd=p.dir, check=True)
assert doors_of("git push origin HEAD", assume=False) == []
subprocess.run(["git", "switch", "-q", "main"], cwd=p.dir, check=True)
ok("catalogue: reads and tests that mention a door, lint pipelines, -g greps, dry runs pass; push HEAD on main, run-log erasure stop")

# 19) round 2, state: a ledger row is written before its stream event and a reader reads the stream first,
#     so a gate never sees another writer's half-written record; the human's fresh decision is the newest
#     even after a merged future-dated row; a closed sprint or a new intake ends the live run
w = Project(mode="spoon-fed")
order = []
real_emit, real_rows, real_read = _events.emit, M.ledger_rows, _events.read


def spy_emit(cwd, event, *a, **k):
    order.append(any(r["event_id"] == k.get("event_id") for r in real_rows(cwd)[0]))
    return real_emit(cwd, event, *a, **k)


_events.emit = spy_emit
try:
    row = M.record(w.dir, "door.crossed", "door:publish-release", "harness", door="publish-release")
finally:
    _events.emit = real_emit
assert order == [True] and any(e["event_id"] == row["event_id"] for e in w.events("door.crossed")), order
late = {"done": False}


def write_between(fn):
    def wrapped(*a, **k):
        out = fn(*a, **k)
        if not late["done"]:
            late["done"] = True
            M.record(w.dir, "door.crossed", "door:publish-release", "harness", door="publish-release")
        return out
    return wrapped


M.ledger_rows, _events.read = write_between(real_rows), write_between(real_read)
try:
    assert M.ledger_problems(w.dir) == [], "a record written between the two reads is not a lost row"
finally:
    M.ledger_rows, _events.read = real_rows, real_read
assert M.ledger_problems(w.dir) == [] and len(M.records(w.dir, "door.crossed", "door:publish-release")) == 2
assert q.run("doors", "authorize", "production-deploy", "--by", "Adeel", "--reason", "re-open now").returncode == 0
assert classify(q, "terraform apply").returncode == 0, "the human's fresh authorize is newest, past a future-dated revoke"
assert ask_in(s1, "Sprint 1: parse_row or parse_line?").stdout != ""         # still executing: decide it
sprints = os.path.join(s1.dir, ".aladdin", "state", "sprints.json")
json.dump({"closed": [{"sprint": 1, "at": "2026-10-20T00:00:00Z", "dir": "docs/agents/sprint1"}]}, open(sprints, "w"))
assert ask_in(s1, "Sprint 2 intake: should exports include archived tasks? Recommended: no").stdout == ""
os.remove(sprints)
assert "two-way" in denied(ask_in(s1, "parse_row or parse_line?"))
_events.emit(s1.dir, "intent.received", role="human", text="exports")
assert ask_in(s1, "Intake: should exports include archived tasks? Recommended: no").stdout == ""
shutil.rmtree(w.dir, ignore_errors=True)
ok("state: ledger before stream, stream read first (no transient lost row); a fresh decision is newest; closed sprint ends live")

# 20) round 3: the shell as bash reads it ([[ … ]] then an operator, a heredoc's literal delimiter, a pipe
#     into a subshell, the last redirect of stdin, $(( … ) …) and (( … ) …) that are not arithmetic, case
#     patterns, comments and ${…} inside $( … )), a python fed code on stdin, the other entry points, paths
#     read where the command runs, branch switches earlier in the line, and the reads that only mention a door
R3 = (("[[ -n x ]]&&git push origin main", "push-default-branch"), ("[[ -d .git ]]; git push origin main", "push-default-branch"),
      ("[[ -n x ]];echo x >> docs/agents/mission/ledger.jsonl", "forge-human-record"),
      ("if [[ -f x ]]; then echo '{}' > .claude/settings.json; fi", "change-agent-settings"),
      ("[[ -n x ]]; SKIP_HOOKS=1 git commit -m x", "bypass-gate"), ("[[ -n x ]]|npm publish", "publish-release"),
      ("cat <<$X\nbody\n$X\ngit push origin main", "push-default-branch"), ("cat <<\"$X\"\nbody\n$X\nnpm publish", "publish-release"),
      ("cat <<*\nbody\n*\nnpm publish", "publish-release"), ("cat <<~\nbody\n~\nnpm publish", "publish-release"),
      ("cat <<${X}\nbody\n${X}\nnpm publish", "publish-release"), ("cat <<E{a,b}\nbody\nE{a,b}\nnpm publish", "publish-release"),
      ("cat <<$X >/dev/null\nbody\n$X\nSKIP_HOOKS=1 git commit -m x", "bypass-gate"),
      ("echo git push origin main | (bash)", "push-default-branch"), ("echo npm publish | ( bash )", "publish-release"),
      ("bash <<<'true' <<EOF\ngit push origin main\nEOF", "push-default-branch"),
      ("bash <<EOF 3<<<x\nnpm publish\nEOF", "publish-release"),
      ("python3 -c 'import sys; exec(sys.stdin.read())' <<'EOF'\nimport _mission as M\nM.record_human('.', 'door.authorized', "
       "'door:x', 'me')\nEOF", "forge-human-record"),
      ("python3 /dev/stdin <<'EOF'\nimport _mission as M\nM.record_human('.', 'door.authorized', 'door:x', 'me')\nEOF",
       "forge-human-record"),
      ("bash /dev/stdin <<'EOF'\nnpm publish\nEOF", "publish-release"),
      ("x=$((true) && npm publish)", "publish-release"), ("echo $((echo a); npm publish)", "publish-release"),
      ("((echo a); npm publish)", "publish-release"), ("((cd sub); git push origin main)", "push-default-branch"),
      ("x=$((npm publish) )", "publish-release"), ("((( 1 )) && npm publish)", "publish-release"),
      ("echo $(case x in x) npm publish;; esac)", "publish-release"),
      ("echo \"$(case x in x) npm publish;; esac)\"", "publish-release"),
      ("echo \"$(# )\nnpm publish\n)\"", "publish-release"), ("echo \"$(echo ${x:-)} ; npm publish)\"", "publish-release"),
      ("x=$(cat <<A <<B\nA); git push origin main", "push-default-branch"),
      ("x=$(cat <<A <<B\nA); mission doors authorize publish-release --by A --reason r", "forge-human-record"),
      ("env -u CLAUDECODE aladdin mission doors authorize push-default-branch --by me --reason r", "forge-human-record"),
      ("agentic mission doors authorize x --by me --reason r", "forge-human-record"),
      ("bash plugin/bin/aloop mission doors authorize x --by me --reason r", "forge-human-record"),
      ("python3 -u plugin/bin/mission doors authorize x --by me --reason r", "forge-human-record"),
      ("set -- a; for i do npm publish; done", "publish-release"), ("coproc P { npm publish; }; wait", "publish-release"),
      ("case a in a) ((1<<2));; esac\nnpm publish", "publish-release"), ("echo $[1<<2]\nnpm publish", "publish-release"),
      ("printf '%s %s\\n' npm publish | bash", "publish-release"), ("echo -e 'true\\nnpm publish' | bash", "publish-release"),
      ("echo 'npm publish' | tee /dev/null | bash", "publish-release"),
      ("python3 -c \"import _mission as M; f = M.record_human; f('.', 'door.authorized', 'door:x', 'me')\"", "forge-human-record"),
      ("python3 -c \"import _mission as M; getattr(M, 'record_human')('.', 'door.authorized', 'door:x', 'me')\"",
       "forge-human-record"),
      ("python3 -c \"from _mission import record_human as r; r('.', 'door.authorized', 'door:x', 'me')\"", "forge-human-record"),
      ("cd docs/agents && echo x >> mission/ledger.jsonl", "forge-human-record"),
      ("cd docs && echo x >> agents/mission/ledger.jsonl", "forge-human-record"),
      ("cd docs/agents && sed -i s/a/b/ mission/ledger.jsonl", "forge-human-record"),
      ("sed -i s/a/b/ .aladdin/worktrees/T1/docs/agents/mission/ledger.jsonl", "forge-human-record"),   # a worktree's ledger
      ("cd .aladdin/logs && rm events.jsonl", "forge-human-record"), ("cd .aladdin && rm logs/events.jsonl", "forge-human-record"),
      ("rm .aladdin/logs/*.jsonl", "forge-human-record"), ("rm -f .aladdin/logs/events.json*", "forge-human-record"),
      ("sed --in-place /door.revoked/d .aladdin/logs/events.jsonl", "forge-human-record"),
      ("sed --in-place s/a/b/ docs/agents/mission/ledger.jsonl", "forge-human-record"),
      ("rm -rf $PWD/.aladdin", "forge-human-record"), ("D=x; cd \"$D\" && rm events.jsonl", "forge-human-record"),
      ("npm -g --loglevel warn install pnpm", "system-change"), ("npm --prefix /usr/local -g install pnpm", "system-change"),
      ("npm --registry https://r.example -g i evil", "system-change"), ("pnpm --reporter silent add -g pnpm", "system-change"),
      ("npm -g ins pnpm", "system-change"), ("npm -g isnt pnpm", "system-change"), ("npm -g udpate", "system-change"),
      ("npm -g install-test pnpm", "system-change"), ("npm -g rebuild", "system-change"), ("npm -g dedupe", "system-change"),
      ("gh secret set TOKEN --body \"abc --help\"", "credentials-and-secrets"),
      ("kubectl create secret generic s --from-literal=\"k=v --dry-run\"", "credentials-and-secrets"),
      ("gcloud run deploy api --image x", "production-deploy"), ("gcloud --project p app deploy", "production-deploy"),
      ("echo npm publish | (true; bash)", "publish-release"), ("echo npm publish | (cd x && bash)", "publish-release"),
      ("perl -0777pi -e 's/a/b/' .aladdin/logs/events.jsonl", "forge-human-record"),
      ("python3 - <<'EOF'\nimport _events\nev = 'door.authorized'\n_events.emit('.', ev, role='human', subject='door:x')\nEOF",
       "forge-human-record"))
for cmd, want in R3:
    assert want in doors_of(cmd), (cmd, doors_of(cmd))
for cmd in ("perl -Mstrict -ne 'print if /door.denied/' .aladdin/logs/events.jsonl",
            "perl -Ilib -ne 'print if /door/' .aladdin/logs/events.jsonl",
            "gcloud deploy releases list --delivery-pipeline=web --region=us-central1",
            "gcloud functions describe deploy-notifier --region=us-central1",
            "rm -rf /tmp/fixture/.aladdin", "cd /tmp/fixture && rm -rf .aladdin", "mv .agentic .aladdin",
            "python3 - <<'EOF'\nimport json, sys\nfor l in open('.aladdin/logs/events.jsonl'):\n"
            "    if json.loads(l)['event'] in ('door.authorized', 'door.revoked'):\n        sys.stdout.write(l)\nEOF",
            "python3 - <<'EOF'\nauth = []\nfor l in open('docs/agents/mission/ledger.jsonl'):\n"
            "    if 'door.authorized' in l:\n        auth.append(l)\nprint(len(auth))\nEOF",
            "npm test -- -g 'install works'", "python3 -c \"import _mission as M; print(len(M.records('.', 'door.authorized')))\""):
    assert doors_of(cmd) == [], (cmd, doors_of(cmd))
body = "import json\nrows = []\n" + "rows.append({'id': 0, 'name': 'task 0', 'tags': ['a', 'b']})\n" * 4000
t0 = __import__("time").time()
assert doors_of("python3 - <<'EOF'\n" + body + "json.dump(rows, open('t.json', 'w'))\nEOF") == []
assert doors_of("python3 -c %s" % json.dumps(body)) == []
assert __import__("time").time() - t0 < 2                     # 240 KB of python twice: linear, far under the 8 s deadline
for cmd, want in (("git checkout -b fix/typo && git commit -qam 'fix: typo' && git push -u origin HEAD", []),   # p is on main
                  ("git switch -c feat/y && git push -u origin HEAD", []),
                  ("git worktree add ../feat-z -b feat/z && cd ../feat-z && git push -u origin HEAD", []),
                  ("git switch -c feat/y; git push -u origin HEAD", ["push-default-branch"]),   # the switch may fail
                  ("cd \"$(git rev-parse --show-toplevel)\" && git push -u origin HEAD", ["push-default-branch"])):
    assert doors_of(cmd, assume=False) == want, (cmd, doors_of(cmd, assume=False))
subprocess.run(["git", "switch", "-q", "-c", "feat3"], cwd=p.dir, check=True)
for cmd, want in (("cd \"$(git rev-parse --show-toplevel)\" && git push -u origin HEAD", []),
                  ("git -C \"$(git rev-parse --show-toplevel)\" push -u origin HEAD", []),
                  ("git checkout main && git merge --ff-only feat3 && git push", ["push-default-branch"])):
    assert doors_of(cmd, assume=False) == want, (cmd, doors_of(cmd, assume=False))
subprocess.run(["git", "switch", "-q", "main"], cwd=p.dir, check=True)
f = Project(mode="spoon-fed")
real = M.now
M.now = lambda: "2030-01-01T00:00:00Z"                         # a row from a branch whose clock ran ahead
M.record_human(f.dir, "door.revoked", D.subject("publish-release"), "A", "r")
M.now = real
M.record_human(f.dir, "door.authorized", D.subject("production-deploy"), "A", "r", grant="standing", until="",
               action_sha256="")                               # its ledger time is clamped to 2030
deploy = D.door(f.dir, "production-deploy")
assert D.authorization(f.dir, deploy, "") is not None
_events.emit(f.dir, "door.revoked", role="human", ledger=True, branch="other", subject=D.subject("production-deploy"),
             by="A", reason="r")                               # revoked on another branch: only the run log has it
assert D.authorization(f.dir, deploy, "") is None, "a later revoke on the run log's clock closes the door"
shutil.rmtree(f.dir, ignore_errors=True)
ok("round 3: [[ ]] and heredoc delimiters, subshell pipes, stdin order, arithmetic fallbacks, entry points, placed paths, "
   "branch switches; reads pass")

# 21) list and the area's docstring
r = p.run("doors", "list", "--json")
lst = json.loads(r.stdout)
pd = next(d for d in lst["doors"] if d["id"] == "publish-release")
assert pd["grant"] == "standing" and pd["authorized"]["by"] == "Adeel", pd
assert "doors" in p.run("--list").stdout.split() and "two-way" in p.run("doors", "--help").stdout
assert p.run("doors", "nope").returncode == 64
assert hashlib.sha256(b"bash\0git push origin main").hexdigest() == D.action_sha("Bash", {"command": "git  push origin   main"})
for x in (p, s, q, q2, s1):
    shutil.rmtree(x.dir, ignore_errors=True)
ok("list names each door's grant and live authorization; discovered as an area; usage errors are 64")

print("mission-doors: %d checks passed" % PASS[0])
