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
import contextlib
import fcntl
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "fixtures"))
from mission_fixture import Project, M, BIN, as_human  # noqa: E402

sys.path.insert(0, M.AREAS_DIR)
import _events  # noqa: E402
import doors as D  # noqa: E402

EVENTS = os.path.join(BIN, "events")
PASS = [0]
GENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
for k in ("DSH_SHELL", "CODEX_THREAD_ID", "ALOOP_AGENT_SHELL"):
    os.environ.pop(k, None)     # the human's own terminal: no agent host's shell may authorize a door


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def classify(p, cmd):
    return p.run("doors", "classify", "--bash", cmd)


def hook(p, tool, inp, session="s1", cwd=None, env=None, **extra):
    payload = dict({"tool_name": tool, "tool_input": inp, "cwd": cwd or p.dir, "session_id": session,
                    "hook_event_name": "PreToolUse"}, **extra)
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
os.makedirs(os.path.join(s1.dir, "docs", "agents", "sprint1"), exist_ok=True)
r = as_human(lambda: subprocess.run([EVENTS, "authority", "--initiated-by", "Adeel", "--mode", "reviewed", "--scope",
                                     "sprint1", "--sprint", "1"], cwd=s1.dir, env=s1.env, capture_output=True, text=True),
             s1.dir, s1.env)                                         # the human approved the plan: their typed line (M0.9)
assert r.returncode == 0 and any(e.get("event") == "plan.approved" for e in _events.read(s1.dir)), r.stderr
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
except D.Deadline:
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

# 21) M9.31: the door rule on every host and role. Codex asks with request_user_input; a patch (Codex's
#     apply_patch command, OpenCode's patchText) is judged path by path as a Write; dsh hands an MCP tool
#     its input as one JSON string and OpenCode names it <server>_<tool>; OpenCode and dsh send a bound
#     worker's directory as tool_input.workdir; every agent host's shell is refused by authorize; the
#     role that hit a door is recorded and a worker reports the ASK; the whole decision has one deadline
def ask_as(tool, text):
    return hook(p, tool, {"questions": [{"id": "q", "header": "h", "question": text, "options": []}]})


for tool in ("request_user_input", "request_user_input_async"):
    assert "two-way" in denied(ask_as(tool, "Should I name the helper parse_row or parse_line?")), tool
    assert "unknown door" in denied(ask_as(tool, "[door:nope] ?")), tool
    assert ask_as(tool, "[door:change-intent] The goal says X but the code needs Y: change the intent?").stdout == ""
assert D.QUESTIONS == ("AskUserQuestion", "request_user_input", "request_user_input_async")
ok("questions: Codex's request_user_input is the question tool too (a two-way question is decided, not asked)")


def patch(*lines):
    return "*** Begin Patch\n" + "\n".join(lines) + "\n*** End Patch\n"


assert "ASK[door] change-agent-settings" in denied(hook(p, "apply_patch", {"command": patch("*** Add File: .claude/settings.json", "+{}")}))
assert "BLOCK[door] forge-human-record" in denied(hook(p, "apply_patch", {"patchText": patch(
    "*** Update File: docs/agents/mission/ledger.jsonl", "@@", "-a", "+b")}))
assert hook(p, "apply_patch", {"command": patch("*** Update File: src/x.py", "@@", "-a", "+b")}).stdout == ""
assert hook(p, "apply_patch", {"patchText": patch("*** Add File: src/y.py", "+*** Add File: .claude/settings.json")}).stdout == ""
assert "change-agent-settings" in denied(hook(p, "apply_patch", {"command": patch(
    "*** Update File: src/x.py", "*** Move to: .codex/config.toml", "@@", "-a", "+b")}))
assert "forge-human-record" in denied(hook(p, "apply_patch", {"command": patch(
    "*** Add File: src/z.py", "+x", "*** Delete File: %s" % os.path.join(wt, "docs/agents/mission/ledger.jsonl"))}))
ok("apply_patch (Codex command, OpenCode patchText): each added, updated, deleted or moved path is judged as a Write")


def ids(tool, inp, cwd=None):
    return [d["id"] for d in D.classify(cwd or p.dir, tool, inp, CAT)]


# a patch is read as loosely as a host parses it: any spacing around a header's words, an indented header
# (Codex trims each line); and apply_patch run through the shell (Codex's heredoc and argument forms,
# applypatch, after a cd, fed by a pipe) judges its paths as writes where it runs
for head in ("*** Add File:.claude/settings.json", "\t***  Add File:\t.claude/settings.json ",
             "  *** Update File: src/x.py\n  ***\tMove to:.codex/config.toml"):
    assert ids("apply_patch", {"command": patch(head, "+{}")}) == ["change-agent-settings"], head
settings = patch("*** Add File: .claude/settings.json", "+{}")
for cmd in ("apply_patch <<'EOF'\n%sEOF" % settings, "apply_patch '%s'" % settings, "applypatch <<'EOF'\n%sEOF" % settings,
            "cd .claude && apply_patch <<'EOF'\n%sEOF" % patch("*** Add File: settings.json", "+{}"),
            "printf '%%s' '%s' | apply_patch" % settings, "timeout 9 apply_patch <<< '%s'" % settings):
    assert ids("Bash", {"command": cmd}) == ["change-agent-settings"], (cmd, ids("Bash", {"command": cmd}))
assert ids("Bash", {"command": "apply_patch <<'EOF'\n%sEOF" % patch("*** Update File: src/x.py", "@@", "-a", "+b")}) == []
assert ids("Bash", {"command": "echo '%s' > notes.txt" % settings}) == []          # a patch written to a file runs nothing
ok("apply_patch: headers with any spacing or indent, and apply_patch run through the shell, judge their paths")

# a path through a symlink and then `..` is where the kernel lands (the link's target's parent), not where the
# words say; opencode.json is a door where OpenCode reads it (the project's, a worktree's, .opencode/, the user's)
os.makedirs(os.path.join(p.dir, ".claude", "sub"), exist_ok=True)
os.makedirs(os.path.join(p.dir, "src", "deep"), exist_ok=True)
os.symlink(os.path.join(p.dir, ".claude", "sub"), os.path.join(p.dir, "into-claude"))
os.symlink(os.path.join(p.dir, "src", "deep"), os.path.join(p.dir, "deep-link"))
for path in ("into-claude/../settings.json", os.path.join(p.dir, "into-claude/../settings.json"), "deep-link/../../opencode.json"):
    assert ids("Write", {"file_path": path if os.path.isabs(path) else os.path.join(p.dir, path)}) == ["change-agent-settings"], path
    assert ids("Bash", {"command": "echo {} > %s" % path}) == ["change-agent-settings"], path
for path in ("opencode.json", "home/worktrees/S1-01-T1/opencode.jsonc", ".opencode/opencode.json", "~/.config/opencode/opencode.json",
             os.path.join(p.dir, "opencode.json")):
    assert ids("Write", {"file_path": path}) == ["change-agent-settings"], path
for path in ("/tmp/opencode.json", "docs/examples/opencode.json", "src/opencode.json"):
    assert ids("Write", {"file_path": path}) == [], path
    assert ids("Bash", {"command": "echo {} > %s" % path}) == [], path
ok("paths: a symlink then `..` is resolved as the kernel does; opencode.json only where OpenCode reads it")

pushed = {"owner": "o", "repo": "r", "branch": "main", "files": [{"path": "a", "content": "b"}], "message": "m"}
dsh_push = {"arguments": json.dumps(pushed), "workdir": p.dir}
assert ids("mcp__github__push_files", dsh_push) == ids("mcp__github__push_files", pushed) == ["push-default-branch"]
assert ids("mcp__github__push_files", {"arguments": json.dumps(dict(pushed, branch="feat")), "workdir": p.dir}) == []
assert ids("mcp__github__push_files", {"arguments": "[1]"}) == ids("mcp__github__push_files", {"arguments": "{"}) == []
# an `arguments` string beside the real fields is one more field the tool gets, not the input: it hides nothing
spoof = dict(pushed, arguments=json.dumps(dict(pushed, branch="feat")))
assert ids("mcp__github__push_files", spoof) == ["push-default-branch"], ids("mcp__github__push_files", spoof)
fresh = Project()                                              # p authorized push-default-branch above
assert "ASK[door] push-default-branch" in denied(hook(fresh, "mcp__github__push_files", spoof))
# dsh's object shape keeps a model's own `arguments` key as the whole object: decoded once, never twice
whole = dict(spoof, arguments=json.dumps(spoof), workdir=fresh.dir)
assert "ASK[door] push-default-branch" in denied(hook(fresh, "mcp__github__push_files", whole))
shutil.rmtree(fresh.dir, ignore_errors=True)
nested = {"arguments": json.dumps({"arguments": json.dumps(pushed)}), "workdir": p.dir}   # the tool gets {arguments: "…"}
assert D.input_of("mcp__github__push_files", nested) == {"arguments": json.dumps(pushed)}
assert D.input_of("mcp__github__push_files", D.input_of("mcp__github__push_files", nested)) == {"arguments": json.dumps(pushed)}
shared = {"fileId": "f1", "role": "writer", "type": "anyone"}
r = denied(hook(p, "mcp__Google_Drive__share_file", {"arguments": json.dumps(shared), "workdir": p.dir}))
assert "ASK[door] visibility-or-sharing" in r and "--once %s" % D.action_sha("mcp__Google_Drive__share_file", shared) in r, r
for tool, inp, want in (("github_merge_pull_request", {"pullNumber": 1}, ["push-default-branch"]),
                        ("github_enable_pr_auto_merge", {}, ["push-default-branch"]),
                        ("github_push_files", pushed, ["push-default-branch"]), ("github_push_files", dict(pushed, branch="feat"), []),
                        ("Gmail_send_message", {}, ["send-external-message"]),
                        ("slack_slack_post_message", {}, ["send-external-message"]),
                        ("Google_Drive_share_file", {}, ["visibility-or-sharing"]),
                        ("github_create_pull_request", {}, []), ("slack_slack_list_channels", {}, []),
                        ("Gmail_search_threads", {}, []), ("slack_get_post_history", {}, []),
                        ("mcp__drive__get_share", {}, [])):
    assert ids(tool, inp) == want, (tool, ids(tool, inp))
ok("MCP: dsh's {arguments: JSON} input and OpenCode's <server>_<tool> names cross the same doors (and the same once digest)")

g = Project(mode="spoon-fed")
subprocess.run(["git", "branch", "-M", "main"], cwd=g.dir, check=True)
subprocess.run(["git", "add", "-A"], cwd=g.dir, check=True)
subprocess.run(["git", "commit", "-qm", "seed"], cwd=g.dir, env=dict(g.env, **GENV), check=True)
gwt = os.path.join(g.dir, "home", "feat-wt")
subprocess.run(["git", "worktree", "add", "-q", gwt, "-b", "feat"], cwd=g.dir, check=True)
push = {"command": "git push origin HEAD", "workdir": gwt}
assert "ASK[door] push-default-branch" in denied(hook(g, "Bash", push))      # Claude Code sends no workdir: ignored
for host in ("opencode", "dsh"):
    on = {"AGENTIC_HOST": host}
    assert hook(g, "Bash", push, env=on).stdout == "", host                  # the worker's own branch: two-way
    assert hook(g, "Bash", {"command": "git reset --hard", "workdir": gwt}, env=on).stdout == "", host
    assert "push-default-branch" in denied(hook(g, "Bash", dict(push, workdir=g.dir), env=on))
    assert "discard-local-work" in denied(hook(g, "Bash", {"command": "git reset --hard", "workdir": g.dir}, env=on))
for wd in (os.path.join(g.dir, "gone"), "gone", 7):        # not there (unknown: assumed), not a path (the cwd decides)
    assert "push-default-branch" in denied(hook(g, "Bash", dict(push, workdir=wd), env={"AGENTIC_HOST": "dsh"})), wd
# a relative workdir is where the host runs the command: under the payload's cwd (OpenCode's bash resolves it so)
for host in ("opencode", "dsh"):
    on = {"AGENTIC_HOST": host}
    assert hook(g, "Bash", dict(push, workdir="home/feat-wt"), env=on).stdout == "", host
    assert hook(g, "Bash", dict(push, workdir="home/../home/feat-wt/"), env=on).stdout == "", host
    os.makedirs(os.path.join(g.dir, ".claude"), exist_ok=True)
    assert "change-agent-settings" in denied(hook(g, "Bash", {"command": "echo {} > settings.json", "workdir": ".claude"}, env=on))
    assert "push-default-branch" in denied(hook(g, "Bash", {"command": "git push origin HEAD", "workdir": "home/feat-wt/../.."},
                                                env=on))
assert "push-default-branch" in denied(hook(g, "Bash", push, env={"AGENTIC_HOST": "codex"}))
ok("OpenCode and dsh: a bound worker's tool_input.workdir is where its command runs (its branch, its worktree)")

ledger_before = open(M.ledger(p.dir)).read()
for var, val in (("DSH_SHELL", "1"), ("CODEX_THREAD_ID", "x"), ("ALOOP_AGENT_SHELL", "1"), ("CLAUDECODE", "1")):
    for verb in ("authorize", "revoke"):
        r = subprocess.run([os.path.join(BIN, "mission"), "doors", verb, "production-deploy", "--by", "Adeel", "--reason", "r"],
                           cwd=p.dir, env=dict(p.env, **{var: val}), capture_output=True, text=True)
        assert r.returncode == 2 and "own terminal" in r.stderr and var in r.stderr, (var, verb, r.returncode, r.stderr)
assert open(M.ledger(p.dir)).read() == ledger_before
assert D.AGENT_SHELL == ("CLAUDECODE", "DSH_SHELL", "CODEX_THREAD_ID", "ALOOP_AGENT_SHELL")
ok("authorize and revoke refuse every agent host's shell (Claude Code, dsh, Codex, OpenCode) and write nothing")

worker = {"agent_id": "a-7", "agent_type": "aloop:green-worker"}
r = denied(hook(p, "Bash", {"command": "terraform apply"}, **worker))
assert "ASK[door] production-deploy" in r and "report ASK[door] production-deploy" in r and "supervisor" in r, r
ev = p.events("door.denied")[-1]                                           # the event's own agent (ids.agent), its type
assert (ev["data"]["door"], ev["agent_id"], ev["ids"]["agent"], ev["data"]["agent_type"]) == \
    ("production-deploy", "a-7", "a-7", "aloop:green-worker"), ev
r = denied(hook(p, "Bash", {"command": "terraform apply"}))                  # the main thread asks the human itself
ev = p.events("door.denied")[-1]
assert "supervisor" not in r and ev["agent_id"] == "" and "agent_type" not in ev["data"], (r, ev)
assert "report ASK" not in denied(hook(p, "Bash", {"command": "git commit --no-verify -m x"}, **worker))   # a wall: nothing to ask
assert hook(p, "Bash", {"command": "git push origin main"}, **worker).stdout == ""      # standing, authorized above
ev = p.events("door.crossed")[-1]
assert (ev["data"]["door"], ev["agent_id"], ev["data"]["agent_type"]) == ("push-default-branch", "a-7", "aloop:green-worker"), ev
assert p.run("doors", "authorize", "credentials-and-secrets", "--by", "Adeel", "--reason", "rotate", "--once",
             D.action_sha("Bash", {"command": "gh secret set W"})).returncode == 0
assert hook(p, "Bash", {"command": "gh secret set W"}, **worker).stdout == ""
x = M.records(p.dir, "door.crossed", "door:credentials-and-secrets")[-1]["data"]
assert (x["agent_id"], x["agent_type"]) == ("a-7", "aloop:green-worker") and M.ledger_problems(p.dir) == [], x
ok("roles: door.denied and door.crossed name the agent; a worker is told to stop and report ASK[door] to its supervisor")

stopping = json.dumps({"tool_name": "Bash", "tool_input": {"command": "terraform apply"}, "cwd": p.dir, "session_id": "s1",
                       "hook_event_name": "PreToolUse"})


def hook_in(payload, seconds=1):
    """The hook in this process, with a short deadline: (exit, stdout, stderr, seconds taken)."""
    real, stdin = D.READ_SECONDS, sys.stdin
    D.READ_SECONDS, sys.stdin = seconds, io.StringIO(payload)
    out, err, t0 = io.StringIO(), io.StringIO(), time.time()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = D.main(["hook"], p.dir)
    finally:
        D.READ_SECONDS, sys.stdin = real, stdin
    return rc, out.getvalue(), err.getvalue(), time.time() - t0


t0 = time.time()
D.classify(p.dir, "Bash", {"command": "terraform apply"}, CAT)          # what runs before the lock, warmed and timed
before = time.time() - t0
held = os.open(os.path.join(D._paths.home(p.dir, "state"), "doors.lock"), os.O_RDWR | os.O_CREAT, 0o644)
fcntl.flock(held, fcntl.LOCK_EX)                     # another gate holds the lock (and never lets go)
watchdog = threading.Timer(10, fcntl.flock, (held, fcntl.LOCK_UN))     # so a gate that blocks fails here, not hangs
watchdog.start()
n = len(p.events("door.denied"))
wait = max(2, 4 * before)                            # the lock, not a slow runner's classify, runs the clock out
try:
    rc, out, err, took = hook_in(stopping, wait)
finally:
    watchdog.cancel()
    os.close(held)
assert rc == 2 and out == "" and "BLOCK[doors]" in err and "doors.lock" in err and took < wait + 2, (rc, out, err, took, before)
assert len(p.events("door.denied")) == n                                    # nothing decided, nothing recorded
rc, out, err, took = hook_in(stopping)
assert rc == 0 and "ASK[door] production-deploy" in out, (rc, out, err)
real_verdict = D.verdict
D.verdict = lambda *a, **k: time.sleep(10)          # a decision that hangs past classify
try:
    rc, out, err, took = hook_in(stopping)
finally:
    D.verdict = real_verdict
assert rc == 2 and out == "" and "BLOCK[doors]" in err and took < 3, (rc, out, err, took)


def swallowing(*a, **k):
    """A step that catches its own errors (a git call's OSError, a parse's ValueError) as `except Exception`."""
    try:
        time.sleep(10)
    except Exception:       # noqa: BLE001 — the deadline is not an Exception: this cannot turn a stall into an allow
        return 0, [], []
    raise AssertionError("slept past the deadline")


D.verdict = swallowing
try:
    rc, out, err, took = hook_in(stopping)
finally:
    D.verdict = real_verdict
assert rc == 2 and out == "" and "BLOCK[doors]" in err and took < 3, (rc, out, err, took)
ok("deadline: one deadline covers the whole decision; a held doors.lock or a hung verdict is exit 2 within it, never a hang")

for x in ("hosts/opencode/aloop.js", "hosts/dsh/aloop.mjs", "bin/host-adapter", "bin/install"):
    assert ids("Write", {"file_path": os.path.join(D.PLUGIN, x)}) == ["change-agent-settings"], x
    assert ids("apply_patch", {"command": patch("*** Update File: %s" % os.path.join(D.PLUGIN, x))}) == ["change-agent-settings"]
for x in (".codex/hooks.json", ".codex/config.toml", "~/.codex/agents/red-worker.toml", ".opencode/plugins/aloop.js",
          ".opencode/opencode.jsonc", "opencode.json", "~/.config/opencode/plugins/aloop.js",
          "~/.dsh/profiles/aloop/cordis.patch.yml", "~/.dsh/profiles/aloop/package.json"):
    assert "change-agent-settings" in denied(hook(p, "Write", {"file_path": x, "content": "{}"})), x
assert ids("Write", {"file_path": os.path.join(D.PLUGIN, "bin", "README.md")}) == []
for cmd in ("echo '{}' > opencode.json", "cat x >> .codex/hooks.json", "printf x | tee ~/.dsh/profiles/aloop/cordis.patch.yml"):
    assert "change-agent-settings" in doors_of(cmd), cmd
for cmd in ("cat .codex/config.toml", "jq . opencode.json", "cat ~/.dsh/profiles/aloop/cordis.patch.yml"):
    assert doors_of(cmd) == [], cmd
# a program that replaces, moves, links or removes a settings file changes it as a redirect does
for cmd in ("cp x .claude/settings.json", "mv /tmp/s .claude/settings.local.json", "ln -sf /tmp/h .codex/hooks.json",
            "install -m 644 x ~/.codex/config.toml", "rm -f .claude/settings.json", "rsync -a x/ .opencode/plugins/aloop.js",
            "sed -i 's/doors//' .claude/settings.json", "perl -pi -e 's/x//' opencode.json", "truncate -s 0 .codex/hooks.json",
            "cd .claude && cp ../x settings.json"):
    assert "change-agent-settings" in doors_of(cmd), (cmd, doors_of(cmd))
for cmd in ("cp src/a.py src/b.py", "sed 's/x//' .claude/settings.json", "rm -rf build", "ln -s x docs/opencode.json"):
    assert doors_of(cmd) == [], cmd
assert p.run("doors", "check").returncode == 0
ok("settings: every host's settings and hooks and aloop's own host adapters are one-way; reading them is two-way")

# 21b) the second M9.31 review. A redirect with no command word, or on a group, a subshell or a compound, is
#      theirs (its writes, its heredoc feeding every command inside)
force = "git push --force origin main"
for cmd in ("> .claude/settings.json", "{ echo '{}'; } > .claude/settings.json", "(echo '{}') > .claude/settings.json",
            "if true; then echo '{}'; fi > .claude/settings.json", "(apply_patch) <<'EOF'\n%sEOF" % settings,
            "{ apply_patch; } <<'EOF'\n%sEOF" % settings, "for x in a; do :; done >> .codex/hooks.json"):
    assert "change-agent-settings" in doors_of(cmd), (cmd, doors_of(cmd))
assert "forge-human-record" in doors_of("> docs/agents/mission/ledger.jsonl")
for cmd in ("(bash) <<'EOF'\n%s\nEOF" % force, "{ bash; } <<'EOF'\n%s\nEOF" % force, "if :; then sh; fi <<< '%s'" % force,
            "( (bash) ) <<'EOF'\n%s\nEOF" % force):
    assert {"push-default-branch", "force-push-or-delete-remote"} <= set(doors_of(cmd)), (cmd, doors_of(cmd))
for cmd in ("{ echo a; echo b; } > out.txt", "(cd src && make) > build.log 2>&1", "> build.log", "(cat) <<'EOF'\nhi\nEOF"):
    assert doors_of(cmd) == [], (cmd, doors_of(cmd))
ok("groups: a bare redirect, and a group's or compound's own redirect or heredoc, reach the commands inside")

# a `cd` that may not run (in an if, case or loop body, after && or ||) leaves the directory unknown after it;
# a directory is also read as the kernel walks it (git -C, cd -P and dsh's workdir follow a link before `..`)
other = tempfile.mkdtemp()                                   # a directory that is no checkout
for cmd in ("if false; then cd %s; fi; git push origin HEAD", "false && cd %s; git push origin HEAD",
            "[ -d /nope ] && cd %s || true; git push origin HEAD", "while false; do cd %s; done; git push origin HEAD",
            "case x in y) cd %s ;; esac; git push origin HEAD", "if false; then :; else cd %s; fi\ngit push origin HEAD"):
    assert "push-default-branch" in denied(hook(g, "Bash", {"command": cmd % other})), cmd
for cmd in ("cd %s && git push origin HEAD" % gwt, "test -d %s && cd %s && git push origin HEAD" % (gwt, gwt),
            "if true; then cd %s && git push origin HEAD; fi" % gwt, "cd %s; git push origin HEAD" % gwt):
    assert hook(g, "Bash", {"command": cmd}).stdout == "", cmd
os.makedirs(os.path.join(g.dir, "tools"), exist_ok=True)
os.symlink(os.path.join(g.dir, "tools"), os.path.join(gwt, "link"))
assert hook(g, "Bash", {"command": "git push origin HEAD"}, cwd=gwt).stdout == ""            # the worktree: its branch
for cmd in ("git -C link/.. push origin HEAD", "cd -P link/.. && git push origin HEAD", "git -C %s/link/.. push origin HEAD" % gwt):
    assert "push-default-branch" in denied(hook(g, "Bash", {"command": cmd}, cwd=gwt)), cmd
assert "discard-local-work" in denied(hook(g, "Bash", {"command": "git -C link/.. reset --hard"}, cwd=gwt))
assert "push-default-branch" in denied(hook(g, "Bash", {"command": "git push origin HEAD", "workdir": gwt + "/link/.."},
                                            env={"AGENTIC_HOST": "dsh"}))
ok("directories: a cd that may not run leaves the directory unknown; git -C, cd -P and a workdir walk a link first")

# a copy, move, link or install into a directory writes DIR/NAME; removing or moving a directory that holds
# settings, or a glob that names one, changes them; sed's abbreviated --in-place; a nested checkout's
# opencode.json and OpenCode's global config.json
os.makedirs(os.path.join(p.dir, ".opencode", "plugins"), exist_ok=True)
open(os.path.join(p.dir, ".opencode", "plugins", "aloop.js"), "w").write("//")
for cmd in ("cp /tmp/settings.json .claude/", "cp /tmp/settings.json .claude", "cp -t .claude /tmp/settings.json",
            "cp --target-directory=.claude /tmp/settings.json", "cp /tmp/opencode.json .", "mv /tmp/settings.json .claude/",
            "install -t .claude -m 644 /tmp/settings.json", "ln -s /tmp/settings.json .claude/", "rsync /tmp/settings.json .claude/",
            "rsync -a /tmp/conf/ .claude/", "cp -r /tmp/conf/. .opencode/plugins", "cp -S .bak x /tmp/y .codex/hooks.json",
            "rm -rf .opencode/plugins", "mv .opencode /tmp/off", "rm -rf .claude", "rm -rf ~/.config/opencode",
            "rm .opencode/plugins/*.js", "rm -f .opencode/plugins/*", "sed --in-pl=.bak 's/x//' .claude/settings.json",
            "sed --i 's/x//' .codex/hooks.json", "echo {} > ~/.config/opencode/config.json"):
    assert "change-agent-settings" in doors_of(cmd), (cmd, doors_of(cmd))
assert doors_of("echo {} > home/feat-wt/opencode.json", cwd=g.dir) == ["change-agent-settings"]
for cmd in ("cp .claude/settings.json /tmp/", "cp .claude/settings.json /tmp/backup.json", "cp src/a.py build/",
            "rsync -a .claude/ /tmp/backup/", "rm -f src/*.pyc", "rm -rf .opencode/node_modules", "mv src/a.py src/b.py",
            "cp -r docs/. /tmp/site", "sed --quiet -n p .claude/settings.json", "rm -rf docs/examples/.claude/tmp"):
    assert doors_of(cmd) == [], (cmd, doors_of(cmd))
assert "forge-human-record" in doors_of("sed --in-pla 's/a/b/' docs/agents/mission/ledger.jsonl")
for x in (os.path.join(gwt, "opencode.json"), "~/.config/opencode/config.json"):
    assert ids("Write", {"file_path": x}, cwd=g.dir) == ["change-agent-settings"], x     # this project's worktree
ok("settings: copies into a settings directory, a settings directory removed or moved, globs, sed --in-pl, nested checkouts")

# 21c) the third M9.31 review. A cd that may not run keeps both directories (where it started and where it
#      goes), not an unknown one: a settings write after it is still placed, an ordinary command after it is
#      not a wall, and an absolute cd brings the directory back
os.makedirs(os.path.join(p.dir, ".codex"), exist_ok=True)
open(os.path.join(p.dir, ".claude", "settings.json"), "w").write("{}")
for cmd in ("mkdir -p .claude && cd .claude; echo '{}' > settings.local.json", "{ cd .claude; }; echo '{}' > settings.json",
            "if [ -d .claude ]; then cd .claude; fi\ncp /tmp/s settings.json", "git fetch && cd .codex; echo x > hooks.json",
            "true && cd .claude; rm settings.json", "true && cd .codex; sed -i s/a/b/ hooks.json"):
    assert "change-agent-settings" in doors_of(cmd), (cmd, doors_of(cmd))
for cmd in ("mkdir -p out && cd out; python3 gen.py > events.jsonl", "test -d out || cd out; echo x > ledger.jsonl"):
    assert doors_of(cmd) == [], (cmd, doors_of(cmd))
os.makedirs(os.path.join(gwt, "web"), exist_ok=True)
for cmd in ("npm ci && cd web && npm run build; git push -u origin HEAD", "if [ -d web ]; then cd web; fi; npm test; git push",
            "{ cd web; npm test; }; git push origin HEAD", "git commit -m x && cd web && npm test; cd %s && git push -u origin HEAD" % gwt,
            "false && cd web; cd %s; git push origin HEAD" % gwt, "if [ -d web ]; then cd web; fi; cd %s; git push origin HEAD" % gwt):
    assert hook(g, "Bash", {"command": cmd}, cwd=gwt).stdout == "", cmd                # the worktree's own branch
# after `||` the cd may have been skipped for the rest of the chain; a subshell in the chain does not end it
# early; `cd -` and a bare pushd go back; popd -n moves nothing; `cd X &` and `cd ''` leave it; eval's cd counts
for cmd in ("true || cd %s && git push origin HEAD", "false && cd %s && (true); git push origin HEAD",
            "cd %s && git log -1 && cd - && git push origin HEAD", "cd %s; cd -- -; git push origin HEAD",
            "pushd %s; pushd; git push origin HEAD", "cd %s; pushd ..; popd -n; git push origin HEAD",
            "cd %s & git push origin HEAD", "cd %s; eval cd ..; git push origin HEAD"):
    assert "push-default-branch" in denied(hook(g, "Bash", {"command": cmd % gwt})), cmd
assert "push-default-branch" in denied(hook(g, "Bash", {"command": "cd '' && git push origin HEAD"}))
for cmd in ("GIT_DIR=%s/.git git push origin HEAD", "export GIT_DIR=%s/.git; git push origin HEAD",
            "env GIT_DIR=%s/.git git push origin HEAD", "GIT_WORK_TREE=%s git reset --hard"):
    assert denied(hook(g, "Bash", {"command": cmd % g.dir}, cwd=gwt)), cmd               # git's own dir is elsewhere
for cmd in ("cd /tmp & rm -rf .aladdin", "rm -rf ~+/.aladdin", "truncate -s0 ~+/.aladdin/logs/events.jsonl"):
    assert "forge-human-record" in doors_of(cmd), (cmd, doors_of(cmd))
assert "change-agent-settings" in doors_of("echo {} > ~+/opencode.json")
# a pipe into a { } group or a compound feeds each command inside; `source /dev/stdin` and more exec
# wrappers (builtin, setsid, flock, taskset, script -c) run what they are given
main = "git push origin main"
for cmd in ("echo '%s' | { true; bash; }" % main, "echo '%s' | if true; then bash; fi" % main,
            "printf 'x\\n%s\\n' | { read l; bash; }" % main, "source /dev/stdin <<< '%s'" % main,
            ". /dev/stdin <<'EOF'\n%s\nEOF" % main, "echo '%s' | source /dev/stdin" % main, "builtin eval '%s'" % main,
            "builtin source /dev/stdin <<< '%s'" % main, "setsid %s" % main, "flock /tmp/l %s" % main,
            "flock -w 5 /tmp/l %s" % main, "taskset -c 0 %s" % main, "taskset 0x1 %s" % main, "script -qc '%s' /dev/null" % main):
    assert "push-default-branch" in doors_of(cmd), (cmd, doors_of(cmd))
# nested groups each with their own heredoc: read once each, not once per enclosing group (no exponential blowup)
nest = "bash; bash"
for i in range(16):
    nest = "{ %s\n} <<E%d\n:\nE%d\n" % (nest, i, i)
t0 = time.time()
assert doors_of(nest) == [] and time.time() - t0 < 3, time.time() - t0
ok("directories: a cd that may not run keeps both; ||, subshells, cd -, pushd, popd -n, &, GIT_DIR, ~+; pipes into groups")

# copies: -t with sources the gate cannot see, --parents/-R, rsync --remove-source-files, links to a settings file,
# a home directory's contents, GNU option abbreviations and options after the operands
for cmd in ("find /tmp/evil -name settings.json -exec cp -t .claude {} +", "echo /tmp/evil/settings.json | xargs cp -t .claude",
            "cd /tmp/tpl && cp --parents .claude/settings.json %s/" % p.dir, "cd /tmp/tpl && rsync -R .claude/settings.json %s/" % p.dir,
            "rsync --remove-source-files .claude/settings.json /tmp/bak/", "ln -s .claude/settings.json s && echo '{}' > s",
            "ln .claude/settings.json alias.json", "cp -l .claude/settings.json alias.json", "cp -as $PWD/.claude/settings.json s.json",
            "cp --target=.claude /tmp/cfg/settings.json", "mv --targ=.claude /tmp/cfg/settings.json",
            "install --target=.claude /tmp/cfg/settings.json", "ln -s --target=.claude /tmp/cfg/settings.json",
            "cp /tmp/s .claude/settings.json --no-preserve mode", "cp /tmp/s .claude/settings.json --sparse always",
            "rsync -a /tmp/s .claude/settings.json --log-format %n", "cp -r /tmp/conf oc.d && mv oc.d .config/opencode"):
    assert "change-agent-settings" in doors_of(cmd), (cmd, doors_of(cmd))
for cmd in ("cp -a /tmp/dot/. ~/", "cp -a /tmp/dot/. ~", "rsync -a /tmp/dot/ ~/"):
    assert "change-agent-settings" in denied(hook(p, "Bash", {"command": cmd})), cmd
for cmd in ("cp notes.md .claude/", "cp -r .claude .claude.bak", "cp -r .claude/ /tmp/bak/", "cp -r .claude /tmp/claude-backup",
            "cp -r .opencode /tmp/oc", "rsync -a .claude /tmp/backup/", "ln -s ../shared/CLAUDE.md .claude/",
            "cp .claude/settings.json /tmp/x.json", "cp -r src/. build"):
    assert doors_of(cmd) == [], (cmd, doors_of(cmd))
# a glob is the shell's: through a link it reaches the link's target, and one that names more files than the gate
# lists asks (its directory is written); a quoted pattern is no glob
far = os.path.join(tempfile.mkdtemp(), ".claude")
os.makedirs(far)
open(os.path.join(far, "settings.json"), "w").write("{}")
os.symlink(far, os.path.join(p.dir, "h"))
for cmd in ("rm -f h/*", "rm -f h/s*", "echo {} > h/settings.j*", "sed -i s/a/b/ h/*.json"):
    assert "change-agent-settings" in doors_of(cmd), (cmd, doors_of(cmd))
many = tempfile.mkdtemp()
subprocess.run(["git", "init", "-q", many], check=True)
os.makedirs(os.path.join(many, ".claude"))
for i in range(D.GLOB_MAX + 100):
    open(os.path.join(many, ".claude", "f%04d" % i), "w").close()
open(os.path.join(many, ".claude", "settings.json"), "w").write("{}")
assert "change-agent-settings" in doors_of("rm -f .claude/*", cwd=many)
t0 = time.time()
assert doors_of("find / -path '/*/*/*/*/*/x' -prune") == [] and time.time() - t0 < 3, time.time() - t0
# another checkout's run home is not this project's; copying a tree into another checkout is no settings change here
demo = tempfile.mkdtemp()
subprocess.run(["git", "init", "-q", demo], check=True)
for cmd in ("rm -rf %s/.aladdin", "cd %s && rm -rf .aladdin", "truncate -s0 %s/.aladdin/logs/events.jsonl",
            "rsync -a --delete dist/ %s/", "cp -r dist/. %s"):
    assert doors_of(cmd % demo) == [], (cmd, doors_of(cmd % demo))
ok("copies: -t, --parents, -R, --remove-source-files, links, home, abbreviations; globs through links; backups pass")
shutil.rmtree(many, ignore_errors=True)
shutil.rmtree(demo, ignore_errors=True)
shutil.rmtree(os.path.dirname(far), ignore_errors=True)
os.unlink(os.path.join(p.dir, "h"))

# 21d) the fourth M9.31 review. A copy option that takes no value (rsync --checksum, install --strip) is no
#      prefix of one that does; a glob source names the files it matches now; a contents or glob copy into a
#      run-record directory forges it; ln -n replaces a linked settings directory; a name the gate cannot see,
#      copied into the project, a checkout or ~, is no settings change; trailing flags neither widen nor slow a copy
evil, fake = tempfile.mkdtemp(), tempfile.mkdtemp()
for d, n in ((evil, "settings.json"), (evil, "config.toml"), (fake, "events.jsonl"), (fake, "ledger.jsonl")):
    open(os.path.join(d, n), "w").write("{}")
for cmd in ("rsync -a --checksum E/settings.json .claude/settings.json", "rsync -a --partial E/ .codex/",
            "rsync -a --backup E/settings.json .claude/", "rsync -a --group E/settings.json .claude/settings.json",
            "install --strip E/settings.json .claude/settings.json", "cp E/*.json .claude/", "mv E/*.json .claude/",
            "cp -t .claude E/*.json", "install -m 644 E/*.json .claude/", "cp E/*.toml .codex/", "cp E/s* .claude/",
            "cp \"$f\" .claude/", "cd \"$REPO\" && rm -rf ./.codex", "cd \"$REPO\" && cp -r /tmp/x/. ./.claude/",
            "cd - && rm -rf ./.claude", "rm -rf \"$(pwd)/.claude\"", "rm -rf \"$REPO/.claude\"", "cd \"$REPO\" && mv /tmp/x ./.claude"):
    assert "change-agent-settings" in doors_of(cmd.replace("E/", evil + "/")), (cmd, doors_of(cmd.replace("E/", evil + "/")))
for cmd in ("rsync -a --compress F/events.jsonl .aladdin/logs/events.jsonl", "cp F/* .aladdin/logs/", "rsync -a F/ .aladdin/logs/",
            "cp -r F/. docs/agents/mission/", "cp -a F/. .aladdin/logs", "mv F/* docs/agents/mission/", "cp -t .aladdin/logs F/*",
            "cp F/*.jsonl docs/agents/mission/", "cp -r \"$SRC\"/. docs/agents/mission/", "cp \"$f\" .aladdin/logs/"):
    assert "forge-human-record" in doors_of(cmd.replace("F/", fake + "/")), (cmd, doors_of(cmd.replace("F/", fake + "/")))
lnr = tempfile.mkdtemp()
subprocess.run(["git", "init", "-q", lnr], check=True)
os.makedirs(os.path.join(lnr, "shared", "claude"))
os.symlink("shared/claude", os.path.join(lnr, ".claude"))
for cmd in ("ln -sfn ../evil/claude .claude", "ln -snf ../evil/claude .claude", "ln --no-dereference -sf ../evil/claude .claude"):
    assert "change-agent-settings" in doors_of(cmd, cwd=lnr), (cmd, doors_of(cmd, cwd=lnr))
assert doors_of("ln -sf ../notes/README.md .claude", cwd=lnr) == []                 # a link inside it, by its own name
for cmd in ("cp \"$f\" .", "cp \"$OUT/bundle.js\" ./", "mv \"$tmp\" .", "find /tmp/gen -name '*.py' -exec cp {} . \\;",
            "ls dist/*.whl | xargs cp -t .", "ls /tmp/gen/*.py | xargs -I{} cp {} .", "ln -s \"$(pwd)/scripts/hook.sh\" .",
            "cp ${BUILD}/app .", "install -m 755 \"$BIN\" .", "cp -r \"$SRC\" .", "cp \"$f\" ~/",
            "rsync -a .claude/settings.json .codex/config.toml host:/bak/ --progress",
            "rsync -a .claude/settings.json .codex/config.toml /tmp/bak/ --progress",
            "rsync -a package.json .claude/settings.json host:/bak/ -z", "cp README.md .claude/settings.json /tmp/bak/ -v"):
    assert doors_of(cmd) == [], (cmd, doors_of(cmd))
    assert doors_of(cmd, cwd=gwt) == [], (cmd, doors_of(cmd, cwd=gwt))             # a worktree's root too
files = " ".join("src/file%04d.py" % i for i in range(600))
for cmd in ("mv %s build/ -f" % files, "cp %s build/ -v" % files, "cp %s build/ --verbose" % files):
    t0 = time.time()
    assert doors_of(cmd) == [] and time.time() - t0 < 3, (cmd[:20], time.time() - t0)
assert "change-agent-settings" in doors_of("cp /tmp/s .claude/settings.json --unknown-flag x")   # a value it may take
ok("copies: no-value options, glob sources, record directories, ln -n over a link, unseen names, trailing flags")

# a `cd` after `||` keeps where the left side left it; OLDPWD follows a cd that may not run; `cd X || { …; exit; }`
# and `&& (…) &&` keep the list in X; a subshell keeps its OLDPWD; a wrapped cd (nohup, env, flock) moves no shell
gwt2 = os.path.join(g.dir, "home", "feat2-wt")
subprocess.run(["git", "worktree", "add", "-q", gwt2, "-b", "feat2"], cwd=g.dir, check=True)
R, W, W2 = g.dir, gwt, gwt2
for cmd, at, want in (
        ("cd R || cd W2; git push origin HEAD", W, "push-default-branch"),
        ("cd R 2>/dev/null || cd /tmp; git push origin HEAD", W, "push-default-branch"),
        ("cd R || cd W2 || exit 1; git push origin HEAD", W, "push-default-branch"),
        ("cd R || cd W2; git reset --hard", W, "discard-local-work"),
        ("cd W; [ -d nosuch ] && cd nosuch; cd -; git push origin HEAD", R, "push-default-branch"),
        ("cd W; if [ -d nosuch ]; then cd nosuch; fi; cd -; git push origin HEAD", R, "push-default-branch"),
        ("cd W\n[ -d nosuch ] && cd nosuch\ncd -\ngit push origin HEAD", R, "push-default-branch"),
        ("cd W || { git push origin HEAD; }", R, "push-default-branch"),            # the group runs where the cd failed
        ("cd W || { echo no; }; git push origin HEAD", R, "push-default-branch"),
        ("nohup cd /tmp; git push origin HEAD", R, "push-default-branch"), ("env cd /tmp; git push origin HEAD", R, "push-default-branch"),
        ("timeout 5 cd /tmp; git push origin HEAD", R, "push-default-branch"),
        ("flock /tmp/l cd /tmp; git push origin HEAD", R, "push-default-branch"),
        ("setsid cd /; git push origin HEAD", R, "push-default-branch")):
    cmd = cmd.replace("W2", W2).replace("R ", R + " ").replace("W;", W + ";").replace("W\n", W + "\n").replace("W ", W + " ")
    assert want in denied(hook(g, "Bash", {"command": cmd}, cwd=at)), cmd
for cmd, at in (("cd W 2>/dev/null || cd W2 && git push -u origin HEAD", R),
                ("cd W || { echo no worktree; exit 1; }; git push -u origin HEAD", R),
                ("cd W || {\n  echo no worktree >&2\n  exit 1\n}\ngit push -u origin HEAD", R),
                ("bash <<'SH'\ncd W || { echo no; exit 1; }\ngit push -u origin HEAD\nSH", R),
                ("cd W || { return 1; }; git push origin HEAD", R), ("cd W || { exit 1; }\ngit reset --hard origin/feat", R),
                ("git fetch && cd W && (make test) && git push -u origin HEAD", R),
                ("git fetch && cd W && (cd sub && make) && git push -u origin HEAD", R),
                ("git fetch && cd W && (make test) && git reset --hard origin/feat", R),
                ("cd R; (cd sub && make); cd -; git push origin HEAD", W), ("cd R; (cd sub; make); cd - >/dev/null; git push origin HEAD", W),
                ("builtin cd W; git push origin HEAD", R), ("time cd W; git push origin HEAD", R)):
    cmd = cmd.replace("W2", W2).replace("R;", R + ";").replace("W ", W + " ").replace("W;", W + ";")
    assert hook(g, "Bash", {"command": cmd}, cwd=at).stdout == "", cmd
# candidate directories are carried into nested reads, not multiplied by them
nest = "make test"
for i in range(5):
    nest = "[ -d a ] && cd a; [ -d b ] && cd b; [ -d c ] && cd c; echo \"$(%s)\"" % nest
t0 = time.time()
assert doors_of(nest) == [] and time.time() - t0 < 3, time.time() - t0
ok("directories: cd after ||, OLDPWD of a cd that may not run, || { exit; } guards, && (…) &&, wrapped cds")

for tool, inp, want in (("mcp__ms365__send-shared-mailbox-draft", {}, "send-external-message"),
                        ("mcp__gmail__send_saved_draft", {}, "send-external-message"),
                        ("mcp__outlook__send_existing_draft", {}, "send-external-message"),
                        ("mcp__workspace-mcp__manage_drive_access", {"action": "grant_batch"}, "visibility-or-sharing")):
    assert ids(tool, inp) == [want], (tool, ids(tool, inp))
for tool in ("mcp__slack__slack_send_message_draft", "mcp__gmail__create_draft", "mcp__outlook__create_mail_draft"):
    assert ids(tool, {}) == [], (tool, ids(tool, {}))
ok("MCP: a tool that sends a draft sends; one that writes a draft does not; Drive grant_batch shares")
# 21e) the fifth M9.31 review (of the fourth's fixes). Directories: an exit inside `|| { … }` that may not run
#      keeps the group's cd; past `&&` or a compound `|| Y` is reached from the success path too; a second cd that
#      may not run keeps where the list stopped; a background list (`cd X && make &`) moves no shell; find -execdir
#      runs in each match's directory; env -C moves its command
for cmd, at, want in (
        ("test -d .git || { cd R || exit 1; }; git push origin HEAD", W, "push-default-branch"),
        ("[ -f .ok ] || { cd R || exit 1; }; git reset --hard", W, "discard-local-work"),
        ("cd R || echo staying && git pull --ff-only || { git stash && git reset --hard origin/main; }", W, "discard-local-work"),
        ("cd R || echo staying && git pull --ff-only || ( git reset --hard origin/main )", W, "discard-local-work"),
        ("cd R || { echo no main; } && git pull || git reset --hard", W, "discard-local-work"),
        ("cd W2 || { cd R && false; } || git push origin HEAD", W, "push-default-branch"),
        ("cd R 2>/dev/null || cd W && npm ci && cd W2; git push origin HEAD", W, "push-default-branch"),
        ("find /tmp -maxdepth 0 -execdir git push origin HEAD \\;", W, "push-default-branch"),
        ("env -C R git push origin HEAD", W, "push-default-branch"), ("env --chdir=R git push origin HEAD", W, "push-default-branch"),
        ("cd W && make & git reset --hard", R, "discard-local-work"),
        ("cd W && npm run dev & sleep 1; git push origin HEAD", R, "push-default-branch"),
        ("cd W && (npm run dev) & sleep 1; git push origin HEAD", R, "push-default-branch")):
    cmd = cmd.replace("W2", W2).replace("R ", R + " ").replace("W ", W + " ")
    assert want in denied(hook(g, "Bash", {"command": cmd}, cwd=at)), cmd
for cmd, at in (("cd W || { echo no >&2; exit 1; }; git reset --hard", R), ("env -C W git reset --hard", R),
                ("cd W2 && npm run dev & sleep 1; git push origin HEAD", W),
                ("cd W2 2>/dev/null || cd W && npm ci && cd W2; git push origin HEAD", W)):
    cmd = cmd.replace("W2", W2).replace("W ", W + " ")
    assert hook(g, "Bash", {"command": cmd}, cwd=at).stdout == "", cmd
ok("directories: a conditional exit, || after &&, a second conditional cd, background lists, find -execdir, env -C")

# copies: `--` ends the options (no crash); a glob or contents copy also names what the line made before it, and a
# `*` names dotfiles (dotglob); cp -T and rsync -R `SRC/./` copy SRC's entries; a variable directory is a settings
# root only for $HOME (`cp -a . "$tmp"` passes); an unseen name into the ledger's directory forges it
for cmd in ("mv -- build/app.js dist/app.js", "cp -- build/app.js dist/", "cp -r -- build dist", "rsync -a -- src/ dist/",
            "ln -sf -- a b", "install -m 644 -- a b"):
    assert doors_of(cmd) == [], (cmd, doors_of(cmd))
assert doors_of("cp -- x .claude/settings.json") == ["change-agent-settings"]
os.makedirs(os.path.join(p.dir, "build"), exist_ok=True)
os.makedirs(os.path.join(p.dir, "dist"), exist_ok=True)
open(os.path.join(p.dir, "build", "app.js"), "w").write("//")
dot = tempfile.mkdtemp()
os.makedirs(os.path.join(dot, ".claude"))
for cmd in ("echo '{}' > build/settings.json && cp build/* .claude/", "echo '{}' > build/settings.json && cp -r build/. .claude",
            "echo '{}' > build/settings.json && rsync -a build/ .claude/", "cp E/settings.json dist/ && cp -r dist/. .claude/",
            "shopt -s dotglob; cp -r D/* ~/", "GLOBIGNORE=x; cp -r D/* .", "cp -rT D ~", "rsync -aR D/./ ~/",
            "cp \"$f\" \"$REPO/.claude/\"", "cp -r \"$SRC\"/. \"$HOME\"/.claude", "rm -rf \"${HOME:?}/.claude\"",
            "rm -rf \"${REPO:?}/.claude\"", "cp -a \"$SRC\"/. \"$(pwd)\"",
            "rsync -a E/settings.json .claude/settings.json" + " --stderr e" * 8):
    cmd = cmd.replace("E/", evil + "/").replace("D/", dot + "/").replace("D ", dot + " ")
    assert "change-agent-settings" in doors_of(cmd), (cmd, doors_of(cmd))
for cmd in ("cp -rT F .aladdin/logs", "rsync -aR F/./ .aladdin/logs/", "cp \"$f\" docs/agents/mission/",
            "mv \"$tmp\" docs/agents/mission/", "cp -t docs/agents/mission \"$f\"", "rm -f \"$(pwd)/.aladdin/logs/events.jsonl\"",
            "sed -i /door.revoked/d \"$REPO/.aladdin/logs/events.jsonl\"", "rm -rf \"$(pwd)/.aladdin\"",
            "rm -f \"${REPO:?}/.aladdin/logs/events.jsonl\"", "cd \"$X\" && sed -i /door.revoked/d P/.aladdin/logs/events.jsonl",
            "cd \"$X\" && rm -rf P/.aladdin"):
    cmd = cmd.replace("F/", fake + "/").replace("F ", fake + " ").replace("P/", p.dir + "/")
    assert "forge-human-record" in doors_of(cmd), (cmd, doors_of(cmd))
for cmd in ("cd \"$X\" && rm -rf P/.claude", "cd \"$(mktemp -d)\" && cp \"$f\" P/.claude/"):
    assert "change-agent-settings" in doors_of(cmd.replace("P/", p.dir + "/")), cmd
tree = tempfile.mkdtemp()
os.makedirs(os.path.join(tree, "plugin"))
open(os.path.join(tree, "Makefile"), "w").write("all:\n")
for cmd in ("echo x > build/v.txt && cp build/* .claude/", "echo x > build/v.txt && cp -r build/. .", "cp -r build/* dist/",
            "cp -rT build out", "mv -T build out", "ln -sfT build out", "rsync -aR src/./lib/ dist/",
            "tmp=$(mktemp -d) && cp -a . \"$tmp\" && cd \"$tmp\" && npm test", "cp -r . \"$TMPDIR\"", "cp -r .claude \"$tmp\"",
            "cp \"$f\" docs/agents/mission/research/", "rm -rf \"$(pwd)/dist\"", "rm -rf \"$OUT/.aladdin\"", "rm -rf \"${OUT:?}/dist\"",
            "cd \"$X\" && rm -rf /tmp/nonexistent-x/.aladdin", "cd \"$(mktemp -d)\" && cp -r TREE/. . && make test",
            "cd \"$(mktemp -d)\" && rsync -a --exclude .git TREE/ ./"):
    cmd = cmd.replace("TREE/", tree + "/")
    assert doors_of(cmd) == [], (cmd, doors_of(cmd))
# a large directory copied whole is placed by its directory once, not name by name
big = os.path.join(p.dir, "big")
os.makedirs(big, exist_ok=True)
for i in range(3000):
    open(os.path.join(big, "m%04d.js" % i), "w").close()
for cmd in ("cp -r big/. out/ && cp -r big/. dist/ && cp -r big/. bak/", "cd src || cd build || cd dist; cp -r ../big/. ../out/",
            "cp big/* out/"):
    t0 = time.time()
    assert doors_of(cmd) == [] and time.time() - t0 < 3, (cmd, time.time() - t0)
ok("copies: --, what the line staged, dotglob, cp -T and rsync -R /./, $VAR roots, the ledger's directory, absolute paths")
for tool in ("mcp__gmail__send_mail_draft", "mcp__gmail__send_email_draft", "mcp__outlook__forward_as_draft"):
    assert ids(tool, {}) == [], (tool, ids(tool, {}))
for tool in ("mcp__ms365__send-shared-mailbox-draft", "mcp__gmail__send_saved_draft", "mcp__gmail__send_draft"):
    assert ids(tool, {}) == ["send-external-message"], (tool, ids(tool, {}))
ok("MCP: a tool that writes a draft (send_mail_draft, forward_as_draft) does not send; one that sends a draft does")
for x in (dot, tree, big, os.path.join(p.dir, "build"), os.path.join(p.dir, "dist")):
    shutil.rmtree(x, ignore_errors=True)
# 21f) the sixth M9.31 review (of the fifth's fixes). The shell that reads a word expands its $PWD and $(pwd):
#      the text a nested shell reads (sh -c "…", eval, <<<, an unquoted heredoc) carries the outer directory,
#      and env -C or find -execdir moves the command, not the words the shell already expanded; `echo … &&
#      exit` and `return 1 || exit 1` always exit
for cmd, want in (("bash -c \"cd W && git -C $(pwd) push origin HEAD\"", "push-default-branch"),
                  ("eval \"cd W && git -C $(pwd) push origin HEAD\"", "push-default-branch"),
                  ("bash <<EOF\ncd W\ngit -C \"$(pwd)\" push origin HEAD\nEOF", "push-default-branch"),
                  ("bash <<< \"cd W && git -C $(pwd) push origin HEAD\"", "push-default-branch"),
                  ("timeout 60 bash -c \"cd W && git -C $(pwd) reset --hard\"", "discard-local-work"),
                  ("bash -c \"cd W && cd $(pwd) && git push origin HEAD\"", "push-default-branch"),
                  ("bash -c \"cd W && git -C $PWD push origin HEAD\"", "push-default-branch"),
                  ("env -C W git -C \"$PWD\" push origin HEAD", "push-default-branch"),
                  ("env -C W git -C \"$(pwd)\" push origin HEAD", "push-default-branch"),
                  ("env -C W git -C \"$PWD\" reset --hard", "discard-local-work"),
                  ("cd W || { [ -d .git ] && exit 1; }; git push origin HEAD", "push-default-branch"),
                  ("cd W || { printf '%d' \"$n\" && exit 1; }; git push origin HEAD", "push-default-branch")):
    cmd = cmd.replace("W ", W + " ").replace("W\n", W + "\n")
    assert want in denied(hook(g, "Bash", {"command": cmd}, cwd=R)), cmd
for cmd in ("bash -c 'cd W && git -C $(pwd) push origin HEAD'", "bash <<'EOF'\ncd W\ngit -C \"$(pwd)\" push origin HEAD\nEOF",
            "env -C W git push origin HEAD", "cd W || { echo \"no worktree\" >&2 && exit 1; }; git push origin HEAD",
            "cd W || { echo x; return 1 2>/dev/null || exit 1; }; git push origin HEAD",
            "cd W || { echo no && exit 1; } && git push origin HEAD", "cd W || { printf 'no\\n' && exit 1; }; git reset --hard",
            "cd W || { : && exit 1; }; git reset --hard"):
    cmd = cmd.replace("W ", W + " ").replace("W\n", W + "\n")
    assert hook(g, "Bash", {"command": cmd}, cwd=R).stdout == "", cmd
for cmd in ("env -C /tmp rm -rf \"$PWD/.aladdin\"", "find /tmp -maxdepth 0 -execdir rm -rf \"$PWD/.aladdin\" \\;",
            "env -C /tmp rm -f \"$(pwd)/.aladdin/logs/events.jsonl\"", "find . -name '*.bak' -execdir rm -rf \"$PWD/.aladdin\" \\;",
            "env --chdir=/tmp sh -c \"rm -rf $PWD/.aladdin\"", "bash -c \"rm -rf $PWD/.aladdin\"",
            "bash <<EOF\ncd /tmp\nrm -rf \"$PWD/.aladdin\"\nEOF", "echo \"cd /tmp && rm -rf $(pwd)/.aladdin\" | sh"):
    assert "forge-human-record" in doors_of(cmd, assume=False), (cmd, doors_of(cmd, assume=False))
assert "change-agent-settings" in doors_of("sh -c \"cd /tmp && cp x $PWD/.claude/settings.json\"")
assert "push-default-branch" in denied(hook(g, "Bash", {"command": "echo \"cd %s && git -C $(pwd) push origin HEAD\" | bash" % W}, cwd=R))
ok("directories: the outer shell's $PWD and $(pwd) in a nested shell and under env -C or -execdir; echo && exit exits")

# copies: a directory the gate cannot list now (missing, or empty) holds more than the line's tracked writes
# (tar, curl -o, unzip are not); a copy onto "$VAR/.claude" may become that directory; a variable last name in a
# guarded directory is any name; a send tool whose last words are not a draft writer's sends
os.makedirs(os.path.join(p.dir, "dist"), exist_ok=True)
stage = os.path.join(tempfile.mkdtemp(), "cfg")
for cmd in ("mkdir -p S && tar xzf cfg.tgz -C S && echo ok > S/.stamp && cp -r S/. .claude/",
            "mkdir -p stage && echo x > stage/README && curl -fsSL URL -o stage/settings.json && cp stage/* .claude/",
            "mkdir -p stage && echo x > stage/README && curl -fsSL URL -o stage/settings.json && rsync -a stage/ .claude/",
            "mkdir -p stage && unzip -o cfg.zip -d stage && echo '{}' > stage/keybindings.json && cp -r stage/. ~/.claude/",
            "echo x > dist/README && curl -fsSL URL -o dist/settings.json && cp dist/* .claude/",
            "echo x > dist/README && curl -fsSL URL -o dist/settings.json && cp -r dist/. .claude/",
            "cp -r .claude \"$WT/.claude\"", "git worktree add \"$WT\" -b x && cp -r .claude \"$WT/.claude\"",
            "mv .claude.bak \"$REPO/.claude\"", "cp -a ~/.claude-template \"$PROJECT/.claude\"", "cp -r tmpl \"$WT/.claude/\"",
            "cp -r cfg \"$WT/.codex\"", "mv opencode-cfg \"$PROJ/.opencode\"", "cd \"$X\" && cp -r cfg \"$(pwd)/.claude\"",
            "cp x .claude/${F:?}", "cp x ~/.claude/${F:?}"):
    cmd = cmd.replace("S/", stage + "/").replace("S ", stage + " ")
    assert "change-agent-settings" in doors_of(cmd), (cmd, doors_of(cmd))
for cmd in ("rm -f .aladdin/logs/${F:?}", "echo x > docs/agents/mission/${F:?}", "rm -f .aladdin/logs/$F",
            "sed -i /door.revoked/d \".aladdin/logs/$F\"", "rm -f \".aladdin/logs/${F?}\""):
    assert "forge-human-record" in doors_of(cmd), (cmd, doors_of(cmd))
for cmd in ("cp -r .claude \"$tmp\"", "cp -r .claude \"$WT/\"", "cp -r . \"$TMPDIR\"", "rm -f build/$F", "echo x > \"logs/$name.log\"",
            "mkdir -p out && echo x > out/a.txt && cp -r out/. dist/"):
    assert doors_of(cmd) == [], (cmd, doors_of(cmd))
ok("copies: an unlisted or empty source directory, a copy that becomes $VAR/.claude, a variable last name")
for tool in ("mcp__zapier__send_gmail_draft", "mcp__composio__send_gmail_draft", "mcp__gmail__send_from_alias_draft",
             "mcp__gmail__send_canvas_draft"):
    assert ids(tool, {}) == ["send-external-message"], (tool, ids(tool, {}))
for tool in ("mcp__gmail__send_mail_draft", "mcp__gmail__send_email_as_draft", "mcp__outlook__forward_as_draft",
             "mcp__slack__slack_send_message_draft"):
    assert ids(tool, {}) == [], (tool, ids(tool, {}))
ok("MCP: a draft writer is one whose last words are message, mail, email or as, then draft")
shutil.rmtree(os.path.dirname(stage), ignore_errors=True)
shutil.rmtree(os.path.join(p.dir, "dist"), ignore_errors=True)
for x in (evil, fake, lnr):
    shutil.rmtree(x, ignore_errors=True)

# MCP names: Composio's VERBS_A_ form, kebab-case tools, scheduled messages, a verb before the service, GitLab and
# Bitbucket merges, Google Workspace's Drive access tools; drafts and reads pass
for tool, inp, want in (
        ("mcp__slack__SLACK_SENDS_A_MESSAGE_TO_A_SLACK_CHANNEL", {}, "send-external-message"),
        ("mcp__composio__SLACK_SENDS_A_MESSAGE_TO_A_SLACK_CHANNEL", {}, "send-external-message"),
        ("mcp__discord__send-message", {}, "send-external-message"), ("mcp__ms365__send-mail", {}, "send-external-message"),
        ("mcp__slack__slack_schedule_message", {}, "send-external-message"),
        ("mcp__composio__SLACK_SCHEDULE_MESSAGE", {}, "send-external-message"),
        ("mcp__slack__conversations_add_message", {}, "send-external-message"),
        ("mcp__google-workspace__send_gmail_message", {}, "send-external-message"), ("mcp__gmail__send_draft", {}, "send-external-message"),
        ("mcp__gitlab__merge_merge_request", {}, "push-default-branch"),
        ("mcp__composio__GITHUB_MERGE_A_PULL_REQUEST", {}, "push-default-branch"),
        ("mcp__bitbucket__mergePullRequest", {}, "push-default-branch"),
        ("mcp__workspace-mcp__manage_drive_access", {"action": "grant", "share_type": "anyone"}, "visibility-or-sharing"),
        ("mcp__workspace-mcp__set_drive_file_permissions", {"link_sharing": "reader"}, "visibility-or-sharing")):
    assert ids(tool, inp) == [want], (tool, ids(tool, inp))
for tool, inp in (("mcp__slack__slack_send_message_draft", {}), ("mcp__slack__slack_posts_list", {}),
                  ("mcp__slack__slack_list_scheduled_messages", {}), ("mcp__google-workspace__draft_gmail_message", {}),
                  ("mcp__claude-code-remote__send_message", {}), ("mcp__gitlab__get_merge_request", {}),
                  ("mcp__gitlab__list_merge_requests", {}), ("mcp__workspace-mcp__manage_drive_access", {"action": "remove"}),
                  ("mcp__workspace-mcp__get_drive_file_permissions", {}),
                  ("mcp__workspace-mcp__set_drive_file_permissions", {"link_sharing": "off"})):
    assert ids(tool, inp) == [], (tool, ids(tool, inp))
ok("MCP: Composio, kebab-case, scheduled and verb-first sends; GitLab and Bitbucket merges; Drive access grants")
shutil.rmtree(g.dir, ignore_errors=True)
shutil.rmtree(other, ignore_errors=True)

# OpenCode MCP names keep a '-' in a server's name, and an aggregator's tool names carry the service: both
# reach the same doors as the service's own server; a word inside another tool's name is no door
for tool, want in (("mcp__slack-mcp__post_message", "send-external-message"), ("mcp__zapier__gmail_send_email", "send-external-message"),
                   ("mcp__composio__GMAIL_SEND_EMAIL", "send-external-message"), ("mcp__slack__chat_postMessage", "send-external-message"),
                   ("mcp__zapier__google_drive_share_file", "visibility-or-sharing"), ("mcp__google-drive__share_file", "visibility-or-sharing"),
                   ("google-drive_share_file", "visibility-or-sharing"), ("mcp__github-mcp__merge_pull_request", "push-default-branch")):
    assert ids(tool, {}) == [want], (tool, ids(tool, {}))
for tool in ("mcp__notion__search_slack_posts", "mcp__drive__get_share", "mcp__slack__get_post_history", "mcp__gmail__list_drafts",
             "mcp__zapier__gmail_find_email", "github_create_pull_request", "mcp__drive__list_shares"):
    assert ids(tool, {}) == [], (tool, ids(tool, {}))
ok("MCP: hyphenated servers and aggregators' tools cross the same doors; a word inside a tool's name is none")

# a U+FEFF around a patch's path: OpenCode's trim() cuts it (JS whitespace), so every gate reads the path without it
bom = "\ufeff"
for head in ("*** Add File:%s.claude/settings.json" % bom, "*** Add File: .claude/settings.json%s" % bom,
             "*** Add File: %sopencode.json" % bom):
    assert ids("apply_patch", {"patchText": patch(head, "+{}")}) == ["change-agent-settings"], repr(head)
assert "forge-human-record" in ids("apply_patch", {"patchText": patch("*** Update File:%sdocs/agents/mission/ledger.jsonl" % bom, "@@")})
ok("apply_patch: a U+FEFF around a header's path is cut as OpenCode cuts it")

# 22) list and the area's docstring
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
