# agentic-agile — Plugin Architecture & Implementation

> **Historical.** Part of the pre-v0.2 design package; counts, paths and status here are out of date. For the current picture read [OVERVIEW.md](OVERVIEW.md).


How this is built as a Claude Code plugin and how every piece is wired together.
Concrete. Reads top-to-bottom as an implementation guide.

================================================================================
## 0. One-paragraph mechanism

A plugin ships four things in one install: a SKILL (the orchestration playbook the
supervisor runs), AGENTS (the 8 dispatched personas), HOOKS (deterministic gates
that fire on agent/tool events regardless of model behavior), and BIN scripts (the
gate bodies + ledger writer, which shell out to `md-db` for artifact validation and
`ctx-symbols` for code-structure checks). The supervisor — the agent that loads the
skill — drives PLANNING interactively with the human, then drives EXECUTION
autonomously, dispatching one worktree-isolated sub-agent per task. Every sub-agent
stop is intercepted by a hook that runs the matching gate; exit 2 blocks the stop
and feeds the failure reason back to the supervisor, which either re-spawns the task
(within its per-task budget, appending feedback to that task's `init.md`) or
escalates. The on-disk artifact tree is the data plane that glues planning output to
execution input and carries the feedback loop.

================================================================================
## 1. Primitive mapping (what each design element compiles to)

| Design element                | Claude Code primitive                                  |
|-------------------------------|--------------------------------------------------------|
| the playbook / orchestration  | `skills/agentic-agile/SKILL.md` (run by the supervisor)|
| supervisor                    | the main agent that loaded the skill                   |
| 8 personas                    | `agents/<role>.md` (dispatched via the Task tool)      |
| per-task isolation            | dispatch with `isolation: "worktree"`                  |
| deterministic gates           | `hooks/hooks.json` — SubagentStop / Stop / PostToolUse |
| gate bodies                   | `bin/gate-*` (exit 0 pass, exit 2 block+feedback)      |
| artifact validation           | `md-db validate ... --schema schemas/*.kdl`            |
| code-structure checks         | `ctx-symbols` (optional) with grep fallback            |
| feedback channel              | `init.md` / `output.md` per task attempt               |
| static handoff                | `plan-ready.md` (per story)                            |
| ledger                        | `execution.log` (append-only)                          |

================================================================================
## 2. Manifest & layout

`.claude-plugin/plugin.json`
```json
{
  "name": "agentic-agile",
  "version": "0.1.0",
  "description": "Two-stage planning + two-phase TDD execution with md-db/KDL validation and hook gates.",
  "skills": "./skills",
  "agents": "./agents",
  "hooks": "./hooks/hooks.json"
}
```

```
agentic-agile/
  .claude-plugin/plugin.json     install entrypoint
  skills/agentic-agile/SKILL.md  the orchestration the supervisor executes   [OPEN-2]
  agents/<role>.md               8 dispatchable personas (assembled from pipeline/)
  hooks/hooks.json               event -> matcher -> gate-script wiring
  bin/gate-*                     gate bodies; bin/log-execution writes the ledger
  schemas/*.kdl                  md-db schemas
  pipeline/                      design source of truth (persona/init/artifacts/gate per activity)
```
`pipeline/` is the human-readable design source; `agents/` is assembled from
`pipeline/*/persona.md`. `${CLAUDE_PLUGIN_ROOT}` resolves to the installed plugin dir
inside every hook command, so all `bin/`/`schemas/` paths are addressed through it.

================================================================================
## 3. Control plane — who runs what

- The SUPERVISOR is the agent that loaded `SKILL.md`. It is present in BOTH phases.
  It owns all planning artifacts, `plan-ready.md`, and `execution.log`; it dispatches
  sub-agents and reacts to gate verdicts. There is no separate "supervisor binary."
- DISPATCH: the supervisor spawns a sub-agent with the Task tool, passing
  `subagent_type: <role>` (matches `agents/<role>.md`) and `isolation: "worktree"`.
  One dispatch = one task. The contract handed in is the task's `init.md` (the
  supervisor writes it before dispatch; it is read-only to the sub-agent).
- A plugin-provided sub-agent IGNORES `hooks` / `mcpServers` / `permissionMode` in
  its own frontmatter. THIS IS WHY every gate lives in the top-level `hooks.json`,
  matched by `agent_type` — not in the agent files. (Design consequence, not a
  preference.)

================================================================================
## 4. Enforcement plane — hooks.json wiring

Exit-code contract for every gate script: **exit 0 = pass**; **exit 2 = block the
event and send stderr back to the supervisor**; other non-zero = surface as error.

| Phase     | Event         | Matcher (agent_type / tool) | Script                      | On exit 2 |
|-----------|---------------|-----------------------------|-----------------------------|-----------|
| planning  | PostToolUse   | Write                       | gate-validate-artifact      | block the write; md-db said the artifact is malformed |
| planning  | SubagentStop  | standards                   | gate-standards-cited        | standards.md has dangling citations |
| planning  | Stop*         | (planning command)          | gate-stage2-complete        | refuse handoff to execution until Stage-2 valid [OPEN-3] |
| execution | SubagentStop  | red-worker                  | gate-red-verify             | a new test passed / prod code written / regression |
| execution | SubagentStop  | scaffolder                  | gate-scaffold-verify        | a body was implemented, a symbol defined twice, shim left, or clobber |
| execution | SubagentStop  | green-worker                | gate-green-verify           | a task test failed / out-of-scope diff / suppression / standards matrix red |
| execution | SubagentStop  | structural-reviewer         | gate-structural-integrity   | a HIGH-severity (foundation-poisoning) finding |
| execution | Stop          | final-gate                  | gate-final                  | matrix red / suppression present / unticked plan-ready box |

`*` Planning is interactive, so the Stage-2 gate is awkward as a hard hook. Two
viable wirings, decided by [OPEN-3]: (a) a Stop hook on the planning command that
blocks "proceed to execution," or (b) a supervisor self-check step that calls
`bin/gate-stage2-complete` before the first dispatch. `gate-validate-artifact`
(PostToolUse Write) is deterministic and works in interactive planning regardless.

**The feedback loop, exactly:** a SubagentStop gate exits 2 → Claude Code blocks the
sub-agent's stop and hands stderr to the supervisor → the supervisor reads the
structured reason, and (if within the task's budget) appends a `# Feedback` entry to
the task's next `attempt-K/init.md` and re-dispatches the SAME `subagent_type` for
the SAME `task_id` → otherwise it escalates (§7). The hook guarantees "you cannot
pass a failed gate"; the supervisor owns the retry/escalate decision.

================================================================================
## 5. Validation plane — md-db and ctx-symbols

Two independent, optional backends. Neither pulls in the prior project's
orchestration engine.

**md-db (validates the .md artifacts).** Every gate that inspects an artifact runs:
```
md-db validate <attempt-or-story-dir> --schema ${CLAUDE_PLUGIN_ROOT}/schemas/<x>.kdl
```
`bin/gate-validate-artifact` routes by filename:
```
init.md|output.md                                  -> agent-io.kdl
stories.md|tasks.md|validate.md|plan.md|plan-ready.md -> planning-artifacts.kdl
```
md-db enforces frontmatter, field types/enums/patterns, required sections, tables,
and cross-doc refs. It CANNOT enumerate the dynamic `## Tn` headings — those are
checked by `bin/gate-plan-shape` (grep: every checkbox line carries `path::fn`).

**ctx-symbols (analyzes the source code).** `gate-scaffold-verify` and
`gate-structural-integrity` probe `command -v ctx-symbols`:
```
ctx-symbols count <symbol> --tree .     # scaffold: must be exactly 1 (no dup defs)
ctx-symbols search/locate ...           # structural: orphan / parallel / duplicate
```
If absent they fall back to grep and log a "weaker check" warning — the plugin never
hard-fails on a missing binary.

**Prerequisites / install:** `md-db` and (optionally) `ctx-symbols` must be on PATH.
[OPEN-8] recommends extracting a minimal `ctx-symbols` (just `locate`/`count` over
ast.rs+resolver.rs) rather than shipping the whole prior binary. Document both as
`cargo install` or bundled platform binaries in the plugin's install notes.

================================================================================
## 6. Data plane — on-disk layout (the glue)

Everything flows through one tree. This is how planning output becomes execution
input and how the feedback loop is carried.

```
docs/agents/
  sprintN/
    stories.md                  Intent + Expectations of record   (planner)
    plan.md                     sprint waves / dep graph          (planner)
    intake.md  standards.md     planning inputs                   (intake / standards)
    execution.log               append-only ledger                (supervisor + log-execution)
    sN-NN-<slug>/
      tasks.md  validate.md     story decomposition + rubric      (planner)
      plan.md                   tests-only plan                   (planner)
      plan-ready.md             RED->GREEN static handoff         (supervisor, once; ticked on GREEN pass)
      init.md                   APPEND-ONLY inbound comms — one block per dispatch  (supervisor)
      output.md                 APPEND-ONLY outbound comms — one block per attempt  (the agent)
```

- init.md/output.md are the STORY's append-only comms channel (story-bound, not
  per-attempt dirs). Each block is headed `## <task> · attempt N · <role> · <ts>`;
  `validate_comms` (in `_gatelib.sh`, called by every worker gate) BLOCKS unless the
  agent appended a well-formed latest block.
- **Per-task retry budget** = the number of that task's `output.md` blocks for the role
  (the `attempt N` counter). The counter is PER-TASK and resets per task, which is why a
  long run of healthy waves can't trip "retries exhausted." [resolves
  OPEN-4 toward per-task dirs]
- `init.md` is append-only and turn-counted (`attempt` field). Writers: supervisor
  (contract + feedback) and scaffolder (the create/update/delete scaffold log). The
  worker only reads it.
- `output.md`'s Result table is the granular failure record the supervisor reads to
  compose the next `init.md` feedback — so even though a gate has one "fail" edge,
  the *reason* is per-check.
- `plan-ready.md` boxes are ticked ONLY on GREEN pass; a retry happens on FAIL, so a
  re-spawn always precedes ticking for that task (no partial-tick race).

================================================================================
## 7. Worktree lifecycle + escalation

- Each dispatch runs in its own worktree (`isolation: "worktree"`). RED/scaffold/
  GREEN for a task share that task's worktree chain; nothing touches the integration
  branch until a gate passes.
- **Merge on pass:** when a task's `gate-green-verify` passes and the supervisor
  ticks `plan-ready.md`, its worktree merges to the sprint integration branch.
- **Abandon on HALT:** when `gate-structural-integrity` reports foundation-poisoning,
  the supervisor HALTS the dependency chain, ABANDONS the in-flight worktrees of that
  chain (`git worktree remove`), and KEEPS already-merged waves. Worktree isolation is
  what makes "revert on halt" clean — there is nothing to unwind on main.
- **Escalation is cause-specific** (not one uniform defer):
    foundation-poisoning -> HALT chain now;
    scope / plan defect   -> human decision;
    budget / no-progress  -> hard stop.
  All three surface at the NEXT planning session, re-entering at the PLANNER (not a
  full re-intake), with context preserved = `execution.log` + the failed task's
  `attempt-*/` history.
- **No-progress guard:** at each wave boundary the supervisor compares ticked-box
  count in `execution.log`; a wave that passed its gates but ticked zero new boxes is
  a stall -> escalate (catches cross-wave livelock that per-attempt budgets miss).

================================================================================
## 8. End-to-end run trace (concrete)

INSTALL
  1. `/plugin install agentic-agile` (or from a marketplace). The manifest registers
     the skill, the 8 agents, and hooks.json. `md-db` (+ optional `ctx-symbols`) must
     be on PATH.

PLANNING  (interactive, human present)
  2. User invokes the planning skill/command. The supervisor runs intake -> writes
     `intake.md`. PostToolUse `gate-validate-artifact` validates it; the supervisor
     checks the five-part Intent (`gate` in 00-intake).
  3. standards -> `standards.md`; SubagentStop `gate-standards-cited` (if dispatched).
  4. planner -> `stories.md`, then per story `tasks.md`/`validate.md`/`plan.md`, then
     `sprintN/plan.md`. Each Write is validated; `gate-plan-shape` checks each
     `plan.md` bullet has a real `path::fn`. Human reviews and approves.
  5. Handoff gate: supervisor runs `gate-stage2-complete` [OPEN-3] — blocks until
     every story is Stage-2 valid with no `TBW`.

EXECUTION  (autonomous, supervisor present, human absent)
  6. Supervisor reads `sprintN/plan.md` waves. For wave K, for each task T<k>:
     a. Write `T<k>/attempt-1/init.md`; dispatch `red-worker` (worktree). Worker
        writes tests vs `mod common` shims (so they FAIL BY ASSERTION) + `output.md`.
     b. SubagentStop -> `gate-red-verify`. exit 0: continue. exit 2: supervisor reads
        reason, writes `attempt-2/init.md` with feedback, re-dispatches (<= budget) or
        escalates.
  7. When all wave-K tasks have clean RED, supervisor writes each story's
     `plan-ready.md`.
  8. Dispatch `scaffolder` (worktree): reads `plan-ready.md` + RED tests; via
     `ctx-symbols` stubs only NEW symbols (idempotent, no clobber), swaps shims for
     `panic("SUB-AGENT-TODO: ...")`, appends scaffold lines to `init.md` +
     `execution.log`. SubagentStop -> `gate-scaffold-verify`.
  9. Dispatch `green-worker` per task (worktree): fills `SUB-AGENT-TODO` bodies;
     `output.md`. SubagentStop -> `gate-green-verify` (tests pass + in-scope diff +
     the fmt/lint/test/coverage matrix DECLARED IN standards.md). Pass -> supervisor
     ticks `plan-ready.md` -> merge worktree.
 10. Dispatch `structural-reviewer` (worktree): `ctx-symbols`/grep for orphan /
     parallel / duplicate. SubagentStop -> `gate-structural-integrity`:
       clean              -> continue;
       isolated + fixable -> re-spawn the implicated GREEN task;
       foundation-poisoning -> HALT chain (abandon its worktrees) -> escalate.
 11. Wave boundary: net-progress check (step §7). Then next wave, or if last wave:
 12. Dispatch `final-gate`: full matrix (from standards.md) + ZERO suppressions +
     every `plan-ready.md` ticked. Stop -> `gate-final`:
       pass    -> merge to main / sprint DONE;
       fixable -> re-dispatch the failing GREEN task;
       scope   -> escalate.
 13. Any escalation -> surfaces at the NEXT planning session at the planner, with
     `execution.log` + `attempt-*/` history as context. Human amends, re-runs.

================================================================================
## 9. Install & invocation boundary

- INSTALL once: `/plugin install`. PATH prereqs: `md-db`, optional `ctx-symbols`.
- PLANNING is a normal interactive session/command — the human is in the loop, the
  loops (`intake clarify`, `standards conflict`, `G2 RE-PLAN`) are human-bounded.
- EXECUTION is triggered as an autonomous run (workflow keyword / a `/goal`-style
  "run until the sprint gate is green") and takes NO mid-run human input — by design;
  faults are adjudicated by hooks, and what hooks can't adjudicate escalates to the
  next planning session.

================================================================================
## 10. How the open decisions change the wiring

- [OPEN-1] scaffolder = the supervisor itself (no dispatch; a supervisor routine that
  writes stubs between RED-verify and GREEN-dispatch) OR a dispatched sub-agent with a
  "stub-only" charter. If dispatched, `gate-scaffold-verify` gains a guard: a body
  implemented instead of `panic+TODO` => reject (mirrors the RED "no prod code" rule).
- [OPEN-2] `skills/agentic-agile/SKILL.md` = the CANONICAL playbook (replace) OR a thin
  wrapper that points at the repo's existing playbook doc (wrap). Replace = single
  source; wrap = lower disruption, two files to keep in sync.
- [OPEN-3] `gate-stage2-complete` as a Stop hook on the planning command, OR a
  supervisor self-check step. Hook = deterministic but awkward in interactive
  planning; self-check = natural but model-trust.
- [OPEN-4] (leaning resolved here) per-task `attempt-K/` dirs as in §6 — gives a clean
  per-task budget and reset. Flatter schemes lose the reset property.
- [OPEN-7] add YAML frontmatter to `stories.md`/`plan.md`/etc. so md-db validates them
  structurally, OR keep them frontmatter-free and rely on `gate-plan-shape` (grep).
  `agent-io.kdl` (init/output) works either way.
- [OPEN-8] ship the whole prior binary as the symbol backend, OR extract a minimal
  `ctx-symbols` (recommended). Either way the two gates degrade to grep if it is absent.

================================================================================
## 11. Stub vs real (honest status)

- REAL and runnable: manifest, agent personas, hooks.json wiring, the artifact layout,
  the md-db routing in `gate-validate-artifact`, the suppression/ticked checks in
  `gate-final`, the shim/ledger checks.
- STUB (marked `SUB-AGENT-TODO`): the language-specific bodies of `gate-red-verify`,
  `gate-green-verify`, `gate-scaffold-verify`, `gate-structural-integrity` — the parts
  that run the repo's actual test runner / assert diff scope / call `ctx-symbols`.
  These need the target repo's commands and the [OPEN] decisions settled before they
  enforce anything. Until then the plugin's structure is correct but the gates are
  advisory.

================================================================================
## 12. v0.2 additions (transcripts, retrospective/memory, manifests)

- Manifests: `plugin/.claude-plugin/plugin.json` (install entrypoint) + root
  `.claude-plugin/marketplace.json` (catalog, source `./plugin`).
- Hooks: `UserPromptSubmit * -> transcripts prompt`; `SubagentStart * -> transcripts stage-in`;
  `PostToolUse * -> transcripts record` (full payload); `Stop * -> transcripts snapshot`;
  `SubagentStop * -> transcripts stop` (snapshot + stage-out). `final-gate` runs on
  `SubagentStop`/`final-gate`.
- `bin/transcripts` (file-based) + per-worktree `.agentic/task.env` contract; `_gatelib.sh`
  sources it so the gates get TASK_ID / SCOPE_GLOBS / SCAFFOLD_SYMBOLS / BASE_REF for
  real (closes the env-injection gap). `.agentic/`, `.transcripts/` are git-ignored.
- Retrospective activity (`pipeline/planning/retrospective/`) + `archivist` agent +
  `schemas/memory.kdl`; `memory.md` -> `init.md # Memory` (role-scoped).
- Scaffold gate fix: symbols from `.agentic/scaffold-symbols`; shims keyed on
  `// agentic:shim`.
