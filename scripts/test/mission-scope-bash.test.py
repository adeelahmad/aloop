#!/usr/bin/env python3
"""ROADMAP §4.12 (M9) M9.9: how gate-mission-scope reads a Bash command. Each row is a command a
mission role, a builder or the supervisor runs, and whether the role path guards allow it (0) or
block it (2). The rows come from two adversarial reviews of the first reader: the commands it let
through (wrappers, comments, line continuations, interpreters, variables) and the legitimate ones it
refused (a rival run in a mktemp scratch dir, `sed -i -e`, `git branch --show-current`).
    python3 scripts/test/mission-scope-bash.test.py
"""
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
BIN = os.path.join(REPO, "plugin", "bin")
GATE = os.path.join(BIN, "gate-mission-scope")
T = os.path.realpath(tempfile.mkdtemp(prefix="aloop-scope-bash-"))
atexit.register(shutil.rmtree, T, ignore_errors=True)
P = os.path.join(T, "proj")
ENV = {k: v for k, v in os.environ.items() if k not in ("SKIP_HOOKS", "AGENTIC_HOST", "TMPDIR")}
ENV.update(HOME=os.path.join(T, "home"), CLAUDE_PROJECT_DIR=P)

os.makedirs(P)
for c in (["init", "-q"], ["config", "user.email", "a@a"], ["config", "user.name", "A"],
          ["commit", "-q", "--allow-empty", "-m", "init"]):
    subprocess.run(["git"] + c, cwd=P, check=True, capture_output=True)
for d in ("eval", "research", "state", "journeys", "baseline", "goals", "ethics", "judges", "telemetry"):
    os.makedirs(os.path.join(P, "docs", "agents", "mission", d))
for d in ("intents", "sprint1", "../../src"):
    os.makedirs(os.path.join(P, "docs", "agents", d), exist_ok=True)
open(os.path.join(P, "docs", "agents", "mission", "mission.json"), "w").write("{}")
open(os.path.join(P, "src", "app.py"), "w").write("x = 1\n")
T1 = os.path.join(P, ".aladdin", "worktrees", "T1")
subprocess.run(["git", "worktree", "add", "-q", "-b", "agentic/T1", T1], cwd=P, check=True, capture_output=True)

E, S, PT, MP, ER = ("aloop:evaluator", "aloop:state-assessor", "aloop:product-tester", "aloop:mission-planner",
                    "aloop:market-researcher")
G, SUP = "aloop:green-worker", ""
MISSION = os.path.join(BIN, "mission")

# (agent, cwd: P or T1, command, rc)
ROWS = [
    # ── the mission CLI behind an interpreter, a shell string, eval or xargs ──
    (MP, P, "python3 %s approve --by human" % MISSION, 2),
    (MP, P, 'python3 "$(aloop --root)/bin/mission" approve --by human', 2),
    (MP, P, "python3 -u %s approve --by human" % MISSION, 2),
    (G, T1, "python3 %s init x --budget-usd 1" % MISSION, 2),
    (MP, P, "bash -c 'aloop mission approve --by h'", 2),
    (MP, P, 'sh -c "cd /tmp && aloop mission approve --by h"', 2),
    (MP, P, 'eval "aloop mission approve --by h"', 2),
    (MP, P, "echo h | xargs aloop mission approve --by", 2),
    (MP, P, "env -S 'aloop mission approve --by h'", 2),
    (MP, P, 'echo "$(aloop mission approve --by h)"', 2),
    (MP, P, "cat <<EOF\n$(aloop mission approve --by h)\nEOF", 2),
    (MP, P, "cat <<'EOF'\n$(aloop mission approve --by h)\nEOF", 0),           # a quoted heredoc is data
    (MP, P, "/usr/bin/env aloop mission goal approve gol_x --by h", 2),
    # ── wrappers, their options, and shell keywords ──
    (MP, P, "timeout 120 aloop mission approve --by human", 2),
    (MP, P, "timeout -k 5 -s TERM 120 aloop mission approve --by human", 2),
    (MP, P, "nice -n 5 aloop mission approve --by human", 2),
    (MP, P, "env -i PATH=$PATH aloop mission approve --by h", 2),
    (MP, P, "if aloop mission approve --by human; then echo ok; fi", 2),
    (MP, P, "! aloop mission approve --by human", 2),
    (MP, P, "aloop mission check && { aloop mission approve --by human; }", 2),
    (MP, P, "while true; do aloop mission tick; break; done", 2),
    (E, P, "env -i git commit -m x", 2),
    (E, P, "nice -n 5 git commit -m x", 2),
    (E, P, "timeout 60 git commit -m x", 2),
    (E, P, "if true; then git commit -am x; fi", 2),
    (E, P, "stdbuf -oL git commit -m x", 2),
    (E, P, "sudo -u root git commit -m x", 2),
    (E, P, "time -p git commit -m x", 2),
    (E, P, "for f in a b; do cp /tmp/$f src/; done", 2),
    (E, P, 'for f in a b; do sed -i "s/x/y/" src/$f; done', 2),
    (G, T1, "for f in x y; do cp /tmp/$f docs/agents/mission/eval/; done", 2),
    (E, P, "case x in x) touch src/a.py;; esac", 2),
    # ── comments ──
    (E, P, "# evaluator's scratch fix\ncd src && echo x > app.py", 2),
    (E, P, "# what's changed\ngit status && git commit -am x", 2),
    (MP, P, "# Let's check then approve\naloop mission check; aloop mission approve --by human", 2),
    (G, T1, "# Fix the evaluator's stale report\ncd docs/agents/mission/eval && echo '{}' > x.json", 2),
    (E, P, "echo x > src/app.py # a trailing comment", 2),
    (PT, P, "# install the rival's pinned release\nmkdir -p /tmp/rival && cd /tmp/rival\nnpm ci > install.log 2>&1", 0),
    (SUP, P, "# snapshot what's in the mission dir before the tick\nmkdir -p /tmp/review\n"
             "cp docs/agents/mission/eval/*.json /tmp/review/", 0),
    (S, P, "# measure twice; flag if run2 > tolerance\naloop mission baseline measure --json", 0),
    (S, P, "echo ok # it's fine; > src/app.py", 0),
    (S, P, 'x="a#b"; echo $x > /tmp/y', 0),
    # ── line continuations ──
    (G, T1, "cp /tmp/fixture.json \\\n   docs/agents/mission/eval/x.json", 2),
    (MP, P, "aloop mission \\\n  approve --by alice", 2),
    (E, P, 'sed -i \\\n  "s/a/b/" src/app.py', 2),
    (E, P, "cp /tmp/report.json \\\n   docs/agents/mission/eval/g1.json", 0),
    (PT, P, "cp /tmp/rival/results.json \\\n   docs/agents/mission/baseline/rival-results.json", 0),
    (PT, P, "mkdir -p /tmp/rival \\\n  /tmp/rival/logs", 0),
    # ── sed / perl / awk in place ──
    (E, P, 'sed -ri "s/a/b/" src/app.py', 2),
    (E, P, 'sed -Ei "s/a/b/" src/app.py', 2),
    (E, P, 'sed --in-place=.bak "s/a/b/" src/app.py', 2),
    (E, P, "sed -i.bak s/a/b/ src/app.py", 2),
    (G, T1, "sed -Ei 's/a/b/' docs/agents/mission/eval/x.json", 2),
    (E, P, "perl -pi -e 's/a/b/' src/app.py", 2),
    (E, P, "awk -i inplace '{print}' src/app.py", 2),
    (E, P, "sed -i -e 's/\"draft\"/\"recorded\"/' docs/agents/mission/eval/evl_x.json", 0),
    (E, P, "perl -pi -e 's/a/b/' docs/agents/mission/eval/evl_x.json", 0),
    (MP, P, "perl -i -pe 's/a/b/' docs/agents/intents/int_x.json", 0),
    (SUP, P, "cd docs/agents/mission && sed -i -e 's/a/b/' mission.json", 0),
    (E, P, "sed -n 's/a/b/p' src/app.py", 0),
    (E, P, "sed -i -n -e 's/x/y/' -e 's/a/b/' docs/agents/mission/eval/e.json", 0),
    (E, P, "sed -i 's/a/b/' docs/agents/mission/eval/*.json", 0),
    # ── redirect forms ──
    (E, P, "echo x >| src/app.py", 2),
    (E, P, "echo x >|src/app.py", 2),
    (E, P, "echo x >& src/app.py", 2),
    (E, P, "echo x &>> src/app.py", 2),
    (E, P, "exec 3<> src/app.py", 2),
    (G, T1, "echo '{}' >| docs/agents/mission/eval/x.json", 2),
    (E, P, "cat <<EOF > src/a.py\nx\nEOF", 2),
    (E, P, "cat > src/a.py <<'EOF'\nx\nEOF", 2),
    (E, P, "(cd src && touch a.py)", 2),
    (E, P, "{ echo a; echo b; } > src/a.py", 2),
    (E, P, "(echo a) > src/a.py", 2),
    (S, P, "echo x 2>/dev/null >&2", 0),
    (S, P, "exec 3>/tmp/fd3.log; echo hi >&3", 0),
    (S, P, "echo hi 1>&2; ls 2>&1 | head", 0),
    (E, P, "echo x > /dev/null; echo y >/dev/stderr", 0),
    (E, P, "cat <<-EOF > /tmp/x\n\tsrc > y\n\tEOF\necho done", 0),
    (E, P, "echo 'a > b; rm src/app.py' > docs/agents/mission/eval/n.txt", 0),
    (E, P, "jq '.x > 1' docs/agents/mission/eval/e.json > /tmp/out", 0),
    (E, P, "awk '{ if ($1 > 3) print }' src/app.py", 0),
    (E, P, "echo 'it'\"'\"'s' > /tmp/x", 0),
    (S, P, "echo don\\'t > /tmp/x", 0),
    # ── variables, substitutions, globs ──
    (E, P, 'echo x > "$PWD/src/app.py"', 2),
    (E, P, "echo x > $(pwd)/src/app.py", 2),
    (E, P, "echo x > ${CLAUDE_PROJECT_DIR}/src/app.py", 2),
    (E, P, "F=src/app.py; echo x > $F", 2),
    (E, P, 'echo x > "${OUT:-src/app.py}"', 2),
    (E, P, 'cd "$(git rev-parse --show-toplevel)" && touch src/a.py', 2),
    (G, T1, "OUT=docs/agents/mission/eval/x.json; echo '{}' > \"$OUT\"", 2),
    (E, P, "echo $(touch src/a.py)", 2),
    (E, P, "x=`git commit -am x`", 2),
    (G, T1, "rm -f docs/agents/mission/eval/*.json", 2),
    (E, P, "echo x > docs/agents/mission/eval/$(date +%s).json", 0),
    (E, P, 'for f in docs/agents/mission/eval/*.json; do jq . "$f" > /dev/null; done', 0),
    (E, P, "arr=(a b c); echo ${arr[0]} > /tmp/x", 0),
    (E, P, "f() { echo hi; }; f", 0),
    (S, P, "case \"$x\" in a) echo a > /tmp/a;; *) echo b;; esac", 0),
    # ── scratch dirs outside the project ──
    (PT, P, "WORK=$(mktemp -d) && cd \"$WORK\" && git clone -q https://github.com/acme/rival && cd rival && "
            "git checkout -q v1.2.3 && npm ci > install.log 2>&1", 0),
    (PT, P, 'cd "$(mktemp -d)" && npm init -y >/dev/null && npm install rival@1.2.3 > install.log 2>&1', 0),
    (PT, P, 'SCRATCH=$(mktemp -d)\ncd "$SCRATCH"\ngit clone --depth 1 --branch v2.1.0 https://github.com/rival/app rival\n'
            "cd rival\nnpm ci > install.log 2>&1", 0),
    (PT, P, "cd $(mktemp -d) && git clone https://github.com/rival/app . && npm ci > install.log", 0),
    (PT, P, "cd `mktemp -d` && touch started", 0),
    (PT, P, 'cd "${TMPDIR:-/tmp}" && mkdir rival-run', 0),
    (PT, P, 'git -C "$SCRATCH/rival" checkout v2.1.0', 0),
    (PT, P, 'cd "$SCRATCH/rival" && git checkout v2.1.0', 0),
    (PT, P, "SCRATCH=$(mktemp -d); git -C \"$SCRATCH/rival\" checkout v2.1.0", 0),
    (PT, P, "pushd /tmp/rival && npm ci > install.log 2>&1 && popd", 0),
    (PT, P, "npm --prefix /tmp/rival ci", 0),
    (S, P, "cd -P /tmp/rival && npm ci > install.log", 0),
    (PT, P, "git clone https://github.com/rival/app .aladdin/scratch/rival && "
            "npm --prefix .aladdin/scratch/rival ci > .aladdin/scratch/install.log", 0),
    (PT, P, "git -C .aladdin/scratch/rival checkout v2.1.0", 0),
    (PT, P, "cd /tmp && mkdir -p src2 && touch src2/x", 0),                       # /tmp/src2/x
    (PT, P, "cd \"$(mktemp -d)\"; cd -; touch src/a.py", 2),                     # back in the project
    # ── option values are not files ──
    (S, P, "truncate -s 0 /tmp/run.log", 0),
    (S, P, "mkdir -m 700 /tmp/rival", 0),
    (S, P, "touch -d 2024-01-01 /tmp/rival/stamp", 0),
    (S, P, "truncate -s 0 docs/agents/mission/state/raw.log", 0),
    (S, P, "mkdir -m 755 docs/agents/mission/state/runs", 0),
    (E, P, "truncate -s 0 src/app.py", 2),
    (E, P, "touch -d 2024-01-01 src/app.py", 2),
    # ── cp/mv/install/ln targets, find, downloads ──
    (E, P, "cp --target-directory=src /tmp/app.py", 2),
    (E, P, "cp -rt src /tmp/app.py", 2),
    (E, P, "install -m 644 -t src /tmp/a", 2),
    (E, P, "mv -t src /tmp/a", 2),
    (E, P, "ln -s /tmp/x src/link", 2),
    (G, T1, "cp --target-directory=docs/agents/mission/eval /tmp/x.json", 2),
    (E, P, "find src -name '*.pyc' -delete", 2),
    (E, P, "find /tmp -name x -exec cp {} src/ \\;", 2),
    (G, T1, "find /tmp -name '*.json' -exec cp {} docs/agents/mission/eval/ +", 2),
    (E, P, "curl -sSo src/a.js https://example.com/a.js", 2),
    (E, P, "wget -O src/a.js https://example.com/a.js", 2),
    (E, P, "dd if=/tmp/x of=src/a.bin", 2),
    (E, P, "cp -r /tmp/rival /tmp/rival2 && find /tmp/rival2 -delete", 0),
    (ER, P, "curl -sSo /tmp/page.html https://example.com/", 0),
    # ── pushd / cd - / subshells ──
    (G, T1, 'pushd docs/agents/mission/eval && echo "{}" > x.json', 2),
    (SUP, P, '(cd docs/agents/mission && git log --oneline -3 .) ; echo "- reviewed" >> docs/agents/sprint1/notes.md', 0),
    (SUP, P, 'cd docs/agents/mission && aloop mission status; cd - >/dev/null && echo "- ok" >> docs/agents/sprint1/notes.md', 0),
    (SUP, P, "mkdir -p docs/agents/mission", 0),
    (SUP, P, "cp /tmp/x.json docs/agents/mission/goals/", 2),
    # ── arithmetic and [[ ]] are not redirects ──
    (S, P, "echo $((pass>3))", 0),
    (S, P, "if (( failed > 0 )); then echo FAIL; fi", 0),
    (S, P, "x=$(( n > 5 ? 1 : 0 ))", 0),
    (S, P, '[[ "$v" > abc ]] && echo later', 0),
    (S, P, "exec > >(tee -a /tmp/assess.log) 2>&1; aloop mission journeys run", 0),
    (S, P, "for ((i=0; i<3; i++)); do echo $i; done", 0),
    (S, P, "while read -r l; do echo \"$l\"; done < /tmp/in.txt", 0),
    (S, P, "diff <(sort /tmp/a) <(sort /tmp/b) > /tmp/d", 0),
    (S, P, "diff <(touch src/a.py) /tmp/b", 2),
    # ── git reads and writes ──
    (S, P, "git branch --show-current; git branch -a; git branch -vv; git branch --contains HEAD", 0),
    (S, P, "git tag --points-at HEAD; git tag -n; git tag --sort=-creatordate; git tag --contains HEAD", 0),
    (S, P, "git -C . branch -r; git branch; git tag; git stash list; git worktree list; git notes list", 0),
    (S, P, "git apply --check /tmp/p.diff; git config --get user.name; git config -l; git clean -n", 0),
    (S, P, "git log --oneline | tee /tmp/log.txt; git diff --stat; git show HEAD:src/app.py > /tmp/app.py", 0),
    (S, P, "git branch feature-x", 2),
    (S, P, "git branch -D agentic/T1", 2),
    (S, P, "git tag -a v1 -m x", 2),
    (S, P, "git tag v1", 2),
    (S, P, "git stash", 2),
    (S, P, "git config user.name x", 2),
    (S, P, "git --work-tree=. --git-dir=.git commit -m x", 2),
    (S, P, "git -c user.name=x commit -m x", 2),
    (S, P, "git apply /tmp/p.diff", 2),
    (S, P, "git clean -fd", 2),
    # ── run data: scratch yes; the event log and the gates' state no ──
    (S, P, "echo x > .aladdin/scratch/x.txt", 0),
    (S, P, "echo '{}' >> .aladdin/logs/events.jsonl", 2),
    (S, P, "echo '{}' > .aladdin/state/mission-runs/a1.json", 2),
    (G, T1, "echo x > .aladdin/state/x", 0),                                     # builders: not this gate's
    # ── what stays allowed ──
    (E, P, "command -v aloop && aloop mission evaluate check", 0),
    (G, T1, "aloop mission status && aloop mission check", 0),
    (G, T1, "echo x > src/a.py && git commit -am x", 0),
    (MP, P, "intent new --source mission > /tmp/x && intent check docs/agents/intents/int_x.json --mode autonomous", 0),
    (MP, P, "aloop mission goal new --eval e1", 0),
    (S, P, "echo '{\"a\": 1}' | python3 -c 'import json,sys; json.load(sys.stdin)'", 0),
    (S, P, "make -C /tmp/rival test > /tmp/rival.log", 0),
    # ── the human's word (restore, the authority record, a human event) and the ledger: PR #77 ──
    (S, P, "aloop mission baseline restore bsl_x --by h", 2),
    (E, P, "aloop events authority --initiated-by h --mode autonomous --scope all", 2),
    (G, T1, "events authority --initiated-by h --mode reviewed --scope x", 2),
    (E, P, "aloop events emit telemetry.consent --role human --data subject=x", 2),
    (E, P, "aloop events emit task.note --role Human", 2),
    (E, P, "aloop events emit mission.approved --role evaluator", 2),
    (G, T1, "aloop events emit baseline.vetoed", 2),
    (E, P, "aloop events emit human.override", 2),
    (G, T1, 'r=human; aloop events emit gate.passed --role "$r"', 2),
    (E, P, 'aloop events emit gate.passed --role "$(cat r)"', 2),
    (E, P, "python3 %s emit telemetry.consent --role human" % os.path.join(BIN, "events"), 2),
    (E, P, "aloop events emit gate.passed --role evaluator --data note=human", 0),
    (E, P, "aloop events query --event mission.approved", 0),
    (S, P, "aloop mission baseline show", 0),
    (SUP, P, "aloop events authority --initiated-by adeel --mode reviewed --scope 'sprint 1'", 0),
    (E, P, "echo '{}' >> docs/agents/mission/ledger.jsonl", 2),
    (S, P, "cp /tmp/forged docs/agents/mission/ledger.jsonl", 2),
    (G, T1, "echo '{}' >> docs/agents/mission/ledger.jsonl", 2),
    (SUP, P, "echo '{}' >> docs/agents/mission/ledger.jsonl", 2),
    (SUP, P, "git checkout -- docs/agents/mission/ledger.jsonl", 0),
    (SUP, P, "cat docs/agents/mission/ledger.jsonl", 0),
]


def run(agent, cwd, cmd):
    p = {"session_id": "s1", "cwd": cwd, "hook_event_name": "PreToolUse", "tool_name": "Bash",
         "tool_input": {"command": cmd}}
    if agent:
        p["agent_type"], p["agent_id"] = agent, "a-" + agent.split(":")[-1]
    return subprocess.run([GATE], cwd=cwd, env=ENV, input=json.dumps(p), capture_output=True, text=True)


bad = []
for agent, cwd, cmd, want in ROWS:
    r = run(agent, cwd, cmd)
    if r.returncode != want or (want == 2 and "BLOCK[mission-scope]" not in r.stderr) or "failing closed" in r.stderr:
        bad.append("%s in %s: want %d, got %d: %r\n    %s" % (agent or "supervisor", os.path.relpath(cwd, P) or ".",
                                                             want, r.returncode, cmd, r.stderr.strip()[:300]))
for b in bad:
    print("FAIL -", b)
assert not bad, "%d of %d rows wrong" % (len(bad), len(ROWS))
print("ok - %d Bash commands judged as a shell reads them (%d blocked, %d allowed)"
      % (len(ROWS), sum(1 for r in ROWS if r[3] == 2), sum(1 for r in ROWS if r[3] == 0)))
print("\n1 checks passed")
sys.exit(0)
