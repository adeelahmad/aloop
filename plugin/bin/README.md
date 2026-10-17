# bin/ — gate scripts & tools

Gates are bash, invoked by hooks (`hooks/hooks.json`) by `agent_type`; the agent
never calls them directly. Shared helpers live in `_gatelib.sh`.

## Exit-code convention (gates)
- `0`  PASS (allow the event)
- `2`  BLOCK the event + write the reason to stderr (fed back to the supervisor)
- other non-zero  error
Transcripts hooks (`transcripts`) ALWAYS exit `0` — they must never block an agent.

## Per-task contract
Gates read `.agentic/task.env` (the supervisor writes it into each worktree at
dispatch): `TASK_ID, ATTEMPT, AGENT_ROLE, SCOPE_GLOBS, SCAFFOLD_SYMBOLS, BASE_REF,
STORY_DIR, BUDGET_*`. Real env overrides. Store paths are NOT part of the contract:
`_paths.sh` / `_paths.py` fix them (`<main tree>/.agentic/{transcripts,budget,ledger,logs,state}`,
`docs/agents/{defaults.md,NEXT.md}`). A missing backend (md-db /
ctx-symbols) → WARN + grep fallback; never a silent pass.

## Gates (which hook fires each)
| script | event · matcher | checks | exit 2 when |
|--------|-----------------|--------|-------------|
| gate-supervisor-scope     | PreToolUse · Write\|Edit\|MultiEdit | MAIN agent (no `agent_type`) may not write production source mid-sprint, nor hand-author planning artifacts (stories/tasks/validate/plan/intake/standards.md — dispatch the activity); sub-agents (`agent_type` present) exempt | supervisor writes source / hand-writes a planner-owned artifact |
| gate-tooling              | SubagentStart · exec roles + supervisor self-check | md-db + ctx-symbols installed (execution may not run grep-degraded); runs `ensure-tools --sync` first | a backend is missing and can't be built |
| gate-validate-artifact    | PostToolUse · Write         | md-db-validate the written artifact | artifact malformed |
| gate-red-verify           | SubagentStop · red-worker   | worktree-isolated; appended output.md block (validate_comms); every new test FAILS BY ASSERTION; no regression; diff = tests+shims | no/stale output.md block / shared-tree run / a new test passes / prod code / regression |
| gate-scaffold-verify      | SubagentStop · scaffolder   | worktree-isolated; appended output.md block; one def per symbol (`.agentic/scaffold-symbols`); panic+TODO; no marked shim; no clobber | no/stale output.md block / shared-tree run / impl body / dup / shim remains |
| gate-green-verify         | SubagentStop · green-worker | worktree-isolated; appended output.md block; tests pass; in-scope diff; standards matrix; zero suppressions | no/stale output.md block / shared-tree run / failing test / out-of-scope / suppression |
| gate-structural-integrity | SubagentStop · structural-reviewer | appended output.md block; orphan/parallel/duplicate via ctx-symbols | no/stale output.md block / foundation-poisoning finding |
| gate-final                | SubagentStop · final-gate   | full matrix; zero suppressions; all plan-ready ticked | matrix red / suppression / unticked |
| gate-standards-cited      | SubagentStop · standards    | standards.md citations resolve | dangling citation |
| gate-stage2-complete      | supervisor self-check + SubagentStop · planner | every story Stage-2 valid; no TBW; sprint self-contained (no later-sprint story IDs, no work deferred to a later sprint) | incomplete or spilling planning |
| gate-plan-shape           | called by gates             | each plan checkbox carries `path::fn` | malformed plan |
| gate-ledger-format        | called by gates             | execution.log line shape | malformed ledger |

## Tools
| script | purpose |
|--------|---------|
| `transcripts`       | full capture: stage-in / record / prompt / snapshot / stop / view / prune (`transcripts --help`) |
| `worktree-hygiene` | SubagentStart · exec roles — sparse-checks `docs/agents/` out of the new worktree (workers reach it via the absolute `STORY_DIR` back into the main tree, never their own copy) + bootstraps the main tree's `.gitignore` with `.agentic/`/`.transcripts/` if missing. Runs regardless of whether the harness's built-in isolation or `worktree-create` made the worktree. |
| `worktree-create` | OPTIONAL `git worktree add` helper — wire into **user `settings.json`** as a `WorktreeCreate` hook for a non-git VCS (NOT a valid plugin-manifest event). Built-in `isolation: "worktree"` covers plain git. Also applies the same sparse-checkout + `.gitignore` bootstrap as `worktree-hygiene`. |
| `worktree-remove` | OPTIONAL `git worktree remove` cleanup companion for the above (user `settings.json` `WorktreeRemove`) |
| `log-execution` | append a transition line to `execution.log` |
| `ensure-tools` | SessionStart — builds md-db + ctx-symbols into `$CLAUDE_PLUGIN_DATA/bin` on first run / version change (background), tells the model where the tools are. `--sync` build now · `--wait` · `--resolve T` real binary path |
| `md-db`, `ctx-symbols` | PATH shims (this `bin/` is on PATH in every session): exec the real binary, building it once if needed. Gates test presence with `ensure-tools --resolve`, never `command -v` |
| `aloop` | the one entrypoint: `aloop <tool> …` (`aloop --list`); `aloop init` = aloop-init, `aloop install` = install. Linked into `~/.local/bin` for Codex/OpenCode |
| `install` | `aloop install codex\|opencode [--project DIR\|--global] [--uninstall]` — renders agents/skills/commands (+ the OpenCode plugin from `hosts/opencode/`) from this plugin's sources; `status`; `claude` |
| `host-adapter` | SubagentStart/Stop: register sub-agents (`.agentic/state/agents/`) for the ledger; PreToolUse on Codex: a worker must `aloop bind <worktree>` first, then its commands/patches must stay in it (denied otherwise) |
| `task-worktree` | `add\|path\|remove <TASK_ID>` — per-task worktree at `.agentic/worktrees/<TASK_ID>` (branch `agentic/<TASK_ID>`) for hosts without built-in isolation |
| `bind` | a worker's first command on Codex/OpenCode: validates its worktree (`aloop bind <path>`) |
| `_usage.py` | token usage from any host's log: Claude transcripts, Codex rollouts (`.jsonl`/`.zst`, cumulative totals → per-call rows), the OpenCode plugin's usage log |
| `aloop-init` | project setup: `--show` the defaults for the human, `--apply KEY=VALUE…` writes `docs/agents/defaults.md`, the managed `.gitignore` lines and the repo-local git author; `--ensure-gitignore` (SessionStart, worktree-hygiene) re-adds them |
| `session-start` | SessionStart — re-applies the managed `.gitignore` lines (if init opted in) and loads `docs/agents/NEXT.md` into the context (the post-`/clear` handoff) |
| `gate-commit-author` | PreToolUse · Bash — a `git commit` must use the configured author: denies `--author`/`-c user.*`/`GIT_AUTHOR_*` overrides, a mismatched repo identity, and Claude co-author/attribution trailers (unless `CLAUDE_COAUTHOR=yes`) |
| `stats` | `hook` (Stop + SubagentStop): rewrites `.agentic/ledger/<session>.jsonl` from the transcripts (tokens per API call, main + sub-agents) and, once after a sprint closed, shows the totals + tells the supervisor to ask for `/clear`. `sprint-close` (run by gate-final on pass): `sprintN/stats.md` + `NEXT.md`. `show`: session / sprint / project totals |
| `budget` | per-attempt time box for red/scaffolder/green workers. `hook pre\|post\|stop` (PreToolUse/PostToolUse `*`, SubagentStop): soft limit → warning in the worker's tool result; hard limit → one chance to write the output.md report, 3 refused calls, then PostToolUse `continue: false` stops the worker (+ `.agentic/budget-exceeded`, so its gate releases the stop unverified). `resolve` · `watch` (supervisor sleep; exits with KILL lines) · `status` · `mark-killed` · `replans` (split cap) |

## v0.2 — self-check at every step

Every dispatched role now has BOTH a `bin/selfcheck` mapping (run it before you report
done) AND a `SubagentStop` hook backstop running the same gate:

| role | gate | event · matcher |
|---|---|---|
| intake | `gate-intake` (five-part intent present) | SubagentStop · `intake` |
| standards | `gate-standards-cited` | SubagentStop · `standards` |
| planner | `gate-stage2-complete` (auto-discovers sprint dir) | SubagentStop · `planner` |
| archivist | `gate-memory` (valid + never relaxes an invariant) | SubagentStop · `archivist` |
| red/scaffold/green/structural/final | as above | SubagentStop · role |

`bin/selfcheck [role]` resolves the role from `$AGENT_ROLE` (task.env) and runs that
gate; exit `0` PASS · `2` FAIL · `64` unknown role. A PASS here means the hook passes.
