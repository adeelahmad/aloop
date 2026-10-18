# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Versioning policy

`aloop` (formerly `agentic-agile`) versions the **plugin** (`plugin/.claude-plugin/plugin.json`) and the
bundled `ctx-symbols` crate together under one SemVer line:

- **MAJOR** — breaking changes to the gate contract (hook events/`agent_type`
  matchers, gate exit-code semantics), the on-disk artifact layout, the KDL schemas,
  or the `ctx-symbols` CLI surface.
- **MINOR** — backward-compatible additions (new gate, new check, new language in
  `ctx-symbols`, new optional env in the gate contract).
- **PATCH** — backward-compatible fixes (gate bug, false positive/negative, docs).

Keep `plugin.json` `version`, the `ctx-symbols` `Cargo.toml` `version`, and the git
tag in lockstep. Tag releases as `vMAJOR.MINOR.PATCH`.

## [Unreleased]

### Changed
- **The soft token limit is telemetry only** (ROADMAP §3.1). Crossing it no longer tells
  the worker to wrap up; `budget` records `task.token_soft_limit` once per attempt in
  `.agentic/logs/events.jsonl`, `stats.md` counts it, and `NEXT.md` lists those tasks as
  planner input. The soft *time* limit still warns.
- **No post-kill narration** (ROADMAP §3.5). At the hard limit the worker is stopped on
  the crossing call instead of being given a last call to write its report. The harness
  appends the gate-valid `status: re-plan` block to output.md itself (limits, worktree
  diff, transcript path) and records `task.token_hard_limit` / `task.time_hard_timeout`.
  A verdict reached before the worker's worktree is known still gets one call to find it.
- **The token meter counts classes separately** (ROADMAP §3.2). The budget charge is now
  fresh (uncached) input + output by default. Cache read and cache write are kept as
  their own classes in each attempt's state (`usage`), and context size is the latest
  call's, never cumulative cache traffic. `TOKEN_METER=context` in defaults.md (or
  `BUDGET_TOKEN_METER=context` in a task.env) keeps the previous context + output charge;
  `budget resolve` / `status` show the meter in force.
- **Budgets cover every model role** (ROADMAP §3.3). Intake, standards, planner,
  archivist, structural-reviewer and final-gate are metered against per-role profiles
  (overridable with `AGENTIC_BUDGET_<ROLE>` in defaults.md). They are observe-only:
  crossings are recorded as events, and the agent is never told or stopped.
- **Hard breaches are classified, not always split** (ROADMAP §3.4). The hook makes a
  deterministic first call (`TASK_TOO_LARGE`, `AGENT_LOOPING`, `EXTERNAL_SERVICE`,
  `TEST_SUITE_SLOW`, `ENVIRONMENT_SLOW`, `CONTEXT_PATHOLOGY`, `HOST_FAILURE`, `UNKNOWN`)
  from usage classes, tool calls and results, with its evidence in the output.md block,
  the event, `budget watch` and the new `budget classify`. The planner confirms or
  overrides it (`breach:` line) and acts per class (SKILL.md "Breach classes").

### Added
- Stuck release: a worker that hits the same gate block 3 times in a row (one gate, task,
  attempt and worktree; `AGENTIC_STUCK_BLOCKS`) is released **unverified** instead of
  looping until its time box (live run 10 lost 29 minutes to a RED worker that stopped 29
  times on one unchanged reason). The release is not a pass: the gate leaves
  `.agentic/state/stuck/<task>.<attempt>` with the reason, `budget watch` prints a `STUCK`
  line once, and the supervisor re-dispatches with feedback or re-plans. A different reason
  or a new attempt resets the count; a self-check never releases. Every gate verdict now
  logs its worktree and a checksum of the block reason. 7 conformance checks (§6.9).
- **Portable project config** (ROADMAP §4.9). `.aladdin/config.yaml` (budgets, context,
  semantic, models, provenance, memory, git, plus per-role budget profiles) replaces
  `docs/agents/defaults.md` as the human's file. `aloop config migrate` writes it from the
  current settings; when it exists it is authoritative and defaults.md is rendered from it
  (`config sync`, run by SessionStart and `aloop-init --apply`), so the bash gates read the
  same values. `config check` refuses unknown sections, keys, bad numbers and schema.
- **The `HostAdapter` interface** (ROADMAP §4.9). `_host.py` names, per host, the one place
  the harness does each thing: capabilities, the dispatch vocabulary (spawn / wait / send /
  cancel / subscribe), model and transcript resolution, usage and context pressure,
  compact / rotate, the worktree seam, gate install and enforcement, tool policy. Claude
  Code is the reference; Codex and OpenCode are adapters. `aloop host describe` prints the
  contract, `host check` (in `make test-hosts`) fails if an adapter is incomplete or would
  enforce a gate as a warning. What a host lacks raises `Unsupported`; nothing is faked.
- **Abstract model routes** (ROADMAP §3.6). Roles map to `strongest | strong | economical`
  and each host resolves the route to its own id (`plugin/hosts/routes.json`; Codex: the
  session's model). `AGENTIC_MODEL_PLANNING` / `AGENTIC_MODEL_WORKER` default to
  `strongest` / `strong`; a pinned id still passes through. `aloop models resolve` is what
  the supervisor passes on dispatch and what `aloop install` renders; `--json` makes the
  route observable (conformance 18).
- **Sprint token envelope and admission control** (ROADMAP §4.2). `budget admit <task>`
  runs before each dispatch. At `SPRINT_TOKENS_SOFT` it prints one rollover warning, and
  at `SPRINT_TOKENS_HARD` admission closes (exit 3). Refused tasks are carried over in
  `sprintN/rollover.md` with the reason, gate-final accepts unticked boxes only for those
  tasks (`budget rollover-check`), and NEXT.md lists them for the next planning cycle.
  Events: `sprint.token_soft_limit`, `sprint.admission_closed`, `sprint.rolled_over`.
- **Planning feedback lifecycle** (ROADMAP §4.3). `budget envelope <task>` prints the
  minimal failure envelope: limit, observed value, breach class, last gate, changed files
  and references, with nothing inlined. NEXT.md gains a "Planner feedback" section that
  applies the grace rule (0–1 failed or hard-breached tasks → no nudge; 2+ → one "plan
  smaller" nudge for the next cycle only) with a calibration verdict derived from the
  breach classes (`plan-smaller`, `raise-time`, `raise-tokens`, `reduce-context`,
  `fix-environment`).
- **The `aladdin.event/1` stream** (ROADMAP §4.7). `_events.py` and `bin/events` give
  every harness event one envelope (ids, `parent_event_id` / `caused_by`, role, host,
  model, authority, decision, refs, data) and refuse unknown event types. Budget events,
  every real gate verdict (`gate.passed` / `gate.failed`) and the worktree lifecycle
  (`worktree.created` / `merged` / `discarded`) are emitted into it. `events query` and
  `events lineage <task>` answer from the stream, with references only and no transcripts.
  `events authority` records the run's authority (who approved it, the mode, the scope and
  any overrides). `events decide` records one-line decisions with evidence refs, linked to
  that authority. A decision without an authority, with a multi-line reason or with an
  unknown kind is refused.
- **The Intent Contract and the clarification policy** (ROADMAP §4.1). `bin/intent new`
  issues a canonical, time-ordered `intent_id`, which is never model text, and the
  contract skeleton with provenance. `intent check` validates every field. It requires a
  class for each open question (`assume` / `default` / `spike` must record their
  resolution) and stops BOTH modes on an unanswered `ask` (exit 3). `intent summary`
  surfaces defaults and assumptions for approval. gate-intake runs the check. Autonomous
  mode is explicit (`events authority --mode autonomous`) and skips approval unless an
  escalation class is hit.
- **Host capability matrix and the context-boundary rule** (ROADMAP §4.4, §4.9).
  `plugin/hosts/capabilities.json` + `bin/capabilities` state what each host advertises
  (`unknown` counts as not advertised). At a sprint boundary the harness rotates (the host's
  own `/clear` or `/new`, named in the sprint-close message) unless
  `AGENTIC_CONTEXT_BOUNDARY=compact` and the host advertises compaction; keeping a session
  needs a recorded reason. SessionStart records `context.rotated` / `context.compacted`,
  and stats.md counts them.
- **Usage accuracy is explicit** (ROADMAP §4.9, conformance 12). Each attempt's usage
  carries `accuracy: exact` and names any class the host never reports (Codex has no cache
  write) instead of a silent zero; stats.md shows it in the Metrics table.
- **Diff scope derived from change targets** (ROADMAP §4.6). When the supervisor gives no
  `SCOPE_GLOBS` and the story has `targets.json`, gate-green-verify enforces the task's
  target paths (`targets scope`) instead of skipping the scope check.
- **LSP adapter seam** (ROADMAP §4.6). `targets refs|callers|callees` ask the language
  server named by `AGENTIC_LSP_COMMAND` (`_lsp.py`: references and the call hierarchy only).
  No server, or a server without the capability, is reported with exit 4; planned targets
  answer "none yet" without touching any server.
- **The orchestrator's inspect step** (ROADMAP §4.2). `bin/sweep` prints one action per
  planned task (DONE, ROLLOVER, WAIT, REPLAN, ACCEPT, RETRY, ESCALATE, SPAWN), decided from
  the event stream, attempt states and rollover.md. `sweep wait` is a token-free sleep
  that wakes on the next event or state change.
- **Time budget `auto` mode** (ROADMAP §4.2). With `AGENTIC_TIME_MODE=auto`, `bin/timebox
  propose` records one stable task duration per project (with min/max) and `budget` uses
  it (soft = 2/3). `timebox revise` changes it only within range and only with evidence
  that exists; each change is a `decision.recorded`. Task overrides still win.
- **Cross-project impact provider seam** (ROADMAP §4.8). `bin/impact query` asks an optional
  provider (`AGENTIC_IMPACT_PROVIDER`, any command speaking `aladdin.impact/1`) for downstream
  consumers, rationale, migration order or standards. Results without a source ref are
  dropped; answers are saved under `docs/agents/impact/` and recorded as planning
  provenance. An unavailable provider is reported and planning continues.
- **More run metrics** (ROADMAP §4.10). The Metrics table adds commits produced and test
  files added (from `commit.created`), time soft limits reached, and semantic context
  served (`targets target|context` emit `semantic_target.resolved`).
- **Commit → intent trace** (ROADMAP §4.7, milestone 7). gate-stage2-complete emits
  `plan.created` / `plan.revised`, caused by the intent; `events authority` emits
  `plan.approved` for the latest plan; `task-worktree remove` emits `commit.created` per
  merged commit. `events trace SHA` walks commit → task → gates → plan → intent → approver
  from the event stream alone. `events commit-msg TASK` builds the commit message from the
  task title, recorded decisions and ids; `events graph` exports the stream as a
  rebuildable graph (JSON or DOT).
- **Semantic change targets, first slice** (ROADMAP §4.6). The planner may write
  `<story>/targets.json` (`change_ref / operation / path / symbol | planned_symbol / reason /
  evidence_refs / constraints`). `bin/targets check` proves each target against tasks.md and
  the real tree, and gate-stage2-complete runs it. Workers start from `targets target` and
  `targets context --budget N`, including for files that do not exist yet; planned files
  form a shadow workspace and no stub is created. `refs/callers/callees` report the missing
  LSP backend (exit 4) instead of guessing.
- **Canonical ids and referential integrity** (ROADMAP §4.5). `bin/ids freeze` checks the
  structured references between planning artifacts, never prose, and issues each story and
  task alias a frozen UUIDv7 in `sprintN/ids.json`. A split task's original is retired and
  its number can never be reused. gate-stage2-complete runs it, and events for a frozen
  task carry `story_uid` and `task_uid`.
- **Planner lesson candidates** (ROADMAP §4.3). `bin/lessons add` records a candidate with
  `symptom / cause / change / applies_to / evidence_refs` and emits `memory.candidate`.
  It refuses empty fields, missing evidence and any change that would weaken a gate.
  `lessons recurring` hands the archivist the groups seen in two or more tasks, as
  memory.md rows. gate-memory blocks a row that promotes a one-off without a human `keep`.
  There is no second memory system.
- **Canonical sprint handoff and the resume rule** (ROADMAP §4.4). sprint-close writes
  `sprintN/handoff.json` (`aladdin.handoff/1`) from accepted state: completed tasks with
  their gate verdict and merge event, unverified, open and carried tasks, the nudge and
  evidence refs. NEXT.md renders it. `stats resume-check` names every task that
  plan-ready.md shows as done but that has no passing gate verdict, and SessionStart
  appends that live check to the loaded handoff.
- **Sprint metrics** (ROADMAP §4.10). stats.md gains a Metrics table: p50/p90/p95 attempt
  duration and tokens, hard breaches by role and class, hard-breach waste, tokens per
  accepted task, first-pass rate by gate, re-plan rate, cache-hit and churn ratios,
  context peak, and admission closures. All of it is derived from harness files, with no
  model calls.
- **Second live e2e intent**: `make e2e-live YES=1 INTENT=wordfreq` runs two dependent stories
  (word counting, then top-N ranking).
- **`gate-final` checks every planned test is on main**: each `path::fn` bullet in a story's
  `plan.md` must exist at its path. `plan-ready.md` lists only what RED wrote, so a test a
  re-plan dropped used to vanish unnoticed (e2e run 6).
- **`aloop init` seeds `docs/agents/memory.md`** as a schema-valid empty table, and
  `gate-memory` prints a valid skeleton when it blocks. e2e run 7's archivist guessed
  `type: agent-memory` and the supervisor's stop was blocked 15 times.
- **`gate-dispatch`** (PreToolUse on Agent/Task): the supervisor's dispatch of an execution
  worker is refused until its prompt opens with `TASK_ID: <id>` and the contract exists.
  e2e run 6 dispatched the structural reviewer before writing its contract, and its gate
  blocked 25 times on a `STORY_DIR` the worker could not supply. `scripts/test/dispatch.test.py`.

- **End-to-end live run (north star).** `make e2e-live YES=1` turns a one-line intent
  (a semver parser) into planned, tested, merged code in a real Claude Code session with
  the plugin, then checks the artifacts, gate verdicts, merge, tests and cost. First run:
  done end to end in 19 min for $1.15 (sonnet supervisor, haiku workers), 22 tests on main;
  it found the gate-matcher, selfcheck and copy-merge bugs fixed below.
- **Conformance fixtures (ROADMAP §6, M1 slice 1).** `make conformance` runs the real RED
  and GREEN gates on a fixture crate in linked worktrees: 16 checks over §6.3–6.9 (RED that
  passes or edits `src/` is rejected, suppressions and out-of-scope diffs are blocked,
  shared-tree workers are blocked, an accepted GREEN merges, a hard breach is released as
  "not a pass"). Items not covered yet are printed with the reason. Runs in CI's rust job.
  §6.2 adds 4 schema checks through the real md-db; task.env tampering adds 6 more (26 in all).

- **Live smoke test (M0).** `make smoke-live YES=1` runs one short real Claude Code
  session with the plugin and checks the handoff injection, the supervisor-scope block,
  transcripts and the token ledger (~$0.004 on haiku). Not run in CI (needs tokens).

- **Gates fail closed (M0.6).** A gate that crashes (exit other than 0 or 2) now blocks
  with "the gate itself failed … failing closed" instead of being a non-blocking hook error
  that lets the action through. Covers every sub-agent gate, and `gate-supervisor-scope`
  once the repo has a sprint lock. `scripts/test/fail-closed.test.py`.

### Security
- **No role gate ran on Claude Code plugin installs.** Claude Code names a plugin's
  sub-agents `aloop:<agent>`, and a plain-name hook matcher is an exact list, so the
  `red-worker`, `green-worker`, … SubagentStop/SubagentStart matchers never matched: every
  "gate" verdict came from the workers' own optional self-checks. Found by the first live
  end-to-end run (all 7 verdicts were self-checks) and confirmed with a probe plugin.
  Matchers now list both `<role>` and `aloop:<role>`; `scripts/test/hook-matchers.test.py`
  failed before.
- **A worker could pass its own gate by editing `task.env`.** The gate library *sourced*
  `.agentic/task.env`, which sits in the worker's worktree: appending `exit 0` passed a
  failing GREEN, and `GATE_RUN_MATRIX=0` or `SKIP_HOOKS=1` switched checks off. The file
  is now parsed as data and only the contract keys are read (`selfcheck` too). Four new
  conformance cases cover it; they failed before the fix. Remaining exposure (a worker
  rewriting its contract keys) is ROADMAP M0.7.
- **A worker could relabel its own role.** `AGENT_ROLE` came from `task.env`, so a RED
  worker claiming `planner` skipped the worktree-isolation check. Gates now take the role
  from the host's hook payload (`agent_type`) and block if `task.env` contradicts it.
  2 conformance cases (26 in all) failed before.
- **The task contract is the supervisor's (M0.7).** The playbook told the supervisor to
  write `task.env` into the worker's worktree before dispatch, but on Claude Code
  `isolation: "worktree"` creates that worktree (`.claude/worktrees/agent-<id>`) at
  dispatch (measured with a live probe), so the contract could only be missing or
  worker-written. The supervisor now writes `.agentic/state/tasks/<TASK_ID>.env` in the main
  tree and puts `TASK_ID: <id>` on the dispatch prompt's first line; gates and the time box
  find the contract from that line in the host's transcript. A worktree copy that disagrees
  with the contract blocks the gate, and `gate-supervisor-scope` stops sub-agents writing
  `.agentic/state/` or `.agentic/task.env`. 4 conformance cases (30 in all) and 4 unit
  checks failed before.
- **Workers could rewrite the contract through Bash.** `gate-supervisor-scope` now also
  sees a sub-agent's Bash calls and blocks a command that names `.agentic/state/` or
  `.agentic/task.env` together with a write (redirect, `tee`, `sed -i`, `cp`, `mv`, …);
  reading it stays allowed. 2 checks failed before.

### Fixed
- `ctx-symbols` (and the gate's grep fallback) no longer scan a host's worktree checkouts
  inside the main tree (`.claude/worktrees/agent-<id>/`), nor `.agentic/` or `.transcripts/`:
  a stale checkout duplicated every symbol of main and the structural review halted on a
  false HIGH finding 20 times (live e2e run 12). The exclusion applies below the walk root, so a
  reviewer whose own worktree is such a checkout is still scanned. 3 conformance checks (§6.7),
  2 unit tests.
- The stuck release also covers the structural reviewer and the final gate, whose blocks can name
  a defect only the supervisor can act on (run 12: 10 identical blocks). 1 conformance check.
- `gate-validate-artifact`'s block now carries md-db's diagnostics (the missing section or field
  and its hint): a PostToolUse block hands the model only stderr, and the bare "failed the
  schema" made the planner guess (live e2e run 11: every planning artifact was written twice,
  12 of 25 gate verdicts).
- `gate-dispatch` refuses a task contract with no `STORY_DIR`, or one naming a dir that does
  not exist, and tells a whole-wave structural review to use the sprint dir
  `docs/agents/sprintN/` (run 11: the reviewer's contract had none, 2 blocks the worker could
  not fix). 5 new dispatch checks.
- OpenCode: a stuck release (exit 0 with `STUCK … NOT verified`) is reported to the supervisor
  as "released UNVERIFIED, do not merge", never as a silent pass; 2 new checks.
- `make test-hosts` masked a failing OpenCode test as "node not found".
- The host tests (Codex hooks, OpenCode plugin, rename-compat, budget, fail-closed, scope,
  dispatch) each leaked a temp dir, and the Codex and OpenCode ones started a real background
  cargo build over their fake tools (~500MB per run; 57 leaked dirs filled the disk and killed
  e2e run 11 with ENOSPC). They now remove their temp dir on exit and set
  `AGENTIC_NO_AUTOINSTALL=1`; `e2e-live` drops its cargo target dirs after the run.
- The comms gate's "no output.md" block now says HOW to append the report: a shell heredoc
  from the worktree, because Claude Code's Write/Edit refuse a path outside it and
  `docs/agents/` lives only in the main tree (e2e run 10: a RED worker stopped 29 times on
  it). The worker personas say the same.
- A worktree `task.env` copy OLDER than the supervisor's contract (a re-dispatch bumped
  `ATTEMPT`) is stale and is ignored, not reported as tampering (e2e run 10: 3 blocks).
- `ensure-tools` rebuilds the bundled md-db / ctx-symbols when their sources change, not
  only on a version bump; e2e run 9 ran a stale ctx-symbols that still had the duplicate bug.
- `gate-stage2-complete` names the expected `sN-NN-<slug>/` story dir (and a misnamed one it
  found); the planner prompt states the naming. e2e run 9: 56 planner retries on `S1-01/`.
- Scope globs are no longer expanded against the worktree: `tests/**` became the files that
  happened to exist, so GREEN was blocked for an in-scope `tests/common/mod.rs` (e2e run 8: 29 blocks).
- `ctx-symbols conflicts` no longer reports the same name in two integration-test files
  (`mod common;`, a local helper) as a HIGH duplicate; each `tests/*.rs` is its own crate.
  e2e run 8's structural review halted 57 times on it. The grep fallback skips `tests/` too.
- `gate-scaffold-verify`'s grep fallback (no ctx-symbols) located a test fn instead of the stub,
  depending on directory order; it now uses the same name-anchored match as `symbol_count`.
- `gate-red-verify` no longer blocks a RED report that lists a test already on the base as
  PASS (an earlier task's test, for context); only a NEW test may not pass. e2e run 7: 22 blocks.
- `make shellcheck` no longer reports a lint error as "shellcheck not installed" and passes.
- `gate-scaffold-verify` skips a `symbol path kind` header row and `Trait for Type` impl
  lines in `.agentic/scaffold-symbols`.
- **Annotated `scaffold-symbols` lines were split into fake symbols** (`(enum)`, `@`, …), each
  "defined 0 times". The first token of a line is the symbol; non-identifiers are dropped.
- **Sub-agents read the plugin's own source** (19–30 commands per e2e run) to work out
  formats `selfcheck` already reports. The agent prompts now say not to; run 6 had 2, and
  cost its lowest yet: $0.65 in 11 min.
- **A Claude Code worker could get another task's budget.** With no `task.env` in its own
  worktree, the time box guessed "the only green-worker worktree" and applied that task's
  limits. It now takes the worker's own worktree from its cwd first.
- **A worker's `selfcheck <role>` crashed on good work** (`AGENT_ROLE: unbound variable`)
  when the role came only from the argument; selfcheck now exports it. Found by the e2e run.
- **The supervisor merged by copying files out of worktrees.** Workers never committed, so
  their branches were empty and the supervisor `cp`'d files into main, past the gate's
  diff. RED/SCAFFOLD/GREEN gates now block uncommitted work, the worker prompts say to
  commit, and the playbook says to `git merge --no-ff` the worktree branch. Found by the
  e2e run.
- **Gates looped on the supervisor's own contract slips.** A comma-separated `SCOPE_GLOBS`
  (34 blocks in e2e run 2) or a mistyped `AGENT_ROLE` in the main-tree contract blocked a
  worker that cannot edit the contract, so it retried until it gave up. Lists now accept
  commas, and the host's role wins over the contract's with a WARN (a contradicting
  worktree copy still blocks).
- **A worker's `selfcheck` could not find its task on Claude Code** (`STORY_DIR unset`, once
  per role in e2e run 2): the worktree is made at dispatch and has no `task.env`. The time
  box's first tool-call hook now copies the supervisor's contract into the worktree (the
  dispatch prompt's `TASK_ID` picks it); the gates still compare the copy to the contract.
- **GREEN looped on RED's formatting** (19 blocks in e2e run 3): RED's test file failed
  `cargo fmt --check`, and GREEN may not touch tests. RED and SCAFFOLD gates now run the
  matrix's format check, so the file is fixed by the role that wrote it.
- **False "isolation dropped" blocks** (27 in e2e run 5): a worker that `cd`s to the main
  tree to read its story left the hook's cwd there. Gates now check the worktree Claude
  Code made for that agent (`.claude/worktrees/agent-<agent_id>`) first. The Bash contract
  guard is per simple command now (run 5 blocked a read-only probe), and RED's
  out-of-scope message says how to undo the change.
- **The supervisor could write source mid-sprint on Linux.** `gate-supervisor-scope`'s
  "recently refreshed lock" arm called `stat -f %m` first; on Linux that prints a
  filesystem report instead of failing, the arithmetic crashed under `set -u`, and a
  crashed hook does not block. GNU `stat -c %Y` now goes first and the value is checked.
  Found by the live smoke test; `scripts/test/supervisor-scope.test.py` covers it.
- **Valid planner artifacts were blocked by the schema gate.** `gate-validate-artifact`,
  `gate-intake` and `gate-memory` ran md-db on the written file's whole directory, and
  `gate-stage2-complete` on the whole sprint tree. Those directories also hold the
  `init.md`/`output.md` comms (and `stats.md`), which the schema does not know, so every
  valid `tasks.md`/`validate.md`/`stories.md` write in a story that had comms was blocked.
  They now validate only the written file (stage-2: only the planning artifacts). Found by
  the new §6.2 fixture.

## [0.11.0] - 2026-10-17

### Changed
- **Renamed to aloop (Aladdin's Loop).** The plugin is `aloop@aloop-marketplace`, the
  skill is `/aloop:aloop` (entry point `/aloop:init`), the CLI is `aloop <tool>` and
  `aloop init`, and the OpenCode plugin file is `aloop.js`. Upgrade: uninstall
  `agentic-agile@agentic-agile-marketplace`, then install `aloop@aloop-marketplace`
  (README → *Upgrading from agentic-agile*).

### Compatibility
- `agentic`, `agentic init`, `agentic-init` and `agentic bind` still work as aliases.
- `aloop install opencode|codex` links both `~/.local/bin/aloop` and `~/.local/bin/agentic`,
  and removes the pre-rename generated files (OpenCode would otherwise load both plugins).
- Project data is untouched: `.agentic/`, `docs/agents/`, `agentic/<task>` branches and the
  `AGENTIC_*` settings keys keep their names (a migrated layout is planned for M1).

## [0.10.1] - 2026-09-23

### Fixed
- **Time-box defaults were far too tight.** 40k/60k tokens killed real RED/GREEN
  attempts (reading the style guide + running fmt/vet/lint/build/test uses 60–190k
  even when the attempt succeeds) before they wrote a line. Defaults are now
  **150k / 250k tokens, 20 / 30 min**.
- **The per-task budget override was never found** when a worker addressed its worktree
  with `git -C <wt>` or absolute paths instead of `cd`: the hook located the worktree
  from the tool call's cwd (the main repo), so it never read the worktree's
  `.agentic/task.env` and applied the default for the whole attempt. The hook now finds
  the worktree from a recorded binding, the paths a tool call names (`-C`, `cd`,
  `--work-tree`, absolute paths, file paths, patch headers), the cwd, or — for a first
  call that only reads main-tree files — the one unclaimed worktree whose `task.env`
  names this role. When the worktree becomes known later, the budget is re-resolved
  without resetting the attempt's clock, and a hard verdict reached under the wrong
  budget is withdrawn.

### Added
- `budget status` shows each attempt's budget source (tasks.md line, task.env,
  defaults.md, built-in) and how its worktree was found; `budget resolve --worktree WT`
  prints the source too; soft/hard messages name the budget's source.
- Regression test `scripts/test/budget-worktree.test.py` (in `make test-hosts` / CI).

## [0.10.0] - 2026-09-23

### Added
- **Tools install themselves and are always on PATH.** New `SessionStart` hook
  `bin/ensure-tools` builds `md-db` + `ctx-symbols` from the bundled source into the
  plugin data dir (background, once per plugin version; `--sync` to force) and tells the
  model where every tool is. `bin/md-db` / `bin/ctx-symbols` are PATH shims (the
  platform puts the plugin's `bin/` on PATH for every session and sub-agent) that exec
  the real binary. Agents now call `selfcheck`, `md-db`, `budget`, … by bare name, and
  the prompts tell them never to search. `gate-tooling` tries to install before blocking.
- **Per-attempt time box (tokens + wall clock).** New `bin/budget`: soft limits (default
  40k tokens / 10 min) warn the worker inside its tool results; hard limits (60k / 15
  min) deny further tool calls except the output.md report, release the worker's
  SubagentStop gate unverified, and surface a KILL to the supervisor via `budget watch`.
  Configurable per project (`docs/agents/defaults.md`, via init) and per task
  (`budget:` line in tasks.md).
- **Orchestrator loop: dispatch → sleep → sweep.** The supervisor dispatches background
  workers, sleeps on `budget watch`, then `TaskStop`s workers past their hard limit.
- **Mid-sprint re-plan route.** A task killed at its hard limit is not retried: the
  sprint pauses, the planner is dispatched with `mode: replan`
  (`pipeline/planning/02-planner/replan.template.md`) to split it (`split_from:`),
  leaving done tasks untouched, and the sprint resumes. Capped per task lineage
  (`AGENTIC_MAX_REPLANS`, default 2; `budget replans`). New execution.log phases
  `budget` and `replan`.
- **Self-contained sprints.** The planner prompt requires every sprint to be completable
  on its own (context is cleared between sprints); `gate-stage2-complete` blocks
  references to a later sprint's stories and work deferred into a later sprint. The
  harness asks for `/clear` at each sprint boundary.

- **Init confirms the project setup with the human.** `/agentic-agile:init` runs
  `agentic-init --show` (limits, models, the `.gitignore` edit, whether `docs/agents/` is
  tracked — default no — and the commit author — default the human's git identity, no
  Claude trailers), asks, then `agentic-init --apply` writes `docs/agents/defaults.md`,
  the managed `.gitignore` lines and the repo-local git author. The hooks read limits
  from that file; the SessionStart hook re-applies the `.gitignore` lines every session.
- **`gate-commit-author`** (PreToolUse · Bash): denies commits with another author,
  identity overrides, or Claude co-author/attribution trailers (unless opted in).
- **One fixed layout** (`bin/_paths.sh` / `_paths.py`): run data lives only in
  `<project>/.agentic/{transcripts,budget,ledger,logs,state}`; no model-chosen or
  per-worktree path, no task.env override. Capture hooks write nothing in a repo that
  isn't an agentic-agile project.
- **Token ledger + sprint stats + handoff, by the harness** (`bin/stats`): Stop and
  SubagentStop rebuild `.agentic/ledger/<session>.jsonl` from the transcripts (main +
  sub-agents). When FINAL-GATE passes, `gate-final` runs `stats sprint-close`:
  `sprintN/stats.md` (tasks, attempts, gate passes/blocks, budget stops, re-plans,
  tokens per sprint/session/project, by role and model, human messages) and
  `docs/agents/NEXT.md` (orchestrator handoff). The next Stop shows the human the totals
  and asks for `/clear`; the SessionStart hook loads `NEXT.md` into the fresh context.
  `stats show` prints totals any time. Every gate verdict is logged to
  `.agentic/logs/gates.jsonl`.

- **Codex and OpenCode support — full feature parity.** `agentic install codex|opencode
  [--project DIR|--global] [--uninstall]` renders the 9 role agents (Codex TOML /
  OpenCode markdown), skills and the init command from this plugin's own sources.
  - **Codex** loads this repo's `.claude-plugin` manifests and `hooks/hooks.json`
    natively (same events, `CLAUDE_PLUGIN_ROOT`); hooks were made Codex-aware:
    `apply_patch` edits (supervisor scope, report-only writes at the hard limit),
    rollout token usage (`.jsonl`/`.zst`, cumulative totals), sub-agent rollouts found
    by thread id.
  - **OpenCode** gets a generated plugin (`hosts/opencode/agentic-agile.js`) that maps
    its hooks onto the same scripts: deny by throwing, context appended to tool output,
    `session.abort` for the hard stop, the role gate on the `task` result (a block
    re-prompts the sub-agent up to 3 rounds), usage written Claude-shaped for the ledger,
    the handoff in the system prompt, `bin/` on PATH via `shell.env`.
  - **Worker isolation without built-in worktrees:** `agentic task-worktree add
    <TASK_ID>` + the worker's first command `agentic bind <path>`; `host-adapter` then
    confines it (Codex: commands/patches outside the worktree are denied; OpenCode:
    rewritten into it) and the SubagentStop gate runs inside the bound worktree.
  - `agentic` — one dispatcher for every tool (`agentic selfcheck`, `agentic init …`),
    linked into `~/.local/bin` where the host has no plugin PATH.
  - `_usage.py` — one token reader for all three hosts (budget + ledger).
  - CI job `hosts`: Codex hook-runner emulation (14 checks) + OpenCode plugin against a
    mock client (18 checks) — `make test-hosts`.

### Changed
- **The hard-limit kill is deterministic:** after one chance to write its report and 3
  refused calls, the time-box hook stops the worker itself (PostToolUse
  `continue: false`); `budget watch` reports `STOPPED`, with `TaskStop` only a backstop.
- `gate-final` also checks `plan-ready.md` in the sprint's main-tree docs (a worker
  worktree has `docs/agents/` sparse-checked out).
- The supervisor may not hand-edit `defaults.md`, `NEXT.md` or `stats.md`.
- **Models:** planning agents (intake, standards, planner, archivist) default to
  `claude-opus-5-5`; execution agents and the orchestrator skill default to `sonnet`.
  Per-project override via `AGENTIC_MODEL_PLANNING` / `AGENTIC_MODEL_WORKER` in defaults.md.
- Gates detect backends with `ensure-tools --resolve` (the real binary), not
  `command -v` (which would now find the shim).

## [0.9.0] - 2026-07-03

### Added
- **`bin/worktree-hygiene`** — new `SubagentStart` hook (exec roles) that sparse-checks
  `docs/agents/` out of a worker's worktree. Workers already reach their story's docs via
  the absolute `STORY_DIR` path back into the main tree, so the tracked `docs/agents/**`
  checkout `git worktree add` puts in every worker worktree was pure, increasingly-stale
  redundancy. Runs regardless of whether the harness's built-in `isolation: "worktree"` or
  the optional `bin/worktree-create` made the worktree (the built-in path never calls
  `worktree-create`, so a fix scoped to that script alone wouldn't have fired for it).
- Same hook (and `bin/worktree-create`, for parity) bootstraps the **main tree's
  `.gitignore`** with `.agentic/`/`.transcripts/` the first time a worker runs against it
  — idempotent, mkdir-lock-guarded against concurrent first dispatches, and skipped
  entirely once the entries already exist.

### Fixed
- Sparse-checkout now **merges** its `!/docs/agents/` exclusion into any pre-existing
  sparse-checkout pattern file instead of overwriting it, in case a target repo already
  used sparse-checkout for its own reasons.

## [0.8.3] - 2026-06-13

### Fixed
- **Invalid plugin manifest: removed the `WorktreeCreate`/`WorktreeRemove` hooks.** They
  were added in v0.6.0 based on bad research — newer Claude Code rejects them with
  *"Unknown hook event(s)"* (they are not valid plugin-manifest events; they only ever
  belonged in user `settings.json`). The plugin now relies on the harness's **built-in
  `isolation: "worktree"`** for git repos (the nesting symptom was in the custom hook, so
  this also removes that failure mode). `bin/worktree-create`/`worktree-remove` are kept
  as an **optional user-`settings.json`** mechanism for a non-git VCS, documented as such.
  `assert_worktree_isolation` still enforces a real linked worktree regardless of source.

### Added
- **`make package`** — bundles the release (`.claude-plugin` + `plugin` + README +
  CHANGELOG + LICENSE) into `package/agentic-agile-vX.Y.Z.tar.gz` (+ SHA-256) and
  publishes it as the **GitHub release** for the current tag (via `gh`, `--latest`, notes
  from the matching CHANGELOG section). `package/` is git-ignored.

## [0.8.2] - 2026-06-13

### Fixed
- **Nested worktrees.** `worktree-create` anchored the new worktree to the dispatch
  `cwd`, so a dispatch from *inside* a worktree created a worktree nested under it
  (`…/worktrees/A/.agentic/worktrees/B/…`) — which compounds and breaks Wave
  consolidation. It now resolves the MAIN working tree (first entry of `git worktree
  list`, regardless of the nested `cwd`) and creates every worktree as a flat sibling
  under `<main>/.agentic/worktrees/`, off the main tree's HEAD. `base_path` from the
  payload is no longer used for placement (it could be nested).

## [0.8.1] - 2026-06-13

### Fixed
- **Supervisor source-block gap in a resumed sprint session.** `gate-supervisor-scope`
  only blocked the supervisor from hand-editing production source if THIS session had
  armed the sprint lock — so a *resumed* session (reads existing plans, then e.g.
  hand-fixes code after a structural-review finding) slipped through before it re-armed.
  The source block now also binds when the lock was **refreshed recently**
  (`AGENTIC_SPRINT_LOCK_TTL`, default 4h), while a lock that is both a different session
  AND stale stays inert (no persistence trap). Tightened the block message to name the
  right move on a structural finding: RE-DISPATCH the implicated GREEN task, don't hand-fix.

## [0.8.0] - 2026-06-13

### Added
- **Hard enforcement: the supervisor can no longer hand-write planning artifacts.** The
  v0.7.1 SKILL text ("dispatch the planner") didn't hold — the supervisor kept writing
  `stories.md`/`plan.md` itself. `gate-supervisor-scope` (PreToolUse) now BLOCKS the
  **main agent** from writing `stories/tasks/validate/plan/intake/standards.md` under
  `docs/agents/**` (the block message tells it to dispatch the `planner`/`intake`/
  `standards` activity). A dispatched sub-agent is exempt — distinguished deterministically
  by the `agent_type` field, which Claude Code includes in a sub-agent's PreToolUse
  payload and omits for the main session (so no fragile sentinel is needed). Path-based,
  so it fires on the very first `stories.md` write, before any sprint lock is armed.

### Changed
- `gate-supervisor-scope` now keys "is this the supervisor?" off the payload `agent_type`
  (present ⇒ sub-agent ⇒ allow) rather than only the worktree `task.env` role, which also
  hardens the existing no-supervisor-source rule. The supervisor still owns
  `plan-ready.md` / `init.md` / `execution.log` / `memory.md`.

## [0.7.1] - 2026-06-13

### Changed
- `SKILL.md`: made it explicit that the supervisor **dispatches the `planner` agent** to
  author Stage-2 (`stories`/`tasks`/`validate`/`plan`/`sprint-plan`) — it does NOT
  hand-write them inline (same anti-pattern as hand-writing code). Added a hard guardrail,
  prefaced the five planning steps with "dispatch the planner, one sprint at a time", and
  scoped execution to ONE sprint per autonomous run (stop at its final gate; the human
  decides the next sprint — no Sprint N → N+1 sweeps, no crossing planning→execution
  without an explicit "go").

## [0.7.0] - 2026-06-13

### Changed
- **init.md/output.md are now a story-bound, append-only comms channel — and it is
  enforced.** Previously: per-attempt directories, the worker agent defs never even
  mentioned `output.md`, and `validate_agent_io` skipped silently when `ATTEMPT_DIR`
  was unset (so agents wrote nothing and gates passed). Now:
  - One `init.md` + `output.md` pair **per story** (`sN-NN-<slug>/`), each opening with a
    single `type: init|output` frontmatter. Every dispatch/attempt across a story's
    RED → SCAFFOLD → GREEN → structural-review (+ retries) **appends** a block headed
    `## <task> · attempt N · <role> · <ts>` — the full inter-agent negotiation history,
    read top-to-bottom. No per-attempt dirs; nothing is rewritten.
  - **Enforced** by `validate_comms` (`_gatelib.sh`), called by the red/scaffold/green/
    structural gates: BLOCKS (exit 2, no more WARN-and-skip) unless `STORY_DIR` is set and
    the latest `output.md` block is present, from the dispatched role, and carries
    `### Summary`/`### Result`/`### Next`. `gate-red-verify` parses only the latest block.
  - Every execution agent def (`red/green/scaffolder/structural-reviewer`) now carries the
    contract: read your `init.md` block, append your `output.md` block, run `selfcheck`.
  - `gate-validate-artifact` validates only the static top frontmatter of init.md/output.md
    (md-db can't enumerate the dynamic blocks); the blocks are grep-checked.
  - `task.env` contract: `ATTEMPT_DIR` → `STORY_DIR` (absolute path to the story dir).

### Fixed
- `changed_paths` (RED/GREEN diff-scope) now excludes `docs/agents/**`, so an agent
  writing its `output.md` comms block is no longer flagged as an out-of-scope source edit.

## [0.6.0] - 2026-06-13

### Added
- **Worktree isolation now works out of the box** (`bin/worktree-create` +
  `bin/worktree-remove`, wired as `WorktreeCreate`/`WorktreeRemove` hooks). The
  execution model requires every worker dispatch to use `isolation: "worktree"`, but
  the harness's built-in worktree feature errored — *"Cannot create agent worktree: not
  in a git repository and no WorktreeCreate hooks are configured"* — because the plugin
  shipped no such hooks. Now it does: they run plain `git worktree add`/`remove`
  (`agentic/<name>` off HEAD), so isolation works on any git repo with no settings.json
  wiring. Idempotent — a task's RED → SCAFFOLD → GREEN reuse one worktree under the same
  isolation name. The created worktree is a real linked worktree, so
  `assert_worktree_isolation` is satisfied. No `jq` dependency.

### Changed
- `SKILL.md`: documented that worktree isolation is hook-backed and that a git repo
  **with an initial commit** (the worktree base ref) is a prerequisite for execution —
  a non-git/uncommitted tree is a prerequisite to FIX, never a reason to fall back to
  the shared tree.
- The CI `shellcheck` target now lints every `bin/` script (was: only `gate-*` +
  `_gatelib.sh` + `log-execution`), so `transcripts`/`selfcheck`/`worktree-*` are covered.

### Fixed
- `gate-supervisor-scope` no longer blocks the supervisor from writing `.gitignore` /
  `.gitattributes` during a sprint — VCS hygiene for the initial commit is never
  application source, so it's allowed without the `SKIP_HOOKS=1` escape. Production
  source is still blocked.

## [0.5.0] - 2026-06-12

### Changed
- **Sprint-entry discipline.** A fresh build/fix/improve request mid-session was being
  treated as "just do the work" instead of opening a new sprint/stories. Broadened the
  skill trigger `description` to fire on terse/visual requests (improve/redesign/polish,
  "the UI is broken", "add facets/filters", a screenshot with a complaint), and added a
  Hard-guardrail: a new build/fix/improve request is NEW SCOPE — re-enter intake →
  planner, never switch into ad-hoc hand-editing.
- **Renamed `lineage` → `transcripts`, and it now records EVERYTHING.** The old
  `bin/lineage` captured only `tool_name` + `file_path` per event — not a transcript.
  Replaced by `bin/transcripts`, which captures:
  - `<task>/events.jsonl` — the FULL hook payload per tool call (tool_input +
    tool_response) plus every user prompt (new `UserPromptSubmit` hook).
  - `<task>/transcript.jsonl` — the complete session snapshot (every user/assistant
    message + thinking + tool result), copied from the session's `transcript_path` on
    each stop (new `Stop` hook + `SubagentStop`).
  - `global.jsonl` — a thin cross-task causal stream for the retrospective to scan.
  Retention keeps everything (no auto-compaction); `transcripts prune`
  (`AGENTIC_TRANSCRIPTS_KEEP`) is a manual, opt-in cap.
  Renamed throughout: the `bin/lineage`→`bin/transcripts` script, the
  `AGENTIC_LINEAGE_DIR`→`AGENTIC_TRANSCRIPTS_DIR` task.env var, the `.lineage/`→
  `.transcripts/` worktree slice, the store dir, hook wiring, schemas-adjacent docs,
  SKILL.md Part C, README/STRUCTURE/ARCHITECTURE/DESIGN, and the archivist
  agent/eval. No back-compat shim.

## [0.4.0] - 2026-06-12

### Fixed
- **`make install` failed on rustc < 1.95.** The committed `md-db` `Cargo.lock`
  pinned `kdl@6.7.1`, whose MSRV jumped to 1.95 at the 6.6.0 release, so the build
  died (`kdl@6.7.1 requires rustc 1.95`) on toolchains as recent as 1.94.1. Pinned
  `kdl` back to `6.5.0` (MSRV 1.82, still within the `kdl = "6"` range). Verified by a
  clean rebuild under rustc 1.94.1. Corrected the stale `install.sh` header comment
  (it claimed edition 2024 / rustc ≥ 1.85).

### Added
- **`gate-supervisor-scope`** (`PreToolUse · Write|Edit|MultiEdit`) — blocks the
  supervisor from writing production source while a sprint is live in the current
  session, forcing all code through dispatched workers. Uses a session-scoped,
  self-arming lock (armed on the first `docs/agents/**` write) so a stale lock from an
  abandoned sprint never blocks unrelated future sessions. Honors `SKIP_HOOKS=1`.
- **`gate-tooling`** (`SubagentStart` on execution roles + a manual supervisor
  preflight) — BLOCKS the first worker dispatch unless `md-db` and `ctx-symbols` are
  on PATH. Execution no longer starts silently grep-degraded; planning may still run
  degraded. `selfcheck tooling` runs it. Honors `SKIP_HOOKS=1`.

- **Worktree-isolation enforcement** in the writing-worker gates. `_gatelib.sh` gains
  `assert_worktree_isolation`, called by `gate-red/scaffold/green-verify`: a worker
  that ran in the shared/main tree (detected via `.git` being a directory rather than
  a worktree's `gitdir:` file) is BLOCKED. Stops the supervisor from silently dropping
  isolation as an "environmental adaptation". Honors `SKIP_HOOKS=1`.

### Changed
- `SKILL.md`: added the "supervisor never writes production source" guardrail and an
  ambiguous-resume rule ("go on"/"continue" = re-dispatch the last activity, never
  "build it directly"); documented the execution tooling preflight. Added the
  **schedule-vs-invariant** rule (adapt order/parallelism freely; never relax
  worktree isolation, gates, TDD ordering, or no-suppression — those escalate to
  planning) and the sanctioned disk-pressure path (serialize worktrees, don't share).

## [0.3.1] - 2026-06-08

### Changed
- **Tool allowlists on the non-building agents** (subagents-doc audit). The five agents
  that read/verify/produce-artifacts only — `intake`, `standards`, `structural-reviewer`,
  `final-gate`, `archivist` — now declare `tools: Read, Grep, Glob, Bash, Write`, omitting
  `Edit`/`MultiEdit` so they can't patch source/tests in place. This is defense-in-depth:
  `Bash`/`Write` can still touch files, so the deterministic gates remain the real
  enforcement (noted inline). The 3 builders (`red`/`scaffold`/`green-worker`) and the
  `planner` intentionally keep full tools — they must edit code or revise `.md` artifacts;
  `planner.md` documents why. Confirmed all agent `name`s still match the `hooks.json`
  `SubagentStop` matchers, and no agent uses the plugin-ignored `hooks`/`mcpServers`/
  `permissionMode` fields.
- `archivist` now drafts memory candidates to its `output.md` only and never writes
  `docs/agents/memory.md` directly — the supervisor + human curate it.

## [0.3.0] - 2026-06-08

### Added
- **`init` entry-point skill** (`plugin/skills/init/`): a thin, explicit-only alias
  (`disable-model-invocation: true`) invoked as `/agentic-agile:init`. It points at the
  canonical `agentic-agile` playbook rather than duplicating it, so the two never drift.
  The main `agentic-agile` skill still auto-triggers on natural-language build/TDD asks.

## [0.2.1] - 2026-06-08

### Fixed
- **Plugin failed to load: duplicate hooks file.** `plugin.json` declared
  `"hooks": "./hooks/hooks.json"`, but Claude Code auto-loads the standard
  `hooks/hooks.json` by convention — the manifest declaration loaded it a second time
  and the plugin refused to load. Removed the redundant `manifest.hooks` entry. Caught
  by a live `claude plugin install` smoke test; static `claude plugin validate` passes
  either way.

### Added
- **Eval suites** (agentskills.io style): expanded the skill suite to 7 cases
  (`plugin/skills/agentic-agile/evals/`) and added per-agent suites for all 9 agents
  (`plugin/evals/agents/*.json`) plus a shared harness (`scripts/eval/`) and
  `make eval-validate` / `make eval`.
- **Vendored `md-db`** (`plugin/tools/md-db/`, AGPL-3.0): `make install` now builds
  both gate backends from source; md-db is no longer an external prerequisite.

### Changed
- `make test` runs both Rust crates; `make ci` now includes `eval-validate`.
- Marketplace gained a `description`; plugin manifest gained `homepage`/`repository`.

## [0.2.0]

### Added
- **Per-task lineage** (`bin/lineage`, file-based, no FUSE): a global append-only
  `lineage.jsonl` + per-task transcripts. Each sub-agent gets a READ-ONLY task slice
  staged into its worktree (`.lineage/`); the supervisor reads the global store. Wired
  via `SubagentStart` (stage-in), `PostToolUse *` (record), `SubagentStop *` (stage-out);
  lineage hooks never block.
- **Planning retrospective + memory** (`pipeline/planning/retrospective/`, the
  `archivist` agent, `schemas/memory.kdl`): every planning session distills recurring
  failures + reliably-good patterns (plus human insight) into terse, role-scoped
  entries in `docs/agents/memory.md`, injected into each `init.md` `# Memory` section.
- **Manifests**: `plugin/.claude-plugin/plugin.json` and root `.claude-plugin/marketplace.json`.
- **Per-worktree task contract** `.agentic/task.env` (supervisor-written), read by the gates.
- **Fix (found by end-to-end dry run): executable bits** — all `bin/gate-*` and
  `log-execution` are now shipped `+x`; previously several were `0644`, so the hooks
  would have failed to exec them at runtime (exit 126).
- **Fix: diff-scope excludes `.agentic/`** — the plugin's own per-task control files
  (`task.env`, `scaffold-symbols`, lineage) no longer count against a GREEN task's
  scope check (joining `target/`, `Cargo.lock`, `.git/` in the filter).
- **Self-check at EVERY step**: added `gate-intake` (five-part intent) and `gate-memory`
  (valid + never relaxes an invariant) so intake and the archivist self-check too;
  `gate-stage2-complete` auto-discovers the sprint dir; SubagentStop now backstops every
  dispatched role (intake/standards/planner/archivist + the 5 execution gates). Nothing
  advances past a failing self-check.
- **Self-check loop** (`bin/selfcheck`): workers run their own gate (the same script the
  hook enforces) + `md-db validate` BEFORE reporting done — layered determinism, fewer
  re-spawns. SKILL.md gains a "Validate before you report" + "Available scripts" section.
- **Skill-quality artifacts** (agentskills.io standards): `skills/agentic-agile/evals/` (evals.json + eval_queries.json), `bin/README.md` (gate usage + exit-code catalog), `lineage --help`, a Prerequisites section and a triggering-optimized description in SKILL.md.

### Fixed
- `final-gate` fires on `SubagentStop` (matcher `final-gate`), not `Stop` — it is a
  dispatched sub-agent.
- `gate-scaffold-verify` resolves production symbols from `.agentic/scaffold-symbols`
  (scaffolder-written) instead of mis-reading test paths from `plan-ready.md`, and flags
  only marked shim files (`// agentic:shim`) instead of any `mod common` (which collided
  with the standard Rust `tests/common` convention).

### Changed
- `agent-io.kdl` `agent_role` enum gains `archivist`.

## [Unreleased]

## [0.1.0] - 2026-06-08

Initial release.

### Added
- **Plugin manifest + marketplace** — `plugin/.claude-plugin/plugin.json` and a
  root `.claude-plugin/marketplace.json` (install via
  `/plugin install agentic-agile@agentic-agile-marketplace`).
- **Supervisor playbook** — canonical `skills/agentic-agile/SKILL.md`: two-stage
  planning (intake → standards → planner) + two-phase TDD execution
  (RED → SCAFFOLD → GREEN → STRUCTURAL-REVIEW → FINAL-GATE), per-task attempt dirs,
  the revision/feedback loop, cause-specific escalation, and the data-plane writers.
- **8 dispatchable agent personas** (`agents/`) assembled from `pipeline/`.
- **Deterministic gates** (`bin/`) wired in `hooks/hooks.json` by `agent_type`:
  `gate-red-verify`, `gate-scaffold-verify`, `gate-green-verify`,
  `gate-structural-integrity`, `gate-final`, `gate-stage2-complete`,
  `gate-validate-artifact`, `gate-standards-cited`, `gate-plan-shape`,
  `gate-ledger-format`, plus `log-execution` and the shared `_gatelib.sh`.
  GREEN/FINAL matrix is read from `standards.md`, not hardcoded.
- **ctx-symbols** (`tools/ctx-symbols`) — minimal tree-sitter symbol resolver
  (`count`/`locate`/`search`/`conflicts`) harvested from the MIT `ctxconfig`
  code-intelligence layer; backs the scaffold/structural gates with grep fallback.
- **md-db KDL schemas** (`schemas/`) — `agent-io`, `planning-artifacts`, `ledger`.
- **Tooling** — `tools/install.sh`, `Makefile`, git hooks (`.githooks/`),
  GitHub Actions CI (`.github/workflows/ci.yml`), and linter configs.

### Known limitations
- Gate bodies are verified offline (positive + negative); the live hook wiring
  should be smoke-tested in a real Claude Code session.
- Suppression detection is syntactic (see SKILL.md "Known gate limitations").

[Unreleased]: https://github.com/adeelahmad/agentic-agile/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/adeelahmad/agentic-agile/releases/tag/v0.1.0
