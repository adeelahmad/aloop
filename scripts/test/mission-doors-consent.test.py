#!/usr/bin/env python3
"""ROADMAP M11.15: a one-way door costs the human one step from where they are, and a test resource is no door.

A field session (2026-11-20) showed three frictions: dropping 35 archived test databases was a one-way door,
`doors authorize` printed a bare usage line without the flags it needed, and the human's `!` shell inside the
agent's session was refused (CLAUDECODE set), so they had to open another terminal. Now:

  1. a drop of test-named databases on a local server (dropdb recast_test_…, psql -c "DROP DATABASE …_test_…")
     is two-way: crossed with no question and logged (`door.two_way`, reason test-scoped). Anything else
     (a non-test name, a remote host, a name the gate cannot read, a mix) stays one-way;
  2. the human consents in chat: the ASK names the line `allow DOOR <code>`, the code naming exactly this action;
     the gate reads it from the prompts the UserPromptSubmit hook captured (M9.17), records a human ledger row
     citing it, and crosses the door once. The line is used once; another action, a negated line or a forged
     row opens nothing, so an agent still cannot authorize itself;
  3. every refusal of `doors authorize` prints the full command to paste, flags filled in.

    python3 scripts/test/mission-doors-consent.test.py
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "fixtures"))
from mission_fixture import Project, M, BIN  # noqa: E402

sys.path.insert(0, os.path.join(BIN, "..", "mission"))
import doors as D  # noqa: E402

PASS = [0]


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def hook(p, cmd, session="s1"):
    payload = {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": p.dir, "session_id": session,
               "hook_event_name": "PreToolUse"}
    r = subprocess.run([os.path.join(BIN, "mission"), "doors", "hook"], input=json.dumps(payload), cwd=p.dir,
                       env=p.env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"] if r.stdout.strip() else ""


def doors_of(p, cmd):
    return [d["id"] for d in D.classify(p.dir, "Bash", {"command": cmd})]


def events(p, name):
    return [e for e in M._events.read(p.dir) if e.get("event") == name]


p = Project(mode="spoon-fed")

# 1) test-scoped databases on a local server are two-way, and logged
for cmd in ("dropdb recast_test_a1b2c3d4e5f60718",
            "dropdb -p 55440 recast_incoming_test_01 recast_test_02",
            "dropdb --if-exists -h localhost recast_test_x",
            "psql -p 55440 -c 'DROP DATABASE IF EXISTS recast_test_a1b2'",
            "psql -h 127.0.0.1 -p 55440 -d postgres -c \"DROP DATABASE recast_test_1; DROP DATABASE test_scratch\"",
            "psql -d recast_test_x -c 'TRUNCATE users'",
            "PGPORT=55440 dropdb test",
            "mysql -e 'DROP DATABASE app_test'"):
    assert doors_of(p, cmd) == [], (cmd, doors_of(p, cmd))
assert hook(p, "dropdb -p 55440 recast_test_a1b2c3d4e5f60718") == ""
logged = events(p, "door.two_way")
assert logged and logged[-1]["data"]["door"] == "destructive-db" and logged[-1]["data"]["reason"] == "test-scoped", logged
ok("dropping test-named databases on a local server is two-way, with a door.two_way row (test-scoped)")

for cmd in ("dropdb recast",                                         # not a test name
            "dropdb recast_test_x recast",                           # a mix
            "dropdb -h db.example.com recast_test_x",                # a remote server
            "dropdb --host=10.0.0.5 recast_test_x",
            "dropdb \"$DB\"",                                        # unreadable
            "dropdb contest",                                        # test only inside a word
            "psql -c 'DROP DATABASE recast'",
            "psql -c 'DROP DATABASE recast_test_x; DROP DATABASE recast'",
            "psql -d recast -c 'TRUNCATE users'",                    # a table of a non-test database
            "psql -c 'TRUNCATE users'",                              # which database: unknown
            "psql -d recast_test_x -c 'DROP DATABASE recast'",       # connected to a test db, drops another
            "psql 'postgresql://prod.example.com/recast_test_x' -c 'TRUNCATE t'",
            "redis-cli flushall"):
    assert "destructive-db" in doors_of(p, cmd), cmd
env = dict(p.env, PGHOST="db.example.com")
r = subprocess.run([os.path.join(BIN, "mission"), "doors", "classify", "--bash", "dropdb recast_test_x"], cwd=p.dir,
                   env=env, capture_output=True, text=True)
assert r.returncode == 3, r.stderr                                    # PGHOST names a remote server
ok("a non-test name, a mix, a remote host (flag or PGHOST), an unreadable name stay one-way")

# 2) one-step consent in chat: the ASK names the line, the human types it, the gate crosses once
cmd = "dropdb recast_prod"
sha = D.action_sha("Bash", {"command": cmd})
code = D.allow_sha("destructive-db", sha)[:8]
why = hook(p, cmd)
assert "allow destructive-db %s" % code in why, why
assert "aloop mission doors authorize destructive-db --by " in why and "--reason " in why and "--once " + sha in why, why
assert "not the agent's `!` shell" in why, why
p.say("ok, allow destructive-db %s" % code)
assert hook(p, cmd) == ""                                             # crossed
rows = M.records(p.dir, "door.authorized", "door:destructive-db", role="human")
assert len(rows) == 1 and rows[0]["data"]["via"] == "typed" and rows[0]["data"]["action_sha256"] == sha, rows
assert rows[0]["data"]["prompt"]["line"].endswith(code) and M.ledger_problems(p.dir) == []
assert len([e for e in events(p, "door.crossed") if e["data"].get("authorization") == rows[0]["event_id"]]) == 1
assert "allow destructive-db" in hook(p, cmd)                         # used once: the same line opens nothing again
assert len(M.records(p.dir, "door.authorized", "door:destructive-db", role="human")) == 1
ok("the human's typed `allow DOOR <code>` crosses that one action once, recorded as a human ledger row citing it")

other = "dropdb recast_staging"
assert "allow destructive-db" in hook(p, other)                       # the code names another action
p.say("don't allow destructive-db %s" % D.allow_sha("destructive-db", D.action_sha("Bash", {"command": other}))[:8])
assert "allow destructive-db" in hook(p, other)                       # a negated line grants nothing
q = Project(mode="spoon-fed")
osha = D.action_sha("Bash", {"command": other})
M.record(q.dir, "door.authorized", "door:destructive-db", "human", by="agent", reason="r", grant="once", until="",
         action_sha256=osha, sha256=D.allow_sha("destructive-db", osha), via="typed",
         prompt={"line": "allow destructive-db 00000000", "sha256": "0" * 64})
assert "allow destructive-db" in hook(q, other)                       # a forged typed row with no granting line
ok("another action, a negated line and a forged typed row open nothing")

# standing doors take the same one-step line, for that one action
pcmd = "git push origin main"
subprocess.run(["git", "branch", "-M", "main"], cwd=p.dir, check=True)
psha = D.action_sha("Bash", {"command": pcmd})
why = hook(p, pcmd)
pc = D.allow_sha("push-default-branch", psha)[:8]
assert "allow push-default-branch %s" % pc in why, why
p.say("allow push-default-branch %s" % pc)
assert hook(p, pcmd) == ""
assert "push-default-branch" in hook(p, pcmd)                         # once, not standing
ok("a standing door's typed line opens that one action, not the door")

# walls never take a typed line
wsha = D.action_sha("Bash", {"command": "SKIP_HOOKS=1 git commit -m x"})
p.say("allow bypass-gate %s" % D.allow_sha("bypass-gate", wsha)[:8])
assert "BLOCK[door] bypass-gate" in hook(p, "SKIP_HOOKS=1 git commit -m x")
ok("a wall takes no typed line")

# 3) every refusal of `doors authorize` prints the full command to paste
den = events(p, "door.denied")
last = [e for e in den if e["data"].get("door") == "destructive-db"][-1]["data"]["action_sha256"]
r = p.raw("doors", "authorize", "destructive-db", "--by", "Adeel")
assert r.returncode == 64 and "aloop mission doors authorize destructive-db --by Adeel --reason \"" in r.stderr, r.stderr
assert "--once " + last in r.stderr, r.stderr                          # the newest stopped action's digest
r = p.raw("doors", "authorize", "destructive-db", "--by", "Adeel", "--reason", "drop archived test DBs")
assert r.returncode == 64 and "--once " + last in r.stderr and "drop archived test DBs" in r.stderr, r.stderr
r = subprocess.run([os.path.join(BIN, "mission"), "doors", "authorize", "destructive-db", "--by", "Adeel", "--reason",
                    "r", "--once", last], cwd=p.dir, env=dict(p.env, CLAUDECODE="1"), capture_output=True, text=True)
assert r.returncode == 2, r.stderr
assert "allow destructive-db %s" % D.allow_sha("destructive-db", last)[:8] in r.stderr, r.stderr
assert "separate terminal" in r.stderr and "cd " in r.stderr, r.stderr
ok("authorize's refusals print the full command (flags filled), and from an agent's shell the one-line chat consent")

print("%d checks passed" % PASS[0])
