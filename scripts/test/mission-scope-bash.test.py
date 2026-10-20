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
# T2: a builder's worktree on a branch that tracks docs/agents/mission/ (the tracked layout), so a removal that
# reaches it through an ancestor ("." included) has something to reach
T2 = os.path.join(P, ".aladdin", "worktrees", "T2")
subprocess.run(["git", "worktree", "add", "-q", "-b", "agentic/T2", T2], cwd=P, check=True, capture_output=True)
for f in ("eval/evl_1.json", "ledger.jsonl", "mission.json"):
    os.makedirs(os.path.dirname(os.path.join(T2, "docs", "agents", "mission", f)), exist_ok=True)
    open(os.path.join(T2, "docs", "agents", "mission", f), "w").write("{}\n")
for c in (["add", "-A"], ["commit", "-q", "-m", "mission"]):
    subprocess.run(["git"] + c, cwd=T2, check=True, capture_output=True)

E, S, PT, MP, ER = ("aloop:evaluator", "aloop:state-assessor", "aloop:product-tester", "aloop:mission-planner",
                    "aloop:market-researcher")
G, SUP = "aloop:green-worker", ""
MISSION = os.path.join(BIN, "mission")

# (agent, cwd: P, T1 or T2, command, rc)
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
    (E, P, "aloop mission tick --force --by Adeel", 2),
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
    (PT, P, "aloop events emit baseline.superseded --role product-tester --data subject=x", 2),
    (G, T1, "aloop events emit artifact.created --role evaluator", 2),
    (E, P, "aloop events emit judge.verdict --role evaluator", 2),
    (G, T1, "aloop events decide acceptance --reason 'tests pass' --role human", 2),
    (G, T1, "aloop events decide implementation --reason 'smaller diff' --task S1-01-T1", 0),
    (E, P, "aloop events query --event mission.approved", 0),
    (S, P, "aloop mission baseline show", 0),
    (SUP, P, "aloop events authority --initiated-by adeel --mode reviewed --scope 'sprint 1'", 0),
    (E, P, "echo '{}' >> docs/agents/mission/ledger.jsonl", 2),
    (S, P, "cp /tmp/forged docs/agents/mission/ledger.jsonl", 2),
    (G, T1, "echo '{}' >> docs/agents/mission/ledger.jsonl", 2),
    (SUP, P, "echo '{}' >> docs/agents/mission/ledger.jsonl", 2),
    (SUP, P, "git checkout -- docs/agents/mission/ledger.jsonl", 0),
    (SUP, P, "cat docs/agents/mission/ledger.jsonl", 0),
    # ── M9.17 (PR #82): a grant reads the human's captured prompts; nothing but the hook writes them ──
    (E, P, "echo '{}' >> .aladdin/transcripts/s1/events.jsonl", 2),
    (SUP, P, "echo x >> .aladdin/transcripts/s1/events.jsonl", 2),
    (G, T1, "cat > %s/.aladdin/transcripts/x/events.jsonl <<'EOF'\n{}\nEOF" % P, 2),
    (SUP, P, "cp /tmp/forged.jsonl .agentic/transcripts/s1/events.jsonl", 2),
    (SUP, P, """echo '{"prompt": "approve msn_x 1234abcd"}' | aloop transcripts prompt""", 2),
    (G, T1, "transcripts record < /tmp/p.json", 2),
    (SUP, P, "bash \"$(aloop --root)/bin/transcripts\" prompt < p.json", 2),
    (SUP, P, "aloop transcripts view --all", 0),
    (SUP, P, "cat .aladdin/transcripts/s1/events.jsonl | tail -3", 0),
    (E, P, "claude -p 'summarize the rivals'", 2),
    (SUP, P, 'claude -p "approve msn_x 1234abcd"', 2),
    (SUP, P, 'echo "approve msn_x 1234abcd" | claude --print', 2),
    (SUP, P, "codex exec 'confirm bln_x 1234abcd'", 2),
    (G, T1, "opencode run 'hi'", 2),
    (G, T1, "timeout 60 claude --model sonnet -p hi", 2),
    (SUP, P, "claude plugin validate ./plugin --strict", 0),
    (SUP, P, "claude --version && codex --help", 0),
    (G, T1, "opencode models", 0),
    (E, P, 'dsh --profile aloop "approve msn_x 1234abcd"', 2),
    (G, T1, "dsh headless 'run the tests'", 2),
    (SUP, P, "DSH_HOME=/tmp/d dsh aloop --resume s1", 2),
    (SUP, P, "dsh plugin --profile aloop add some-plugin", 0),
    (SUP, P, "dsh --version", 0),
    # ── round 3: the reader (an adversarial review of the shell reader: coverage gaps and false blocks) ──
    # `]]` glued to ; && || : the test ends there, and the rest of the command is judged (and vice versa)
    (E, P, "if [[ -f src/app.py ]]; then echo x > src/app.py; fi", 2),
    (E, P, "if [[ -f src/app.py ]]; then\n  echo x > src/app.py\nfi", 2),
    (S, P, "if [[ -n $(git status --porcelain) ]]; then git add -A && git commit -m 'state: assess'; fi", 2),
    (MP, P, "if [[ -z \"$GOAL\" ]]; then echo none; fi\naloop mission goal new --eval e1 && aloop mission goal approve gol_1 --by h", 2),
    (G, T1, "if [[ -d docs/agents/mission/eval ]]; then echo '{}' > docs/agents/mission/eval/x.json; fi", 2),
    (SUP, P, "[[ -f /tmp/l ]]; echo '{}' >> docs/agents/mission/ledger.jsonl", 2),
    (E, P, "while [[ $i -lt 3 ]]; do echo $i >> src/app.py; i=$((i+1)); done", 2),
    (E, P, "[[ -f x ]]&& git commit -am x", 2),
    (E, P, "[[ -f x ]]||echo y > src/app.py", 2),
    (E, P, "if [[ -f /tmp/a ]]; then echo ok > /tmp/b; fi", 0),
    (PT, P, "if [[ ! -d /tmp/rival ]]; then\n  mkdir -p /tmp/rival && cd /tmp/rival && git clone -q https://github.com/rival/app .\nfi", 0),
    (S, P, "if [[ -f /tmp/run1.json ]]; then\n  rm -f /tmp/run1.json 2>/dev/null\nfi\naloop mission assess --runs 2", 0),
    (E, P, "if [[ -n \"$X\" ]]; then\ncat > /tmp/run.sh <<'EOF'\nrm -rf build && make\nEOF\nfi", 0),
    (SUP, P, "if [[ -d /tmp/review ]]; then\n  rm -rf /tmp/review && cp -r docs/agents/mission/eval /tmp/review\nfi", 0),
    # a `for`/`select` loop variable is bound to the first item of its list ("$f" is a source write)
    (E, P, "for f in src/app.py src/b.py; do sed -i 's/a/b/' \"$f\"; done", 2),
    (E, P, "for d in src tests; do touch \"$d/__init__.py\"; done", 2),
    (G, T1, "for f in docs/agents/mission/eval/*.json; do jq '.status=\"recorded\"' \"$f\" > /tmp/t && mv /tmp/t \"$f\"; done", 2),
    (SUP, P, "for f in docs/agents/mission/eval/*.json; do echo '{}' > \"$f\"; done", 2),
    (E, P, "select f in src/app.py; do echo x > \"$f\"; break; done", 2),
    (E, P, "for f in src/*.py; do echo x >> \"$f\"; done", 2),
    (E, P, "for f in docs/agents/mission/eval/*.json; do sed -i 's/a/b/' \"$f\"; done", 0),
    # export/declare/local/readonly/typeset NAME=… records NAME, like a bare assignment
    (G, T1, "export OUT=docs/agents/mission/eval/x.json; echo '{}' > \"$OUT\"", 2),
    (E, P, "export TARGET=src/app.py && sed -i 's/a/b/' \"$TARGET\"", 2),
    (E, P, "f() { local out=src/app.py; echo x > \"$out\"; }; f", 2),
    (E, P, "declare F=src/app.py; echo x > \"$F\"", 2),
    (E, P, "readonly F=src/app.py; echo x > \"$F\"", 2),
    (E, P, "typeset F=src/app.py; echo x > \"$F\"", 2),
    (E, P, "export ROOT=$(git rev-parse --show-toplevel); cd \"$ROOT\" && git commit -am x", 2),
    (S, P, "export OUT=/tmp/s.json; echo x > \"$OUT\"", 0),
    # a `$` right before a closing double quote is a literal $ (an end-anchored regex), not a nested string
    (PT, P, "grep -q \"^v1.2.3$\" /tmp/rival/VERSION && cd /tmp/rival && echo \"pinned\" > run.log", 0),
    (E, P, "grep -q \"^ok$\" /tmp/run.log && jq -e '.gaps[] | select(.kind == \"regression\") | .delta > 0' docs/agents/mission/eval/evl_x.json", 0),
    (E, P, "grep -c \"x$\" /tmp/a; echo hi > src/app.py; echo \"done\"", 2),
    # a script fed to a shell on stdin (heredoc, here-string) is the shell's program, read again
    (E, P, "bash <<'EOF'\ngit commit -am x\nEOF", 2),
    (MP, P, "bash -s <<'EOF'\naloop mission approve --by h\nEOF", 2),
    (E, P, "bash <<< 'git commit -am x'", 2),
    (MP, P, "sh <<EOF\naloop mission approve --by h\nEOF", 2),
    (S, P, "bash <<'EOF'\naloop mission baseline measure --json\nEOF", 0),
    # the redirect of a { } group is judged where the shell opens it, before a `cd` inside the group
    (E, P, "{ cd docs/agents/mission/state && jq -c . *.json; } > .aladdin/scratch/state.jsonl", 0),
    (E, P, "{ cd /tmp; echo; } > src/app.py", 2),
    # xargs -I{} replaces only the placeholder inside the sh -c string, so the inner command is read
    (E, P, "ls /tmp/fix/*.py | xargs -I{} sh -c 'cp {} src/'", 2),
    (E, P, "find /tmp/fix -name '*.py' | xargs -I % bash -c 'cp % src/'", 2),
    (S, P, "ls /tmp/r/*.json | xargs -I{} sh -c 'jq . {} > /dev/null'", 0),
    # perl -0pi / -lpi: -0 and -l take only an octal value, so a clustered -i is still found
    (E, P, "perl -0pi -e 's/a/b/' src/app.py", 2),
    (E, P, "perl -lpi -e 's/a/b/' src/app.py", 2),
    (E, P, "perl -0777 -i -pe 's/a/b/' src/app.py", 2),
    # a simple {a,b} brace at the start of a word expands to its alternatives
    (E, P, "touch {src,tests}/__init__.py", 2),
    (E, P, "mkdir -p {src,tests}/pkg", 2),
    (E, P, "cp /tmp/a.py {src,tests}/", 2),
    (E, P, "touch docs/agents/mission/eval/{a,b}.json", 0),
    # `cd` is not followed where bash would not run it: a function body, an untaken branch, a pipeline
    (E, P, "cleanup() { cd /; rm -rf \"$WORK\"; }; trap cleanup EXIT; echo x > src/app.py", 2),
    (E, P, "f() { cd /tmp; }; echo x > src/app.py", 2),
    (E, P, "if [ -d /tmp/rival ]; then cd /tmp/rival; fi; touch src/a.py", 2),
    (E, P, "case $1 in a) cd /tmp;; esac; touch src/a.py", 2),
    (E, P, "cd /tmp | true; touch src/a.py", 2),
    # wrappers: setsid / runuser are looked through, and `su USER -c` finds its -c after the user
    (E, P, "setsid git commit -am x", 2),
    (E, P, "su root -c 'git commit -am x'", 2),
    (E, P, "su - postgres -c 'git commit -am x'", 2),
    (E, P, "runuser -u root -- git commit -am x", 2),
    (E, P, "su -c 'git commit -am x' root", 2),
    # round 3: the rules
    # ── downloads save into the cwd: curl -O, wget (reader-coverage F4) ──
    (ER, P, "curl -LO https://arxiv.org/pdf/2401.00001.pdf", 2),
    (ER, P, "curl --remote-name https://example.com/report.pdf", 2),
    (ER, P, "wget https://example.com/report.pdf", 2),
    (ER, P, "wget --mirror --page-requisites https://rival.example.com", 2),
    (ER, P, "curl -sSLO https://example.com/a.pdf", 2),
    (ER, P, "curl -OJ https://example.com/a", 2),
    (ER, P, "wget -q https://example.com/a.pdf", 2),
    (ER, P, "wget -nc https://example.com/a https://example.com/b", 2),
    (ER, P, "wget -r -l1 https://example.com/", 2),
    (ER, P, "curl -O --output-dir src https://example.com/a.js", 2),
    (ER, P, "cd docs/agents/mission/research && curl -LO https://example.com/report.pdf", 0),
    (ER, P, "wget -qO- https://example.com | head", 0),
    (ER, P, "curl -LO --output-dir /tmp/src https://example.com/a.pdf; wget -P /tmp/w https://example.com/b", 0),
    # ── the supervisor's mission dir: only mkdir/touch may name it bare; a tree removal reaches it (F5) ──
    (SUP, P, "rm -rf docs/agents/mission", 2),
    (SUP, P, "cp /tmp/backup/ledger.jsonl docs/agents/mission/", 2),
    (SUP, P, "cp -r /tmp/review/eval docs/agents/mission/", 2),
    (SUP, P, "tar xzf /tmp/mission-snap.tgz -C docs/agents/mission", 2),
    (SUP, P, "rsync -a /tmp/snap/ docs/agents/mission/", 2),
    (SUP, P, "unzip -o /tmp/snap.zip -d docs/agents/mission", 2),
    (SUP, P, "mv /tmp/x.json docs/agents/mission/", 2),
    (SUP, P, "cp /tmp/x.json docs/agents/mission", 2),
    (SUP, P, "rm -rf docs/agents", 2),
    (SUP, P, "rm -rf docs/agents/mission && aloop mission init x --budget-usd 1", 2),
    (SUP, P, "find . -name '*.json' -delete", 2),
    (SUP, P, "rsync -a --delete /tmp/snap/ docs/", 2),
    (G, T1, "rm -rf docs/agents", 2),
    (G, T1, "rm -rf docs", 2),
    (G, T1, "cp -r /tmp/snap/mission docs/agents/", 2),
    (SUP, P, "cp /tmp/m.json docs/agents/mission/mission.json", 0),
    (SUP, P, "cp /tmp/mission.json docs/agents/mission/", 0),
    (SUP, P, "find . -name '*.pyc' -delete", 0),
    (SUP, P, "rm -rf .aladdin/worktrees/T1 && rm -rf /tmp/review", 0),
    (SUP, P, "touch docs/agents/mission && tar xzf /tmp/fixtures.tgz -C docs", 0),
    (G, T1, "find . -name '*.pyc' -delete; rm -rf build dist node_modules", 0),
    # ── mv removes its source (F6) ──
    (SUP, P, "mv docs/agents/mission/eval/evl_bad.json /tmp/", 2),
    (S, P, "mv docs/agents/mission/ledger.jsonl /tmp/ledger.bak", 2),
    (G, T1, "mv docs/agents/mission/eval/x.json /tmp/", 2),
    (E, P, "mv src/app.py /tmp/app.py.bak", 2),
    (E, P, "rsync -a --remove-source-files src/ /tmp/b/", 2),
    (E, P, "tar -czf /tmp/s.tgz --remove-files src", 2),
    (SUP, P, "mv docs/agents docs/agents.old", 2),
    (E, P, "mv docs/agents/mission/eval/a.json docs/agents/mission/eval/b.json", 0),
    (E, P, "tar -czf /tmp/s.tgz src && rsync -a src/ /tmp/b/", 0),
    # ── other tools' clones and installs land in the cwd (F9) ──
    (PT, P, "gh repo clone rival/app", 2),
    (PT, P, "npm install rival@1.2.3", 2),
    (PT, P, "gh release download v1.2.3 -R rival/app", 2),
    (PT, P, "npm pack rival@1.2.3", 2),
    (PT, P, "pip download rival==1.2.3", 2),
    (PT, P, "pip download rival==1.2.3 -d .", 2),
    (PT, P, "gh repo clone rival/app /tmp/rival", 0),
    (PT, P, "npm --prefix /tmp/rival install rival@1.2.3", 0),
    (PT, P, "gh release download v1.2.3 -R rival/app -D /tmp/rel; pip download rival==1.2.3 -d /tmp/pip", 0),
    (G, T1, "npm install left-pad@1.3.0 && npm ci", 0),
    # ── git subcommands that write the repo or the tree (F10) ──
    (S, P, "git bisect start HEAD v1.0 && git bisect run ./measure.sh", 2),
    (E, P, "git format-patch -1 HEAD", 2),
    (PT, P, "git remote add rival https://github.com/rival/app && git fetch rival", 2),
    (S, P, "git update-index --assume-unchanged src/app.py", 2),
    (S, P, "git checkout-index -f -a", 2),
    (S, P, "git read-tree -u --reset HEAD", 2),
    (S, P, "git init", 2),
    (E, P, "git archive -o src/x.tar HEAD", 2),
    (S, P, "git lfs pull", 2),
    (S, P, "git symbolic-ref HEAD refs/heads/x", 2),
    (S, P, "git gc --prune=now", 2),
    (S, P, "git fetch origin", 2),
    (G, T1, "git bisect run sh -c 'echo x > docs/agents/mission/eval/x.json'", 2),
    (G, T1, "git rebase -x 'touch docs/agents/mission/eval/x.json' HEAD~1", 2),
    (S, P, "git bisect log; git remote -v", 0),
    (S, P, "git symbolic-ref --short HEAD; git remote show origin; git lfs ls-files", 0),
    (S, P, "git archive -o /tmp/x.tar HEAD && git format-patch -1 HEAD -o /tmp/patches", 0),
    (PT, P, "git -C /tmp/rival-repo init && git init /tmp/rival2", 0),
    # ── a builder's git pathspecs in docs/agents/mission/ (F11) ──
    (G, T1, "git rm -r -q docs/agents/mission/eval", 2),
    (G, T1, "git checkout main -- docs/agents/mission/eval/evl_1.json", 2),
    (G, T1, "git restore --source=main docs/agents/mission/", 2),
    (G, T1, "git mv docs/agents/mission/eval/a.json docs/agents/mission/eval/b.json", 2),
    (G, T1, "git checkout main -- src/app.py && git commit -am x", 0),
    (G, T1, "git restore src/app.py; git stash push -- src/app.py; git rm -q src/old.py", 0),
    # ── in-place tools, editors and formatters (F12) ──
    (E, P, "sort -o src/list.txt src/list.txt", 2),
    (G, T1, "jq '.status=\"x\"' docs/agents/mission/eval/x.json | sponge docs/agents/mission/eval/x.json", 2),
    (E, P, "yq -i '.x = 1' src/c.yaml", 2),
    (E, P, "ex -sc '%s/a/b/g|x' src/app.py", 2),
    (E, P, "vim -c '%s/a/b/g' -c 'wq' src/app.py", 2),
    (E, P, "printf ',s/a/b/g\\nw\\n' | ed -s src/app.py", 2),
    (E, P, "ruff check --fix src/", 2),
    (E, P, "npx prettier --write src/", 2),
    (E, P, "black src/", 2),
    (E, P, "ruff format src/", 2),
    (E, P, "gofmt -w src/", 2),
    (E, P, "dos2unix src/app.py", 2),
    (E, P, "iconv -f latin1 -t utf-8 /tmp/a -o src/a.py", 2),
    (E, P, "split -l 100 /tmp/big src/part_", 2),
    (E, P, "mktemp -p src", 2),
    (E, P, "mktemp src/tmp.XXXX", 2),
    (E, P, "scp /tmp/a.py src/", 2),
    (E, P, "gzip src/app.py", 2),
    (S, P, "ruff check src/ --statistics > /tmp/lint.txt; black --check src/; npx prettier --check src/", 0),
    (E, P, "prettier --write docs/agents/mission/eval/evl_x.json && sort -o /tmp/s.txt src/list.txt", 0),
    (E, P, "gzip -c src/app.py > /tmp/app.gz; mktemp -d; mktemp -t x.XXXX; scp src/app.py host:/tmp/", 0),
    # ── events: a decision or event as the human (F19) ──
    (E, P, "aloop events decide acceptance --reason ok --role human", 2),
    (G, T1, "aloop events emit plan.approved --caused-by abc", 2),
    # ── git -C after a cd the gate cannot resolve: no crash (reader-false-positive F1) ──
    (PT, P, 'cd "$(ls -d /tmp/rival-*/ | head -1)" && git -C /tmp/rival-src rev-parse HEAD', 0),
    (SUP, P, 'cd "$WORK" && git -C /tmp/rival log -1', 0),
    (E, P, "popd; git -C /tmp/x status", 0),
    (SUP, P, "cd rival-*; git -C /tmp log", 0),
    (S, P, 'cd "$WORK"; git -C ~/x status', 0),
    # ── BSD sed -i '' (F3) ──
    (MP, P, "sed -i '' 's/draft/final/' /tmp/draft-goal.json", 0),
    (E, P, "sed -i '' 's/\"draft\"/\"recorded\"/' docs/agents/mission/eval/evl_x.json", 0),
    (E, P, "sed -i '' 's/a/b/' src/app.py", 2),
    # ── an unknown -C / -d / --directory is judged by its known start (F4) ──
    (PT, P, 'SCRATCH=/tmp/rival-$(date +%s); mkdir -p "$SCRATCH"; tar -xzf /tmp/rival.tgz -C "$SCRATCH"', 0),
    (PT, P, 'WORK=/tmp/rival-$$; mkdir -p "$WORK" && unzip -q /tmp/rival.zip -d "$WORK"', 0),
    (PT, P, "for v in 1.2.0 1.3.0; do d=/tmp/rival-$v; mkdir -p $d; tar -xzf /tmp/rival-$v.tgz -C $d; done", 0),
    (PT, P, 'patch -d "$RIVAL_SRC" -p1 < /tmp/fix.patch', 0),
    (E, P, "tar -xzf /tmp/r.tgz -C src/$X", 2),
    (E, P, "tar -xzf /tmp/r.tgz --directory=src/$X", 2),
    # ── find -exec: {} inside a word, -execdir at the match's dir (F5) ──
    (E, P, "find docs/agents/mission/eval -name '*.json' -exec cp {} {}.bak \\;", 0),
    (PT, P, "find /tmp/rival -name result.json -execdir cp {} result.bak \\;", 0),
    (E, P, "find src -name '*.py' -exec cp {} {}.bak \\;", 2),
    (E, P, "find src -execdir touch result.bak \\;", 2),
    (E, P, "find src lib -exec cp {} {}.bak \\;", 2),
    # ── cp -t with an unknown dir (F8) ──
    (E, P, 'cp --target-directory="$WORK" docs/agents/mission/state/a.json', 0),
    (E, P, 'cp -t "$WORK" docs/agents/mission/state/a.json docs/agents/mission/state/b.json', 0),
    (E, P, 'cp -t "src/$D" /tmp/a.py', 2),
    (E, P, 'sed -i"$SUF" "s/a/b/" src/app.py', 2),
    # ── tar to stdout (F9) ──
    (PT, P, "tar -xzOf /tmp/rival.tgz package/package.json | jq -r .version", 0),
    (PT, P, "tar -xzf /tmp/rival.tgz --to-stdout package/package.json | jq -r .version", 0),
    (PT, P, "tar xzOf /tmp/rival.tgz package/package.json; tar -O -xzf /tmp/rival.tgz p", 0),
    (E, P, "tar -xzf /tmp/rival.tgz", 2),
    # ── read-only forms (F10) ──
    (S, P, "git config get user.email", 0),
    (S, P, "git tag --sort -creatordate | head -1", 0),
    (PT, P, "unzip -h | head -3", 0),
    (PT, P, "find -L /tmp/rival -name '*.log' -delete", 0),
    (S, P, "git config set user.email x", 2),
    (E, P, "unzip -o /tmp/snap.zip", 2),
    (E, P, "find -L src -name '*.pyc' -delete", 2),
    # ── a builder's bare `mission evaluate|assess` writes a report (X1) ──
    (G, T1, "aloop mission evaluate", 2),
    (G, T1, "aloop mission assess", 2),
    (G, T1, "aloop mission evaluate --help; aloop mission due; aloop mission evaluate check evl_x; aloop mission", 0),
    # ── Aladdin intents are the mission-planner's (real-areas F5) ──
    (E, P, 'aloop aladdin "make the list show a task right after it is added"', 2),
    (E, P, 'aladdin "show the task right after it is added"', 2),
    (E, P, 'aloop intent new --source prompt --raw "add dark mode"', 2),
    (ER, P, "aladdin run 'add dark mode'", 2),
    (S, P, "aladdin make the list faster", 2),
    (PT, P, "intent new --source prompt --raw x", 2),
    (MP, P, "aloop intent new --source mission --goal gol_x --gap g1 && aladdin intent check x.json", 0),
    (E, P, "aladdin status; aloop intent summary docs/agents/intents/int_x.json; aladdin --help; aladdin run", 0),
    (E, P, "aladdin mission evaluate check evl_x", 0),
    # ── M9.42: the judge command is the supervisor's to set, never a role's or a builder's ──
    (E, P, "ALOOP_JUDGE_CMD='python3 /tmp/j.py --model {model}' aloop mission judge run rub_x --subject s", 2),
    (E, P, "export ALOOP_JUDGE_CMD='x --model {model}'; aloop mission judge run rub_x", 2),
    (G, T1, "env ALOOP_JUDGE_TIMEOUT=1 aloop mission status", 2),
    (E, P, "declare -x ALOOP_JUDGE_CMD=x", 2),
    (E, P, "ALOOP_JUDGE_TIMEOUT=1", 2),
    (E, P, "bash -c 'ALOOP_JUDGE_CMD=x aloop mission judge run rub_x'", 2),
    (E, P, "bash -c 'echo $ALOOP_JUDGE_CMD'", 2),
    (E, P, "python3 -c 'import os; os.environ[\"ALOOP_JUDGE_CMD\"] = \"x\"'", 2),
    (G, T1, "node -e 'process.env.ALOOP_JUDGE_CMD = \"x\"'", 2),
    (SUP, P, "ALOOP_JUDGE_CMD='python3 /tmp/j.py --model {model}' aloop mission judge run rub_x --subject s", 0),
    (E, P, "aloop mission judge run rub_x --subject s --input /tmp/in.json", 0),
    (E, P, 'echo "$ALOOP_JUDGE_CMD"; env | grep ALOOP_JUDGE', 0),
    (G, T1, "grep -rn ALOOP_JUDGE_CMD plugin/mission/judge.py", 0),
    # ── an array assignment runs its substitutions; `time [-p] --` runs the command after `--` (M9.30's review) ──
    (E, P, "x=( $(git commit -am x) )", 2),
    (E, P, "declare -a x=( `git commit -am x` )", 2),
    (E, P, "f() { local x=( \"$(git commit -am x)\" ); }; f", 2),
    (E, P, "x=( a \"$(echo y > src/app.py)\" )", 2),
    (E, P, "time -p -- git commit -am x", 2),
    (E, P, "time -- git commit -am x", 2),
    (E, P, "x=( a b c ); echo ${x[@]}", 0),
    (E, P, "x=( $(ls docs) ); echo ${#x[@]}", 0),
    (E, P, "time -p -- ls", 0),
    # round 4: the rules
    # ── find -exec: {} is a match under EVERY start path, and the text around it is kept (F5 regression) ──
    (E, P, "find docs/agents/mission/eval src -name '*.py' -exec touch {}.x \\;", 2),
    (E, P, "find -L docs/agents/mission/eval src -exec cp {} {}.bak \\;", 2),
    (E, P, "find /tmp/x src -execdir touch result.bak \\;", 2),
    (E, P, "find docs/agents/mission/eval -maxdepth 0 -exec cp /tmp/evil.py {}/../../../../src/app.py \\;", 2),
    (E, P, "find docs/agents/mission/eval -name '*.json' -exec cp {} /tmp/bak/{} \\;", 0),
    (E, P, "find docs/agents/mission/eval -name '*.json' -exec mv {} {}.old \\;", 0),
    (PT, P, "find /tmp/rival src -name '*.log' -exec cat {} +; find /tmp/a /tmp/b -execdir touch r.bak \\;", 0),
    # ── a {} is a match, not the start dir: find's -name tests bound what a removal of it reaches (over-block) ──
    (SUP, P, "find . -type d -name node_modules -prune -exec rm -rf {} +", 0),
    (SUP, P, "find . -name '*.log' -exec mv {} /tmp/logs/ \\;", 0),
    (SUP, P, "rm -rf build dist; find . -name __pycache__ -prune -exec rm -rf {} +", 0),
    (G, T2, "find . -name __pycache__ -exec rm -rf {} +; find . -name '*.orig' -exec rm {} +", 0),
    (SUP, P, "find . -exec rm -rf {} +", 2),
    (SUP, P, "find . -name '*.json' -exec rm {} +", 2),
    (G, T2, "find . -name mission -exec rm -rf {} +", 2),
    (SUP, P, "find docs -name '*.json' -exec mv {} /tmp/ \\;", 2),
    # ── -o, ! and -not: find's -name tests no longer bound a removal (F5) ──
    (SUP, P, "find . -name '*.pyc' -o -name '*.json' -delete", 2),
    (SUP, P, "find . \\( -name '*.pyc' -o -name '*.json' \\) -delete", 2),
    (SUP, P, "find . ! -name '*.pyc' -delete", 2),
    (SUP, P, "find . -not -name '*.pyc' -type f -delete", 2),
    (SUP, P, "find . -name '*.pyc' -delete; find . -name '*.pyc' -exec test ! -s {} \\; -print", 0),
    # ── "." is the mission dir's ancestor in a builder's worktree too (F5) ──
    (G, T2, "find . -name '*.json' -delete", 2),
    (G, T2, "find . -type f -delete", 2),
    (G, T2, "rm -rf .", 2),
    (G, T1, "rm -rf .", 2),
    (G, T2, "rsync -a --delete /tmp/clean/ ./", 2),
    (G, T2, "find . -name '*.pyc' -delete; rm -rf build dist node_modules", 0),
    (SUP, P, "rm -rf .aladdin/worktrees/T2 && git worktree prune", 0),
    # ── an extract into the mission dir's parent: an archive's mission/ lands over it (F5) ──
    (SUP, P, "tar xzf /tmp/snap.tgz -C docs/agents", 2),
    (SUP, P, "unzip -o /tmp/snap.zip -d docs/agents", 2),
    (G, T2, "cd docs/agents && tar xzf /tmp/snap.tgz", 2),
    (SUP, P, "tar xzf /tmp/fixtures.tgz -C docs/agents/sprint1 && unzip -o /tmp/f.zip -d docs/agents/intents", 0),
    # ── the supervisor's git: a removal that reaches the mission dir (F5 through git) ──
    (SUP, P, "git rm -r -q docs/agents/mission", 2),
    (SUP, P, "git rm docs/agents/mission/eval/evl_1.json", 2),
    (SUP, P, "git rm -r --cached docs", 2),
    (SUP, P, "git clean -fdx", 2),                                             # docs/agents/ is untracked here
    (SUP, P, "git clean -fd", 2),
    (SUP, P, "git stash -u --all", 2),
    (SUP, P, "git stash push --include-untracked", 2),
    (SUP, P, "git clean -fdx src; git clean -n; git stash; git rm -q src/old.py; git checkout -- docs/agents/mission/ledger.jsonl", 0),
    (G, T2, "git clean -fdx; git stash -u", 0),                               # tracked there: clean and stash leave it
    # ── a builder's pathspecs: through an ancestor, without `--`, and with pathspec magic (F11) ──
    (G, T1, "git rm -r -q docs", 2),
    (G, T1, "git rm -r -q docs/agents", 2),
    (G, T1, "git rm -r -q .", 2),
    (G, T1, "git checkout main -- docs/agents/", 2),
    (G, T1, "git checkout main -- .", 2),
    (G, T1, "git restore --source=main docs/", 2),
    (G, T1, "git restore -s main -- .", 2),
    (G, T1, "git checkout main docs/agents/mission/eval/evl_1.json", 2),
    (G, T1, "git reset main docs/agents/mission/eval", 2),
    (G, T1, "git stash push docs/agents/mission/eval", 2),
    (G, T1, "git rm -r ':(glob)docs/agents/mission/**'", 2),
    (G, T1, "git rm -r ':/docs/agents/mission'", 2),
    (G, T1, "git mv docs/agents /tmp/agents", 2),
    (G, T1, "git checkout -- .; git restore .; git reset -- .; git reset HEAD -- .; git checkout HEAD -- src", 0),
    (G, T1, "git checkout main -- src/ && git restore --source=main src/app.py && git rm -r -q src/old", 0),
    (G, T1, "git checkout feature; git stash push -m wip src/app.py; git rm -r ':!docs' src; git reset --hard origin/main", 0),
    # ── mv -t and zip -m remove their sources; zip writes its archive (F6) ──
    (S, P, "mv -t /tmp docs/agents/mission/ledger.jsonl", 2),
    (E, P, "mv --target-directory=/tmp src/app.py", 2),
    (E, P, 'mv -t "$WORK" docs/agents/mission/state/a.json', 2),
    (E, P, "zip -m /tmp/s.zip src/app.py", 2),
    (E, P, "zip -r src/x.zip /tmp/a", 2),
    (E, P, "mv -t /tmp/b docs/agents/mission/eval/a.json; zip -r /tmp/s.zip src", 0),
    # ── git merge-file writes its first file (F10) ──
    (E, P, "git merge-file src/app.py /tmp/b /tmp/o", 2),
    (E, P, "git merge-file -p src/app.py /tmp/b /tmp/o > /tmp/m.py", 0),
    # ── clones, downloads and installs into the cwd (F9) ──
    (PT, P, "gh repo fork rival/app --clone", 2),
    (PT, P, "gh gist clone abc123", 2),
    (PT, P, "gh run download 123 -R rival/app", 2),
    (PT, P, "yarn", 2),
    (PT, P, "pnpm add rival", 2),
    (E, P, "pip install -t src/vendor rival", 2),
    (PT, P, "gh pr checkout 12", 2),
    (PT, P, "gh repo fork rival/app --clone=false; gh gist clone abc123 /tmp/g; gh run download 123 -D /tmp/run", 0),
    (PT, P, "cd /tmp/r && yarn && bun install; pip install --user rival; yarn global add rival", 0),
    # ── over-blocks: a wget that saves nothing, gh -O, a global npm install, pip wheel -w, format-patch --stdout ──
    (ER, P, "wget --spider https://e.com/a.pdf", 0),
    (ER, P, "wget --version", 0),
    (ER, P, "wget --help | head", 0),
    (ER, P, "wget -S --spider https://e.com 2>&1 | grep HTTP", 0),
    (ER, P, "wget -o src/wget.log -O /tmp/a.pdf https://e.com/a.pdf", 2),
    (PT, P, "gh release download v1 -R rival/app -O /tmp/x.tgz", 0),
    (PT, P, "gh release download v1 -R rival/app -O src/x.tgz", 2),
    (PT, P, "npm install -g rival@1.2.3; npm i --global rival; pnpm add -g rival", 0),
    (PT, P, "pip wheel -w /tmp/w rival; pip wheel --wheel-dir=/tmp/w rival", 0),
    (PT, P, "pip wheel rival", 2),
    (E, P, "git format-patch -1 HEAD --stdout > /tmp/x.patch", 0),
    (E, P, "git format-patch --stdout HEAD~1 | head", 0),
    (E, P, "git format-patch -1 HEAD -o src/patches", 2),
    # ── over-blocks: isort's read-only flags, dos2unix -n's input file, unzip with no archive ──
    (S, P, "isort --check-only src/; isort -c src/; isort --diff src/", 0),
    (S, P, "dos2unix -n src/a.py /tmp/a.py", 0),
    (PT, P, "unzip", 0),
    (E, P, "dos2unix -n /tmp/a src/a.py", 2),
    (E, P, "isort src/", 2),
    # ── in-place tools: the files they really rewrite (F12) ──
    (E, P, "cat /tmp/x | sponge -a src/x", 2),
    (E, P, "yq -i '.x = 1' src/c.yaml /tmp/other.yaml", 2),
    (E, P, "ex src/app.py <<< '%s/a/b/g|x'", 2),
    (E, P, "vim +'%s/a/b/g' +wq src/app.py", 2),
    (E, P, "nvim --headless +'%s/a/b/g' +wq src/app.py", 2),
    (E, P, "ruff check --fix", 2),
    (E, P, "ruff format", 2),
    (E, P, "gofmt -l -w src/", 2),
    (E, P, "black -l 100 src/", 2),
    (E, P, "eslint --fix src/", 2),
    (E, P, "clang-format -i src/a.c", 2),
    (E, P, "npx prettier@3 --write src/", 2),
    (E, P, "pnpm dlx prettier --write src", 2),
    (E, P, "npm exec -- prettier --write src", 2),
    (E, P, "mktemp --tmpdir=src x.XXXX", 2),
    (E, P, "split /tmp/big", 2),
    (S, P, "gofmt -l src/; black --check -l 100 src/; ruff format --check; ruff check; eslint src/; clang-format --dry-run src/a.c", 0),
    (S, P, "cat /tmp/x | sponge -a /tmp/y; yq '.x' src/c.yaml; vim --version; mktemp --tmpdir x.XXXX; split -l 9 /tmp/big /tmp/p_", 0),
    (S, P, "npx prettier@3 --check src/; pnpm dlx prettier --check src; cd docs/agents/mission/state && ruff check --fix", 0),
    # ── `aladdin <tool>` runs that aloop tool, and the aloop re-dispatch chain is followed (real-areas F5, X1, F19) ──
    (E, P, "aladdin stats show", 0),
    (E, P, "aladdin sweep docs/agents/sprint1", 0),
    (E, P, "aladdin capabilities; aladdin ids; aladdin config get x", 0),
    (E, P, "aladdin -x; aladdin --list; aloop aladdin status; aloop aloop mission status", 0),
    (E, P, "aladdin intent new --source prompt --raw x", 2),
    (E, P, "aloop aladdin intent new --source prompt --raw x", 2),
    (G, T1, "aloop aladdin mission evaluate", 2),
    (G, T1, "aloop aloop mission evaluate", 2),
    (E, P, "aloop aloop events decide acceptance --reason ok --role human", 2),
    (E, P, "agentic aladdin 'add dark mode'", 2),
    # ── intake is the planner's: no other role checks or normalizes an intent, and no agent emits intake's events ──
    (E, P, "aloop intent check docs/agents/intents/int_x.json", 2),
    (S, P, "intent normalize docs/agents/intents/int_x.json", 2),
    (ER, P, "aladdin intent check docs/agents/intents/int_x.json --mode autonomous", 2),
    (E, P, "aloop events emit intent.normalized --role intake", 2),
    (G, T1, "aloop events emit intent.received --role intake", 2),
    (E, P, "aloop intent summary docs/agents/intents/int_x.json; intent --help", 0),
    (MP, P, "aloop intent check docs/agents/intents/int_x.json --mode autonomous; intent normalize x.json", 0),
    # ── M9.42: setting the judge command blocks; naming it as data does not ──
    (G, T1, "git commit -m \"$(cat <<'EOF'\nM9.42: pin ALOOP_JUDGE_CMD to the supervisor\nEOF\n)\"", 0),
    (G, T1, "bash -c 'grep -rn ALOOP_JUDGE_CMD plugin | wc -l'", 0),
    (G, T1, "python3 -c 'import sys; print(\"ALOOP_JUDGE_CMD\" in open(\"plugin/mission/judge.py\").read())'", 0),
    (G, T1, "python3 -c 'import os; print(os.environ.get(\"ALOOP_JUDGE_CMD\", \"\"))'", 0),
    (E, P, "read -r line < /tmp/x; printf -v out '%s' \"$line\"; declare -n ref=out; mapfile -t rows < /tmp/x", 0),
    (E, P, "echo x | xargs -I{} env ALOOP_JUDGE_CMD={} aloop mission judge run rub_x", 2),
    (E, P, "set -a; read -r ALOOP_JUDGE_CMD <<< 'python3 /tmp/j.py --model {model}'; aloop mission judge run rub_x", 2),
    (E, P, "set -a; printf -v ALOOP_JUDGE_CMD '%s' 'x {model}'; aloop mission judge run rub_x", 2),
    (E, P, "declare -n r=ALOOP_JUDGE_CMD; r='x {model}'; export r; aloop mission judge run rub_x", 2),
    (G, T1, "mapfile -t ALOOP_JUDGE_TIMEOUT < /tmp/t", 2),
    (G, T1, "let ALOOP_JUDGE_TIMEOUT=9", 2),
    (E, P, "perl -e '$ENV{ALOOP_JUDGE_CMD}=\"x {model}\"; exec qw(aloop mission judge run rub_x)'", 2),
    (E, P, "awk 'BEGIN{ENVIRON[\"ALOOP_JUDGE_CMD\"]=\"x\"; system(\"aloop mission judge run rub_x\")}'", 2),
    (G, T1, "python3 -c 'import os; os.putenv(\"ALOOP_JUDGE_CMD\", \"x\")'", 2),
    (G, T1, "python3 -c 'import os, subprocess; subprocess.run([\"aloop\"], env={**os.environ, \"ALOOP_JUDGE_CMD\": \"x\"})'", 2),
    (G, T1, "bash -c 'export ALOOP_JUDGE_TIMEOUT=1; aloop mission status'", 2),
    # ── the bounds that keep judging linear (hook timeout 15 s): a find too large, a climb too deep, too many asks ──
    (G, T1, "find %s %s" % (" ".join("d%d" % i for i in range(400)), " ".join("-exec touch {}/a%d \\;" % i
                                                                             for i in range(300))), 2),
    (SUP, P, "find %s %s" % (" ".join("d%d" % i for i in range(20)), " ".join("-exec touch {}/a%d \\;" % i
                                                                            for i in range(5))), 0),
    (SUP, P, "find src -exec cp /tmp/x {}%s/a \\;" % ("/.." * 33), 2),
    (G, T1, "find src -exec cp /tmp/x {}%s/a \\;" % ("/.." * 32), 0),
    (G, T2, "; ".join("git clean -fd -e a%d ." % i for i in range(17)), 2),
    (G, T2, "; ".join("git clean -fd -e a%d ." % i for i in range(16)) + "; git clean -fd -e a0 .", 0),
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

# Python 3.13 deprecates a positional maxsplit; a later Python would raise, and the gate would fail closed on every export
r = run(E, P, "export FOO=1; declare -x BAR=2")
assert r.returncode == 0 and "Warning" not in r.stderr, r.stderr
print("ok - an export is judged without a Python warning")
print("\n2 checks passed")
sys.exit(0)
