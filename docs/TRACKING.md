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
