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
# shellcheck source=/dev/null
. "$GATELIB_DIR/_failclosed.sh"
# A block's reason is logged as a checksum, so the same block repeating is detectable.
GATE_BLOCK_REASON=""
_gate_log_verdict() {
  local rc=$?
  _gate_fail_closed "$rc"; rc=$?                 # a crashed gate blocks (see _failclosed.sh)
  mkdir -p "$AGENTIC_LOGS" 2>/dev/null || return "$rc"
  local self rh="" released=""
  self="$([ "${AGENTIC_SELFCHECK:-}" = 1 ] && echo true || echo false)"
  if [ "$rc" -eq 2 ]; then
    rh="$(printf '%s' "$GATE_BLOCK_REASON" | cksum | cut -d' ' -f1)"
    _gate_stuck "$rh" "$self" && { rc=0; released=',"released":"stuck"'; }
  fi
  printf '{"ts":"%s","gate":"%s","role":"%s","task":"%s","attempt":"%s","story_dir":"%s","wt":"%s","exit":%d,"selfcheck":%s,"reason":"%s"%s}\n' \
    "$(date -u +%FT%TZ)" "${GATE_NAME:-?}" "${AGENT_ROLE:-}" "${TASK_ID:-}" "${ATTEMPT:-}" "${STORY_DIR:-}" "$(repo_dir)" \
    "$rc" "$self" "$rh" "$released" >> "$AGENTIC_LOGS/gates.jsonl" 2>/dev/null
  # the aladdin.event/1 stream: real verdicts only (a self-check is not a gate run)
  if [ "${AGENTIC_SELFCHECK:-}" != 1 ] && [ "$rc" -le 2 ]; then
    # a stuck release exits 0 but is NOT a pass: it is a gate.failed with released=stuck
    "$GATELIB_DIR/events" emit "$([ "$rc" = 0 ] && [ -z "$released" ] && echo gate.passed || echo gate.failed)" \
      --task "${TASK_ID:-}" --attempt "${ATTEMPT:-}" --role "${AGENT_ROLE:-}" \
      --ref "story_dir=${STORY_DIR:-}" --data "gate=${GATE_NAME:-?}" --data "exit=$rc" \
      ${released:+--data "released=stuck"} >/dev/null 2>&1 || true
  fi
  return "$rc"
}
trap '_gate_log_verdict; exit $?' EXIT

# Stuck release. A worker that hits the SAME block AGENTIC_STUCK_BLOCKS times in a row (same
# gate, task and attempt, host-run verdicts only) is not going to fix it: in live run 10 a RED
# worker stopped 29 times on one unchanged reason and burnt its whole time box. The Nth
# identical block releases the stop UNVERIFIED: not a pass, never merged. The release leaves
# `.agentic/state/stuck/<TASK_ID>.<ATTEMPT>` with the reason, which `budget watch` reports as
# a STUCK line; the supervisor re-dispatches the task with feedback that addresses the
# reason (a new attempt starts the count over), or re-plans it. A different reason resets the
# count, and a self-check never releases. The key includes the worktree, so one worker's
# repeats count and a re-dispatch in a fresh worktree starts over. Returns 0 on the release.
_gate_stuck() {
  local rh="$1" self="$2" n="${AGENTIC_STUCK_BLOCKS:-3}" prior log="$AGENTIC_LOGS/gates.jsonl"
  [ "$self" = false ] && [ -n "${TASK_ID:-}" ] && [ -n "${ATTEMPT:-}" ] || return 1
  # The build roles, plus the two read-only roles whose blocks can name a defect only the
  # supervisor can act on (live e2e run 12: a HIGH structural finding blocked the reviewer 10x).
  case "${AGENT_ROLE:-}" in red-worker|scaffolder|green-worker|structural-reviewer|final-gate) ;; *) return 1 ;; esac
  [ "$n" -ge 2 ] 2>/dev/null && [ -f "$log" ] || return 1
  prior="$(grep -F "\"gate\":\"${GATE_NAME:-?}\"" "$log" | grep -F "\"task\":\"$TASK_ID\",\"attempt\":\"$ATTEMPT\"" \
    | grep -F "\"wt\":\"$(repo_dir)\"" | grep -F '"selfcheck":false' | tail -n $((n - 1)) | grep -c -F "\"exit\":2,\"selfcheck\":false,\"reason\":\"$rh\"}")"
  [ "$prior" -ge $((n - 1)) ] || return 1
  mkdir -p "$AGENTIC_STATE/stuck" 2>/dev/null
  printf 'role=%s gate=%s task=%s attempt=%s blocks=%s\n%s\n' "${AGENT_ROLE:-}" "${GATE_NAME:-?}" "$TASK_ID" "$ATTEMPT" "$n" \
    "$GATE_BLOCK_REASON" > "$AGENTIC_STATE/stuck/$TASK_ID.$ATTEMPT" 2>/dev/null
  echo "[${GATE_NAME:-gate}] STUCK — the same block $n times in a row; stop released, NOT verified." >&2
  echo "  This attempt is not a pass. Supervisor: do not merge; discard the worktree and re-dispatch" >&2
  echo "  $TASK_ID with a Feedback block that addresses the reason (.agentic/state/stuck/$TASK_ID.$ATTEMPT), or re-plan." >&2
  return 0
}

warn() { echo "WARN[$GATE_NAME]: $*" >&2; }
fail() { GATE_BLOCK_REASON="$*"; echo "BLOCK[$GATE_NAME]: $*" >&2; exit 2; }
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
# The hook payload (stdin), read once with a bounded wait; a self-check has none.
GATE_PAYLOAD=""
[ -t 0 ] || IFS= read -r -d '' -t 2 GATE_PAYLOAD || true
_gate_enter_bound_worktree() {
  [ -z "${REPO_DIR:-}" ] || return 0
  local payload="$GATE_PAYLOAD" aid reg wt
  aid="$(printf '%s' "$payload" | sed -nE 's/.*"agent_id"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' | head -1)"
  [ -n "$aid" ] || return 0
  # Claude Code's `isolation: "worktree"` makes <main>/.claude/worktrees/agent-<agent_id>: the
  # host's own record, so it wins. The agent may have cd'd back to the main tree (to read its
  # story), so its cwd can't decide. Else a Codex/OpenCode `aloop bind` registration.
  wt="$AGENTIC_PROJECT/.claude/worktrees/agent-$(printf '%s' "$aid" | tr -c 'A-Za-z0-9_.-' '_')"
  if [ ! -e "$wt/.git" ]; then
    reg="$AGENTIC_STATE/agents/$(printf '%s' "$aid" | tr -c 'A-Za-z0-9_.-' '_').json"
    [ -f "$reg" ] || return 0
    wt="$(sed -nE 's/.*"worktree"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' "$reg" | head -1)"
  fi
  if [ -n "$wt" ] && [ -d "$wt" ]; then export REPO_DIR="$wt"; cd "$wt" || return 0; fi
}
_gate_enter_bound_worktree

# The per-task contract: TASK_ID, ATTEMPT, AGENT_ROLE, SCOPE_GLOBS, SCAFFOLD_SYMBOLS,
# BASE_REF, STORY_DIR, AGENTIC_TRANSCRIPTS_DIR (+ BUDGET_*, read by bin/budget). Real env
# still overrides (only unset keys are loaded).
#
# WHERE IT LIVES. The supervisor writes it in the MAIN tree's run-data dir (`aloop layout show`):
# .aladdin/state/tasks/<TASK_ID>.env, or .agentic/... in a legacy project (both are searched),
# and names the task on the dispatch prompt's first line (`TASK_ID: <id>`). On Claude Code
# `isolation: "worktree"` creates the worktree (.claude/worktrees/agent-<id>) at dispatch, so
# no one can pre-write a task.env inside it. The host records the dispatch prompt in the
# sub-agent transcript (SubagentStop's agent_transcript_path), so the task id comes from the
# host, not from the worker. A worktree task.env copy (.aladdin/ or .agentic/; Codex/OpenCode, older sprints) is
# only a hint: when the contract exists, any key the copy sets must match it, or the gate
# blocks; with no contract the copy is the contract, as before.
#
# PARSED, NEVER SOURCED. The worktree copy is the worker's to edit: sourcing it let `exit 0`
# pass the gate, and GATE_RUN_MATRIX=0 / SKIP_HOOKS=1 switch checks off. Only the contract
# keys are read, as plain KEY=VALUE data (one layer of quotes stripped, no expansion).
TASK_ENV_KEYS="TASK_ID ATTEMPT AGENT_ROLE SCOPE_GLOBS SCAFFOLD_SYMBOLS BASE_REF STORY_DIR AGENTIC_TRANSCRIPTS_DIR"
# Print the contract keys of a task.env-format file as KEY<TAB>VALUE lines.
_task_env_kv() {
  local line k v
  [ -f "$1" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line#export }"
    case "$line" in ''|'#'*) continue ;; *=*) ;; *) continue ;; esac
    k="${line%%=*}"; v="${line#*=}"
    case " $TASK_ENV_KEYS " in *" $k "*) ;; BUDGET_*) case "$k" in *[!A-Z_]*) continue ;; esac ;; *) continue ;; esac
    case "$v" in \"*\") v="${v#\"}"; v="${v%\"}" ;; \'*\') v="${v#\'}"; v="${v%\'}" ;; esac
    printf '%s\t%s\n' "$k" "$v"
  done < "$1"
}
# The TASK_ID the host's dispatch prompt names (first user turn of the sub-agent transcript).
_host_task_id() {
  local tp
  tp="$(printf '%s' "$GATE_PAYLOAD" | sed -nE 's/.*"agent_transcript_path"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' | head -1)"
  [ -n "$tp" ] && [ -f "$tp" ] || return 0
  grep -m1 '"type"[[:space:]]*:[[:space:]]*"user"' "$tp" 2>/dev/null \
    | sed -nE 's/.*TASK_ID:[[:space:]]*([A-Za-z0-9._-]+).*/\1/p' | head -1
}
# First value of contract key $2 in task.env-format file $1 (the loader's first-wins rule).
_task_env_get() {
  local k v
  while IFS=$'\t' read -r k v; do
    [ "$k" = "$2" ] && { printf '%s\n' "$v"; return 0; }
  done < <(_task_env_kv "$1")
  return 1
}
load_task_env() {
  local te contract="" tid k v cv
  te="$(agentic_marker "$(repo_dir)" task.env)" || true
  # Which task: the host's dispatch prompt, else the task worktree's own name
  # (.agentic/worktrees/<TASK_ID>, made by `aloop task-worktree add`), else the copy's hint.
  tid="$(_host_task_id)"
  if [ -z "$tid" ]; then
    case "$(repo_dir)" in "$AGENTIC_HOME"/worktrees/*) tid="$(basename "$(repo_dir)")" ;; esac
    agentic_contract_path "$tid" >/dev/null || tid=""
  fi
  [ -n "$tid" ] || tid="$(_task_env_get "$te" TASK_ID)"
  contract="$(agentic_contract_path "$tid")" || contract=""
  # A copy OLDER than the contract is stale (the supervisor re-dispatched and bumped the
  # contract; bin/budget refreshes the copy on the worker's next tool call), not tampering:
  # a worker's edit makes the copy newer. Only a copy at least as new is compared.
  if [ -n "$contract" ] && [ -f "$te" ] && [ "$te" -ot "$contract" ]; then
    note "the worktree's task.env copy ($te) is older than the contract $contract; using the contract"
  elif [ -n "$contract" ]; then
    while IFS=$'\t' read -r k v; do
      cv="$(_task_env_get "$contract" "$k")" || cv=""
      if [ "$cv" != "$v" ]; then
        fail "the worktree's task.env copy ($te) disagrees with the supervisor's contract on $k ('$v' vs '$cv' in $contract). The contract is the supervisor's: do not edit it."
      fi
    done < <(_task_env_kv "$te")
  fi
  [ -n "$contract" ] && te="$contract"
  TASK_CONTRACT="$contract"
  while IFS=$'\t' read -r k v; do
    case "$k" in ''|BUDGET_*) continue ;; esac
    [ -n "${!k+x}" ] && continue
    export "$k=$v"
  done < <(_task_env_kv "$te")
  # list keys: space- or comma-separated (supervisors write both)
  [ -z "${SCOPE_GLOBS:-}" ] || export SCOPE_GLOBS="${SCOPE_GLOBS//,/ }"
  [ -z "${SCAFFOLD_SYMBOLS:-}" ] || export SCAFFOLD_SYMBOLS="${SCAFFOLD_SYMBOLS//,/ }"
  [ -z "${TEST_GLOBS:-}" ] || export TEST_GLOBS="${TEST_GLOBS//,/ }"
}
load_task_env

# The role comes from the HOST (the hook payload's agent_type), not from task.env, which the
# worker can edit: claiming AGENT_ROLE=planner would otherwise skip the worktree-isolation
# check that only applies to writing roles. A contradiction is tampering (or a mis-dispatch).
_gate_bind_role() {
  local at role
  at="$(printf '%s' "$GATE_PAYLOAD" | sed -nE 's/.*"agent_type"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' | head -1)"
  [ -n "$at" ] || return 0
  role="${at##*:}"
  if [ -n "${AGENT_ROLE:-}" ] && [ "$AGENT_ROLE" != "$role" ] && [ -n "${TASK_CONTRACT:-}" ]; then
    # the supervisor's own contract: a slip the worker cannot fix, so never block on it
    warn "the task contract says AGENT_ROLE=$AGENT_ROLE but the host dispatched a '$role'; gating as '$role'."
  elif [ -n "${AGENT_ROLE:-}" ] && [ "$AGENT_ROLE" != "$role" ]; then
    fail "task.env says AGENT_ROLE=$AGENT_ROLE but the host dispatched a '$role' — the contract was edited or the wrong agent was dispatched for this worktree."
  fi
  export AGENT_ROLE="$role"
}
_gate_bind_role

# Budget overrun release (bin/budget). A worker that hit its HARD time/token limit is
# being stopped — its SubagentStop gate must not block the stop (exit 2 would make it keep
# working past the limit). It is released WITHOUT verification: the attempt is NOT a pass,
# the supervisor never merges it, and the task goes to a mid-sprint re-plan. (A worker
# forging the marker gains nothing: the only outcome is its task being re-planned.)
budget_release() {
  local m; m="$(agentic_marker "$(repo_dir)" budget-exceeded)" || return 0
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
  [ -n "$dir" ] || fail "STORY_DIR unset — cannot find the story's comms (init.md/output.md). The supervisor must set STORY_DIR in the task contract <run-data>/state/tasks/<TASK_ID>.env (\`aloop layout show\`) and name TASK_ID on the dispatch prompt's first line (absolute path to docs/agents/sprintN/sN-NN-<slug>/; a whole-wave structural review uses the sprint dir docs/agents/sprintN/)."
  local out="$dir/output.md" ini="$dir/init.md"

  # init.md: the supervisor's inbound contract must exist (this is how you were briefed).
  [ -f "$ini" ] || fail "no init.md in $dir — the supervisor must APPEND a dispatch block to the story's init.md before dispatch."
  frontmatter_type "$ini" init || fail "init.md missing top frontmatter 'type: init'"

  # output.md: the agent must have appended its report block.
  local how="append it from your worktree with a shell heredoc: cat >> \"$dir/output.md\" <<'EOF' … EOF (create the file with a 'type: output' frontmatter first if it is missing). The Write/Edit tools refuse a path outside your worktree, and docs/agents/ lives only in the main tree."
  [ -f "$out" ] || fail "no output.md in $dir — APPEND your report block to the story's output.md. This is the comms channel between agents, not optional. How: $how"
  frontmatter_type "$out" output || fail "output.md missing top frontmatter 'type: output'"

  # The LAST appended block must be THIS dispatch's (role match) and well-formed.
  local last; last="$(awk '/^## /{n=NR} END{print n+0}' "$out")"
  [ "${last:-0}" -gt 0 ] || fail "output.md has no '## <task> · attempt N · <role> · <ts>' block — append your report. How: $how"
  local block; block="$(awk -v s="$last" 'NR>=s' "$out")"
  if [ -n "${AGENT_ROLE:-}" ]; then
    printf '%s\n' "$block" | head -1 | grep -q "$AGENT_ROLE" \
      || fail "the latest output.md block is not from '$AGENT_ROLE' — append YOUR report block for this dispatch (one block per attempt, append-only)."
  fi
  printf '%s\n' "$block" | grep -qE '^###[[:space:]]*Summary' || fail "latest output.md block missing '### Summary'"
  printf '%s\n' "$block" | grep -qE '^###[[:space:]]*Result'  || fail "latest output.md block missing '### Result'"
  printf '%s\n' "$block" | grep -qE '^###[[:space:]]*Next'    || fail "latest output.md block missing '### Next'"
  note "comms ok: latest output.md block is a well-formed '${AGENT_ROLE:-?}' report in $(basename "$dir")"
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
changed_paths_filter() { grep -vE '(^|/)(target/|Cargo\.lock$|\.git/|\.agentic/|\.aladdin/|docs/agents/)' || true; }

# Changed paths in REPO_DIR (one per line). Uses BASE_REF if set, else working tree.
changed_paths() {
  local root; root="$(repo_dir)"
  if [ -n "${BASE_REF:-}" ]; then
    git -C "$root" diff --name-only "$BASE_REF" 2>/dev/null | changed_paths_filter
  else
    # Workers commit their work, so the uncommitted diff alone would miss it: also take
    # what this branch committed since it left the main tree's HEAD.
    local common main_head base=""
    common="$(git -C "$root" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
    main_head="$(git -C "${common%/.git}" rev-parse HEAD 2>/dev/null)"
    [ -n "$main_head" ] && base="$(git -C "$root" merge-base HEAD "$main_head" 2>/dev/null)"
    { [ -n "$base" ] && git -C "$root" diff --name-only "$base" HEAD 2>/dev/null
      git -C "$root" diff --name-only 2>/dev/null
      git -C "$root" diff --name-only --cached 2>/dev/null
      git -C "$root" ls-files --others --exclude-standard 2>/dev/null; } | sort -u | changed_paths_filter
  fi
}

# True if $1 matches any space-separated glob in $2.
path_matches_any() {
  # Split on whitespace WITHOUT pathname expansion: `tests/**` must stay a pattern, not
  # become the files that happen to exist in the cwd (live e2e run 8).
  local path="$1" g
  local -a globs
  read -r -a globs <<<"$2"
  for g in "${globs[@]}"; do
    # shellcheck disable=SC2254  # $g is a glob pattern on purpose
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

# The work must be COMMITTED on the worktree's branch: the supervisor merges that branch, and
# an uncommitted edit is either lost or copied into main by hand, past the gate's diff.
assert_work_committed() {
  [ "${SKIP_HOOKS:-}" = "1" ] && return 0
  case "${AGENT_ROLE:-}" in red-worker|scaffolder|green-worker) ;; *) return 0 ;; esac
  local dirty
  dirty="$(git -C "$(repo_dir)" status --porcelain --untracked-files=all 2>/dev/null \
    | awk '!($1 == "??" && $NF ~ /(^|\/)Cargo\.lock$/) {print $NF}' \
    | grep -vE '^(\.agentic/|\.aladdin/|\.transcripts/)' | head -5)"   # an untracked Cargo.lock is cargo's, not the worker's
  [ -z "$dirty" ] || fail "uncommitted work in your worktree — commit it on this branch (git add -A && git commit -m '<task> <phase>') so the supervisor can merge it:
$(printf '    %s\n' $dirty)"
}

# The matrix's FORMAT checks only. RED and SCAFFOLD files must already be formatted: GREEN may
# not touch the tests, so an unformatted RED file would block every GREEN attempt.
FORMAT_CHECK_RE='(^|[[:space:]/])(cargo fmt|rustfmt|gofmt|prettier|black|ruff format)([[:space:]]|$)'
run_format_checks() { run_standards_matrix "$FORMAT_CHECK_RE"; }

# Run the gate matrix declared in standards.md (standards-bind-gates). Falls back
# to a default Rust matrix with a WARN if standards.md / its matrix is not found.
# Optional $1: an ERE; only the matrix commands matching it run (e.g. just the format check).
run_standards_matrix() {
  [ "${GATE_RUN_MATRIX:-1}" = "1" ] || { note "matrix skipped (GATE_RUN_MATRIX=0)"; return 0; }
  local only="${1:-}"
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
    if [ -n "$only" ] && ! printf '%s\n' "$c" | grep -qE "$only"; then continue; fi
    note "matrix: $c"
    ( cd "$root" && eval "$c" ) || fail "matrix command failed: $c"
  done
}

# Detect a cargo project under REPO_DIR.
is_cargo_repo() { [ -f "$(repo_dir)/Cargo.toml" ] || find "$(repo_dir)" -maxdepth 2 -name Cargo.toml 2>/dev/null | head -1 | grep -q .; }
