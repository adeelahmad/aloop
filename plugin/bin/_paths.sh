#!/usr/bin/env bash
# _paths.sh — THE one definition of where aloop keeps its files. Every script
# sources this (Python scripts mirror it in bin/_paths.py). No model picks these names,
# no task.env variable overrides them — so every project gets the same layout.
#
#   <project>/.agentic/                 run data — ALWAYS git-ignored, never committed
#     transcripts/                      full capture (global.jsonl + <task>/…)
#     budget/                           one <agent_id>.json per worker attempt (time box)
#     ledger/                           one <session_id>.jsonl per session (token usage)
#     logs/gates.jsonl                  every gate verdict (pass/block)
#     state/                            sprints.json (sprint close times), locks, markers,
#       tasks/<TASK_ID>.env             the supervisor's per-task contract (bin/_gatelib.sh)
#   <project>/docs/agents/              planning docs (git-ignored unless init said track)
#     defaults.md                       confirmed project settings (limits, models, git)
#     NEXT.md                           the orchestrator handoff, re-loaded after /clear
#     sprintN/…                         the sprint's artifacts + stats.md
#
# <project> is the MAIN worktree (first entry of `git worktree list`), so a worker in a
# linked worktree writes to the same place as the supervisor.

agentic_main_tree() {
  local d="${1:-$PWD}" m
  m="$(git -C "$d" worktree list --porcelain 2>/dev/null | sed -n 's/^worktree //p' | head -1)"
  [ -n "$m" ] || m="$(git -C "$d" rev-parse --show-toplevel 2>/dev/null)"
  [ -n "$m" ] || m="$d"
  printf '%s\n' "$m"
}

# ALADDIN_<KEY> is the new spelling of every AGENTIC_<KEY> environment override (ROADMAP M1);
# both are read, the old one wins when both are set, scripts keep using the AGENTIC_ name.
for _k in HOST CWD NO_AUTOINSTALL NO_BUDGET LSP_COMMAND IMPACT_PROVIDER SELFCHECK WORKTREE \
          AUTONOMY_MODE STUCK_BLOCKS SPRINT_LOCK_TTL TRANSCRIPTS_KEEP; do
  if [ -z "$(eval "printf '%s' \"\${AGENTIC_$_k:-}\"")" ] && [ -n "$(eval "printf '%s' \"\${ALADDIN_$_k:-}\"")" ]; then
    eval "export AGENTIC_$_k=\"\$ALADDIN_$_k\""
  fi
done
unset _k

AGENTIC_PROJECT="$(agentic_main_tree "${AGENTIC_CWD:-$PWD}")"
# The run-data dir: .aladdin/ (ROADMAP M1), or .agentic/ where a project still has one
# (read-old fallback; `aloop layout migrate` moves it). .aladdin/ also holds the committed
# config.yaml, so its run data is protected by its own .gitignore, written by agentic_home_guard.
agentic_home_name() {
  local p="$1" d
  for d in transcripts budget ledger logs state host worktrees; do
    [ -d "$p/.aladdin/$d" ] && { echo ".aladdin"; return 0; }
  done
  [ -d "$p/.agentic" ] && { echo ".agentic"; return 0; }
  echo ".aladdin"
}
agentic_home_guard() {          # .aladdin/.gitignore: run data never leaves the machine
  [ "$AGENTIC_HOME_NAME" = ".aladdin" ] || return 0
  [ -d "$AGENTIC_HOME" ] || return 0
  [ -f "$AGENTIC_HOME/.gitignore" ] || printf '%s\n' '# aloop run data (transcripts, budgets, ledger, logs, state) — never committed' '*' '!.gitignore' '!config.yaml' > "$AGENTIC_HOME/.gitignore" 2>/dev/null || true
}
AGENTIC_HOME_NAME="$(agentic_home_name "$AGENTIC_PROJECT")"
AGENTIC_HOME="$AGENTIC_PROJECT/$AGENTIC_HOME_NAME"
agentic_home_guard
# Worktree-local markers (task.env copy, budget-exceeded, scaffold-symbols) live in the
# worker's worktree: written to .aladdin/ (guarded) or .agentic/ in a legacy project, read
# from either. agentic_marker prints the existing one, else the write path (returns 1).
agentic_marker_dir() {
  local top="$1" d
  if [ "$AGENTIC_HOME_NAME" = ".agentic" ]; then d="$top/.agentic"; else d="$top/.aladdin"; fi
  mkdir -p "$d" 2>/dev/null || true
  # the marker dir ignores itself too: nothing there may reach the task's diff
  case "$d" in */.aladdin) [ -f "$d/.gitignore" ] || printf '%s\n' '# aloop worktree markers — never committed' '*' > "$d/.gitignore" 2>/dev/null || true ;; esac
  echo "$d"
}
agentic_marker() {
  local top="$1" name="$2" h
  for h in .aladdin .agentic; do [ -f "$top/$h/$name" ] && { echo "$top/$h/$name"; return 0; }; done
  if [ "$AGENTIC_HOME_NAME" = ".agentic" ]; then echo "$top/.agentic/$name"; else echo "$top/.aladdin/$name"; fi
  return 1
}
AGENTIC_TRANSCRIPTS="$AGENTIC_HOME/transcripts"
AGENTIC_BUDGET="$AGENTIC_HOME/budget"
AGENTIC_LEDGER="$AGENTIC_HOME/ledger"
AGENTIC_LOGS="$AGENTIC_HOME/logs"
AGENTIC_STATE="$AGENTIC_HOME/state"
# The task contract <run-data>/state/tasks/<TASK_ID>.env. A supervisor that wrote the literal
# legacy path (.agentic/state/tasks) in a fresh .aladdin project, or the reverse, is still found:
# both homes are searched, the resolved one first. Prints the path (even when missing, so
# messages can name it); returns 1 when no such file exists.
agentic_contract_path() {
  local tid="$1" f
  case "$tid" in ''|*[!A-Za-z0-9._-]*) return 1 ;; esac
  for f in "$AGENTIC_STATE/tasks/$tid.env" "$AGENTIC_PROJECT/.aladdin/state/tasks/$tid.env" "$AGENTIC_PROJECT/.agentic/state/tasks/$tid.env"; do
    [ -f "$f" ] && { echo "$f"; return 0; }
  done
  echo "$AGENTIC_STATE/tasks/$tid.env"; return 1
}
AGENTIC_DOCS="$AGENTIC_PROJECT/docs/agents"
AGENTIC_DEFAULTS="$AGENTIC_DOCS/defaults.md"
AGENTIC_NEXT="$AGENTIC_DOCS/NEXT.md"
# The .gitignore lines this plugin owns (docs/agents/ only when init chose not to track it).
# .aladdin/ is NOT listed: config.yaml there is committed; its run data is covered by
# .aladdin/.gitignore (agentic_home_guard).
AGENTIC_IGNORE_ALWAYS=".agentic/ .transcripts/"

# Value of AGENTIC_<KEY> from defaults.md (a `KEY=value` line anywhere; parsed, never run).
agentic_setting() {
  local key="$1" def="${2:-}" v=""
  [ -f "$AGENTIC_DEFAULTS" ] && v="$(sed -nE "s/^[[:space:]]*(AGENTIC|ALADDIN)_${key}[[:space:]]*=[[:space:]]*['\"]?([^'\"#]*[^'\"#[:space:]])?['\"]?[[:space:]]*(#.*)?$/\2/p" "$AGENTIC_DEFAULTS" | tail -1)"
  printf '%s\n' "${v:-$def}"
}
