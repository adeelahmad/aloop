#!/usr/bin/env bash
# _failclosed.sh — a gate that crashes must BLOCK, never allow.
#
# Claude Code (and Codex) treat any hook exit other than 2 as a non-blocking error, so a
# gate that dies on an unbound variable or a bad value lets the action through. Sourcing
# this installs an EXIT trap that turns every exit other than 0 (pass) or 2 (block) into
# exit 2 with the reason. _gatelib.sh sources it; gates that don't use the library source
# it directly, once they know the event is theirs to judge.
_gate_fail_closed() {
  local rc="${1:-$?}"
  if [ "$rc" -ne 0 ] && [ "$rc" -ne 2 ]; then
    echo "BLOCK[${GATE_NAME:-gate}]: the gate itself failed (exit $rc) — failing closed. This is a gate bug, not your work: report it with the error above. (Deliberate bypass? SKIP_HOOKS=1.)" >&2
    rc=2
  fi
  return "$rc"
}
trap '_gate_fail_closed $?; exit $?' EXIT
