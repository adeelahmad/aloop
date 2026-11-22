#!/usr/bin/env bash
# _finddoc.sh — agentic_find_doc NAME [ROOT]: the planning doc NAME (intake.md, standards.md, …)
# a gate judges when none was named. Hidden dirs are skipped: a host keeps its own agent
# definitions there (.opencode/agents/intake.md, .claude/agents/standards.md, …), never the
# project's planning doc, and `find | head -1` judged whichever directory order gave first (CI:
# OpenCode's intake agent file, whose dir holds no Intent Contract, so an open ask passed
# unchecked). Linked worktrees, node_modules and target are skipped too. The newest wins (the doc
# the role just wrote); a tie goes to the last path in C order. Prints nothing when none exists.
agentic_find_doc() {
  local best="" p
  while IFS= read -r p; do
    { [ -z "$best" ] || ! [ "$best" -nt "$p" ]; } && best="$p"
  done < <(find "${2:-.}" -mindepth 1 -maxdepth 6 \( -name '.*' -o -name worktrees -o -name node_modules -o -name target \) -prune \
             -o -type f -name "$1" -print 2>/dev/null | LC_ALL=C sort)
  [ -n "$best" ] && printf '%s\n' "$best"
  return 0
}
