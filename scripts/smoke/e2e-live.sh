#!/usr/bin/env bash
# End-to-end live run (the north star): a one-line intent becomes merged, tested code in a
# REAL Claude Code session with the plugin, the human's only input being approval (given up
# front here). Spends real tokens (dollars, not cents), so it needs --yes; CI does not run it.
#
#   scripts/smoke/e2e-live.sh --yes [--intent semver|wordfreq] [--model sonnet] [--worker-model haiku]
#   (or: make e2e-live YES=1 [INTENT=wordfreq])
#
# Intents: semver (one story) · wordfreq (two dependent stories: counting, then top-N ranking).
#
# Measures, in a throw-away repo: did planning produce schema-valid artifacts, did RED come
# before GREEN, did gates run and pass, is the work merged into main, do main's tests pass,
# and what it cost (USD, turns, wall time). Prints the run dir for the full transcript.
set -uo pipefail
[ "${1:-}" = "--yes" ] || { echo "spends tokens: re-run with --yes"; exit 2; }
shift
FIXTURE=semver SUP_MODEL=sonnet WORKER_MODEL=haiku TIMEOUT="${E2E_TIMEOUT:-3600}"
while [ $# -gt 0 ]; do
  case "$1" in
    --intent) FIXTURE="$2"; shift 2 ;;
    --model) SUP_MODEL="$2"; shift 2 ;;
    --worker-model) WORKER_MODEL="$2"; shift 2 ;;
    *) echo "unknown arg: $1"; exit 2 ;;
  esac
done
command -v claude >/dev/null || { echo "claude CLI not found"; exit 2; }
command -v cargo >/dev/null || { echo "cargo not found"; exit 2; }
PLUGIN="$(cd "$(dirname "$0")/../../plugin" && pwd)"
T="${E2E_DIR:-$(mktemp -d)}"; mkdir -p "$T/repo"; cd "$T/repo" || exit 1

git init -q -b main && git config user.email ada@x.io && git config user.name Ada
case "$FIXTURE" in
  semver)
    CRATE=semver_lite KEYWORD=parse
    INTENT="Plan and build a small Rust library that parses a semver string \"MAJOR.MINOR.PATCH\" into (major, minor, patch), rejecting malformed input. One story, one sprint, TDD." ;;
  wordfreq)
    CRATE=wordfreq KEYWORD=count
    INTENT="Plan and build a small Rust library that (1) counts word frequencies in a text, case-insensitively, where a word is a maximal run of alphanumeric characters, and (2) returns the top N words ordered by count descending, ties alphabetically. Two stories, the second depending on the first; one sprint, TDD." ;;
  *) echo "unknown intent: $FIXTURE (semver|wordfreq)"; exit 2 ;;
esac
cargo init -q --lib --name "$CRATE" . 2>/dev/null
printf '//! %s\n' "$CRATE" > src/lib.rs          # no template test: the run must write its own
# The lockfile is part of the seed: left untracked, the supervisor commits it on main itself
# (run 16: "Track Cargo.lock"), a commit no task owns and `events trace` cannot explain.
cargo generate-lockfile -q 2>/dev/null || true
git add -A && git commit -qm init
"$PLUGIN/bin/aloop-init" --apply MODEL_PLANNING="$WORKER_MODEL" MODEL_WORKER="$WORKER_MODEL" GITIGNORE=yes >/dev/null
git add -A && git commit -qm "aloop init" >/dev/null 2>&1 || true

PROMPT="Use the aloop skill. Intent: $INTENT
I am the human. I approve in advance: accept your recommended answer to every question, approve the plan as you propose it, and do not wait for me. Run the whole loop without stopping: retrospective (none yet), intake, standards, planner, then execution (RED, SCAFFOLD, GREEN per task, structural review, final gate), merging each passed task into main. Finish with the final gate green. Then print one line: E2E-DONE."

start=$(date +%s)
timeout "$TIMEOUT" claude -p --plugin-dir "$PLUGIN" --model "$SUP_MODEL" \
  --output-format stream-json --verbose --max-turns 400 \
  --allowedTools "Agent Task Bash Read Write Edit MultiEdit Glob Grep Skill TaskStop TodoWrite" \
  <<<"$PROMPT" > "$T/out.jsonl" 2> "$T/err.log"
rc=$?
secs=$(( $(date +%s) - start ))

fails=0
check() { if eval "$2"; then echo "ok - $1"; else echo "FAIL - $1"; fails=$((fails + 1)); fi; }
story="$(ls -d docs/agents/sprint1/s1-* 2>/dev/null | head -1)"
check "session finished (exit $rc)" '[ "$rc" -eq 0 ]'
check "planning wrote stories.md + a story dir" '[ -f docs/agents/sprint1/stories.md ] && [ -n "$story" ]'
[ "$FIXTURE" = wordfreq ] && check "planning made both stories" '[ "$(ls -d docs/agents/sprint1/s1-* 2>/dev/null | wc -l)" -ge 2 ]'
check "planning artifacts are schema-valid" \
  '[ -n "$story" ] && find docs/agents/sprint1 -name "*.md" \( -name stories.md -o -name tasks.md -o -name validate.md -o -name plan.md \) | "$PLUGIN/bin/md-db" validate --stdin-list --schema "$PLUGIN/schemas/planning-artifacts.kdl" >/dev/null 2>&1'
check "RED and GREEN gates both passed at least once" \
  'grep -q "\"gate\":\"gate-red-verify\".*\"exit\":0" .agentic/logs/gates.jsonl 2>/dev/null && grep -q "\"gate\":\"gate-green-verify\".*\"exit\":0" .agentic/logs/gates.jsonl'
check "work is merged into main (src/ changed since init)" '[ -n "$(git diff --name-only HEAD~1 -- src 2>/dev/null; git log --oneline main | sed -n 3p)" ]'
check "main has a test that exercises the library ($KEYWORD)" 'grep -rqsE "#\[test\]" tests src && grep -rqsiE "$KEYWORD" tests src'
check "main's tests pass" 'cargo test -q >/dev/null 2>&1'
check "no suppressed tests on main" '! grep -rqsE "#\[ignore\]|cfg\(not\(test\)\)" src tests'
check "SubagentStop gates ran on the host (not only workers' self-checks)" \
  'grep -q "\"selfcheck\":false" .agentic/logs/gates.jsonl 2>/dev/null'
check "final gate passed" 'grep -q "\"gate\":\"gate-final\".*\"exit\":0" .agentic/logs/gates.jsonl 2>/dev/null'
check "no hook crashed" '! grep -q "the gate itself failed" "$T/out.jsonl"'
# Auditable: every commit the run merged traces back to its task from the event stream alone
# (ROADMAP §4.7). `init` and `aloop init` are the two seed commits.
untraced=0; for sha in $(git log --no-merges --format=%H HEAD | head -n -2); do "$PLUGIN/bin/events" trace "$sha" >/dev/null 2>&1 || untraced=$((untraced + 1)); done
check "every merged commit traces to its task (events trace)" '[ "$untraced" -eq 0 ]'

python3 - "$T/out.jsonl" "$secs" <<'PY'
import json, sys
cost = turns = 0
for l in open(sys.argv[1]):
    try: e = json.loads(l)
    except ValueError: continue
    if e.get("type") == "result":
        cost, turns = e.get("total_cost_usd") or 0, e.get("num_turns") or 0
print("cost: $%.2f · supervisor turns: %s · wall: %dm%02ds" % (cost, turns, int(sys.argv[2]) // 60, int(sys.argv[2]) % 60))
PY
# A sub-agent "read" of the plugin's source is any tool call that names a plugin path, except
# passing `--schema $PLUGIN/schemas/…` to md-db, which is a legitimate use of the schema.
echo "sub-agent reads of the plugin's own source: $(python3 - "$T/out.jsonl" "$PLUGIN" <<'PY'
import json, re, sys
n = 0
plug, pat = sys.argv[2], re.compile(r"--schema[= ]+" + re.escape(sys.argv[2]) + r"/schemas/\S*")
for l in open(sys.argv[1]):
    try: e = json.loads(l)
    except ValueError: continue
    m = e.get("message")
    content = m.get("content") if isinstance(m, dict) else None
    for c in content if isinstance(content, list) else []:
        if not isinstance(c, dict) or c.get("type") != "tool_use": continue
        i = c.get("input") or {}
        text = i.get("command") or i.get("file_path") or i.get("path") or ""
        if plug + "/" in pat.sub("", text): n += 1
print(n)
PY
)"
[ -f .agentic/logs/gates.jsonl ] && echo "gate verdicts: $(grep -c . .agentic/logs/gates.jsonl) ($(grep -c '"exit":2' .agentic/logs/gates.jsonl) blocks)"
# Keep the transcript and the repo, drop the build artifacts (a cargo target dir per worktree
# is hundreds of MB; a dozen kept runs filled the disk).
rm -rf "$T"/repo/target "$T"/repo/.claude/worktrees/*/target 2>/dev/null
echo "run dir: $T"
exit $((fails > 0))
