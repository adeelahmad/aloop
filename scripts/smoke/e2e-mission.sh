#!/usr/bin/env bash
# Live mission run (ROADMAP M9.13, the live part): one real mission cycle on a seeded to-do repo, on
# the claude host. The run plays the human in three ways only: it types the first message (the
# SET_MISSION answers, scripts/smoke/mission_plan.py), types back each grant line a session showed
# the human (mission approve and baseline confirm, nothing else), and acts as the cron between
# sessions. Every human record names scripted-human (e2e). Spends real tokens (dollars), so it needs
# --yes; CI runs only --dry-run (scripts/test/mission-e2e-live.test.py).
#
#   scripts/smoke/e2e-mission.sh --yes [--host claude|codex|opencode] [--model M] [--worker-model M] [--dry-run]
#   (or: make e2e-mission YES=1 [HOST=claude] [MODEL=M] [DRY=1])
#
# --dry-run seeds the repo, runs the zero-token self-checks, writes $T/plan.json (the first prompt
# and its sha256, the seed sha, each session's argv, the caps and the allowlist) and prints them;
# it never runs a host. Live runs are claude only; codex and opencode plan with --dry-run alone.
# The run dir (E2E_DIR, else a new temp dir) must be empty, outside every git repo and under no
# CLAUDE.md: a typed line only ever reaches a repo this script made, and nobody's standing
# instructions reach the sessions. The live loop is scripts/smoke/mission_driver.py.
set -uo pipefail
[ "${1:-}" = "--yes" ] || { echo "spends tokens: re-run with --yes"; exit 2; }
shift
HOST=claude SUP_MODEL='' WORKER_MODEL='' DRY=''
while [ $# -gt 0 ]; do
  case "$1" in
    --host|--model|--worker-model)
      [ $# -ge 2 ] || { echo "$1 needs a value"; exit 2; }
      case "$1" in --host) HOST="$2" ;; --model) SUP_MODEL="$2" ;; *) WORKER_MODEL="$2" ;; esac
      shift 2 ;;
    --dry-run) DRY=1; shift ;;
    *) echo "unknown arg: $1"; exit 2 ;;
  esac
done
case "$HOST" in claude|codex|opencode) ;; *) echo "unknown host: $HOST (claude|codex|opencode)"; exit 2 ;; esac
if [ -z "$DRY" ] && [ "$HOST" != claude ]; then
  echo "--host $HOST: a live mission run is claude only (no verified cost line or reply session on $HOST yet); --dry-run plans one"
  exit 2
fi
command -v "$HOST" >/dev/null || { echo "$HOST CLI not found"; exit 2; }
command -v git >/dev/null && command -v python3 >/dev/null || { echo "git and python3 needed"; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
PLUGIN="$(cd "$HERE/../../plugin" && pwd)"
FIXTURE="$HERE/fixtures/todo-mission"
DRIVER="$HERE/mission_driver.py"
PLAN="$HERE/mission_plan.py"
RUNNER=docs/agents/mission/journeys/runners/todo_journey.py
if [ -z "$DRY" ] && [ ! -f "$DRIVER" ]; then
  echo "driver not built: $DRIVER does not exist yet (M9.13 stage B); --dry-run plans the run"
  exit 2
fi

# No session and no seed step inherits a gate or loop override, a worker identity or the parent
# Claude Code session (by prefix: AGENTIC_*, ALADDIN_*, ALOOP_*, GATE_*; mission_plan.py names them).
SCRUBBED="$(python3 -B "$PLAN" scrub-names)" || { echo "mission_plan.py scrub-names failed"; exit 2; }
for v in $SCRUBBED; do unset "$v" 2>/dev/null; done
# git addresses the run's own repo only (a GIT_DIR from a hook or a shell would redirect the seed)
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_COMMON_DIR GIT_NAMESPACE

# The run dir: empty, in no git repo, under no CLAUDE.md (checked on the nearest dir that exists).
T="${E2E_DIR:-}"
[ -n "$T" ] || T="$(mktemp -d)" || exit 1
case "$T" in /*) ;; *) T="$PWD/$T" ;; esac
if [ -e "$T" ] && [ ! -d "$T" ]; then echo "E2E_DIR $T is not a directory"; exit 2; fi
if [ -d "$T" ] && [ -n "$(ls -A "$T" 2>/dev/null)" ]; then
  echo "E2E_DIR $T is not empty: the run seeds a repo of its own there"; exit 2
fi
probe="$T"; while [ ! -d "$probe" ]; do probe="$(dirname "$probe")"; done
d="$(cd "$probe" && pwd -P)" || exit 1
while :; do
  if [ -e "$d/.git" ]; then echo "E2E_DIR $T is inside a git repo ($d): the run needs a repo of its own"; exit 2; fi
  for m in CLAUDE.md CLAUDE.local.md .claude/CLAUDE.md; do
    if [ -e "$d/$m" ]; then echo "E2E_DIR $T is under $d/$m: Claude Code would load it into every session"; exit 2; fi
  done
  [ "$d" = / ] && break
  d="$(dirname "$d")"
done
if (cd "$probe" && git rev-parse --git-dir >/dev/null 2>&1); then
  echo "E2E_DIR $T is inside a git repo (git rev-parse): the run needs a repo of its own"; exit 2
fi

# Live preflight: no installed aloop next to --plugin-dir, the web for research, pytest for the gates.
if [ -z "$DRY" ]; then
  plugins="$(claude plugin list --json 2>/dev/null)" || { echo "preflight: claude plugin list --json failed"; exit 2; }
  printf '%s' "$plugins" | python3 -B "$PLAN" preflight-plugins || exit 2
  python3 -B "$PLAN" preflight-network "${E2E_NET_URL:-https://example.com/}" || exit 2
  python3 -m pytest --version >/dev/null 2>&1 || { echo "preflight: python3 with pytest needed (RED and GREEN run it)"; exit 2; }
fi

# Models as e2e-live's: the host's strong route for the supervisor, economical for the roles and workers.
[ -n "$SUP_MODEL" ] || SUP_MODEL="$("$PLUGIN/bin/models" routes --host "$HOST" | sed -n 's/.*strong=\([^ ]*\).*/\1/p')"
case "$SUP_MODEL" in "<session"*) SUP_MODEL= ;; esac
WORKER_MODEL="${WORKER_MODEL:-economical}"

# The seed, as e2e-live's: the fixture committed as `init`, then `aloop init` (docs/agents/ ignored).
# The runner is committed first, so it stays tracked under the ignored docs/agents/ (fix.diff is the
# offline test's, never the run's).
mkdir -p "$T/repo" "$T/bin" "$T/sessions" || exit 1
cd "$T/repo" || exit 1
git init -q -b main && git config user.email e2e@example.invalid && git config user.name "aloop e2e" || exit 1
cp -R "$FIXTURE/todo" "$FIXTURE/tests" "$FIXTURE/docs" "$FIXTURE/.gitignore" . || exit 1
find . -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null
git add -A && git commit -qm init || { echo "FAIL - seed commit init"; exit 1; }
export DSH_HOME="$T/dsh-home"; mkdir -p "$DSH_HOME"     # the user's own dsh profile is never re-rendered
ALOOP_NO_HOST_RERENDER=1 "$PLUGIN/bin/aloop-init" --apply MODEL_PLANNING="$WORKER_MODEL" MODEL_WORKER="$WORKER_MODEL" GITIGNORE=yes >/dev/null \
  || { echo "FAIL - aloop-init"; exit 1; }
[ "$HOST" = claude ] || "$PLUGIN/bin/install" "$HOST" --project . >/dev/null || { echo "FAIL - aloop install $HOST"; exit 1; }
git add -A && git commit -qm "aloop init" || { echo "FAIL - seed commit aloop init"; exit 1; }
SEED_SHA="$(git rev-parse HEAD)"

# The sessions find this crontab first on PATH: declining the cron is a guard, not a request.
cat > "$T/bin/crontab" <<'SH'
#!/bin/sh
echo "crontab is disabled in this run (scripts/smoke/e2e-mission.sh declines the cron; crontab -l unchanged is a post-run check)" >&2
exit 1
SH
chmod +x "$T/bin/crontab"

# Zero-token self-checks: the seed is what the plan assumes, before anything is spent.
fails=0
check() { local name="$1"; shift; if "$@"; then echo "ok - $name"; else echo "FAIL - $name"; fails=$((fails + 1)); fi; }
see_false() {      # two runs printed the same line, and only `see` fails
  [ -n "$1" ] && [ "$1" = "$2" ] && python3 -B -c 'import json, sys
s = json.loads(sys.argv[1])["steps"]
sys.exit(0 if (s.get("open"), s.get("add"), s.get("see")) == (True, True, False) else 1)' "$1" 2>/dev/null
}
peek_is() { [ "$1" -eq "$2" ] && python3 -B -c 'import json, sys; sys.exit(0 if json.loads(sys.argv[1]).get("action") == sys.argv[2] else 1)' "$3" "$4" 2>/dev/null; }
pytest_passes() { PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider >/dev/null 2>&1; }
no_pycache() { [ -z "$(find docs/agents/mission/journeys -name __pycache__ 2>/dev/null)" ]; }
two_commits() { [ "$(git rev-list --count HEAD)" = 2 ]; }
tracked_and_ignored() { git ls-files --error-unmatch "$RUNNER" >/dev/null 2>&1 && git check-ignore -q docs/agents/mission/mission.json; }
clean() { [ -z "$(git status --porcelain)" ]; }
J1="$(python3 "$RUNNER" 2>/dev/null | tail -n 1)"
J2="$(python3 "$RUNNER" 2>/dev/null | tail -n 1)"
check "the journey runner run twice gives the same result, see false" see_false "$J1" "$J2"
check "the runner is the fixture's, byte for byte" cmp -s "$RUNNER" "$FIXTURE/$RUNNER"
if python3 -m pytest --version >/dev/null 2>&1; then
  check "pytest passes" pytest_passes
else
  echo "skip - pytest is not installed for python3: the seed's own test was not run (a live run needs it)"
fi
check "no __pycache__ under journeys/" no_pycache
PEEK_RC=0; PEEK="$("$PLUGIN/bin/mission" tick --peek --json 2>/dev/null)" || PEEK_RC=$?
check "tick --peek: SET_MISSION (exit 3)" peek_is "$PEEK_RC" 3 "$PEEK" SET_MISSION
check "exactly 2 seed commits" two_commits
check "the runner is tracked and docs/agents/ is ignored" tracked_and_ignored
check "the self-checks left the seed clean" clean
if [ "$fails" -gt 0 ]; then echo "self-checks failed: $fails (nothing was spent)"; echo "run dir: $T"; exit 1; fi

DRYFLAG=''; [ -n "$DRY" ] && DRYFLAG=--dry-run
python3 -B "$PLAN" plan --run-dir "$T" --repo "$T/repo" --host "$HOST" --model "$SUP_MODEL" --worker-model "$WORKER_MODEL" \
  --seed-sha "$SEED_SHA" --shim-dir "$T/bin" --scrubbed "$SCRUBBED" $DRYFLAG || { echo "FAIL - plan.json"; exit 1; }
if [ -n "$DRY" ]; then echo "run dir: $T"; exit 0; fi

# The live loop: sessions, relays, caps and the post-run checks are the driver's (plan.json is its input).
python3 -B "$DRIVER" --plan "$T/plan.json"
rc=$?
python3 -B "$HERE/mission_summary.py" "$T" --sessions "$T/sessions" --kind mission --plugin "$PLUGIN" --host "$HOST"
echo "run dir: $T"
exit "$rc"
