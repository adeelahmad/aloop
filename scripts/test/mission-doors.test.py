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
open(lp, "w").write(text.replace('"by": "Adeel", "grant": "standing", "reason": "r"', '"by": "Eve", "grant": "standing", "reason": "r"'))
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

# 10) list and the area's docstring
r = p.run("doors", "list", "--json")
lst = json.loads(r.stdout)
pd = next(d for d in lst["doors"] if d["id"] == "publish-release")
assert pd["grant"] == "standing" and pd["authorized"]["by"] == "Adeel", pd
assert "doors" in p.run("--list").stdout.split() and "two-way" in p.run("doors", "--help").stdout
assert p.run("doors", "nope").returncode == 64
assert hashlib.sha256(b"bash\0git push origin main").hexdigest() == D.action_sha("Bash", {"command": "git  push origin   main"})
for x in (p, s):
    shutil.rmtree(x.dir, ignore_errors=True)
ok("list names each door's grant and live authorization; discovered as an area; usage errors are 64")

print("mission-doors: %d checks passed" % PASS[0])
