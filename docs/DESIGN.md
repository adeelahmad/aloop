# agentic-agile Plugin — Consolidated Design

> **Historical.** Part of the pre-v0.2 design package; counts, paths and status here are out of date. For the current picture read [OVERVIEW.md](OVERVIEW.md).


Status: DESIGN ONLY. No code yet. Every section traces to a decision made in
conversation; open questions are marked [OPEN] and not guessed.

────────────────────────────────────────────────────────────────────────────
## 0. What this is

A Claude Code **plugin** that packages the existing two-stage agile planning +
two-phase TDD execution playbook into one installable unit, and adds the
deterministic enforcement (hooks) and the post-RED scaffolding pass that the
playbook today relies on the model to do by hand.

It does NOT invent new planning artifacts. It wraps the artifacts the playbook
already defines:
  stories.md, sprintN/plan.md            (Stage-1 contract)
  sN-NN-<slug>/{tasks.md,validate.md,plan.md}  (Stage-2 per story)
  plan-ready.md                          (RED→GREEN handoff)
  execution.log                          (append-only state ledger)

init.md / output.md ARE in the design — they carry the per-task-attempt REVISION
loop (feedback when a task isn't met), which NO existing artifact covers:
  - plan-ready.md  : static, once-written task spec — not rewritten per attempt
  - execution.log  : sprint-level one-line index — records THAT it failed, not WHY
  - init.md        : per-task-attempt context; append-only, turn-counted; written
                     ONLY by post-planner (scaffold actions) + supervisor (feedback);
                     READ-ONLY to the sub-agent; POINTS at plan-ready.md, never copies
  - output.md      : the sub-agent's OWN report (per-test outcome, diff, notes);
                     supervisor reads it to verify + compose the next feedback entry
See §9 for the loop. These do NOT duplicate plan-ready.md / execution.log — different
scope (per-task-attempt vs per-story-static vs per-sprint-index).

────────────────────────────────────────────────────────────────────────────
## 1. The two boundaries that define the whole design

HUMAN boundary:   humans interact ONLY during planning. Execution is autonomous.
PHASE boundary:   PLANNING (interactive, human-gated) → EXECUTION (autonomous,
                  hook-enforced). RED → [scaffold] → GREEN → GATE.

Everything below hangs off these two lines.

────────────────────────────────────────────────────────────────────────────
## 2. Lifecycle (the spine)

PLANNING  (interactive, human in the loop)
  Step 1  stories.md          (sprint contract)
  Step 5  sprintN/plan.md      (waves, dep graph, parallelism)   ── Stage 1 ──
  Step 2  tasks.md   (per story)
  Step 3  validate.md (per story)
  Step 4  plan.md    (per story, tests-only)                     ── Stage 2 ──
  ── GATE: planning→execution handoff refuses unless all Stage-2
     artifacts exist & validate for every story (the missing-plan.md fix) ──

EXECUTION (autonomous, per wave from sprintN/plan.md)
  RED    one sub-agent per task: write tests against mod-common shims; all fail
         supervisor verifies RED, writes plan-ready.md
  SCAFFOLD  ← post-planner fires HERE (after RED verified, before GREEN)
         reads verified tests + plan-ready.md; for every production symbol the
         tests reference, create canonical stub ONCE with panic("SUB-AGENT-TODO: …");
         delete now-redundant mod-common shims; log each action to execution.log
  GREEN  one sub-agent per task: fill SUB-AGENT-TODO bodies in the canonical
         stubs; no new structure possible (it already exists, singular)
  GATE   final-gate agent: full matrix, zero skips/suppressions, every
         plan-ready.md fully ticked

────────────────────────────────────────────────────────────────────────────
## 3. Where the platform primitives map (nothing custom that already exists)

Hooks (deterministic, fire regardless of model choice):
  - planning→execution gate     : a gate run at handoff — validates Stage-2 complete
  - RED verification            : SubagentStop(red sub-agent) — every new test FAILs,
                                  no regression, diff touches only test files
  - SCAFFOLD trigger + guard    : fires post-RED; SubagentStop(post-planner) guard =
                                  "stubbed only, no bodies implemented" else reject
  - GREEN verification          : SubagentStop(green sub-agent) — task tests pass,
                                  no regression, diff in tasks.md scope only, gates green
  - structural-integrity        : PostToolUse / gate — no orphan module, no parallel
                                  implementation, no duplicate helper (compiler-invisible)
  - bounded-cost                 : PreToolUse(Bash) — long-running/expensive command
                                  on the wrong target is blocked; smallest-safe-target
Worktree isolation              : platform isolation:"worktree" per sub-agent (built in)
Subagent roles                  : agents/ — supervisor-orchestrated worker types
Workflow / orchestration        : skills/ — the playbook as the orchestration script
Ledger                          : execution.log (append-only, the playbook's own)

────────────────────────────────────────────────────────────────────────────
## 4. Supervisor judgment on gate failure (decided: "let supervisor choose")

When an execution gate fails and can't auto-resolve, the supervisor classifies
against the dependency graph and chooses, then LOGS the choice to execution.log:

  foundation-poisoning (failure sits on a dependency other pending work needs)
        → HALT that dependency chain; leave completed work intact
  isolated (failure confined to its own branch)
        → CONTINUE other work; COLLECT the escalation

All halts + collected escalations surface at the NEXT PLANNING SESSION (where the
human is). Supervisor never asks a human mid-execution — there isn't one. The
choice is auditable + replayable from execution.log.

────────────────────────────────────────────────────────────────────────────
## 5. Artifact ownership (from the playbook, unchanged)

Supervisor owns ALL planning artifacts. Sub-agents NEVER edit stories.md,
tasks.md, validate.md, plan.md, plan-ready.md, sprintN/plan.md.
  - RED sub-agent writes: test files + mod-common shims only
  - post-planner writes : production STUBS only (panic+TODO), deletes shims, logs
  - GREEN sub-agent writes: production bodies (fills TODOs) + removes shim leftovers
  - supervisor writes   : every planning artifact, plan-ready.md, execution.log

────────────────────────────────────────────────────────────────────────────
## 6. Proposed plugin layout

  agentic-agile/
  ├── .claude-plugin/plugin.json        # manifest
  ├── skills/
  │   └── agentic-agile/SKILL.md         # the playbook router + orchestration
  ├── agents/                            # worker roles
  │   ├── red-worker.md  green-worker.md
  │   ├── post-planner.md                # NEW: post-RED scaffold pass
  │   └── final-gate.md
  ├── hooks/hooks.json                   # the deterministic gates (§3)
  ├── bin/                               # scripts the hooks call
  │   ├── gate-stage2-complete           # planning→execution handoff gate
  │   ├── gate-red-verify
  │   ├── gate-green-verify
  │   ├── gate-structural-integrity      # orphan/dup/parallel detection
  │   ├── gate-bounded-cost
  │   └── log-execution                   # append a line to execution.log
  └── settings.json                      # worktree isolation defaults, etc.

  (rules additions fold into skills/ as guidance, not separate files)

────────────────────────────────────────────────────────────────────────────
## 7. Honest constraints (from the docs, not to be designed around)

- Dynamic workflows take NO mid-run user input — fine, humans only plan.
- Plugin-provided subagents IGNORE hooks/mcpServers/permissionMode frontmatter
  → per-agent gates must live in top-level hooks.json matched by agent_type,
    not in the agent .md files.
- PostToolUse hooks cannot undo an action (tool already ran) — so destructive
  prevention must be PreToolUse, not PostToolUse.
- This is a DESIGN. None of it is verified against a live runtime yet; the
  bin/ gates are offline-testable, the hook wiring needs a real CC session.

────────────────────────────────────────────────────────────────────────────
## 8. [OPEN] — not decided, do NOT guess

[OPEN-1] post-planner = the supervisor itself, or a dispatched sub-agent with a
         "stub-only" charter? (§5 works either way; changes who the SubagentStop
         guard targets.)
[OPEN-2] replace vs wrap: does the plugin's skills/ become the CANONICAL copy of
         the playbook (replace), or call out to the existing doc (wrap)?
[OPEN-3] does the planning→execution gate (Stage-2-complete) run as a hook, or as
         a supervisor self-check step? (hook = deterministic but planning is
         interactive, so a UserPromptSubmit/Stop-style gate is awkward.)
[OPEN-4] init.md / output.md path scheme — per-attempt dir
         (sN-NN-<slug>/<task>/attempt-K/) or flatter? not decided.
[OPEN-5] output.md — one file appended per attempt, or a fresh file per attempt?
[OPEN-6] max revision attempts before the supervisor escalates to next planning —
         what default? (connects the §9 inner loop to the §4 escalation)
[OPEN-7] add YAML frontmatter to stories.md/plan.md/etc. so md-db validates them
         structurally, or keep them frontmatter-free + grep-gate only?
         (see schemas/SCHEMA-NOTES.md; agent-io.kdl works either way)
[OPEN-8] symbol-resolution backend packaging (see §10): ship the whole prior
         binary as an optional dependency, or extract a minimal `ctx-symbols`
         helper (locate_symbol/resolve_symbol only)? Recommendation: extract minimal.

────────────────────────────────────────────────────────────────────────────
## 9. The revision loop (init.md / output.md) — fills the "reject and re-spawn" gap

The playbook rejects a bad RED/GREEN attempt and "re-spawns" but defines NO
feedback payload — a blind re-spawn repeats the mistake. init.md/output.md close it.

Per task attempt (RED or GREEN), fully autonomous, supervisor <-> sub-agent, NO human:

  1. init.md  (supervisor/post-planner authored; append-only; turn-counted;
               read-only to sub-agent) contains:
                 - pointer to this task's plan-ready.md section (NOT a copy)
                 - scaffold actions from the post-planner (one line per create/update/delete)
                 - on re-spawn: a feedback entry (what was wrong, what to fix, what
                   NOT to touch) derived from the prior output.md
  2. sub-agent reads init.md, does the work, writes output.md (per-test outcome,
     git diff --stat, notes)
  3. supervisor reads output.md, verifies vs validate.md + plan-ready.md
  4. PASS -> tick plan-ready.md, log to execution.log, next task
  5. FAIL -> append a feedback entry to init.md (turn K+1), re-spawn -> back to (2)

Bound + escalation: after a configured MAX attempts [OPEN-6], the supervisor stops
looping and applies the §4 judgment — classify (foundation-poisoning -> HALT chain;
isolated -> CONTINUE + collect) and surface at the NEXT planning session. The inner
revision loop and the outer to-planning escalation are the same mechanism at two
scales: escalation is what happens when revision is exhausted.

Scope distinction (so nothing duplicates):
  init.md       per-task-attempt   context + feedback   (post-planner+supervisor write; sub-agent reads)
  output.md     per-task-attempt   sub-agent's report   (sub-agent writes; supervisor reads)
  plan-ready.md per-story          static task spec     (supervisor writes once)
  execution.log per-sprint         one-line index       (supervisor appends)

────────────────────────────────────────────────────────────────────────────
## 10. Symbol-resolution backend (harvested from a prior build)

DECIDED: harvest ONE subsystem of an earlier (pre-Claude-Code-workflows) build to
back the two gates md-db + grep cannot do well. Take nothing else.

Why two gates need it (md-db operates on .md artifacts, not source code; grep is
fragile — matches comments, misses overloads, can't see semantic duplication):
  - gate-scaffold-verify     needs "is symbol X defined EXACTLY ONCE in the tree?"
  - gate-structural-integrity needs "two definitions filling one role?" (the check
                             that would have caught the inv duplicate fixity engine)

HARVEST (the code-intelligence layer only):
  - ast.rs      : AstParser::{locate_symbol, search, process_content}  (tree-sitter, 6 langs)
  - resolver.rs : resolve_symbol, resolve_symbol_detailed -> byte ranges
  - conflicts.rs: detect_all (the duplicate/collision CONCEPT; reimplement against
                  our tree, not the prior JSONL plan DSL)

LEAVE OUT (deliberately):
  - orchestrate/ (ProcessSpawner, WaveRunner, SQLite task tracking) — a custom
    pre-workflows wave engine. Claude Code subagents + worktrees + hooks now provide
    this natively; adopting it would re-add the engine the plugin pivot removed.
  - the plan DSL + projection layer (plan/schema, plan/index, plan/iiif,
    plan/annotation) — would replace the playbook's tests-only plan.md (parallel-
    mechanism trap) and pulls in out-of-scope projection machinery.

INTEGRATION (optional, with fallback — so the plugin never hard-fails):
  - gate-scaffold-verify and gate-structural-integrity call the symbol resolver IF
    present on PATH; else fall back to grep with a logged "weaker check" warning.
  - Two complementary, independently-optional backends:
      md-db          validates the .md ARTIFACTS  (init.md/output.md/plan-*)
      symbol-resolver analyzes the SOURCE CODE     (uniqueness/duplication)
    Neither drags in the orchestration engine.

Packaging is [OPEN-8]: ship-whole-binary vs extract a minimal `ctx-symbols` helper.

────────────────────────────────────────────────────────────────────────────
## 11. ICE mapping & the presence→hooks principle (framing; no structural change)

ICE (Intent · Context · Expectations) is a LENS over the artifacts we already have,
not a renaming — the files stay as the playbook names them.

  Intent        what's wanted + constraints + failure scenarios + success scenarios
                + connections. Owned by the HUMAN.
                -> intake.md (problem) + stories.md (the five parts)
  Expectations  the done-ness boundary, in the USER'S terms. Owned by the SAME human
                who owns Intent.
                -> stories.md Definition of Done + Sprint demo  (EXPECTATIONS OF RECORD);
                   validate.md / plan.md are the DERIVED, implementation-level expressions.
  Context       the how — stack, codebase, constraints. Owned by the HARNESS, fed
                PROGRESSIVELY (not dumped).
                -> standards.md -> plan-ready.md -> init.md (per-task slice) + the
                   symbol resolver (§10)

Presence -> hooks: the human owns Expectations at planning, then steps out of
execution. The gates enforce that human-authored done-ness on EVERY attempt — so
"presence in the loop" becomes mechanical, continuous, per-attempt enforcement, not
a single human approval of an oversized diff at the end. That substitution is sound
ONLY because Expectations are machine-checkable (validate.md = literal PASS/FAIL).
Completeness of INTENT remains the irreducible human judgment.

ADOPTED front-end discipline: intake.md and stories.md MUST carry all five parts of
Intent; the planner gate rejects a story missing failure-scenarios or connections.
(see pipeline/planning/00-intake/ and 02-planner/gate.md)

DEFERRED: full Intent->code traceability / impact analysis (the richest "connections"
facet). The five-part list captures connections in prose now; the graph stays out of
the first build.

────────────────────────────────────────────────────────────────────────────
## 12. Corrections from diagram review (applied; supersede §4/§9 where noted)

A structured review of the end-to-end flow surfaced real holes. Applied:

- RENAME: semantic-reviewer -> structural-reviewer (it runs structural-integrity
  checks; the old name oversold a semantic remit it never had).
- REMEDIATION EDGES: structural-reviewer and final-gate now have FIX paths, not just
  halt/escalate. Isolated+fixable structural findings, and fixable final failures
  (red test / unticked box / non-scope matrix fail), re-spawn the implicated GREEN
  task via init.md. ESCALATE is reserved for genuine scope/plan defects.
- ESCALATION (supersedes §4): cause-specific, not one uniform defer —
    foundation-poisoning = HALT the dependency chain, ABANDON its in-flight
      worktrees, KEEP already-merged waves (worktree isolation makes this clean);
    scope or plan defect = human decision;
    budget or no-progress = hard stop.
  Escalation RE-ENTERS at the planner (PLN) with context preserved — NOT a full re-intake.
- RED clarified: RED writes tests against minimal mod-common SHIMS, so each test
  FAILS BY ASSERTION (not by missing-symbol/compile error). The scaffolder swaps
  shims for panic+TODO stubs AFTER RED is verified. RED is not vacuous.
- SCAFFOLDER idempotent: stub only new symbols; never clobber an implemented body.
- STANDARDS bind execution: the gate matrix is the one declared in standards.md, not
  hardcoded — the rules that governed planning also bind execution gates.
- BUDGET scope (refines §9 / OPEN-4): retry budget is PER-TASK, counter resets per
  task (each task's own init.md). No cross-wave accumulation.
- NO-PROGRESS GUARD (adds to §9): a wave that passes its gates but ticks zero new
  plan-ready boxes is a stall -> escalate (catches cross-wave livelock).
- TICK invariant: plan-ready boxes are ticked ONLY on pass, so a re-spawn (on fail)
  always precedes ticking for that task; no partial-tick race.
- SUPERVISOR is the explicit orchestrator across both phases (dispatch + gate
  verdicts + plan-ready + execution.log); the human is present only in planning.

Held (intended, not changed): human absent from execution (presence -> hooks); plans
remain human-owned (plan defects escalate, not edited mid-run); planning loops are
human-bounded (no machine budget needed).

────────────────────────────────────────────────────────────────────────────
## 13. Retrospective + memory (every planning session)

Every planning session opens with a retrospective (the `archivist`, read-only). It
reads the global transcripts + the failure/feedback trail since the last session and
distills RECURRING patterns (>=2) — failures and reliably-good moves — into terse,
role-scoped one/two-line entries. The human is present and adds their own insights
(continuous-failure guidance). Kept entries land in `docs/agents/memory.md`
(schema: schemas/memory.kdl) and are injected, role-scoped, into every sub-agent's
`init.md` `# Memory` section. Guardrails: memories are advisory and NEVER relax an
invariant (no-suppression, human-only-planning, scaffolder-leaves-panic, the gates);
patterns not one-offs; bounded + deduped.

## 14. Transcripts (task-scoped, file-based)

A global append-only `global.jsonl`, plus per-task `events.jsonl` (the full hook
payload per tool call + every user prompt) and `transcript.jsonl` (the complete session
snapshot — every message and thinking block — copied from the session's `transcript_path`
on stop). No FUSE. At `SubagentStart` the supervisor's `bin/transcripts stage-in` copies
the task's slice into the worktree as READ-ONLY (`.transcripts/`); `UserPromptSubmit`
records prompts; `PostToolUse *` records the full tool payload (`transcripts record`);
`Stop`/`SubagentStop *` snapshot the full transcript and remove the slice (`transcripts
stop`). The canonical store lives outside the tracked tree (`.agentic/transcripts`,
git-ignored) so it never rides a code merge; only that task's transcripts are folded into
its home. Everything is kept (no auto-compaction; `prune` is manual). Real
isolation = the global file is never staged into the worktree; the read-only chmod is
the softer "not yours to write" signal. The gates read their per-task contract from
`.agentic/task.env`. Transcripts subsumes the verdict/decision/worktree-event gaps and
feeds the retrospective.
