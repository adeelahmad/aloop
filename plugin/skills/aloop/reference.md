# aloop — reference (read on demand)

The supervisor's playbook is `SKILL.md` next to this file; it is re-read on every supervisor
turn, so the sections a normal run does not need live here and are read only when the
playbook points to them (a budget breach, a non-Claude host, a review blind spot).

## Running on Codex, OpenCode or dsh (same playbook, host vocabulary)

The gates, time box, ledger, stats and handoff are the SAME scripts on every host (Codex
runs this plugin's `hooks/hooks.json` natively; OpenCode and DeepSeek Harness (`dsh`) run
them through the plugin `aloop install opencode|dsh` generates). Only the dispatch
vocabulary differs:

| Playbook step | Claude Code | Codex | OpenCode | dsh |
|---|---|---|---|---|
| Install | `/plugin install` | `aloop install codex` + `codex plugin add aloop@aloop-marketplace`, then trust hooks in `/hooks` | `aloop install opencode`, restart | `aloop install dsh`, then run `dsh --profile aloop` |
| Dispatch a role | Agent tool, `subagent_type: <role>` | `spawn_agent`, `agent_type: <role>` | `task` tool, `subagent_type: <role>` | `subagent` (red-worker, scaffolder, green-worker, structural-reviewer, final-gate, product-tester, state-assessor) or `subagent_planning` (intake, standards, planner, archivist, market-researcher, evaluator, mission-planner, ethics-reviewer), `agent_preset: aloop-<role>`; a call without a role is refused |
| Worker isolation | `isolation: "worktree"` | `aloop task-worktree add <TASK_ID>` → path; the worker's FIRST command is `aloop bind <path>` (hooks then confine it) | same as Codex (the plugin then rewrites its commands/edits into the worktree) | same as Codex, and dispatch with `cwd: <worktree>`; calls outside it are refused |
| Parallel / wait | `run_in_background` + `budget watch` | several `spawn_agent`, then `wait_agent` | several `task` calls in one message | several `subagent` calls in one message, then end your turn: each "finished" notice wakes you (there is no wait tool; do not poll `list_agents`) |
| Stop a worker (backstop) | `TaskStop` | `close_agent` | — (the plugin aborts it) | `interrupt_agent` |
| Merge a passed task | `aloop task-worktree merge <TASK_ID> <worktree-branch>` (records the lineage) | merge `agentic/<TASK_ID>`, then `aloop task-worktree remove <TASK_ID>` | same as Codex | same as Codex |
| Fresh context per sprint | `/clear` | `/clear` or `/new` | `/new` | a new `dsh --profile aloop` run |

On dsh a role's result is its `output.md` block (read it after the "finished" notice); a gate
verdict on a role that did not pass arrives as an `[aloop gate]` message — never merge such a
task.

On Codex/OpenCode/dsh the dispatch message MUST contain the worktree line — e.g. "Your
worktree: /abs/.aladdin/worktrees/S1-02-T3. First command: `aloop bind
/abs/.aladdin/worktrees/S1-02-T3`." The task contract is written before dispatch,
exactly as on Claude Code (see "The task contract" below). Models per role come from the generated agent files
(`aloop install` renders them from `docs/agents/defaults.md`).

## NEEDS-HUMAN: an intake that stops to ask

An `ask` question (irreversible, security-sensitive or intent-changing) is the human's, never the
intake's: `gate-intake` releases its stop at once, **not verified** (as a STUCK release), records the
questions in `.aladdin/state/needs-human/<intent_id>.json` and logs `gate.failed` (`released=needs-human`).
You are told `[aloop gate] intake <id>: NEEDS-HUMAN — released UNVERIFIED (not a pass)` with the
questions: with the Agent result on Claude Code (on Codex, your next tool result), in the `task` result
on OpenCode, as a message on dsh. It is not a pass:

1. Ask the human each question as written. Never answer one yourself.
2. Re-dispatch `intake` with a `### Feedback` block quoting each question and the human's answer; it
   records each `answer` in the contract (you never edit it).
3. Dispatch the planner only once `gate-intake` passes. In autonomous mode this is the escalation class
   (`intent check` exit 3).

A malformed contract or an incomplete five-part intent is still blocked and sent back to the intake.

## Budget overrun → mid-sprint re-plan (the re-plan route)

A task killed at its hard limit is too big — retrying it would burn the same budget
again. Instead:

1. **Pause the sprint.** Let already-running workers finish (sleep/sweep as usual) but
   dispatch nothing new.
2. **Check the cap:** `budget replans SN-NN-Tn`. At the cap (`AGENTIC_MAX_REPLANS`,
   default 2 splits per task lineage) → escalate as budget/no-progress (hard stop).
3. **Dispatch the planner in re-plan mode** — append an init block from
   `pipeline/planning/02-planner/replan.template.md` (`mode: replan`) listing: DONE tasks
   (merged/ticked — must not be touched), KILLED tasks (with their tokens/minutes, last
   output.md block, transcript path), NOT-STARTED tasks, and the budget in force. Log
   `log-execution wave-K replan SN-NN start Tn`. The planner splits each killed task into
   smaller ones (`split_from: Tn`), removes the original, and updates the waves.
   (Autonomous: this re-plan does NOT wait for the human — splitting a task inside the
   approved sprint scope is not a scope change. Scope changes still escalate.)
4. **Re-validate:** `gate-stage2-complete docs/agents/sprintN` must pass again. Log
   `replan SN-NN complete Tn`.
5. **Resume** the sprint: dispatch the new tasks (from RED) and continue the loop.
   Reviewed mode: the re-plan changed the plan, so `gate-dispatch` blocks every dispatch of the
   sprint until the human approves the new plan: run `events authority … --sprint N` again and
   relay the `approve SN <code>` line it asks for (ROADMAP M0.9). An autonomous grant for the
   sprint covers its re-plans.

### Breach classes (classify before you split)

A hard stop is not always "too big". The hook writes a first, deterministic breach class
with its evidence into the attempt's output.md block (`| breach class | … |`), the
`task.*_hard_limit` event and `budget watch`'s line (`budget classify <task>` re-prints
it). Pass it to the planner, which confirms or overrides it and then acts on it:

| Class | Action |
|---|---|
| `TASK_TOO_LARGE` | Split into smaller, independently testable tasks (vertical slices) |
| `AGENT_LOOPING` | Reframe the task and put the failed strategy in its `### Feedback` |
| `PLAN_AMBIGUOUS` | Repair the contract (plan.md tests / validate.md rubric) |
| `BLOCKED_DEPENDENCY` | Schedule the dependency first |
| `ENVIRONMENT_SLOW` / `TEST_SUITE_SLOW` | Adjust the commands or the task's `budget:` time line; splitting may not help |
| `EXTERNAL_SERVICE` | Isolate or mock the service in the task's contract |
| `HOST_FAILURE` | Retry the infrastructure: re-dispatch the task unchanged (does not count as a split) |
| `CONTEXT_PATHOLOGY` | Narrow the task's inputs (fewer files to read), or split |
| `UNKNOWN` | Conservative split, or escalate |

`PLAN_AMBIGUOUS` and `BLOCKED_DEPENDENCY` are never guessed by the hook: they need the
planner's judgment. Every replacement task must still fit the normal budgets.

## Known gate limitations (compensate in review)

- **Suppression grep is syntactic.** `assert_no_suppression` catches `#[ignore]`,
  `.skip(`, `xit(`, `#[cfg(not(test))]`. It CANNOT see a test that was weakened by
  deleting/replacing an assertion (e.g. `assert!(true)`). The compensating control
  is the RED invariant: a test that was failing in RED and is unchanged in GREEN
  cannot be silently weakened without the diff-scope check flagging the test-file
  edit. When a GREEN attempt edits its RED test file, scrutinize the diff.
- **Backends optional.** With `md-db` absent, artifact structure is frontmatter-only;
  with `ctx-symbols` absent, duplicate detection is name-only grep. Both WARN. A run
  with WARNs has weaker — not absent — gates; install both for full enforcement.

## Available scripts

All of these are on PATH — call them by bare name.

- `selfcheck [role]` — run your activity's gate as a self-check before reporting
  (`selfcheck tooling` runs the execution preflight).
- `gate-tooling` — execution preflight; BLOCKS the first dispatch unless md-db +
  ctx-symbols are installed (auto-installs first; SubagentStart hook + manual check).
- `ensure-tools` — SessionStart installer (`--sync` build now, `--wait`, `--resolve T`).
- `budget` — the time box: `contract` (writes the task contract), `resolve` (BUDGET_* alone), `watch` (your sleep),
  `status`, `mark-killed`, `replans` (split-cap check); `hook` is the hook entrypoint.
- `stats show` — token totals (this session, per sprint, whole project); `stats
  sprint-close` is run by gate-final, not by you.
- `aloop-init` — project setup (`--show` / `--apply`), used by the init skill.
- `gate-supervisor-scope` — PreToolUse guard; blocks the supervisor from writing
  production source while a sprint is live this session (code goes through workers).
- `transcripts …` — full interaction capture (managed by hooks; `transcripts --help`).
- `bin/<gate>` — the individual gates (hook-invoked; see `bin/README.md` for each
  gate, its event/matcher, and exit codes).
- `md-db validate … --schema schemas/*.kdl` — validate any `.md` artifact you write.

## Transcripts (full capture, file-based — bin/transcripts)
- Store at `.aladdin/transcripts/` in the main tree (fixed path, git-ignored, never merged):
  - `global.jsonl` — thin cross-task causal stream (tool/file/prompt/stop markers) the
    retrospective scans without reading every payload.
  - `<task>/events.jsonl` — the FULL hook payload per tool call (tool_input +
    tool_response) + every user prompt. Nothing is dropped.
  - `<task>/transcript.jsonl` — the complete session snapshot (every user/assistant
    message + thinking + tool result), copied from the session's `transcript_path` on
    each stop. The main (supervisor) session lands under `<task>=session`.
  You read the GLOBAL stream + any task's full record; a sub-agent only ever sees its
  own task slice.
- Lifecycle (wired in hooks.json, all non-blocking — exit 0):
    UserPromptSubmit → `transcripts prompt`   capture each human message.
    UserPromptSubmit → `intent-trigger hook`  a plain build/fix request in an aloop project with no
        sprint running gets context to run it as `/aladdin "<intent>"` (quiet for questions,
        slash commands, a live sprint; off with `ALADDIN_AUTO_TRIGGER=off` / `intent.auto_trigger: false`).
    SubagentStart    → `transcripts stage-in` copy this task's slice into the worktree
        as READ-ONLY `.transcripts/` (a frozen pre-run snapshot).
    PostToolUse *    → `transcripts record`   append the full tool payload to the store.
    Stop             → `transcripts snapshot`  snapshot the full session transcript.
    SubagentStop *   → `transcripts stop`      snapshot + remove `.transcripts/` + marker.
- Real isolation = the global store is never staged into the worktree. The read-only
  chmod is the softer "not yours to write" signal (defeatable, but it errors loudly).
- Retention: everything is kept (no auto-compaction). `transcripts prune` is a manual,
  opt-in cap (`AGENTIC_TRANSCRIPTS_KEEP=<n>`) if disk gets tight; it keeps the human's prompts,
  which their grants cite.

## Worktree hygiene (bin/worktree-hygiene)
- SubagentStart (exec roles) sparse-checkout-excludes `docs/agents/` from the new
  worktree — a worker reaches its story's docs at the absolute `STORY_DIR` path back
  into the main tree, never a copy inside its own worktree, so the checked-out copy is
  pure redundant, would-go-stale weight. Per-worktree config; the main tree and
  sibling worktrees are untouched.
- Same hook bootstraps the main tree's `.gitignore` with `.agentic/`/`.transcripts/` (and
  `.aladdin/.gitignore` guards that dir's run data)
  the first time a worker runs against it (idempotent — a no-op check after that).
  Fires regardless of whether the harness's built-in `isolation: "worktree"` or the
  optional `bin/worktree-create` made the worktree, since the built-in path never
  calls `worktree-create` at all.
- The retrospective reads this to distill memory. Gate verdicts, supervisor decisions,
  and worktree create/merge/abandon are all captured here.

## Reference

- `pipeline/` — the design source of truth (persona / init.template / artifacts /
  gate per activity).
- `schemas/` — md-db KDL schemas (`agent-io`, `planning-artifacts`, `ledger`).
- `bin/` — the gate bodies + `log-execution`.
- `tools/ctx-symbols/` — the symbol backend (build + install per its README).
- `bin/_paths.sh` / `bin/_paths.py` — the one definition of the file layout.
- Backends: `md-db` (artifact validation) and `ctx-symbols` (code-structure checks),
  auto-installed by `ensure-tools`. Planning gates degrade to grep with a WARN if they
  are missing; execution does not start without them.
