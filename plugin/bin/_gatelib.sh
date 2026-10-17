#!/usr/bin/env bash
# _gatelib.sh — shared helpers for the aloop gate bodies (Rust target).
#
# Gates are wired in hooks.json (SubagentStop / Stop) matched by agent_type. The
# supervisor sets the per-dispatch contract via environment variables; every var
# has a safe fallback so a gate degrades to a WARN rather than a false pass.
#
# Contract (supervisor-set; all optional, with fallbacks):
#   STORY_DIR       absolute path to the story dir holding the append-only init.md +
#                   output.md comms (docs/agents/sprintN/sN-NN-<slug>/). REQUIRED for
#                   worker gates — validate_comms BLOCKS if unset.
#   REPO_DIR        worktree root with source + tests        (default: git toplevel or PWD)
#   STANDARDS_FILE  path to standards.md (the gate matrix)   (default: auto-discovered)
#   BASE_REF        git ref to diff scope against            (default: working-tree diff)
#   TEST_GLOBS      space-sep globs for test files (RED scope)  (default: rust test conventions)
#   SCOPE_GLOBS     space-sep globs this task may touch (GREEN scope) (default: unset → skip)
#   SCAFFOLD_SYMBOLS  space-sep symbols the RED tests reference (scaffold count==1)
#   GATE_RUN_TESTS  1 to actually run cargo (default: 1 if a Cargo.toml is found)
#   GATE_RUN_MATRIX 1 to run the standards matrix (default: 1)
#
# Exit-code contract for callers: 0 pass · 2 block+feedback (stderr) · other = error.

set -uo pipefail

# bin/ensure-tools resolves the REAL backend binaries (bin/md-db + bin/ctx-symbols are
# PATH shims, so `command -v` alone would always succeed and hide a missing backend).
GATELIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
have_tool() { "$GATELIB_DIR/ensure-tools" --resolve "$1" >/dev/null 2>&1; }
export PATH="$GATELIB_DIR:$PATH"   # bare `md-db`/`ctx-symbols` -> shim -> real binary

# shellcheck source=/dev/null
. "$GATELIB_DIR/_paths.sh"

# Every verdict is logged (pass AND block) to .agentic/logs/gates.jsonl — the sprint
# stats count attempts/failures from here, not from anything a model wrote.
_gate_log_verdict() {
  local rc=$?
  mkdir -p "$AGENTIC_LOGS" 2>/dev/null || return 0
  printf '{"ts":"%s","gate":"%s","role":"%s","task":"%s","attempt":"%s","story_dir":"%s","exit":%d,"selfcheck":%s}\n' \
    "$(date -u +%FT%TZ)" "${GATE_NAME:-?}" "${AGENT_ROLE:-}" "${TASK_ID:-}" "${ATTEMPT:-}" "${STORY_DIR:-}" \
    "$rc" "$([ "${AGENTIC_SELFCHECK:-}" = 1 ] && echo true || echo false)" >> "$AGENTIC_LOGS/gates.jsonl" 2>/dev/null
  return "$rc"
}
trap _gate_log_verdict EXIT

warn() { echo "WARN[$GATE_NAME]: $*" >&2; }
fail() { echo "BLOCK[$GATE_NAME]: $*" >&2; exit 2; }
note() { echo "[$GATE_NAME] $*" >&2; }

# Resolve REPO_DIR.
repo_dir() {
  if [ -n "${REPO_DIR:-}" ]; then echo "$REPO_DIR"; return; fi
  git rev-parse --show-toplevel 2>/dev/null || pwd
}

# Hosts without built-in sub-agent isolation (Codex, OpenCode) run the SubagentStop gate
# in the session's cwd — the MAIN tree. A worker there bound its worktree with
# `aloop bind` (host-adapter recorded it under .agentic/state/agents/<agent_id>.json),
# so the gate moves into that worktree before checking anything. Reads the hook payload
# from stdin with a bounded wait (a self-check has no payload and just proceeds).
_gate_enter_bound_worktree() {
  [ -z "${REPO_DIR:-}" ] || return 0
  [ -t 0 ] && return 0
  local payload="" aid reg wt
  IFS= read -r -d '' -t 2 payload || true
  aid="$(printf '%s' "$payload" | sed -nE 's/.*"agent_id"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' | head -1)"
  [ -n "$aid" ] || return 0
  reg="$AGENTIC_STATE/agents/$(printf '%s' "$aid" | tr -c 'A-Za-z0-9_.-' '_').json"
  [ -f "$reg" ] || return 0
  wt="$(sed -nE 's/.*"worktree"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' "$reg" | head -1)"
  if [ -n "$wt" ] && [ -d "$wt" ]; then export REPO_DIR="$wt"; cd "$wt" || return 0; fi
}
_gate_enter_bound_worktree

# Per-worktree task contract the supervisor writes at dispatch (.agentic/task.env):
# TASK_ID, ATTEMPT, AGENT_ROLE, SCOPE_GLOBS, SCAFFOLD_SYMBOLS, BASE_REF, STORY_DIR,
# AGENTIC_TRANSCRIPTS_DIR. Real env still overrides (load is non-clobbering only for unset).
load_task_env() {
  local te; te="$(repo_dir)/.agentic/task.env"
  [ -f "$te" ] || return 0
  # shellcheck source=/dev/null
  set -a; . "$te"; set +a
}
load_task_env

# Budget overrun release (bin/budget). A worker that hit its HARD time/token limit is
# being stopped — its SubagentStop gate must not block the stop (exit 2 would make it keep
# working past the limit). It is released WITHOUT verification: the attempt is NOT a pass,
# the supervisor never merges it, and the task goes to a mid-sprint re-plan. (A worker
# forging the marker gains nothing: the only outcome is its task being re-planned.)
budget_release() {
  local m; m="$(repo_dir)/.agentic/budget-exceeded"
  [ -f "$m" ] || return 0
  case "${AGENT_ROLE:-}" in red-worker|scaffolder|green-worker) ;; *) return 0 ;; esac
  grep -q "role=${AGENT_ROLE} attempt=${ATTEMPT:-}" "$m" 2>/dev/null || return 0
  echo "[${GATE_NAME:-gate}] BUDGET-EXCEEDED — stop released, NOT verified: $(head -1 "$m")" >&2
  echo "  This attempt is not a pass. Supervisor: do not merge; TaskStop/mark-killed, re-plan." >&2
  exit 0
}
budget_release

# Story-bound, append-only inter-agent comms (init.md <-> output.md) — THE channel,
# not a throwaway. Per story: the supervisor APPENDS one block to init.md per dispatch
# (the contract/feedback it sends the agent); the agent APPENDS one block to output.md
# per attempt (its report back). A later agent in the chain reads the prior blocks.
# Block header: `## <task> · attempt <N> · <role> · <ISO8601>`; sub-sections are `###`.
#
# ENFORCED, not advisory: a dispatched role MUST leave a well-formed latest output.md
# block from itself — missing STORY_DIR, missing output.md, a stale last block, or a
# missing required sub-section all BLOCK (exit 2). md-db still validates the static top
# frontmatter; the dynamic `##` blocks are grep-checked (md-db can't enumerate them).
validate_comms() {
  local dir="${STORY_DIR:-${1:-}}"
  [ -n "$dir" ] || fail "STORY_DIR unset — cannot find the story's comms (init.md/output.md). The supervisor must set STORY_DIR in .agentic/task.env (absolute path to docs/agents/sprintN/sN-NN-<slug>/)."
  local out="$dir/output.md" ini="$dir/init.md"

  # init.md: the supervisor's inbound contract must exist (this is how you were briefed).
  [ -f "$ini" ] || fail "no init.md in $dir — the supervisor must APPEND a dispatch block to the story's init.md before dispatch."
  frontmatter_type "$ini" init || fail "init.md missing top frontmatter 'type: init'"

  # output.md: the agent must have appended its report block.
  [ -f "$out" ] || fail "no output.md in $dir — APPEND your report block to the story's output.md. This is the comms channel between agents, not optional."
  frontmatter_type "$out" output || fail "output.md missing top frontmatter 'type: output'"

  # The LAST appended block must be THIS dispatch's (role match) and well-formed.
  local last; last="$(awk '/^## /{n=NR} END{print n+0}' "$out")"
  [ "${last:-0}" -gt 0 ] || fail "output.md has no '## <task> · attempt N · <role> · <ts>' block — append your report."
  local block; block="$(awk -v s="$last" 'NR>=s' "$out")"
  if [ -n "${AGENT_ROLE:-}" ]; then
    printf '%s\n' "$block" | head -1 | grep -q "$AGENT_ROLE" \
      || fail "the latest output.md block is not from '$AGENT_ROLE' — append YOUR report block for this dispatch (one block per attempt, append-only)."
  fi
  printf '%s\n' "$block" | grep -qE '^###[[:space:]]*Summary' || fail "latest output.md block missing '### Summary'"
  printf '%s\n' "$block" | grep -qE '^###[[:space:]]*Result'  || fail "latest output.md block missing '### Result'"
  printf '%s\n' "$block" | grep -qE '^###[[:space:]]*Next'    || fail "latest output.md block missing '### Next'"
  note "comms ok: latest output.md block is a well-formed '$AGENT_ROLE' report in $(basename "$dir")"
}

# True if $1 opens with YAML frontmatter carrying `type: $2`. md-db validates this
# region too (it is the static, single-frontmatter part of the append-only file).
frontmatter_type() {
  awk 'NR==1&&/^---/{f=1;next} f&&/^---/{exit} f' "$1" | grep -qiE "^type:[[:space:]]*$2([[:space:]]|$)"
}

# Echo the LATEST `## ...` block (its header line → EOF) of an append-only comms file.
# Gates that parse a worker's report (e.g. RED's Result table) must read only the most
# recent block, not the whole accumulated history. Returns the whole file if no block.
latest_block() {
  local f="$1"; [ -f "$f" ] || return 1
  local s; s="$(awk '/^## /{n=NR} END{print n+0}' "$f")"
  if [ "${s:-0}" -gt 0 ]; then awk -v s="$s" 'NR>=s' "$f"; else cat "$f"; fi
}

# True if ctx-symbols is available.
have_ctx_symbols() { have_tool ctx-symbols; }

# Count definitions of a symbol; echoes an integer. Uses ctx-symbols, else grep.
symbol_count() {
  local sym="$1" root="$2"
  if have_ctx_symbols; then
    ctx-symbols count "$sym" --tree "$root"
  else
    # grep fallback (fragile: matches fn/struct/etc. definitions only, name-anchored)
    { grep -rohE "\\b(fn|struct|enum|trait|type|const|static|mod)\\s+$sym\\b" \
        --include='*.rs' "$root" 2>/dev/null | wc -l | tr -d ' '; } || echo 0
  fi
}

# Build artifacts that are never "worker edits" — excluded from diff-scope checks.
# `|| true` so an all-artifacts diff (grep -v matches nothing) is success, not exit 1.
changed_paths_filter() { grep -vE '(^|/)(target/|Cargo\.lock$|\.git/|\.agentic/|docs/agents/)' || true; }

# Changed paths in REPO_DIR (one per line). Uses BASE_REF if set, else working tree.
changed_paths() {
  local root; root="$(repo_dir)"
  if [ -n "${BASE_REF:-}" ]; then
    git -C "$root" diff --name-only "$BASE_REF" 2>/dev/null | changed_paths_filter
  else
    { git -C "$root" diff --name-only 2>/dev/null
      git -C "$root" diff --name-only --cached 2>/dev/null
      git -C "$root" ls-files --others --exclude-standard 2>/dev/null; } | sort -u | changed_paths_filter
  fi
}

# True if $1 matches any space-separated glob in $2.
path_matches_any() {
  local path="$1" globs="$2" g
  for g in $globs; do
    case "$path" in $g) return 0;; esac
  done
  return 1
}

# Suppression patterns that must never appear in source. exit 2 if found.
assert_no_suppression() {
  local root; root="$(repo_dir)"
  if grep -rEn '#\[ignore\]|\.skip\(|(^|[^A-Za-z])xit\(|#\[cfg\(not\(test\)\)\]' \
      --include='*.rs' "$root" 2>/dev/null \
      | grep -v '/target/' >/dev/null 2>&1; then
    fail "test-suppression pattern present (no #[ignore]/.skip/xit/cfg(not(test)))"
  fi
}

# Worktree-isolation invariant. A writing worker (red/scaffolder/green) MUST run in a
# linked git worktree, never the shared/main working tree: diff-scoping, clean
# abandon-on-HALT, and parallel safety all depend on it. Detection is deterministic —
# a linked worktree's .git is a FILE (a `gitdir:` pointer); the main tree's .git is a
# DIRECTORY. A memory/environment note (e.g. low disk) may change the SCHEDULE
# (serialize waves, one worktree at a time, clean up between tasks) but must NEVER drop
# isolation; that is not a supervisor-adjudicable adaptation. SKIP_HOOKS=1 overrides.
assert_worktree_isolation() {
  [ "${SKIP_HOOKS:-}" = "1" ] && return 0
  case "${AGENT_ROLE:-}" in red-worker|scaffolder|green-worker) ;; *) return 0 ;; esac
  local root; root="$(repo_dir)"
  if [ -d "$root/.git" ]; then
    fail "worktree isolation dropped: ${AGENT_ROLE} ran in the SHARED tree ($root), not a
  linked worktree. Isolation is an INVARIANT, not a schedule choice — re-dispatch this
  task with isolation: \"worktree\". Under disk/resource pressure, SERIALIZE worktrees
  (one at a time, removed between tasks); do not fall back to the shared tree. If a
  constraint truly forces relaxing an invariant, ESCALATE to planning — never
  self-authorize. (Deliberate override? SKIP_HOOKS=1.)"
  fi
}

# Run the gate matrix declared in standards.md (standards-bind-gates). Falls back
# to a default Rust matrix with a WARN if standards.md / its matrix is not found.
run_standards_matrix() {
  [ "${GATE_RUN_MATRIX:-1}" = "1" ] || { note "matrix skipped (GATE_RUN_MATRIX=0)"; return 0; }
  local root; root="$(repo_dir)"
  local sf="${STANDARDS_FILE:-}"
  if [ -z "$sf" ]; then
    sf="$(find "$root" -maxdepth 6 -name standards.md 2>/dev/null | head -1)"
  fi

  local -a cmds=()
  if [ -n "$sf" ] && [ -f "$sf" ]; then
    # Extract fenced command lines under a "Gate matrix" / "Cross-cutting gates" heading.
    mapfile -t cmds < <(awk '
      /^#+.*([Gg]ate matrix|[Cc]ross-cutting gates)/ {insec=1; next}
      insec && /^#+[[:space:]]/                        {insec=0}
      insec && /^```/                                  {infence=!infence; next}
      insec && infence && NF                           {print}
    ' "$sf")
  fi

  if [ "${#cmds[@]}" -eq 0 ]; then
    warn "no matrix found in standards.md; using DEFAULT Rust matrix (weaker: not standards-bound)"
    cmds=(
      "cargo fmt --all -- --check"
      "cargo clippy --workspace --all-targets -- -D warnings"
      "cargo test --workspace"
    )
  else
    note "running ${#cmds[@]} gate command(s) from $(basename "$sf")"
  fi

  local c
  for c in "${cmds[@]}"; do
    note "matrix: $c"
    ( cd "$root" && eval "$c" ) || fail "matrix command failed: $c"
  done
}

# Detect a cargo project under REPO_DIR.
is_cargo_repo() { [ -f "$(repo_dir)/Cargo.toml" ] || find "$(repo_dir)" -maxdepth 2 -name Cargo.toml 2>/dev/null | head -1 | grep -q .; }
