# bin/ — gate scripts & tools

Gates are bash, invoked by hooks (`hooks/hooks.json`) by `agent_type`; the agent
never calls them directly. Shared helpers live in `_gatelib.sh`.

## Exit-code convention (gates)
- `0`  PASS (allow the event)
- `2`  BLOCK the event + write the reason to stderr (fed back to the supervisor)
- any other exit is a gate bug and is turned into `2` (BLOCK, "failing closed") by `_failclosed.sh`, which every sub-agent gate loads; `gate-supervisor-scope` arms it only once the repo has a sprint lock, because it sees every session on the machine
Transcripts hooks (`transcripts`) ALWAYS exit `0` — they must never block an agent.

## Per-task contract
Gates read the supervisor's contract `<main tree>/.aladdin/state/tasks/<TASK_ID>.env`
(`.agentic/...` in a legacy project; both dirs are searched)
(written before dispatch; the dispatch prompt's first line `TASK_ID: <id>` names it):
`TASK_ID, ATTEMPT, AGENT_ROLE, SCOPE_GLOBS, SCAFFOLD_SYMBOLS, BASE_REF, STORY_DIR,
BUDGET_*`. A worktree `.agentic/task.env` is a fallback and must match the contract when
both exist. Parsed, never sourced. Real env overrides. Store paths are NOT part of the contract:
`_paths.sh` / `_paths.py` fix them (`<main tree>/.aladdin/{transcripts,budget,ledger,logs,state}`,
or `.agentic/` where a project still has one — `aloop layout show|check|migrate`;
`docs/agents/{defaults.md,NEXT.md}`). A missing backend (md-db /
ctx-symbols) → WARN + grep fallback; never a silent pass.

## Gates (which hook fires each)
| script | event · matcher | checks | exit 2 when |
|--------|-----------------|--------|-------------|
| gate-supervisor-scope     | PreToolUse · Write\|Edit\|MultiEdit\|Bash | MAIN agent (no `agent_type`) may not write production source mid-sprint, nor hand-author planning artifacts (stories/tasks/validate/plan/intake/standards.md — dispatch the activity); sub-agents (`agent_type` present) exempt | supervisor writes source / hand-writes a planner-owned artifact |
| gate-dispatch             | PreToolUse · Agent\|Task | the MAIN agent's dispatch of an execution role opens with `TASK_ID: <id>` and that task's contract `<run-data>/state/tasks/<id>.env` exists (repos with a run-data dir only) | no TASK_ID line / contract not written yet |
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
| `log-execution` | `wave-K activity SN-NN transition [detail…] [execution.log]` — append one transition line (default ledger: the latest sprint's); `--help` |
| `ensure-tools` | SessionStart — builds md-db + ctx-symbols into `$CLAUDE_PLUGIN_DATA/bin` on first run / version change (background), tells the model where the tools are. `--sync` build now · `--wait` · `--resolve T` real binary path |
| `md-db`, `ctx-symbols` | PATH shims (this `bin/` is on PATH in every session): exec the real binary, building it once if needed. Gates test presence with `ensure-tools --resolve`, never `command -v` |
| `aladdin` | the product entry point (ROADMAP §4.11): `aladdin "<intent>"` records the contract and prints how to start the run · `run` · `status` (sweep + resume rule + token totals) · `resume` (what SessionStart loads) · `report` (stats.md) · `target\|context\|refs\|callers\|callees\|shadow\|scope` → `targets` · any other aloop tool unchanged. |
| `aloop` | the one entrypoint: `aloop <tool> …` (`aloop --list`); `aloop init` = aloop-init, `aloop install` = install. Linked into `~/.local/bin` for Codex/OpenCode |
| `install` | `aloop install codex\|opencode [--project DIR\|--global] [--uninstall]` — renders agents/skills/commands (+ the OpenCode plugin from `hosts/opencode/`) from this plugin's sources; `status`; `claude` |
| `host` | `swap --to H [SPRINT_DIR]`: hand the sprint to another host from portable state only (sprint dir, `.aladdin/config.yaml`, `.agentic/logs`); blocks on live worker worktrees or a missing piece, names the target's capability gaps, writes `.agentic/state/host-swap.json` (consumed by SessionStart on the new host) and emits `sprint.context_handoff`. · the `HostAdapter` interface (ROADMAP §4.9, `_host.py`): Claude Code is the reference, Codex and OpenCode are adapters. `describe [--host H] [--json]` prints the contract (capabilities, spawn/wait/send/cancel/subscribe vocabulary, compact/rotate, isolation, gate install/enforcement, tool policy, resolved models) · `check` (every adapter complete; gates `native` or `outer`, never warn; in `make test-hosts`) · `worktree create\|remove\|diff TASK`. A capability a host lacks raises `Unsupported`, never a guess. |
| `host-adapter` | SubagentStart/Stop: register sub-agents (`.agentic/state/agents/`) for the ledger; PreToolUse on Codex: a worker must `aloop bind <worktree>` first, then its commands/patches must stay in it (denied otherwise) |
| `task-worktree` | `add\|path\|remove <TASK_ID>` — per-task worktree at `.agentic/worktrees/<TASK_ID>` (branch `agentic/<TASK_ID>`) for hosts without built-in isolation; `merge <TASK_ID> <BRANCH>` — `--no-ff` merge of a host-made branch that emits `worktree.merged` + `commit.created` (the lineage on Claude Code) |
| `bind` | a worker's first command on Codex/OpenCode: validates its worktree (`aloop bind <path>`) |
| `_usage.py` | token usage from any host's log: Claude transcripts, Codex rollouts (`.jsonl`/`.zst`, cumulative totals → per-call rows), the OpenCode plugin's usage log |
| `aloop-init` | project setup: `--show` the defaults for the human, `--apply KEY=VALUE…` writes `docs/agents/defaults.md`, the managed `.gitignore` lines and the repo-local git author; `--ensure-gitignore` (SessionStart, worktree-hygiene) re-adds them |
| `session-start` | SessionStart — re-applies the managed `.gitignore` lines (if init opted in) and loads `docs/agents/NEXT.md` into the context (the post-`/clear` handoff) |
| `gate-commit-author` | PreToolUse · Bash — a `git commit` must use the configured author: denies `--author`/`-c user.*`/`GIT_AUTHOR_*` overrides, a mismatched repo identity, and Claude co-author/attribution trailers (unless `CLAUDE_COAUTHOR=yes`) |
| `stats` | `hook` (Stop + SubagentStop): rewrites `.agentic/ledger/<session>.jsonl` from the transcripts (tokens per API call, main + sub-agents) and, once after a sprint closed, shows the totals + tells the supervisor to ask for `/clear`. `sprint-close` (run by gate-final on pass): `sprintN/stats.md` + `NEXT.md`. `show`: session / sprint / project totals |
| `budget` | per-attempt time box for red/scaffolder/green workers. `hook pre\|post\|stop` (PreToolUse/PostToolUse `*`, SubagentStop): soft time limit → warning in the worker's tool result; soft token limit → telemetry only (`task.token_soft_limit` in `.agentic/logs/events.jsonl`, surfaced in stats.md/NEXT.md, worker not told); hard limit → PostToolUse `continue: false` stops the worker on that call, later calls refused; the harness (not the worker) appends the `status: re-plan` output.md block and records `task.token_hard_limit`/`task.time_hard_timeout` (+ `.agentic/budget-exceeded`, so its gate releases the stop unverified). `resolve` · `watch` (supervisor sleep; exits with STOPPED / KILL / STUCK lines) · `status` · `mark-killed` · `replans` (split cap) |
| `events` | the `aladdin.event/1` stream in `.agentic/logs/events.jsonl` (`_events.py`): `emit EVENT --task … --parent …` · `query --task/--event/--sprint` · `lineage TASK_ID` (worktree → limits → gates → decisions → merged/discarded, from refs, never transcripts) · `trace SHA` (commit → task → gates → plan → intent → approver) · `commit-msg TASK` (title + decisions + ids) · `graph [--format json|dot]` (downstream export). Written by `budget`, every gate verdict (`_gatelib.sh`) and `task-worktree`. |
| `intent` | the internal Intent Contract (ROADMAP §4.1): `new` issues the canonical `intent_id` + skeleton under `docs/agents/intents/`; `check FILE [--mode reviewed\|autonomous]` validates fields and the clarification policy (exit 2 invalid, 3 an `ask` needs the human); `summary FILE` lists defaults/assumptions/spikes for approval. Run by `gate-intake`. |
| `ids` | canonical identity + referential integrity (ROADMAP §4.5): `freeze SPRINT_DIR` checks the structured references (story frontmatter ↔ dir, dirs ↔ stories.md, validate.md ↔ tasks.md headings, `split_from` → a retired task) and issues frozen UUIDv7 ids in `sprintN/ids.json`; a retired alias is never reused · `check` · `resolve ALIAS`. Run by `gate-stage2-complete`; events then carry `story_uid`/`task_uid`. |
| `targets` | semantic execution map (ROADMAP §4.6): the planner's `<story>/targets.json` — change targets and, with `tasks`, the whole semantic plan. `render STORY_DIR [--check]` writes tasks.md / validate.md / plan.md as deterministic views over it (the gate refuses a hand-edited view) · `check STORY_DIR` (run by `gate-stage2-complete`) · `target Tn STORY_DIR` (file:line, or `planned` for the shadow workspace) · `shadow` · `scope Tn STORY_DIR` (the diff scope gate-green-verify derives when SCOPE_GLOBS is unset) · `context Cn --budget N` (just the target's definition) · `refs\|callers\|callees` via the LSP server in `AGENTIC_LSP_COMMAND` (`_lsp.py`: references + call hierarchy); no server or a missing capability → exit 4, never guessed. Backend: ctx-symbols, else a definition regex flagged weaker. |
| `impact` | optional cross-project impact provider seam (ROADMAP §4.8): `query consumers\|rationale\|migration\|standards SUBJECT [--task]` runs `AGENTIC_IMPACT_PROVIDER` (defaults.md or env), keeps only results with a `source_ref`, saves them under `docs/agents/impact/` and emits `artifact.created`. No provider or a failing one → WARN, exit 0. Builds no index. |
| `layout` | the run-data dir (ROADMAP M1): `show` (which of `.aladdin/` / `.agentic/` is live and why) · `check` (exit 2 unless every run dir is git-ignored) · `migrate` (move `.agentic/` run data into `.aladdin/`, refused while worker worktrees are live; in `make test-hosts`) |
| `config` | the portable project config `.aladdin/config.yaml` (ROADMAP §4.9; sections budgets · context · semantic · models · provenance · memory · git, plus `budgets.roles.<role>`): `migrate` writes it from the current settings and renders `docs/agents/defaults.md` from it · `sync` re-renders defaults.md (SessionStart, `aloop-init --apply`) so bash readers see the same values · `check` validates (exit 2) · `show [--json]` effective value + source per setting. When it exists it is authoritative. |
| `capabilities` | the host capability matrix (`plugin/hosts/capabilities.json`: usage, compaction, live messaging, lifecycle, gate blocking; `unknown` = not advertised) · `boundary [HOST]` → rotate (default, the host's `/clear` or `/new`) or compact only when `AGENTIC_CONTEXT_BOUNDARY=compact` and advertised · `boundary --keep --reason` records the exception. SessionStart records `context.rotated` / `context.compacted`. |
| `models` | abstract model routes (ROADMAP §3.6): `resolve planning\|worker\|<role> [--host H] [--json]` → the host's id for the group's route (`strongest\|strong\|economical`, or a pinned id passed through; `--json` shows the route, conformance 18) · `routes` prints `hosts/routes.json`. `aloop install` renders from the same table. |
| `sweep` | the orchestrator loop's inspect step (ROADMAP §4.2): one action per planned task (DONE · ROLLOVER · WAIT · REPLAN · ACCEPT · RETRY · ESCALATE · SPAWN) from events, attempt states and rollover.md · `sweep wait --timeout S` blocks without a model call until something changes (exit 4 on timeout). |
| `timebox` | time budget `auto` mode (ROADMAP §4.2): with `AGENTIC_TIME_MODE=auto`, `propose --minutes N --min A --max B --reason` records one project task duration (once); `budget` then uses it (soft = 2/3); `revise --minutes N --reason --evidence REF…` only with existing evidence and within range · `show`. Task overrides still win. |
| `lessons` | planner lesson candidates (ROADMAP §4.3) in `docs/agents/lessons.jsonl`: `add --symptom --cause --change --applies-to --evidence [--task]` (refuses empty fields, no evidence, or a gate-weakening change) · `list` · `recurring` (groups seen in ≥ 2 tasks, as memory.md rows for the archivist) · `check-memory FILE` (run by `gate-memory`: a row citing `L-NNN` needs recurrence ≥ 2 tasks or a human `keep`). |

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
