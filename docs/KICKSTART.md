# KICKSTART — instruction for an AI agent

> **Historical.** Part of the pre-v0.2 design package; counts, paths and status here are out of date. For the current picture read [OVERVIEW.md](OVERVIEW.md).


You are picking up a DESIGN package and turning it into a working Claude Code
plugin. This file tells you what to read, what to decide, and the exact order to
build in. Do not improvise past the design; where something is undecided it is
marked OPEN with a recommended default.

================================================================================
## 0. Goal

Build the `agentic-agile` plugin to the design in this package: two-stage planning
(human-in-the-loop) + two-phase TDD execution (autonomous, hook-enforced), with
md-db/KDL artifact validation and a symbol-resolver backend for two gates.

================================================================================
## 1. Read in this order (do not skip)

1. KICKSTART.md            (this file)
2. ARCHITECTURE.md         HOW it is wired: primitive mapping, hooks.json table,
                           md-db/ctx-symbols invocation, on-disk data plane,
                           worktree lifecycle, end-to-end run trace.
3. DESIGN.md               the WHAT/WHY: §0–§10 design, §11 open questions,
                           §12 the corrections from the structural review.
4. reference/existing-skill/SKILL.md   the REAL planning playbook (authoritative
                           process; the plugin's skill is built from this).
5. PLATFORM-NOTES.md       the Claude Code mechanics this relies on + doc pointers.

Then skim:
- agentic-agile-design.html   illustrated overview (all four diagrams)
- reference/ctxconfig-source/ the prior project — HARVEST TARGET ONLY (see §4)

================================================================================
## 2. What already exists in plugin/

- `.claude-plugin/plugin.json`     manifest (skills/agents/hooks registered)
- `agents/<role>.md`               8 personas, assembled from `pipeline/*/persona.md`
- `hooks/hooks.json`               event -> matcher -> gate wiring (real)
- `bin/gate-*`, `bin/log-execution` gate bodies — REAL where structural, STUB
                                   (SUB-AGENT-TODO) where language/repo-specific
- `schemas/*.kdl`                  md-db schemas (agent-io / planning-artifacts / ledger)
- `pipeline/`                      design source of truth (4 files per activity)
- `skills/agentic-agile/SKILL.md`  PLACEHOLDER — you populate this (OPEN-2)

================================================================================
## 3. Decide first (these block the build). Recommended defaults in brackets.

- OPEN-2  skill = canonical playbook (replace) or wrapper (point at repo doc).
          [replace: make `skills/agentic-agile/SKILL.md` the playbook, built from
           reference/existing-skill/SKILL.md + the §12 corrections.]
- OPEN-1  scaffolder = supervisor routine or dispatched stub-only sub-agent.
          [dispatched sub-agent; add the gate guard "a body implemented instead of
           panic+TODO => reject".]
- OPEN-3  Stage-2 gate as a Stop hook or a supervisor self-check step.
          [supervisor self-check calling bin/gate-stage2-complete; keep the
           PostToolUse md-db hook either way.]
- OPEN-4  init.md/output.md path scheme.
          [per-task `T<k>/attempt-K/` dirs — see ARCHITECTURE §6; gives per-task
           budget + reset.]
- OPEN-7  frontmatter on planning artifacts (enables md-db) vs grep-only.
          [add minimal `--- type: / sprint: ---` frontmatter so md-db validates
           stories/plan/etc.; keep gate-plan-shape for the dynamic Tn bodies.]
- OPEN-8  symbol-resolver packaging.
          [extract a minimal `ctx-symbols` from ctxconfig — see §4.]

If the human has not overridden these, proceed on the defaults and STATE that you
are doing so.

================================================================================
## 4. Harvest rules for reference/ctxconfig-source (STRICT)

HARVEST (build a small standalone `ctx-symbols` CLI from these):
- `src/ast.rs`            AstParser::{locate_symbol, search, process_content}
- `src/plan/resolver.rs`  resolve_symbol / resolve_symbol_detailed -> byte ranges
- `src/search.rs`         supporting search
- `src/plan/conflicts.rs` the duplicate/collision CONCEPT (reimplement against the tree)
Target CLI: `ctx-symbols count <symbol> --tree .` (==1 check) and
`ctx-symbols locate/search ...` (orphan/parallel/duplicate). Put it on PATH.

DO NOT pull into the plugin (these are why this is a plugin, not that binary):
- `src/workflow/`   (executor, dispatch, lifecycle, generator, checkpoint) — the
                    pre-Claude-Code orchestration engine; the platform replaces it.
- `src/store/`      (SQLite db, sessions, task seeding) — not needed.
- `src/context.rs`  token-budget context assembly — out of scope.
- `src/plan/` DSL + snapshot/migrate, IIIF/annotation, `src/bin/lsp.rs`,
  `cli_orchestrate.rs`, the TUI — out of scope (would replace plan.md / the playbook).

================================================================================
## 5. Ordered build tasks

1. POPULATE the skill. Per OPEN-2, write `plugin/skills/agentic-agile/SKILL.md`
   from `reference/existing-skill/SKILL.md`, folding in the §12 corrections
   (structural-reviewer rename, post-RED scaffold step, cause-split escalation,
   idempotent scaffold, standards-bind-gates, no-progress guard, per-task budget,
   RED-fails-by-assertion). The skill must instruct the supervisor in the
   ARCHITECTURE §8 run trace and the artifact layout in §6.
2. EXTRACT `ctx-symbols` (OPEN-8 / §4). Build, test against a sample repo, install.
3. FILL the four stub gate bodies, using the target repo's real commands:
   - gate-red-verify       : run the test runner; assert every NEW test FAILs BY
                             ASSERTION and none regressed; diff touches only test+shim;
                             validate output.md via agent-io.kdl.
   - gate-scaffold-verify  : ctx-symbols count==1 per referenced symbol; every new
                             body is panic+TODO; no shim remains; no clobber.
   - gate-green-verify     : task tests pass; no regression; diff in tasks.md scope;
                             run the fmt/lint/test/coverage matrix DECLARED IN
                             standards.md; zero suppressions; validate output.md.
   - gate-structural-integrity : ctx-symbols for orphan/parallel/duplicate; exit 2
                             only on foundation-poisoning; emit findings.
4. FINALIZE hooks.json per OPEN-3.
5. IMPLEMENT the data-plane writers in the skill: per-task `attempt-K/init.md`
   (append-only, turn-counted), `output.md`, `plan-ready.md` (tick on pass only),
   `execution.log` via `bin/log-execution`. Worktree per dispatch; merge on pass;
   abandon chain worktrees on HALT.
6. VERIFY end to end: `md-db validate` a sample init.md/output.md against
   agent-io.kdl; dry-run a one-story sprint; confirm RED fails by assertion,
   scaffold is idempotent, green merges, structural review + final gate behave, and
   an injected failure escalates to the planner (not a full re-intake).

================================================================================
## 6. Guardrails (hard — do not violate)

- Human interacts ONLY during planning. Execution takes no mid-run human input.
- The supervisor owns ALL planning artifacts; sub-agents NEVER edit
  stories/tasks/validate/plan/plan-ready/sprint-plan.
- The scaffolder leaves `panic("SUB-AGENT-TODO: ...")`; it NEVER implements a body.
- No test suppression, ever (`#[ignore]`, `.skip`, etc.). RED is cleared by GREEN.
- Gates live in top-level hooks.json matched by agent_type (plugin sub-agents
  ignore their own hook frontmatter).
- A gate that cannot run its backend (md-db / ctx-symbols absent) WARNS and falls
  back; it never silently passes a real check.

================================================================================
## 7. Kick-start prompt (give this to the AI)

"Read KICKSTART.md, then ARCHITECTURE.md and DESIGN.md in this package. Resolve
OPEN-1/2/3/4/7/8 using the recommended defaults unless I override. Then: populate
skills/agentic-agile/SKILL.md from reference/existing-skill/SKILL.md plus the §12
corrections; extract a minimal ctx-symbols from reference/ctxconfig-source per the
harvest rules; and fill the four stub gate bodies for <TARGET REPO / LANGUAGE>.
Show me a build plan and the resolved decisions BEFORE writing code."
