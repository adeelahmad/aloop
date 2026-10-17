#!/usr/bin/env bash
# Live smoke test of the hook wiring in a REAL Claude Code session (ROADMAP M0).
# Spends tokens (one short haiku session, about $0.005), so it needs --yes; CI does not run it.
#
#   scripts/smoke/live-claude.sh --yes        (or: make smoke-live YES=1)
#
# Checks, in a throw-away repo with a live sprint lock from another session:
#   1. SessionStart hooks inject the NEXT.md handoff (the model repeats its marker)
#   2. gate-supervisor-scope blocks the supervisor's write to src/ (file never created)
#   3. the transcripts hook captured the session, and the Stop hook wrote the token ledger
set -uo pipefail
[ "${1:-}" = "--yes" ] || { echo "spends tokens: re-run with --yes"; exit 2; }
command -v claude >/dev/null || { echo "claude CLI not found"; exit 2; }
PLUGIN="$(cd "$(dirname "$0")/../../plugin" && pwd)"
T="$(mktemp -d)"; cd "$T" || exit 1
git init -q -b main && git config user.email ada@x.io && git config user.name Ada
echo "# demo" > README.md && git add -A && git commit -qm init
mkdir -p docs/agents .agentic/state
printf '# handoff\nMarker: SMOKE-7731. Next: plan sprint 2.\n' > docs/agents/NEXT.md
echo "other-session" > .agentic/state/sprint.lock          # a resumed, live sprint

AGENTIC_NO_AUTOINSTALL=1 timeout 300 claude -p --plugin-dir "$PLUGIN" --model haiku \
  --output-format stream-json --verbose --allowedTools Write --max-turns 4 \
  "First, reply with the handoff marker that starts with SMOKE- from your session context. Then use the Write tool to create src/lib.rs containing 'pub fn a() {}'. If the write is refused, quote the refusal reason in one line and stop." \
  > out.jsonl 2> err.log
rc=$?

fails=0
check() { if eval "$2"; then echo "ok - $1"; else echo "FAIL - $1"; fails=$((fails + 1)); fi; }
check "session ran (exit $rc)" '[ "$rc" -eq 0 ]'
check "SessionStart injected the NEXT.md handoff" 'grep -q "SMOKE-7731" out.jsonl && grep -q "\"text\": *\"[^\"]*SMOKE-7731" out.jsonl'
check "supervisor write to src/ was blocked" '[ ! -e src/lib.rs ] && grep -q "may not write production source" out.jsonl'
check "no hook crashed" '! grep -q "unbound variable" out.jsonl'
check "transcripts captured the session" '[ -s .agentic/transcripts/global.jsonl ]'
check "Stop hook wrote the token ledger" 'ls .agentic/ledger/*.jsonl >/dev/null 2>&1'
cost="$(python3 -c 'import json,sys
for l in open("out.jsonl"):
    e=json.loads(l)
    if e.get("type")=="result": print("%.4f" % (e.get("total_cost_usd") or 0))' 2>/dev/null)"
echo "cost: \$${cost:-?} · run dir: $T"
exit $((fails > 0))
