#!/usr/bin/env bash
# End-to-end live run (the north star): a one-line intent becomes merged, tested code in a
# REAL host session with the plugin, the human's only input being approval (given up front
# here). Spends real tokens (dollars, not cents), so it needs --yes; CI does not run it.
#
#   scripts/smoke/e2e-live.sh --yes [--intent semver|wordfreq|slugify|roman|camel|extend|modify|cli] [--host claude|codex|opencode]
#                                   [--model M] [--worker-model M] [--dry-run]
#   (or: make e2e-live YES=1 [INTENT=wordfreq] [HOST=opencode])
#
# Intents: semver (one story) · wordfreq (two dependent stories: counting, then top-N ranking) ·
# slugify (Python, pytest) · roman (Go, go test) · camel (JS, node --test) ·
# extend (brownfield: a working Rust crate with its own tests gets a new function) ·
# modify (brownfield: the same crate's existing parse changes behaviour) ·
# cli (a Rust binary, tested by running it).
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
  roman) command -v go >/dev/null || { echo "go needed for INTENT=roman"; exit 2; } ;;
  camel) command -v node >/dev/null && command -v npm >/dev/null || { echo "node and npm needed for INTENT=camel"; exit 2; } ;;
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
  roman)     # a Go intent: RED runs the matrix's `go test` and blocks a build failure
    CRATE=roman KEYWORD=roman
    INTENT="Plan and build a small Go package \"roman\" (in the roman/ directory of this module) with ToRoman(n int) (string, error) for 1..3999 using subtractive notation, and FromRoman(s string) (int, error) that rejects malformed numerals. Tests with go test. One story, one sprint, TDD." ;;
  camel)     # a JS intent: RED runs the matrix's `npm test` (node --test) and blocks a load failure
    CRATE=camel KEYWORD=camel
    INTENT="Plan and build a small JavaScript ES module src/camel.js exporting camelCase(text), which splits the text into words on any run of characters that are not ASCII letters or digits, lowercases every word, then capitalises the first letter of each word after the first and joins them (\"hello world\" -> \"helloWorld\", \"  Foo-BAR_baz \" -> \"fooBarBaz\", \"\" -> \"\"). Tests with node's built-in test runner (npm test runs node --test), in src/*.test.js files. One story, one sprint, TDD." ;;
  extend)    # brownfield: the run changes a crate that already works and has tests of its own
    CRATE=semver_lite KEYWORD=compare
    INTENT="This repo is an existing Rust library, semver_lite, whose parse(s) -> Result<(u64, u64, u64), String> already works and is tested in tests/parse.rs. Add compare(a, b) -> Result<std::cmp::Ordering, String> that parses both versions with parse and orders them by major, then minor, then patch, returning parse's error for either malformed input. Reuse parse; do not change it or its tests. One story, one sprint, TDD." ;;
  modify)    # brownfield: the run changes a function that already exists, works and is tested
    CRATE=semver_lite KEYWORD=parse
    INTENT="This repo is an existing Rust library, semver_lite, whose parse(s) -> Result<(u64, u64, u64), String> already works and is tested in tests/parse.rs. Change parse so it also accepts an optional leading \"v\" or \"V\" (\"v1.2.3\" parses as (1, 2, 3)) and ignores leading and trailing whitespace; everything it rejects today it still rejects (\"vv1.2.3\", \"v\", \"1.2\"). Keep its signature and its existing tests. One story, one sprint, TDD." ;;
  cli)       # an application, not a library: a binary whose tests run it as a process
    CRATE=minwc KEYWORD=minwc
    INTENT="Plan and build a small Rust command-line tool, minwc, that reads text from stdin and prints counts like wc: with no flags it prints \"<lines> <words> <bytes>\"; -l, -w and -c print only that count; an unknown flag prints a usage line to stderr and exits with status 2. Keep the counting in the library (src/lib.rs) and the argument handling in src/main.rs. Test the binary end to end by running it (env!(\"CARGO_BIN_EXE_minwc\")) from tests/. One sprint, TDD." ;;
  *) echo "unknown intent: $FIXTURE (semver|wordfreq|slugify|roman|camel|extend|modify|cli)"; exit 2 ;;
esac
if [ "$FIXTURE" = camel ]; then
  SRC=src TEST_RE='(^|[^A-Za-z.])(test|it)\(' TEST_CMD='npm test --silent' SUPPRESS_RE='\.(skip|todo)\(|(^|[^A-Za-z])x(it|describe|test)\('
  printf '{\n  "name": "camel",\n  "version": "0.0.0",\n  "type": "module",\n  "private": true,\n  "scripts": { "test": "node --test" }\n}\n' > package.json
  mkdir -p src && printf '// camel: camelCase conversion\n' > src/camel.js
  printf 'node_modules/\n' > .gitignore
elif [ "$FIXTURE" = roman ]; then
  SRC=roman TEST_RE='func Test' TEST_CMD='go test ./...' SUPPRESS_RE='t\.Skip(Now|f)?\('
  # go.mod by hand: seeding needs no go toolchain call (and no network)
  printf 'module example.com/roman\n\ngo 1.21\n' > go.mod
  mkdir -p roman && printf '// Package roman converts integers to and from Roman numerals.\npackage roman\n' > roman/roman.go
elif [ "$FIXTURE" = slugify ]; then
  # python3 -m pytest: a bare `pytest` leaves the repo root off sys.path (run 36)
  SRC=slugify TEST_RE='def test_' TEST_CMD='python3 -m pytest -q' SUPPRESS_RE='pytest\.mark\.(skip|xfail)|pytest\.skip\('
  mkdir -p slugify && printf '"""slugify."""\n' > slugify/__init__.py
  printf '__pycache__/\n.pytest_cache/\n' > .gitignore
elif [ "$FIXTURE" = extend ] || [ "$FIXTURE" = modify ]; then
  SRC=src TEST_RE='#\[test\]' TEST_CMD='cargo test -q' SUPPRESS_RE='#\[ignore\]|cfg\(not\(test\)\)'
  # Cargo.toml by hand (no `cargo init`): the seed is a finished crate, written exactly
  printf '[package]\nname = "semver_lite"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n' > Cargo.toml
  mkdir -p src tests
  cat > src/lib.rs <<'RS'
//! semver_lite: parse "MAJOR.MINOR.PATCH".

/// Parses "MAJOR.MINOR.PATCH" into its three numbers.
pub fn parse(s: &str) -> Result<(u64, u64, u64), String> {
    let parts: Vec<&str> = s.split('.').collect();
    if parts.len() != 3 {
        return Err(format!("expected MAJOR.MINOR.PATCH, got {s:?}"));
    }
    let num = |p: &str| -> Result<u64, String> {
        if p.is_empty() || !p.bytes().all(|b| b.is_ascii_digit()) {
            return Err(format!("not a number: {p:?}"));
        }
        p.parse().map_err(|e| format!("{p:?}: {e}"))
    };
    Ok((num(parts[0])?, num(parts[1])?, num(parts[2])?))
}
RS
  cat > tests/parse.rs <<'RS'
use semver_lite::parse;

#[test]
fn parses_a_version() {
    assert_eq!(parse("1.2.3"), Ok((1, 2, 3)));
}

#[test]
fn rejects_two_parts() {
    assert!(parse("1.2").is_err());
}

#[test]
fn rejects_a_non_number() {
    assert!(parse("1.x.3").is_err());
}
RS
  printf '/target\n' > .gitignore
  cargo generate-lockfile -q 2>/dev/null || true
else
  SRC=src TEST_RE='#\[test\]' TEST_CMD='cargo test -q' SUPPRESS_RE='#\[ignore\]|cfg\(not\(test\)\)'
  cargo init -q --lib --name "$CRATE" . 2>/dev/null
  printf '//! %s\n' "$CRATE" > src/lib.rs          # no template test: the run must write its own
  # cli: a binary next to the library (a stub main; the run writes the argument handling)
  if [ "$FIXTURE" = cli ]; then mkdir -p src && printf 'fn main() {}\n' > src/main.rs; fi
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
# Brownfield: the change kept what was already there (the seed's own tests, unweakened, still pass)
# An application: the merged binary does what the intent says when it is run
[ "$FIXTURE" = cli ] && check "the minwc binary counts stdin (-w) and exits 2 on a bad flag" \
  '[ "$(printf "a b\nc\n" | cargo run -q -- -w 2>/dev/null | tr -d " ")" = 3 ] && { cargo run -q -- --nope </dev/null >/dev/null 2>&1; [ $? -eq 2 ]; }'
case "$FIXTURE" in extend|modify) check "the seed's own tests are still on main" \
  '[ "$(grep -cE "fn (parses_a_version|rejects_two_parts|rejects_a_non_number)\(\)" tests/parse.rs 2>/dev/null)" = 3 ] && cargo test -q --test parse >/dev/null 2>&1' ;; esac
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
