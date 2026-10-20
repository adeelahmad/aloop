# Roadmap tracking

Status of every requirement in [ROADMAP.md](ROADMAP.md), in the four states §8 asks for.
`IMPLEMENTED_TESTED` rows name the test that proves them; `scripts/check-tracking.py`
(run by `make test-hosts`) fails if that test file is missing or not run by the Makefile.

| § | Requirement | Status | Evidence |
|---|---|---|---|
| 3.1 | Soft token limit is telemetry only | IMPLEMENTED_TESTED | `scripts/test/budget-soft-telemetry.test.py` |
| 3.2 | Token meter keeps classes apart (fresh default, context legacy) | IMPLEMENTED_TESTED | `scripts/test/budget-meter.test.py` |
| 3.3 | Every model role metered against a role profile (non-workers observe-only) | IMPLEMENTED_TESTED | `scripts/test/budget-roles.test.py` |
| 3.4 | Hard breaches classified with evidence before acting | IMPLEMENTED_TESTED | `scripts/test/budget-classify.test.py` |
| 3.5 | No post-kill narration; harness writes the re-plan block | IMPLEMENTED_TESTED | `scripts/test/budget-hard-stop.test.py` |
| 3.6 | Abstract model routes | IMPLEMENTED_TESTED | `scripts/test/model-routes.test.py` |
| 3.6 | The dispatch model is the project's resolved route, enforced by `gate-dispatch` | IMPLEMENTED_TESTED | `scripts/test/dispatch.test.py` |
| 4.1 | Intent Contract with harness-issued ids | IMPLEMENTED_TESTED | `scripts/test/intent-contract.test.py` |
| 4.1 | Clarification policy (assume / default / ask / spike) | IMPLEMENTED_TESTED | `scripts/test/intent-contract.test.py` |
| 4.1 | Explicit autonomous mode | IMPLEMENTED_TESTED | `scripts/test/intent-contract.test.py` |
| 4.2 | Sprint token envelope, admission control, rollover | IMPLEMENTED_TESTED | `scripts/test/sprint-admission.test.py` |
| 4.2 | Time budget `auto` mode | IMPLEMENTED_TESTED | `scripts/test/timebox-auto.test.py` |
| 4.2 | Task contract written by the harness (`budget contract`: keys, BASE_REF, resolved budget) | IMPLEMENTED_TESTED | `scripts/test/budget-contract.test.py` |
| 4.2 | Interruptible orchestrator loop (inspect step, token-free wait) | IMPLEMENTED_TESTED | `scripts/test/orchestrator-sweep.test.py` |
| 4.3 | Failure envelope, grace rule, calibration verdict | IMPLEMENTED_TESTED | `scripts/test/planner-feedback.test.py` |
| 4.3 | Lesson candidates promoted only by the recurrence rule | IMPLEMENTED_TESTED | `scripts/test/lesson-candidates.test.py` |
| 4.4 | Canonical sprint handoff + resume rule | IMPLEMENTED_TESTED | `scripts/test/handoff-resume.test.py` |
| 4.4 | Compaction detection / rotation rule | IMPLEMENTED_TESTED | `scripts/test/context-boundary.test.py` |
| 4.5 | Canonical ids, referential check, frozen map | IMPLEMENTED_TESTED | `scripts/test/ids-integrity.test.py` |
| 4.6 | Structured change targets, shadow workspace, `target` / `context` | IMPLEMENTED_TESTED | `scripts/test/semantic-targets.test.py` |
| 4.6 | LSP / structural adapter (refs, callers, callees) | IMPLEMENTED_TESTED | `scripts/test/lsp-adapter.test.py` |
| 4.6 | Semantic plan as the execution source: GREEN diff scope derived from targets | IMPLEMENTED_TESTED | `scripts/test/targets-scope.test.py` |
| 4.6 | Semantic plan as the execution source: tasks.md / validate.md / plan.md rendered from it (`targets render`) | IMPLEMENTED_TESTED | `scripts/test/plan-render.test.py` |
| 4.7 | Event stream `aladdin.event/1` | IMPLEMENTED_TESTED | `scripts/test/events.test.py` |
| 4.7 | Decision rationale + authority record | IMPLEMENTED_TESTED | `scripts/test/events.test.py` |
| 4.7 | Lineage: commit → intent → approver | IMPLEMENTED_TESTED | `scripts/test/commit-trace.test.py` |
| 4.7 | Every commit on main is a task's: the supervisor's own `git commit` on main is blocked mid-sprint | IMPLEMENTED_TESTED | `scripts/test/supervisor-scope.test.py` |
| 6.9 | Stuck release covers the planner; its stop verdicts are logged like every gate | IMPLEMENTED_TESTED | `scripts/test/planner-stuck.test.py` |
| 4.7 | Commit messages from rationale; graph export | IMPLEMENTED_TESTED | `scripts/test/commit-trace.test.py` |
| 4.8 | Cross-project impact provider seam | IMPLEMENTED_TESTED | `scripts/test/impact-provider.test.py` |
| 4.9 | Usage reporting: classes apart, exact or marked, unreported classes named | IMPLEMENTED_TESTED | `scripts/test/budget-meter.test.py` |
| 4.9 | Host capability matrix | IMPLEMENTED_TESTED | `scripts/test/context-boundary.test.py` |
| 4.9 | HostAdapter interface extraction, gate equivalence (outer enforcement) | IMPLEMENTED_TESTED | `scripts/test/host-adapter.test.py` |
| 4.9 | Portable `.aladdin/config.yaml` with migration | IMPLEMENTED_TESTED | `scripts/test/portable-config.test.py` |
| 4.9 | Host swap: resume a sprint on another host from portable state (`host swap`) | IMPLEMENTED_TESTED | `scripts/test/host-swap.test.py` |
| 4.10 | Metrics table (percentiles, breaches, waste, rates, cache, context peak) | IMPLEMENTED_TESTED | `scripts/test/metrics.test.py` |
| 4.10 | Commits, tests added, time soft limits, semantic context served | IMPLEMENTED_TESTED | `scripts/test/metrics.test.py` |
| 4.10 | Rotations / compactions | IMPLEMENTED_TESTED | `scripts/test/metrics.test.py` |
| 4.10 | Context-window share (peak / `AGENTIC_CONTEXT_WINDOW`) | IMPLEMENTED_TESTED | `scripts/test/metrics.test.py` |
| 4.11 | Entry points: `aladdin` CLI (intent, run, status, resume, report, semantic queries) and `/aladdin` | IMPLEMENTED_TESTED | `scripts/test/aladdin-cli.test.py` |
| 4.11 | Compatibility aliases (`agentic`, `/aloop`, `.agentic/` alongside `.aladdin/`) | IMPLEMENTED_TESTED | `scripts/test/rename-compat.test.py` |
| M1 | Run data in `.aladdin/` (guarded by its `.gitignore`), `.agentic/` read as legacy, `aloop layout show\|check\|migrate`, `ALADDIN_*` aliases, contract and worktree markers found in either dir | IMPLEMENTED_TESTED | `scripts/test/layout.test.py` |
| 4.11 | README reordered product-first (intent → demo → TDD agents → loop → gates → install → hosts → evidence → audit → architecture → limitations) | IMPLEMENTED_TESTED | `scripts/test/readme.test.py` (section order, named tools exist, install names, links, demo figures sourced from §0) |
| 4.11 | Site (`docs/index.html`, GitHub Pages from `/docs`) with the lamp motif, RED explainer and audit tree; numbers sourced from §0 | IMPLEMENTED_TESTED | `scripts/test/site.test.py` |
| 4.11 | Natural-language auto-trigger inside hosts (`intent-trigger` on UserPromptSubmit; Claude Code, Codex, OpenCode) | IMPLEMENTED_TESTED | `scripts/test/intent-trigger.test.py` |
| 4.11 | Evidence: every public claim links to a reproducible measured run (`docs/evidence/README.md`, `run-summary`) | IMPLEMENTED_TESTED | `scripts/test/evidence.test.py` |
| 4.10 | The supervisor's playbook draws the enforced layout (sprint under `docs/agents/`) and stays within a byte budget | IMPLEMENTED_TESTED | `scripts/test/skill-layout.test.py` |
| 7 | The final-gate persona's suppression check is the gate's (`assert_no_suppression`), never its own list | IMPLEMENTED_TESTED | `scripts/test/suppression-contract.test.py` |
| 4.11 | The north-star run reproducible on every host (`e2e-live.sh --host codex|opencode`: host install + headless CLI, same checks) | IMPLEMENTED_TESTED | `scripts/test/e2e-live-hosts.test.py` (measured Codex/OpenCode rows still pending: no CLIs here) |
| M0.6 | Gates behave the same on stock macOS bash 3.2 (no bash-4+ construct; the standards.md matrix is the one that runs) | IMPLEMENTED_TESTED | `scripts/test/bash-portable.test.py` |
| 6.3 | RED fails by assertion on any language: a non-cargo repo runs the standards.md matrix's test command (pytest exit codes, `go test` build failures) | IMPLEMENTED_TESTED | `scripts/test/any-language.test.py` |
| 7 | The sprint and every STORY_DIR live in `docs/agents/sprintN/`, never in the git-ignored run-data dir (`gate-stage2-complete`, `gate-dispatch`) | IMPLEMENTED_TESTED | `scripts/test/planner-stuck.test.py`, `scripts/test/dispatch.test.py` |
| 4.11 | The supervisor's playbook keeps on-demand sections in `skills/aloop/reference.md`, installed beside it on OpenCode | IMPLEMENTED_TESTED | `scripts/test/aladdin-cli.test.py` |
| M9.0 | Mission layer core: sealed mission file, approval events for exact content, harness ids, schema subset, snapshot, area discovery | IMPLEMENTED_TESTED | `scripts/test/mission-core.test.py` |
| M9.1 | Research: every claim has a source ref; sources archived with a matching sha256 | NOT_IMPLEMENTED | §4.12 |
| M9.2 | Baseline: re-runnable, measured twice within tolerance, frozen and versioned on human confirmation | IMPLEMENTED_TESTED | `scripts/test/mission-baseline.test.py` |
| M9.3 | Journeys and metrics: events per step, query definitions, no removal or weakening without approval | IMPLEMENTED_TESTED | `scripts/test/mission-journeys.test.py` |
| M9.4 | Ethics: three tiers; gray consent bound to the goal; hard line never unlocked | NOT_IMPLEMENTED | §4.12 |
| M9.5 | LLM judges: pinned model, hashed rubric, several runs, agreement threshold | NOT_IMPLEMENTED | §4.12 |
| M9.6 | Telemetry (GA4): opt-in, Admin API setup, debug hit, fixed reports, minimum sample; never a gate | NOT_IMPLEMENTED | §4.12 |
| M9.7 | Standing principles in two variants (autonomous, spoon-fed) | NOT_IMPLEMENTED | §4.12 |
| M9.8 | Mission loop: `tick` from facts, zero-token `due`, cron, budget caps | IMPLEMENTED_TESTED | `scripts/test/mission-tick.test.py` |
| M9.9 | Six fixed mission roles, wired; role path guards; artifact check at stop | NOT_IMPLEMENTED | §4.12 |
| M9.10 | State Assessor report and Evaluator gap list | IMPLEMENTED_TESTED | `scripts/test/mission-evaluate.test.py` |
| M9.11 | Goals become Aladdin intents; lineage reaches the mission | NOT_IMPLEMENTED | §4.12 |
| M9.12 | `/aloop:mission` skill | IMPLEMENTED_TESTED | `scripts/test/mission-skill.test.py` |
| M9.13 | Mission end to end (offline in CI, one live cycle measured) | NOT_IMPLEMENTED | §4.12 |
| M9.17 | Approvals name the captured human prompt | NOT_IMPLEMENTED | §4.12 |
| M9.19 | Mission spend counts mission sessions only | NOT_IMPLEMENTED | §4.12 |
| M9.20 | Mission price table kept current | NOT_IMPLEMENTED | §4.12 |
| M9.21 | Mission USD meter prices fast mode and server tools | NOT_IMPLEMENTED | §4.12 |
