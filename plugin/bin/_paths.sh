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

AGENTIC_PROJECT="$(agentic_main_tree "${AGENTIC_CWD:-$PWD}")"
AGENTIC_HOME="$AGENTIC_PROJECT/.agentic"
AGENTIC_TRANSCRIPTS="$AGENTIC_HOME/transcripts"
AGENTIC_BUDGET="$AGENTIC_HOME/budget"
AGENTIC_LEDGER="$AGENTIC_HOME/ledger"
AGENTIC_LOGS="$AGENTIC_HOME/logs"
AGENTIC_STATE="$AGENTIC_HOME/state"
AGENTIC_DOCS="$AGENTIC_PROJECT/docs/agents"
AGENTIC_DEFAULTS="$AGENTIC_DOCS/defaults.md"
AGENTIC_NEXT="$AGENTIC_DOCS/NEXT.md"
# The .gitignore lines this plugin owns (docs/agents/ only when init chose not to track it).
AGENTIC_IGNORE_ALWAYS=".agentic/ .transcripts/"

# Value of AGENTIC_<KEY> from defaults.md (a `KEY=value` line anywhere; parsed, never run).
agentic_setting() {
  local key="$1" def="${2:-}" v=""
  [ -f "$AGENTIC_DEFAULTS" ] && v="$(sed -nE "s/^[[:space:]]*AGENTIC_${key}[[:space:]]*=[[:space:]]*['\"]?([^'\"#]*[^'\"#[:space:]])?['\"]?[[:space:]]*(#.*)?$/\1/p" "$AGENTIC_DEFAULTS" | tail -1)"
  printf '%s\n' "${v:-$def}"
}
