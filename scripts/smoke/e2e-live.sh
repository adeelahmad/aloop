#!/usr/bin/env bash
# End-to-end live run (the north star): a one-line intent becomes merged, tested code in a
# REAL host session with the plugin, the human's only input being approval (given up front
# here). Spends real tokens (dollars, not cents), so it needs --yes; CI does not run it.
#
#   scripts/smoke/e2e-live.sh --yes [--intent semver|wordfreq] [--host claude|codex|opencode]
#                                   [--model M] [--worker-model M] [--dry-run]
#   (or: make e2e-live YES=1 [INTENT=wordfreq] [HOST=opencode])
#
# Intents: semver (one story) · wordfreq (two dependent stories: counting, then top-N ranking).
# Hosts (ROADMAP §4.11, M8: measured runs on every host):
#   claude    `claude -p --plugin-dir` (the reference; the plugin needs no install)
#   opencode  `aloop install opencode` into the run repo, then `opencode run --format json`
#   codex     `aloop install codex` into the run repo, then `codex exec --json --full-auto`.
#             Codex loads the skills/hooks through its own plugin manager, so the owner adds the
#             plugin once first (`codex plugin add aloop@aloop-marketplace`, /hooks → trust).
# Models default to the host's routes (plugin/hosts/routes.json: strong supervisor, economical
# workers); Codex's routes are empty, so it runs the session's own model unless --model is given.
# --dry-run seeds the repo and prints the host command without spending anything.
#
# Measures, in a throw-away repo: did planning produce schema-valid artifacts, did RED come
# before GREEN, did gates run and pass, is the work merged into main, do main's tests pass,
# and what it cost (USD, turns, wall time). Prints the run dir for the full transcript.
set -uo pipefail
[ "${1:-}" = "--yes" ] || { echo "spends tokens: re-run with --yes"; exit 2; }
shift
FIXTURE=semver HOST="${E2E_HOST:-claude}" SUP_MODEL='' WORKER_MODEL='' DRY='' TIMEOUT="${E2E_TIMEOUT:-3600}"
while [ $# -gt 0 ]; do
  case "$1" in
    --intent) FIXTURE="$2"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    --model) SUP_MODEL="$2"; shift 2 ;;
    --worker-model) WORKER_MODEL="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    *) echo "unknown arg: $1"; exit 2 ;;
  esac
done
case "$HOST" in claude|codex|opencode) ;; *) echo "unknown host: $HOST (claude|codex|opencode)"; exit 2 ;; esac
command -v "$HOST" >/dev/null || { echo "$HOST CLI not found"; exit 2; }
case "$FIXTURE" in
  slugify) python3 -m pytest --version >/dev/null 2>&1 || { echo "python3 with pytest needed for INTENT=slugify"; exit 2; } ;;
  *) command -v cargo >/dev/null || { echo "cargo not found"; exit 2; } ;;
esac
PLUGIN="$(cd "$(dirname "$0")/../../plugin" && pwd)"
# The host's own ids for the routes; empty (Codex) means the session's model: pass no --model.
[ -n "$SUP_MODEL" ] || SUP_MODEL="$("$PLUGIN/bin/models" routes --host "$HOST" | sed -n 's/.*strong=\([^ ]*\).*/\1/p')"
[ -n "$WORKER_MODEL" ] || WORKER_MODEL="$("$PLUGIN/bin/models" routes --host "$HOST" | sed -n 's/.*economical=\([^ ]*\).*/\1/p')"
case "$SUP_MODEL" in "<session"*) SUP_MODEL= ;; esac
case "$WORKER_MODEL" in "<session"*) WORKER_MODEL= ;; esac
T="${E2E_DIR:-$(mktemp -d)}"; mkdir -p "$T/repo"; cd "$T/repo" || exit 1

git init -q -b main && git config user.email ada@x.io && git config user.name Ada
case "$FIXTURE" in
  semver)
    CRATE=semver_lite KEYWORD=parse
    INTENT="Plan and build a small Rust library that parses a semver string \"MAJOR.MINOR.PATCH\" into (major, minor, patch), rejecting malformed input. One story, one sprint, TDD." ;;
  wordfreq)
    CRATE=wordfreq KEYWORD=count
    INTENT="Plan and build a small Rust library that (1) counts word frequencies in a text, case-insensitively, where a word is a maximal run of alphanumeric characters, and (2) returns the top N words ordered by count descending, ties alphabetically. Two stories, the second depending on the first; one sprint, TDD." ;;
  slugify)   # a Python intent: the gates hold beyond Rust (RED runs the matrix's pytest)
    CRATE=slugify KEYWORD=slugify
    INTENT="Plan and build a small Python package \"slugify\" with one function slugify(text) that lowercases the text, replaces every run of characters that are not ASCII letters or digits with a single hyphen, and strips leading and trailing hyphens. Tests with pytest. One story, one sprint, TDD." ;;
  *) echo "unknown intent: $FIXTURE (semver|wordfreq|slugify)"; exit 2 ;;
esac
if [ "$FIXTURE" = slugify ]; then
  # python3 -m pytest: a bare `pytest` leaves the repo root off sys.path (run 36)
  SRC=slugify TEST_RE='def test_' TEST_CMD='python3 -m pytest -q' SUPPRESS_RE='pytest\.mark\.(skip|xfail)|pytest\.skip\('
  mkdir -p slugify && printf '"""slugify."""\n' > slugify/__init__.py
  printf '__pycache__/\n.pytest_cache/\n' > .gitignore
else
  SRC=src TEST_RE='#\[test\]' TEST_CMD='cargo test -q' SUPPRESS_RE='#\[ignore\]|cfg\(not\(test\)\)'
  cargo init -q --lib --name "$CRATE" . 2>/dev/null
  printf '//! %s\n' "$CRATE" > src/lib.rs          # no template test: the run must write its own
  # The lockfile is part of the seed: left untracked, the supervisor commits it on main itself
  # (run 16: "Track Cargo.lock"), a commit no task owns and `events trace` cannot explain.
  cargo generate-lockfile -q 2>/dev/null || true
fi
git add -A && git commit -qm init
"$PLUGIN/bin/aloop-init" --apply MODEL_PLANNING="${WORKER_MODEL:-economical}" MODEL_WORKER="${WORKER_MODEL:-economical}" GITIGNORE=yes >/dev/null
# Codex and OpenCode load the agents, skills and hooks from the project: render them in.
[ "$HOST" = claude ] || "$PLUGIN/bin/install" "$HOST" --project . >/dev/null
git add -A && git commit -qm "aloop init" >/dev/null 2>&1 || true

PROMPT="Use the aloop skill. Intent: $INTENT
I am the human. I approve in advance: accept your recommended answer to every question, approve the plan as you propose it, and do not wait for me. Run the whole loop without stopping: retrospective (none yet), intake, standards, planner, then execution (RED, SCAFFOLD, GREEN per task, structural review, final gate), merging each passed task into main. Finish with the final gate green. Then print one line: E2E-DONE."

case "$HOST" in
  claude)   CMD=(claude -p --plugin-dir "$PLUGIN" --model "$SUP_MODEL" --output-format stream-json --verbose --max-turns 400
                 --allowedTools "Agent Task Bash Read Write Edit MultiEdit Glob Grep Skill TaskStop TodoWrite") ;;
  opencode) CMD=(opencode run --format json ${SUP_MODEL:+--model "$SUP_MODEL"} "$PROMPT") ;;
  codex)    CMD=(codex exec --json --full-auto ${SUP_MODEL:+--model "$SUP_MODEL"} "$PROMPT") ;;
esac
echo "host: $HOST · supervisor model: ${SUP_MODEL:-<session model>} · worker model: ${WORKER_MODEL:-<session model>}"
# Claude Code's `--allowedTools` takes every word after it, so its prompt goes on stdin (as an
# argument it became a tool name and `claude -p` exited at once: "Input must be provided").
STDIN_PROMPT=''; [ "$HOST" = claude ] && STDIN_PROMPT=1
if [ -n "$DRY" ]; then echo "cmd: ${CMD[*]}"; [ -n "$STDIN_PROMPT" ] && echo "prompt: stdin"; echo "run dir: $T"; exit 0; fi

# A run started from inside a Claude Code session must not inherit it: live runs 32-34 reported
# the parent's session id and recalled the parent's team memory, so the measured supervisor was
# not a clean one. Drop the session binding, memory store and messaging socket (auth is untouched).
unset CLAUDE_CODE_SESSION_ID CLAUDE_CODE_REMOTE_SESSION_ID CLAUDE_CODE_REMOTE_SDK_URL \
      CLAUDE_COWORK_MEMORY_PATH_OVERRIDE CLAUDE_MEMORY_STORES \
      CLAUDE_CODE_MESSAGING_SOCKET CLAUDE_CODE_MESSAGING_TOKEN
# macOS has no GNU `timeout` (coreutils installs it as gtimeout); perl's alarm survives exec.
if command -v timeout >/dev/null; then TO=(timeout "$TIMEOUT")
elif command -v gtimeout >/dev/null; then TO=(gtimeout "$TIMEOUT")
else TO=(perl -e 'alarm shift; exec @ARGV or die "exec: $!\n"' "$TIMEOUT"); fi
start=$(date +%s)
if [ -n "$STDIN_PROMPT" ]; then
  "${TO[@]}" "${CMD[@]}" <<<"$PROMPT" > "$T/out.jsonl" 2> "$T/err.log"
else
  "${TO[@]}" "${CMD[@]}" > "$T/out.jsonl" 2> "$T/err.log" </dev/null
fi
rc=$?
secs=$(( $(date +%s) - start ))

fails=0; checks=0
check() { checks=$((checks + 1)); if eval "$2"; then echo "ok - $1"; else echo "FAIL - $1"; fails=$((fails + 1)); fi; }
story="$(ls -d docs/agents/sprint1/s1-* 2>/dev/null | head -1)"
check "session finished (exit $rc)" '[ "$rc" -eq 0 ]'
check "planning wrote stories.md + a story dir" '[ -f docs/agents/sprint1/stories.md ] && [ -n "$story" ]'
[ "$FIXTURE" = wordfreq ] && check "planning made both stories" '[ "$(ls -d docs/agents/sprint1/s1-* 2>/dev/null | wc -l)" -ge 2 ]'
check "planning artifacts are schema-valid" \
  '[ -n "$story" ] && find docs/agents/sprint1 -name "*.md" \( -name stories.md -o -name tasks.md -o -name validate.md -o -name plan.md \) | "$PLUGIN/bin/md-db" validate --stdin-list --schema "$PLUGIN/schemas/planning-artifacts.kdl" >/dev/null 2>&1'
check "RED and GREEN gates both passed at least once" \
  'grep -q "\"gate\":\"gate-red-verify\".*\"exit\":0" .aladdin/logs/gates.jsonl 2>/dev/null && grep -q "\"gate\":\"gate-green-verify\".*\"exit\":0" .aladdin/logs/gates.jsonl'
check "work is merged into main ($SRC/ changed since init)" '[ -n "$(git diff --name-only HEAD~1 -- "$SRC" 2>/dev/null; git log --oneline main | sed -n 3p)" ]'
check "main has a test that exercises the library ($KEYWORD)" 'grep -rqsE "$TEST_RE" tests "$SRC" && grep -rqsiE "$KEYWORD" tests "$SRC"'
check "main's tests pass" '$TEST_CMD >/dev/null 2>&1'
check "no suppressed tests on main" '! grep -rqsE "$SUPPRESS_RE" "$SRC" tests'
check "SubagentStop gates ran on the host (not only workers' self-checks)" \
  'grep -q "\"selfcheck\":false" .aladdin/logs/gates.jsonl 2>/dev/null'
check "final gate passed" 'grep -q "\"gate\":\"gate-final\".*\"exit\":0" .aladdin/logs/gates.jsonl 2>/dev/null'
check "no hook crashed" '! grep -q "the gate itself failed" "$T/out.jsonl"'
# Auditable: every commit the run merged traces back to its task from the event stream alone
# (ROADMAP §4.7). `init` and `aloop init` are the two seed commits.
untraced=0; for sha in $(git log --no-merges --format=%H HEAD | head -n -2); do "$PLUGIN/bin/events" trace "$sha" >/dev/null 2>&1 || untraced=$((untraced + 1)); done
check "every merged commit traces to its task (events trace)" '[ "$untraced" -eq 0 ]'

# The measured record (ROADMAP §4.11): summary.json, summary.md and a docs/evidence/README.md row
# in the run dir; the numbers come from the session's `result` lines and the gate log.
"$PLUGIN/bin/run-summary" "$T" --intent "$FIXTURE" --host "$HOST" --checks "$((checks - fails))/$checks" --secs "$secs" --plugin "$PLUGIN"
# Keep the transcript and the repo, drop the build artifacts (a cargo target dir per worktree
# is hundreds of MB; a dozen kept runs filled the disk).
rm -rf "$T"/repo/target "$T"/repo/.claude/worktrees/*/target 2>/dev/null
echo "run dir: $T"
exit $((fails > 0))
