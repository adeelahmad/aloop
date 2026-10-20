# Roadmap — aloop → Aladdin

*Internal working document. Revision 0.2 of the Aladdin spec, reduced to what is **not built yet**.*

![Aladdin's Loop (aloop)](diagrams/aladdins-loop.webp)

---

## 0. At a glance

What the project is today: [OVERVIEW.md](OVERVIEW.md). Sections 1–8 below are the full
spec; this table is the plan. Versions are targets, not promises. M1–M7 and the M8 items marked
done shipped together as **v0.12.0** (2026-10-18); the per-milestone targets below are the
original plan.

| Milestone | Target | Contents | Status |
|---|---|---|---|
| **M0 — Hygiene** | v0.10.x | Mark `DESIGN` / `ARCHITECTURE` / `KICKSTART` historical; fix stale counts (8 → 9 roles) and the README CI section; add the `CONTRIBUTING.md` the README badge links to; one live-session smoke test of the hook wiring (open since `DEVLOG` v0.1) | done: live smoke test `make smoke-live YES=1` (6 checks, ~$0.004 on haiku); its first run found the Linux fail-open in `gate-supervisor-scope` (fixed, regression test added) |
| **M0.8 — North-star run** | v0.11.x | `make e2e-live YES=1`: a one-line intent (semver parser) goes through retrospective, intake, standards, planner, RED/SCAFFOLD/GREEN per task, structural review and the final gate to merged, tested code in a real Claude Code session, with the approval given up front. Reason: nothing had ever run the whole loop live; the first run found that no role gate fired on plugin installs | run 1: done end to end, 19 min, $1.15 (sonnet supervisor, haiku workers), 22 tests on main, 9/11 checks; found and fixed the `aloop:<role>` matcher bug, a selfcheck crash and copy-merging. Run 2: **11/11**, 12m46s, $0.92, host gates on every role, worktree branches merged with `--no-ff`; found gates looping on the supervisor's contract slips (fixed). Run 3: 11/11, 12m00s, **$0.79**; found GREEN looping on RED's unformatted tests (fixed: RED/SCAFFOLD run the format check) and selfcheck missing the contract in Claude Code worktrees (fixed: the contract is copied in). Run 4 (all fixes): 11/11, 17m56s, $1.10, 12 gate blocks and no loops (28 → 17 → 12). Cost varies $0.79–1.15 per run; next: cut it (scaffold symbol parsing slips such as `(enum,`, supervisor turns), then a second intent beyond the semver fixture. Run 5: 11/11 but 29 min, $1.83, 44 blocks (the planner made 5 tasks; 27 false isolation blocks after workers `cd`'d to the main tree, fixed; subagents also read the plugin's own source, which costs tokens). Run 6: 11/11, **11m10s, $0.65**, plugin-source reads 30 → 2 after the agent prompts said not to; found a dispatch-before-contract loop (25 blocks, fixed by `gate-dispatch`) and annotated scaffold-symbol lines (fixed). Run 7 (after #9): 11/11, 13m46s, $0.98, 20 blocks, no dispatch loop; found RED blocked for listing an earlier task's passing tests, scaffold-symbols tables with a header row and trait-impl lines, and a guessed memory.md frontmatter (all fixed; `aloop init` now seeds memory.md). The re-plan coverage loss is now caught by `gate-final` (every planned test must be on main). Run 8, **second intent** (`INTENT=wordfreq`, two dependent stories): 12/12, 18m05s, $1.53, 54 blocks; found scope globs expanded against the worktree (29 GREEN blocks) and `mod common;` across test crates reported as a HIGH duplicate (57 review halts), both fixed. Run 9 (wordfreq again): 12/12, 19m42s, $1.56, 29 blocks; the ctx-symbols fix had not reached the run (`ensure-tools` rebuilt only on a version bump, fixed) and the planner named story dirs `S1-01/` (block message now names `s1-01-<slug>/`). Run 10 (rebuilt tools): 12/12, $1.20, but 44m48s and 45 blocks: the duplicate false positive is gone (0 HIGH); a RED worker's Write/Edit to the story's output.md was refused by Claude Code's worktree isolation and it stopped 29 times without trying a shell append (fixed: the block and the personas say how), plus 3 stale-`ATTEMPT` tamper blocks (fixed). Fixed after run 10: a worker that repeats an identical block 3 times is released unverified with a `STUCK` marker the supervisor acts on (reason: 29 minutes lost in run 10). Run 11 (all run-10 fixes): 12/12, **12m56s, $0.86, 6 blocks** (from 44m48s, $1.20, 45): no repeated block, one RED report-append block fixed on the first retry, one scaffold fmt block, two structural-reviewer comms blocks (STORY_DIR unset for a sprint-wide review, then a stale block) and 12 `gate-validate-artifact` write blocks on the planner's artifacts. Fixed after run 11: the artifact-write block carries md-db's diagnostics (the planner guessed twice per artifact: 12 of 25 verdicts), `gate-dispatch` requires an existing `STORY_DIR` and names the sprint dir for a whole-wave structural review (2 blocks), and OpenCode reports a stuck release as unverified. Run 12 (those fixes): 12/12, 15m22s, $1.10, 21 blocks: the planner fixed each artifact on the first retry (8 write blocks, from 12) and the reviewer's contract had `STORY_DIR`, but a stale worktree checkout under `.claude/worktrees/` made `ctx-symbols conflicts` report every symbol of main as a duplicate and the reviewer was blocked 10 times on a HIGH finding it could not fix (fixed: the walk skips host checkouts, `.agentic/`, `.transcripts/`). The stuck release now covers the structural reviewer and the final gate too (reason: 10 identical blocks, run 12). Gap: `targets check` blocked the planner twice because a context target named a planned symbol not yet in `src/lib.rs` (reason: planned symbols do not exist before SCAFFOLD; §4.6 owner to decide). Run 13 (those fixes): 12/12, 16m31s, $1.33, 16 blocks; the first build of the walk exclusion also excluded a worktree that IS a host checkout, so the scaffold gate counted every symbol 0 times (9 blocks; the worker was then released by the stuck release and the run still finished); fixed before the run ended (the exclusion applies below the walk root) and covered by a conformance case. Open: `targets check` blocks on the planner's first `targets.json` (2 per run, fixed on retry; §4.6 owner), and sub-agents reading the plugin's own source rose to 17 (from 2–10). Run 14 (all fixes): 12/12, **13m47s, $0.88, 11 blocks**, one host-run block in all (RED touched `src/lib.rs`, fixed on retry), no stuck worker; the rest are planning write blocks fixed on the first retry and workers' own self-checks. The supervisor's GREEN mandates in runs 11–14 named one symbol and `src/lib.rs only`, and the diff scope (from `tasks.md` or `targets scope`) bounds them anyway, so the "GREEN instructions overreaching" item is closed as not reproduced. The planner persona now carries the schema's artifact shapes and the `targets check` rules (to measure). Run 15 (planner shapes + rules, `task-worktree merge`): **13/13**, 13m13s, $1.42, 6 blocks: **0 planning write blocks** (from 8–12: every artifact and `targets.json` passed first time), no host-run block, and §4.7 is live on Claude Code for the first time: 8 commits, 8 `commit.created`, 6 `worktree.merged`, every merged commit answers `events trace` (new e2e check). Plugin-source reads, counted precisely (tool calls naming a plugin path; `--schema` uses excluded): 9, 8, 6 in runs 13–15, all by the supervisor reading `init.template.md` and `log-execution` to learn shapes SKILL.md now states (`log-execution --help` added). Run 15 cost more than run 14 ($1.42 vs $0.88) because the supervisor dispatched every single-task wave in the background and woke 16 times (run 14: 2), each wake re-reading its context: 5.5M cached Sonnet tokens vs 2.8M. SKILL.md now runs a one-task wave in the foreground. Run 16 (that rule): 12/13, **13m04s, $0.79** (from $1.42), **4 wakes** (from 16), 2.3M cached Sonnet tokens (from 5.5M), 9 blocks, 4 plugin-source reads (from 6); the one failed check was the trace: the supervisor committed `Track Cargo.lock` on main itself (the fixture left the lockfile untracked), a commit no task owns. SKILL.md: the supervisor never commits on main; the fixture seeds the lockfile. Of run 16's 9 blocks, 4 were workers self-checking before appending their report block (the personas said "run selfcheck, append your report"; fixed: report first, then selfcheck), 2 were scaffold fmt/uncommitted work and 1 RED touching `src/lib.rs`. Run 17 (report-first personas, never-commit-on-main, seeded lockfile): **13/13, 12m58s, $0.95, 2 wakes, 0 plugin-source reads**, 6 blocks: 3 were the structural reviewer's own `selfcheck` finding no contract (only workers got the worktree copy; fixed in `budget hook`, test-first), 2 RED self-checks before the report (the persona fix landed after this run started), 1 RED touching `src/lib.rs`. Run 18 (reviewer contract copy, v0.12.0): 13/13, 21m21s, $1.03, 6 wakes, 2 plugin-source reads, 6 blocks: the first RED self-check found no contract because Claude Code now prepends a session-context line to the sub-agent transcript and `budget` read only the first `user` line for `TASK_ID` (fixed, test-first: it reads on to the first line naming one); a RED worker's `cat >>` created a story's `output.md` without its frontmatter and self-checked twice against it (fixed: `budget hook pre` seeds `output.md` with `type: output` before the worker's first call); the planner's `targets check` and `TBW` self-checks (3, fixed on retry, by design); one RED touched `src/lib.rs` because the supervisor's mandate told it to shim there (SKILL.md: the mandate names `tests/common/mod.rs`); one scaffold symbol slip. Wall time was runner contention, not more turns. The never-commit-on-main rule is now a gate (`gate-supervisor-scope` blocks the supervisor's `git commit` in the main tree while the sprint is live; reason: a prose rule that one run already broke is cheaper to enforce than to re-find). Next: measure blocks ≤3 with these fixes Run 19 (never-commit-on-main gate): 12/12, 15m47s, $0.99, 39 supervisor turns, 2 plugin-source reads, **2 logged blocks** (one RED whose T2 tests already passed against T1's placeholder, one scaffold) — but 9 unlogged ones: the mid-sprint re-plan of T2 ran the planner's stop gate again, and `targets check` blocked its stop 9 times with "create targets tests/parse.rs, which already exists" for T1's merged change, which the brief had told it not to touch; it stopped only once it rewrote C1/C2 to `modify` and the supervisor dispatched it a second time. Fixed: a `create` whose file now exists and defines its planned_symbol is realized, not a defect (`targets check` passes; `target` resolves it to file:line; test: `semantic-targets.test.py`, reproduced on run 19's plan). Fixed after run 19: the planner's stop-gate verdicts are logged in `gates.jsonl` and the stuck release covers the planner (reason: 9 identical blocks in run 19 went uncounted and nothing ended the loop; test: `planner-stuck.test.py`). The planner persona now plans a later task's RED tests against what the earlier tasks leave on main (reason: run 19's T2 re-plan; to measure in run 20). A second, parallel run 19 (the other thread's, on #29's run-18 fixes): **13/13, 15m24s, $1.01, 1 wake**, 0 plugin-source reads (first counted as 5: the planner's `md-db describe --schema "$SCH"` on the plugin's schemas, a legitimate use the counter now recognises), 3 host-run blocks: run 18's two classes (unbound worker, bare `output.md`) did not recur; the scaffolder wrote its `### Scaffold` report shape (`+ create fn crate::count_words @ src/lib.rs`) into `.aladdin/scaffold-symbols` and the gate read `create` as a symbol twice (fixed, conformance cases: markers, verbs, keywords and crate paths are skipped, the block names the file's shape); the structural reviewer was dispatched without a worktree and its self-check found no contract in the main tree (fixed, test-first: `budget hook` copies the contract into the main tree for an observed role and removes it at stop). The planner's `targets render` diff and `targets check` self-checks (7, fixed on retry) are its own loop. Run 20 (planner stuck release + RED rule): 12/12, **10m47s, 1 block, 0 re-plans, 0 stuck** (the planner's 5 stop verdicts are now in `gates.jsonl`), 36 supervisor turns, 2 plugin-source reads — but **$2.79**: the supervisor dispatched the planning roles on opus and the workers on sonnet although defaults.md said haiku for both and its own `aloop models resolve` call had printed `haiku` (opus $1.11 + sonnet $1.68; run 19 on haiku workers: $0.99). Fixed, test-first: `gate-dispatch` refuses a dispatch whose `model` is not the role's resolved one, or that passes none (reason: a per-dispatch model choice is a project setting the supervisor had no business re-deciding; the cheapest deterministic fix). Run 21 (model gate): 12/12, **8m17s, $0.66** (from $2.79; run 19: $0.99), 29 supervisor turns, 1 wake, 0 plugin-source reads, 3 blocks (two planner self-checks fixed on retry, one structural-review finding fixed on retry), 0 stuck: every dispatch went on haiku (haiku $0.10 + supervisor sonnet $0.56). The cost is now where the model table puts it; the next lever is the supervisor's own context (sonnet $0.56 of $0.66). Run 22 (wordfreq on master before the model gate, 361f8d7): **13/13, 16m18s, $1.14, 2 wakes, 0 plugin-source reads**, 3 logged blocks (all the planner's own stop-gate self-checks, fixed on retry), 0 re-plans, 0 stuck. One loop the gate log does not show: the final-gate persona grepped its own suppression list, failed the sprint on RED's `#![allow(dead_code)]` in `tests/common/mod.rs` (a shared test-helper module, the Rust idiom; the deterministic gate accepts it) and cost a second GREEN dispatch and a final-gate retry. Fixed: the persona names exactly `assert_no_suppression`'s patterns and defers to `selfcheck final-gate` (reason: models explore, gates decide; test: `suppression-contract.test.py`, eval case final-gate #4). Run 23 (model gate, second intent `wordfreq`): **13/13, 10m58s, $0.76, 0 blocks**, 24 supervisor turns, 4 wakes, 1 plugin-source read: both dependent stories planned, built and merged with no gate block for the first time (run 22 on the same intent without the gate: $1.14, 16m18s, 3 blocks). Run 24 (experiment: the same intent with a **haiku supervisor**, `e2e-live.sh --model haiku`): 10/13, 19m09s, $0.66, 51 supervisor turns (from 24), 6 blocks — no cheaper than sonnet (the turns doubled), and the supervisor put the whole sprint under `.aladdin/sprint1/`, the git-ignored run-data dir, so the three planning checks found no sprint; every gate passed it. Fixed, test-first: `gate-stage2-complete` and `gate-dispatch` refuse a sprint or `STORY_DIR` inside `.aladdin/`/`.agentic/` and name `docs/agents/sprintN/` (reason: the plan is the audit trail and must be where the layout commits it; tests: `planner-stuck.test.py`, `dispatch.test.py`). The supervisor stays on the strong route. Run 25 (wordfreq under the model gate, #40's head, a second sample of run 23's setup): **13/13, 16m13s, $0.98** (from $1.14; haiku $0.19 + supervisor sonnet $0.79), 3 wakes, 0 plugin-source reads, 5 blocks, 0 re-plans, and the final gate passed first time (the persona fix held). 4 of the 5 blocks were one GREEN worker: it tried the Write tool on the main tree's `output.md`, worktree isolation refused it, and the gate's "latest block is not from 'green-worker'" named no way out, so it stopped three times, hit the stuck release and needed a supervisor wake to append with `cat >>`. Fixed: that block now carries the same shell-heredoc instruction as the missing-block one (conformance case). Run 26 (the supervisor's playbook trimmed: the Codex/OpenCode notes, the budget re-plan route, review blind spots, the script list, transcripts and worktree hygiene moved to an on-demand `skills/aloop/reference.md`, SKILL.md 58k → 49k chars): 13/13, 16m01s, $0.95, 36 supervisor turns, 2 wakes, 1 plugin-source read, 7 blocks. The playbook load fell from 25.0k to 18.8k tokens, about 6k fewer tokens re-read on every supervisor turn, and the supervisor never needed the reference file; the run's cost was set by a RED loop instead (5 of 7 blocks): the supervisor's mandate allowed a `src/` skeleton, the gate made RED revert it, and the tests then called a symbol that no longer existed; the block said only "add mod-common shims", so the worker stopped three times and hit the stuck release. Fixed: the compile block names each missing symbol and its shim, e.g. `pub fn count_words(…) -> … { todo!() }` in `tests/common/mod.rs`, called through `mod common;` (conformance case). Run 27 (master with the trimmed playbook and the named-symbol RED block, wordfreq): **13/13, 9m54s, $0.70, 1 wake**, 33 supervisor turns, 1 plugin-source read, 3 blocks (the planner's own self-check, and two scaffolders self-checking before they committed, each fixed on retry), 0 stuck, 0 re-plans: the fastest and cheapest two-story run so far (haiku $0.12 + supervisor sonnet $0.59). All 3 of those blocks were the workers' own `selfcheck` catches, with 0 blocks at stop. `run-summary` now counts them as `selfcheck_blocks`, and the worker prompts give the finish order as block, commit, selfcheck. Run 28 (master after #47, wordfreq): 13/13, 13m14s, $1.15, 3 wakes, 4 blocks (2 self-check catches). The supervisor took the manual task-worktree route without copying the contract into the worktree, so RED's `aloop bind` failed and its stop blocked twice in the shared tree. Fixed: `gate-dispatch` copies and refreshes the contract in a task worktree on every dispatch (test: `dispatch.test.py`). Run 29 (master after #49, with `aloop budget contract`, wordfreq): 13/13, 12m55s, $0.91 (haiku $0.15 + supervisor sonnet $0.76), 1 wake, 0 plugin-source reads, **0 gate blocks**, 38 supervisor turns. The supervisor wrote every contract with `budget contract` (no heredocs), but spent 5 turns polling for the planners' files with `sleep` loops after dispatching intake and standards in the background, and the final gate's sign-off never reached the main tree: its Write was refused, it wrote a worktree copy the host discarded, its stop passed because `gate-final` never checked comms, and the supervisor wrote the final-gate block itself. Fixed: `gate-final` runs `validate_comms` for a final-gate worker under a contract (conformance cases), the persona says how to append, and SKILL.md runs planning roles in the foreground (two in one message) and forbids writing an agent's block. Run 30 (master after #50, wordfreq): 13/13, 11m27s, **$0.70** (haiku $0.15 + supervisor sonnet $0.54), **23 supervisor turns** (from 38), 2 wakes, 0 plugin-source reads, 4 blocks, all caught by the workers' own self-checks (0 at stop). Every planning role ran in the foreground with no polling, and the final gate appended its own sign-off block (its first self-check caught it missing; the new comms check working as meant). The never-write-an-agent's-block rule is now a gate too (`gate-supervisor-scope`, test: `supervisor-scope.test.py`; reason: run 29 broke the prose rule once). Run 31 (semver on master after #52): 12/12, 10m24s, $0.91 (haiku $0.12 + supervisor sonnet $0.79), 27 supervisor turns, 2 wakes, 1 plugin-source read, 5 blocks; the supervisor-scope gate never fired. 4 of the 5 blocks were one loop: T2's tests called only T1's finished `parse`, the scaffolder listed it, and the gate demanded a TODO panic in a body it had not touched, so it hit the stuck release and needed a second dispatch. Fixed: a listed symbol that existed at `BASE_REF` in an unchanged file needs no stub (conformance cases). Run 32 (semver on master after #53): 12/12, $0.90, 0 scaffold blocks (2 blocks, both self-check catches), but 23m30s wall for 11m of work: the supervisor set a 1200s `ScheduleWakeup` "heartbeat" when the host moved the planner to the background, and the headless session waited for it 12 minutes after the final gate. It also ran `task-worktree add --help`, which made a worktree named `--help`. Fixed: `gate-supervisor-scope` blocks the supervisor's `ScheduleWakeup` while a sprint is live, and `task-worktree` prints usage for `--help` and refuses a dash-led TASK_ID (tests). Run 33 (wordfreq on master after #54): 13/13, 12m20s, $0.84, 27 supervisor turns, 2 wakes, 0 plugin-source reads, 5 blocks (all self-check catches), no wakeup. Runs 31-33 each improvised the same RED-to-SCAFFOLD handoff on Claude Code (`task-worktree --help`, inspect the host worktree, `task-worktree add` on RED's branch, then SCAFFOLD/GREEN bound to it); SKILL.md Part B now states it (skill-layout test). Run 34 (semver on master after #55): 12/12, 10m23s, $0.74, 21 supervisor turns (27 in run 31), 2 wakes, 1 block (a self-check catch); the supervisor went straight from RED to `task-worktree add` on RED's branch for both tasks, with no `--help` or inspection turns. Runs 32-34 also inherited the launching agent session (its session id and its team memory, recalled at start); `e2e-live.sh` now unsets those before the run (e2e-live-hosts test), so earlier runs from an agent session carry that caveat. Run 35 (wordfreq, the first with a clean env): 13/13, 12m59s, $0.91, 31 supervisor turns, 2 wakes, 0 plugin-source reads, 2 blocks (self-check catches), no memory recall and its own session id; within the range of runs 27-33, so the inherited memory had not been carrying the earlier results. Next: the Codex/OpenCode public runs (need a machine with those CLIs) |
| **M0.6 — Gates fail closed** | v0.11.x | A gate that crashes (exit 1, e.g. an unbound variable) is a non-blocking hook error on Claude Code, so the action goes through. Make every gate trap unexpected errors into exit 2 with the reason, and add a conformance case per gate. Reason: the smoke run showed one real instance | done: `_failclosed.sh` on all 10 sub-agent gates + supervisor-scope inside a sprint; `scripts/test/fail-closed.test.py` (5 checks). Left fail-open on purpose: `gate-commit-author` and `gate-validate-artifact` outside a sprint, which see every session on the machine |
| **M0.7 — Supervisor-owned task contract** | v0.11.x | The contract lives in the main tree, `.agentic/state/tasks/<TASK_ID>.env`, and the dispatch prompt's first line names the task; gates and the time box find it from the host's transcript, a worktree `task.env` that disagrees blocks, and sub-agents may not Write/Edit `.agentic/state/` or `task.env`. Settled by a live probe: Claude Code's `isolation: "worktree"` makes `.claude/worktrees/agent-<id>` at dispatch, so a pre-written in-worktree contract was impossible there. Bash writes are guarded too (a command naming the contract with a write verb is blocked; a heuristic, so an obfuscated write still gets through). On hosts with no sub-agent transcript the worktree's `TASK_ID` (or its `.agentic/worktrees/<id>` path) picks the contract | done: `make conformance` (58 today) + `scripts/test/{budget-worktree,supervisor-scope}.test.py` |
| **M0.5 — Rename to aloop** | v0.11.0 | Plugin, marketplace, skill, CLI (`aloop`, `aloop init`), OpenCode plugin file and docs renamed; `agentic`, `agentic init`, `agentic-init` and `agentic bind` kept as aliases; OpenCode/Codex installs clean up pre-rename files. Data layout and settings keys unchanged (moved to M1). GitHub repo rename is the owner's call | done: `scripts/test/rename-compat.test.py` |
| **M1 — Trustworthy state** | v0.12 | Session 1: conformance fixtures around current behaviour, canonical IDs and the reference remap (§4.5); then move the data layout to the new name (`.agentic/` → `.aloop/`, `AGENTIC_*` → `ALOOP_*`) with a read-old fallback, proven by those fixtures | done: §6 fixtures (`make conformance`, 58 checks incl. every false positive the live runs found) and canonical IDs + the reference remap (§4.5, `ids freeze`, `scripts/test/ids-integrity.test.py`); **layout move shipped (Unreleased)**: run data lives in `.aladdin/` (chosen over `.aloop/`, 2026-10-18, because `config.yaml` already lives there and the product name is Aladdin), guarded by `.aladdin/.gitignore`; a project with `.agentic/` keeps using it until `aloop layout migrate`; `ALADDIN_<KEY>` is an alias of every `AGENTIC_<KEY>` env/setting (`AGENTIC_` wins when both are set); the gates find the task contract in either dir. Test: `scripts/test/layout.test.py`; every suite runs on the new layout. The worktree-local markers (`task.env`, `budget-exceeded`, `scaffold-symbols`) moved too: written to the worktree's `.aladdin/` (guarded), read from `.agentic/` as well (hosts and older sprints still write there). The layout move is complete |
| **M2 — Budgets that plan** | v0.13 | Sessions 2 and 4: budget semantics (§3.1–3.5), sprint envelope and admission control (§4.2), breach classes, failure envelope, grace rule, lesson candidates (§4.3) | done: §3.1–3.5 budget semantics, §4.2 sprint envelope + admission control (`budget admit`, `sweep`), §4.3 breach classes, failure envelope, grace rule and lesson candidates (tests: `budget-*.test.py`, `orchestrator-sweep.test.py`, `lesson-candidates.test.py`) |
| **M3 — Clean boundaries** | v0.14 | Session 3: canonical handoff, rotation and the resume rule (§4.4) | done: canonical `handoff.json` + NEXT.md view, rotation/compaction recorded from SessionStart, the resume rule (`stats resume-check`; tests: `context-boundary.test.py`, `handoff-resume.test.py`) |
| **M4 — Intent in, autonomy optional** | v0.15 | Session 5: Intent Contract, clarification policy, autonomous mode (§4.1) | done: Intent Contract (`bin/intent`), clarification policy, autonomous mode (tests: `intent-contract.test.py`); `aladdin "<intent>"` records it (§4.11) |
| **M5 — Narrow context** | v0.16 | Session 6: semantic execution map, shadow workspace, `target` / `context` CLI (§4.6) | done: targets, shadow workspace, `target`/`context`, LSP seam, GREEN scope from targets, and tasks.md/validate.md/plan.md rendered from the semantic plan (`targets render`; tests: `semantic-targets`, `lsp-adapter`, `targets-scope`, `plan-render`) |
| **M6 — Audit** | v0.17 | Sessions 7–8: event stream, rationale, authority, lineage query (§4.7); provider seam (§4.8) | done: `aladdin.event/1` stream, decisions, authority, lineage, `events trace SHA`, commit messages and graph export (§4.7); impact provider seam (§4.8) |
| **M7 — Any host** | v0.18 | Session 9: `HostAdapter`, capability matrix, `.aladdin/config.yaml`, host swap, metrics (§4.9–4.10) | done: `HostAdapter` + `host check` in CI, capability matrix, usage accuracy, `.aladdin/config.yaml` with migration, `host swap`, the full metrics table incl. context-window share (tests: `host-adapter`, `portable-config`, `host-swap`, `metrics`) |
| **M8 — Aladdin 1.0** | v1.0 | Session 10: `/aladdin` + `aladdin` CLI with aliases, site, measured public runs (§4.11). The rename to aloop shipped early in v0.11.0 | in progress: `bin/aladdin` + `/aladdin` with `aloop`/`agentic` aliases (test: `aladdin-cli.test.py`); natural-language auto-trigger on every host (`intent-trigger`, test: `intent-trigger.test.py`); README reordered product-first; site at `docs/index.html` (Pages switch is the owner's). evidence ledger `docs/evidence/README.md` + `run-summary` (test: `evidence.test.py`). `e2e-live.sh --host codex|opencode` runs the same loop on those hosts (`aloop install <host>` into the run repo, then `codex exec --json` / `opencode run --format json`; `--dry-run` prints the command; test: `e2e-live-hosts.test.py`). Open: the measured public runs themselves (this environment has no `codex`/`opencode` CLI: run `make e2e-live YES=1 HOST=codex` and `HOST=opencode` on a machine with both and add the rows; reason: a number we cannot measure is not published) Mac release run (2026-10-19) found two bugs Linux CI never sees, both fixed with tests: `stats sprint-close` counted 0 attempts when the project sat behind a symlink (macOS `/var` → `/private/var`; paths are now compared by realpath), and `make conformance` hung when another aloop checkout's `plugin/bin` was ahead on PATH (`ensure-tools --resolve` returned that checkout's shim; any aloop bin dir is now skipped). RED is now verified on any language: a non-cargo repo runs its `standards.md` matrix's test command, and a RED whose tests pass or do not collect is blocked (pytest, `go test`; test: `any-language.test.py`). Reason: the north star is any intent, and RED was dynamically checked only for Rust. GREEN and the final gate now refuse Python/Go/JS skips, and the final gate finds a planned `def`/`func` test (before, it found only `fn`, so every planned Python test blocked). `INTENT=slugify` is the Python fixture. Run 36 (slugify, the first Python run): 11/12, 11m37s, $0.83, 5 wakes, 0 plugin-source reads, 15 blocks (9 caught by self-checks). The sprint merged a correct `slugify` with 13 passing tests and the final gate passed. The one failed check was the fixture's own: bare `pytest` leaves the repo root off `sys.path` (now `python3 -m pytest`). It found two gate bugs that Rust runs had hidden. First, a worker's worktree has no `docs/agents/` (git-ignored), so the gates never found `standards.md` and ran the default Rust matrix: RED blocked 4 times on `cargo fmt`, and in a Rust project a custom matrix was silently swapped for the default. Second, `ctx-symbols conflicts` walked the merged task checkout under `.aladdin/worktrees/` and reported `slugify` as a HIGH duplicate, so the structural reviewer was rejected 4 times. Fixed with tests: the gates read the story's sprint `standards.md` (else this tree, else the main tree), and a non-cargo project with no matrix blocks with a message naming `standards.md`; ctx-symbols skips `.aladdin/`. Run 37 on these fixes: 12/12, 9m42s, $0.69, 1 wake, 11 blocks (7 caught by self-checks). It found that a `standards.md` whose matrix is a table parses empty, so RED blocked 3 times; `gate-standards-cited` now refuses a standards.md with no runnable `## Gate matrix` block (test: `any-language.test.py`). Run 38 (`INTENT=roman`, the first Go run): 12/12, 14m15s, $1.02, 3 wakes, 9 blocks (7 caught by self-checks). It found that the sprint dir's `init.md` had no `type: init` header (the supervisor began it with a dispatch block), so the structural reviewer was blocked 7 times; `aloop budget contract` now writes that header (test: `budget-contract.test.py`). Run 39 on the fix: 12/12, 7m52s, $0.56, 0 init.md blocks. It found that GREEN's default scope let it touch Rust test files only, so a Go GREEN removing RED's `_test.go` shim was blocked 10 times; GREEN now shares RED's any-language test globs (test: `any-language.test.py`). Run 40 on it: 12/12, 13m03s, $0.75, 1 wake, 0 GREEN scope blocks (5 blocks, all caught by self-checks). The structural reviewer's HIGH foundation-poisoning finding in runs 38 and 40 was a false positive: a worker's `selfcheck` resolved a stale 0.11.0 `ctx-symbols` (before the `.aladdin/` exclusion) ahead of the session's current build; `ensure-tools --resolve` now skips a stale own build (test: `ensure-tools.test.py`). RED is verified on JS too (a test file that cannot load what it imports is blocked; test: `any-language.test.py`), and `INTENT=camel` is the JS fixture. Run 41 (camel, the first JS run): 12/12, 6m45s, $0.56, 1 wake, 2 blocks (both RED self-checks refusing a source edit). The loop is now measured on Rust, Python, Go and JS. Run 42 (`INTENT=extend`, the first brownfield intent: add `compare` to a crate whose `parse` already works and is tested): 13/13, 7m11s, $0.67, 2 blocks (both self-check catches); the seed's code and tests were left unchanged and still pass (reason: every earlier fixture started from an empty library, and most real intents land on existing code). Run 43 (`INTENT=modify`: change that existing `parse` to accept a leading `v` and trim whitespace): 13/13, 9m10s, $0.67, 6 blocks, all self-check catches; the change was 3 lines in `parse`, with its signature and seed tests kept. `INTENT=cli` (an application: a wc-like binary tested by running it, checked by running the merged binary) is ready; run 44 (the retry after the usage limit lifted): 13/13, 24m55s, $1.54, 6 blocks (all self-check catches), and the merged binary works. Its cost came from planning: 2 stories and 7 tasks for a small tool, so 7 RED/SCAFFOLD/GREEN cycles and 56 supervisor turns (runs 42–43: 1–2 tasks, 16–30 turns). Next: the planner sizes tasks by behaviour that needs its own RED proof, not one task per flag or count (reason: per-task cost is a fixed ~8 supervisor turns). Planner friction next: across runs 36–41 the most frequent block was `targets` "create needs planned_symbol" on a new test file (8); a test-file create now takes it from the task's `tests` (test: `semantic-targets.test.py`). The `gate-validate-artifact` plan.md refusals (4) were the persona's bare `stage: 1`, now quoted and checked (test: `persona-shapes.test.py`). Next: the final-gate block order (3). Running every suite under a locally built bash 3.2 found that the `standards.md` gate matrix was silently replaced by the default Rust matrix (`mapfile` does not exist in bash 3.2). It is fixed and guarded by `bash-portable.test.py`. A `macos` CI job now runs the host tests and the conformance fixtures on macOS (reason: both were found only by hand on the owner's Mac). |
| **M9 — Mission layer** | v0.13 | The "Missionary Aladdin Genie" spec (2026-10-17): a human-set mission above goals; fixed roles (research, product testing, state, evaluation, planning, ethics) build a confirmed baseline and a reproducible evaluation layer from user journeys; goals derived from the gap list become Aladdin intents; re-evaluate on a cadence and after each sprint; GA4 usage informs goals and is never a gate; every element has a deterministic check (§4.12). Reason: the owner's mission-driven loop, stacked on Aladdin | in progress: M9.0 core (`aloop mission init / check / approve / status`, sealed mission + approval events, area discovery, test glob; test: `mission-core.test.py`); M9.2 baseline, M9.3 journeys and M9.10 assess/evaluate (`mission-{baseline,journeys,evaluate}.test.py`). Open tracks: M9.1, M9.4–M9.9; the task graph is in §4.12, track notes in `plugin/mission/README.md`; hardened after an adversarial review (ledger records over status fields) |

**Order rationale:** identity and conformance fixtures first, so every later change is
measured against today's behaviour; budgets and handoff next, because they shape every run;
the semantic map and audit trail after, since they build on canonical IDs; portability and
the rebrand last, once the conformance suite (§6) can prove parity.

**Exit for each milestone:** the §5 exit criterion holds, the §6 conformance items it
touches pass on Claude Code, and each requirement is reported per §8.

## 1. Goal

Evolve **agentic-agile** into **Aladdin**. Aladdin is an intent-driven, test-driven, cross-harness system for software engineering. It takes anything from a one-line request to a full specification and turns it into planned, tested, reviewed and auditable software.

- **Public thesis:** *Intent is all you need.*
- **Technical thesis:** *Do not merely give an agent a goal. Give it a failing proof of what is missing, then make it earn GREEN.*
- **Key line:** *Models explore. Gates decide.*

**Out of scope for this roadmap:**
- harness or workflow synthesis;
- personas invented per mission (the mission layer's six roles are fixed, §4.12);
- non-software workflows;
- a Mission OS (the §4.12 mission layer is a loop that turns one software product's mission into Aladdin intents, not an OS);
- building an enterprise knowledge graph (Aladdin only consumes one through a provider seam);
- IDE or human-facing LSP features.

## 2. Baseline — already shipped (v0.10.x), not repeated below

| Area | Shipped |
|---|---|
| Roles and TDD | The nine fixed roles, RED → SCAFFOLD → GREEN → STRUCTURAL-REVIEW → FINAL-GATE, and parallel waves |
| Determinism | Hook gates (`gate-*`), KDL-backed `md-db` schema validation, `ctx-symbols` scaffold and duplicate checks, and a supervisor-scope guard |
| Isolation | One git worktree per task. Merge happens only on a pass; killed or failed worktrees are discarded. |
| Hosts | Claude Code (reference), Codex and OpenCode. The same gates run through `host-adapter`, and the non-Claude hosts get generated agents and skills. |
| Approval | Reviewed mode: Stage-1 with the human, then approval of the full Stage-2 plan, then an autonomous run |
| Per-attempt budget | Soft and hard limits on tokens and wall-clock time. At the hard limit the attempt is stopped and the task is split by the planner (≤ `MAX_REPLANS`), never retried as-is. |
| Sprint boundary | The harness writes `stats.md` and `NEXT.md`. Context is cleared or a new session starts, and the SessionStart hook reloads the handoff. The planner keeps sprints self-contained. |
| Telemetry | A harness token ledger (in / cache read / cache write / out, by role and by model), attempts, gate blocks, kills, re-plans and human messages |
| Evidence | `init.md`, `output.md`, `plan-ready.md`, `execution.log`, `memory.md`, full transcripts and git history |
| Memory | The retrospective (`archivist`) promotes recurring lessons into `memory.md` |
| Model routing | Per role: planning agents on `claude-opus-5-5`, workers on `sonnet`, overridable in `defaults.md` |
| Project init | Limits, models, `.gitignore`, whether `docs/agents` is tracked, and commit authorship (the human's) are confirmed, then written to `docs/agents/defaults.md` |

## 3. Changes to existing behaviour

These parts already exist but their semantics must change:

1. **The soft token limit becomes telemetry only.** ✅ *Done (Unreleased): `budget` records the event silently; stats.md and NEXT.md surface it. Test: `scripts/test/budget-soft-telemetry.test.py`.* Previously the worker was told to wrap up. Now:
   - emit `task.token_soft_limit` and record it;
   - let the worker continue;
   - feed the event to the *next* planning cycle.

   Graceful wrap-up stays for the *time* soft limit only.
2. **The token meter counts classes separately.** ✅ *Done (Unreleased): `TOKEN_METER=fresh` default, classes in attempt state. Test: `scripts/test/budget-meter.test.py`.* Previously it measured context in use plus output. Change the default task-length charge to *fresh input + output*, and keep cache read and cache write visible for cost and churn. Never confuse cumulative cache traffic with context size.
3. **Budgets cover every model role.** ✅ *Done (Unreleased), observe-only for non-workers: they are metered and their crossings recorded, but not stopped, since a planner runs with the human present. Enforcing on them is a follow-up once the telemetry shows real profiles. Test: `scripts/test/budget-roles.test.py`.* Previously only `red-worker`, `scaffolder` and `green-worker` are metered. Extend the meter to planners, reviewers and `final-gate`, with a limit profile per role.
4. **Hard breaches are classified, not always split.** ✅ *Done (Unreleased): the hook pre-classifies from facts it owns, and the planner confirms the class and acts on it. Test: `scripts/test/budget-classify.test.py`.* The planner first classifies the breach, then acts:

   | Class | Action |
   |---|---|
   | `TASK_TOO_LARGE` | Split into smaller independently testable tasks |
   | `AGENT_LOOPING` | Reframe the task and inject the failed-strategy evidence |
   | `PLAN_AMBIGUOUS` | Repair the contract |
   | `BLOCKED_DEPENDENCY` | Schedule the dependency first |
   | `ENVIRONMENT_SLOW` / `TEST_SUITE_SLOW` | Adjust commands or the time budget (splitting may be useless) |
   | `EXTERNAL_SERVICE` | Isolate or mock it |
   | `HOST_FAILURE` | Retry the infrastructure, not the implementation |
   | `CONTEXT_PATHOLOGY` | Rotate or narrow the context |
   | `UNKNOWN` | Conservative re-plan, or escalate |

   Replacement tasks must fit the normal time and token budgets, and vertical slices are preferred.
5. **No post-kill narration.** ✅ *Done (Unreleased): the hook stops the worker on the crossing call and the harness writes the `status: re-plan` block. Test: `scripts/test/budget-hard-stop.test.py`.* After the hard stop, no model tokens are spent asking the worker for a report.
6. **Abstract model routes.** ✅ *Done (Unreleased): `bin/models` + `plugin/hosts/routes.json`; `AGENTIC_MODEL_*` default to routes; `aloop install` renders the host's id. Test: `scripts/test/model-routes.test.py`.* Roles map to `strongest | strong | economical`, and each host resolves those to concrete IDs.

## 4. New work

### 4.1 Intent normalization

- ✅ *Done (Unreleased): `bin/intent` (new/check/summary), gate-intake wiring, the clarification policy and explicit autonomous mode. Test: `scripts/test/intent-contract.test.py`.*
- **An internal Intent Contract** produced by `intake`. The user never fills it in. Fields:
  - `intent_id`
  - `source` (prompt | file | issue | image | mixed)
  - `actor`, `want`, `why`
  - `constraints`
  - `success_scenarios`, `failure_scenarios`
  - `connections`
  - `done_looks_like`
  - `open_questions`, `assumptions`
  - `provenance`
- **Clarification policy:**

  | Uncertainty | Handling |
  |---|---|
  | Safe and reversible | Planner assumes and records the assumption |
  | Material but recoverable | Planner picks a default and surfaces it in the approval summary |
  | Irreversible, security-sensitive, or changes intent | Ask the human |
  | Unknown but testable | Create a spike task |

- **Autonomous mode**, explicit at run start: generated plan → deterministic planning checks → execution, with no approval unless an escalation class is hit. Reviewed mode stays the default.

### 4.2 Sprint budget and admission control

- A sprint aggregate token envelope (soft/hard). ✅ *Done (Unreleased): `budget admit`, `rollover.md`, gate-final `rollover-check`. Test: `scripts/test/sprint-admission.test.py`.*
  - **Soft:** a rollover warning. Optionally, stop starting non-critical work.
  - **Hard:**
    - close admission;
    - let in-flight tasks finish under their own limits;
    - carry not-started tasks to the next planning cycle;
    - record the reason.

    A rollover is **not** a failure.
- ✅ *Done (Unreleased): `bin/timebox` + `AGENTIC_TIME_MODE=auto`. Test: `scripts/test/timebox-auto.test.py`.* Time budget `auto` mode: the planner proposes one stable task duration per project (with a min/max), recorded by the supervisor. It is revised only when there is evidence.
- ✅ *Done (Unreleased): `bin/sweep` (inspect → one action per task) and `sweep wait` (token-free sleep, wake on event). Test: `scripts/test/orchestrator-sweep.test.py`.* An interruptible orchestrator loop: *sleep → wake on event → inspect → accept / retry / preempt / replan / rollover / spawn → sleep*. The orchestrator spends no tokens watching workers.

### 4.3 Planning feedback lifecycle

- ✅ *Done (Unreleased): failure envelope (`budget envelope`), grace rule and calibration verdict in NEXT.md. Test: `scripts/test/planner-feedback.test.py`. Lesson candidates: `bin/lessons` + gate-memory recurrence check. Test: `scripts/test/lesson-candidates.test.py`.*
- **A minimal failure envelope for the planner.** It carries:
  - the task, attempt, role and limit type;
  - the observed value;
  - the last gate result, changed symbols and a test summary;
  - references to the transcript, events, `output.md`, diff and tests.

  Deeper evidence is retrieved on demand, never injected by default.
- **Grace rule:**
  - 0–1 failed or hard-breached tasks in a sprint → no nudge.
  - 2 or more → one concise "plan smaller" nudge for the **next planning cycle only**.
- **Calibration verdict:** when many tasks breach, the planner recommends one of:
  - keep the budget and plan smaller;
  - raise the time budget;
  - raise the token budget;
  - reduce context;
  - fix the environment.

  Persistent changes are recorded.
- **Planner lesson candidates** use `symptom / cause / change / applies_to / evidence_refs`. Promotion to `memory.md` goes only through the existing archivist recurrence rule. There is no second memory system.

### 4.4 Context boundary: completing the model

- ✅ *Done (Unreleased): `handoff.json` + resume rule (`stats resume-check`, SessionStart). Test: `scripts/test/handoff-resume.test.py`. Compaction detection and the rotation rule are done too (`bin/capabilities`, SessionStart events; test: `scripts/test/context-boundary.test.py`).*
- A **canonical sprint handoff** built from accepted state. It records completed refs, accepted commits, open and carried work, invariants, the semantic targets needed next, decision and evidence refs, and the transient nudge. It references evidence and never inlines transcripts. `NEXT.md` becomes a rendered view of it.
- **Compaction detection:** use explicit host compaction only when the adapter advertises it. Otherwise rotate to a fresh session, which is the correctness path. Keeping a session open across a boundary needs a recorded reason.
- **Resume rule:** resume from git, artifacts, lineage, gate evidence and the handoff, never from "the agent said done". If evidence is missing, re-run the gate.

### 4.5 Identity and referential integrity

- ✅ *Done (Unreleased): `bin/ids` (freeze/check/resolve) run by gate-stage2-complete; events carry `story_uid`/`task_uid`. Test: `scripts/test/ids-integrity.test.py`. Aliases stay in the markdown; the frozen map is the rewrite layer.*
- Models keep emitting short aliases (`S1-03`, `T2`, `C7`). On ingest the orchestrator:
  1. issues canonical IDs (ULID / UUIDv7);
  2. rewrites **structured** references (never regex over prose);
  3. validates the reference graph;
  4. freezes the mapping.
- A cross-artifact referential check becomes a gate alongside the existing `md-db` schema gate.

### 4.6 Semantic execution map

- ✅ *Done (Unreleased): structured targets, shadow workspace and `targets target|context|shadow` (`bin/targets`, checked by gate-stage2-complete). Test: `scripts/test/semantic-targets.test.py`. The LSP adapter seam is done too (`AGENTIC_LSP_COMMAND`, `_lsp.py`; test: `scripts/test/lsp-adapter.test.py`). GREEN's diff scope is derived from the targets when no SCOPE_GLOBS is given (test: `scripts/test/targets-scope.test.py`). With `tasks` in targets.json the plan is the execution source: `targets render` writes tasks.md, validate.md and plan.md as deterministic views and `targets check` (gate-stage2-complete) refuses a hand-edited view (test: `scripts/test/plan-render.test.py`). Open: `refs`/`callers`/`callees` need a configured LSP server, so the narrow-context CLI is complete only where one exists.*
- **Structured change targets:**
  - `change_ref`
  - `operation` (create | modify | extend | refactor | replace | delete)
  - `path`, `symbol` / `planned_symbol`
  - `reason`, `evidence_refs`, `constraints`
- **A virtual/shadow workspace:** the real tree plus planned files and symbols. It is queryable before the code exists, and no physical stubs are created in the accepted tree.
- **An LSP/structural adapter seam**, using only symbols, references, incoming/outgoing call hierarchy and rename/workspace-edit preview. Missing backend capability is surfaced, never guessed.
- **A narrow-context CLI:** `target`, `refs`, `callers`, `callees`, `context <ref> --budget N`. Workers start from the resolved target instead of scanning the repo.
- **The semantic plan becomes the execution source.** `plan.md`, `tasks.md` and the rest become deterministic views over it. Derive anything that can be derived.

### 4.7 Provenance and lineage

- ✅ *Done (Unreleased): envelope + `bin/events` (emit/query/lineage); emitted by budget, gates, worktrees. Test: `scripts/test/events.test.py`. Decision rationale (`events decide`) and the authority record (`events authority`) are done too. Intent, plan (`ids freeze` → plan.created/revised; `events authority` → plan.approved) and commit (`task-worktree remove` → commit.created) emitters are done, and `events trace SHA` walks commit → intent → approver (test: `scripts/test/commit-trace.test.py`). `events commit-msg` and `events graph` close the last two items.*
- **A normalized event stream** (`aladdin.event/1`) for the events listed in the table below. Its envelope carries IDs, `parent_event_id` / `caused_by`, role, host, model, authority, decision, and artifact and evidence refs. It references transcripts and never copies them.
- **Structured decision rationale** at durable boundaries (plan, split, target, implementation, replan, acceptance). Each record gives a one-line reason, evidence refs and an authority ref. There is no chain-of-thought capture.
- **An authority record per run:** `initiated_by`, policy profile, `autonomy_mode`, approved scope, overrides.
- **Lineage query:** commit → attempt → decision → gate → tests → target → planner decision → story → intent → human prompt → approver, with no transcript archaeology.
- **Commit messages** built from task and change rationale.
- **Graph-export compatibility.** The graph stays downstream and rebuildable; the event stream is the source of truth.

Event types:

| Group | Events |
|---|---|
| Intent and plan | `intent.received`, `intent.normalized`, `plan.created`, `plan.approved`, `plan.revised` |
| Sprint and context | `sprint.started`, `sprint.token_soft_limit`, `sprint.admission_closed`, `sprint.rolled_over`, `sprint.context_handoff`, `context.compacted`, `context.rotated` |
| Tasks | `task.created`, `task.dispatched`, `task.completed`, `task.retry`, `task.time_soft_preempt`, `task.time_hard_timeout`, `task.token_soft_limit`, `task.token_hard_limit`, `task.cancelled` |
| Gates and worktrees | `gate.started`, `gate.passed`, `gate.failed`, `worktree.created`, `worktree.discarded`, `worktree.merged` |
| Artifacts and decisions | `artifact.created`, `artifact.updated`, `semantic_target.created`, `semantic_target.resolved`, `decision.recorded`, `commit.created` |
| Memory | `memory.candidate`, `memory.promoted`, `memory.retired` |
| Human and run | `human.approval`, `human.override`, `run.completed`, `run.aborted` |

### 4.8 Cross-project impact provider seam

✅ *Done (Unreleased): `bin/impact` + the `aladdin.impact/1` provider contract. Test: `scripts/test/impact-provider.test.py`. An MCP-backed provider plugs in as the command.*

An optional MCP/provider contract that planners may query for:
- downstream consumers;
- historical rationale;
- migration and deprecation order;
- organization standards.

Results carry source refs and are attached to planning provenance. An unavailable provider is surfaced, and local execution continues. Aladdin does not build the index.

### 4.9 Portable host core

- ✅ *Done (Unreleased): `_host.py` + `bin/host` (Claude reference, Codex and OpenCode adapters; `host check` runs in CI). Test: `scripts/test/host-adapter.test.py`.* **Extract a `HostAdapter` interface:**
  - `Capabilities`
  - `Spawn` / `Wait` / `Send` / `Cancel`
  - `Subscribe`
  - `ResolveModel` / `ResolveTranscript`
  - `Usage` / `ContextPressure` / `Compact` (optional) / `RotateSession`
  - worktree create / remove / diff
  - `InstallGate` / `EnforceGate`
  - `ToolPolicy`

  Claude Code is the reference implementation, and Codex and OpenCode become adapters behind it.
- ✅ *Usage reporting done (Unreleased): classes apart, `accuracy` + `unreported` per attempt. Test: `scripts/test/budget-meter.test.py`.* **Usage reporting** distinguishes fresh input, output, cache read, cache write and current context against the context window. Estimated counts are marked `estimated`.
- **A host capability matrix** covering usage, compaction, live messaging (for the time soft-stop), lifecycle and gate blocking.
- ✅ *Done (Unreleased): `enforce_gate` is `native` or `outer`; `host check` fails on anything else.* **Gate equivalence:** if a host cannot block natively, the outer controller enforces it. Never downgrade BLOCK to WARN.
- ✅ *Done (Unreleased): `bin/config` migrate/sync/check/show; config.yaml is authoritative, defaults.md is rendered from it. Test: `scripts/test/portable-config.test.py`.* **A portable project config** in `.aladdin/config.yaml` (budgets, context, semantic, models, provenance, memory), replacing `docs/agents/defaults.md` with a migration path.
- ✅ *Done (Unreleased): `host swap --to H` hands the sprint over from the sprint dir, `.aladdin/config.yaml` and `.agentic/logs` only; live worker worktrees or a missing piece block; SessionStart on the new host resumes with the resume rule and records the rotation. Test: `scripts/test/host-swap.test.py`.* **Host swap:** a sprint started on one host resumes on another from portable state.

### 4.10 Metrics

✅ *Done (Unreleased): the stats.md Metrics table. Test: `scripts/test/metrics.test.py`. Commits, test files added, time soft limits and semantic context served now come from the event stream. Rotations/compactions are counted from SessionStart events. Context-window share divides the context peak by `AGENTIC_CONTEXT_WINDOW` (`context.window` in config.yaml; hosts do not report the window, so the human sets it; default 200000).*

The run report should add:
- time soft preemptions;
- sprint admission closures and rollovers;
- context peak and context-window share;
- context rotations and compactions;
- breaches by role and by class;
- p50/p90/p95 task duration and tokens;
- tests added and commits produced;
- semantic context served and full-repo scans avoided.

Derived metrics:
- tokens per accepted task and per accepted story;
- first-pass rates for RED, GREEN and gates;
- hard-breach waste;
- cache-hit and churn ratios;
- replan rate.

No extra model calls are made just for telemetry.

### 4.11 Rebrand and product surface (last)

- ✅ *Done (Unreleased): `bin/aladdin` (`"<intent>"`, `run`, `status`, `resume`, `report`, the semantic queries, every aloop tool) and the `/aladdin` skill, linked and copied by the installers beside `aloop` / `agentic`. Test: `scripts/test/aladdin-cli.test.py`. The README is reordered product-first (test: `scripts/test/readme.test.py` checks the §4.11 order, that every tool it names exists, the install names, every link and that each demo figure is one §0 records) and the site is `docs/index.html` (one self-contained page: original lamp mark, the RED explainer, the audit tree; test: `scripts/test/site.test.py` checks every time/cost number against §0). Turning GitHub Pages on (Settings → Pages → deploy from branch, `/docs`) is the owner's switch.*
- **New entry points:** `/aladdin` and an `aladdin` CLI (`"<intent>"`, `run`, `status`, `resume`, `report`, plus the semantic queries). ✅ *Natural-language auto-trigger inside hosts (Unreleased): `bin/intent-trigger` on UserPromptSubmit (Claude Code, Codex via hooks.json; OpenCode via `chat.message`) adds the `/aladdin "<intent>"` context when a plain build/fix request lands in an aloop project with no sprint running; quiet for questions, slash commands, short replies, a live sprint lock and non-aloop repos; off with `ALADDIN_AUTO_TRIGGER=off` or `intent: {auto_trigger: false}`. Test: `scripts/test/intent-trigger.test.py`. Reason: the intent is the product's entry point, so typing it should be enough.*
- **Compatibility aliases for one migration window:**
  - `/agentic-agile:init`
  - the `agentic` command
  - the `agentic-agile:*` agent prefixes
  - `.agentic/` alongside `.aladdin/` (read as the run-data dir until `aloop layout migrate`; M1)
- **README reordered, product first:**
  1. Intent is all you need
  2. Demo
  3. Test-Driven Agents
  4. How the loop works
  5. Why failing first matters
  6. Models explore. Gates decide.
  7. Install / Run
  8. Supported harnesses
  9. Evidence
  10. Audit
  11. Architecture
  12. Limitations
- **A site / GitHub Pages** with an original lamp motif (no Disney visual language), the RED explainer ("Give the agent something to make false.") and the audit tree.
- **Evidence:** every public claim links to a reproducible measured run. ✅ *(Unreleased) `docs/evidence/README.md` is the ledger: one row per live run with intent, plugin version, checks, wall, cost, wakes, plugin reads, gate blocks and the `make e2e-live` command; `bin/run-summary` writes each run's `summary.json` and its row from the session's `result` lines and the gate log (called by `e2e-live.sh`). The README demo table and the site link every figure to it. Test: `scripts/test/evidence.test.py` (a public figure without a row fails).*

### 4.12 Mission layer: Missionary Aladdin Genie (M9)

*Source: the 17 Oct 2026 voice-session spec ("Missionary Aladdin Genie"). Its **Decision** and
**Confirmed** items are requirements below; each **Proposed** item is a call recorded here with
a one-line reason. Status per task: the table at the end of this section.*

**What it is.** A mission layer stacked on Aladdin. The human sets a **mission** (bigger than a
goal: "the world's best to-do app with the best user experience"), a budget and a cadence,
confirms the starting baseline and the direction, and sets and forgets. Fixed roles research
the market, measure the product, build a verifiable evaluation layer from user journeys,
derive the next **goal** from the gap list, and hand it to Aladdin as intents. When a
re-evaluation shows the goal is met, the loop goes back to the mission and raises the bar.
The loop is IBM's autonomic MAPE-K: *monitor* (State Assessor) → *analyze* (Evaluator) →
*plan* (Mission Planner) → *execute* (an Aladdin sprint) over a shared *knowledge* base
(`docs/agents/mission/`). Usage telemetry (Google Analytics) informs goals and is never a gate.

**Scope against §1.** The out-of-scope list stands, read this way: *personas invented per
mission* stay out, because the six mission roles are fixed and the same for every mission;
*a Mission OS* stays out, because this layer is a deterministic loop that turns one software
product's mission into Aladdin intents, with no workflow engine or scheduler of its own (it
runs on the host's cron) and no non-software missions; *harness or workflow synthesis* stays out.

**Requirements (Decision / Confirmed).**
1. The human's role is a high-level mission, a budget, a cadence, confirming the baseline and
   direction in plain language. The human never writes or edits a metric or a journey.
2. The baseline comes first, immediately after the mission is set, and is confirmed by the human.
3. Baseline, current state and evaluation are done by dedicated roles in a fixed structure:
   market/product research, current state, evaluation (plus the Ethics Reviewer, confirmed).
4. Evaluation is verifiable and reproducible, built on **user journeys**, run only through
   pinned scripts, and every journey and metric cites its research source.
5. Lifecycle: mission → baseline + definition of best → evaluation layer → sprints →
   re-evaluate after each epic/sprint cycle and on the cron → compare with the baseline → goal
   met: back to the research board, "what is best that the user is not even thinking of" →
   new goal. Everything is capped by budget, time and constraints, and by gates outside the model.
6. Usage is captured from day one through Google Analytics, set up by the agents through its
   API; no self-hosted servers, no other SaaS. It is opt-in, with the human's consent and API
   access. Live usage feeds research and goal-setting (most of a later goal, once there is
   usage) and is never a gate; user telemetry is consented and anonymized before an agent sees it.
7. Standing principles in every goal, in two variants (autonomous, spoon-fed): IBM's autonomic
   loop as a native principle, grow adoption and usage, keep it simple, and more; they create
   tension that drives the flywheel (Amazon's flywheel and Leadership Principles as the influence).
8. Ethics is checked when the mission is set and on every derived goal, in three tiers: clear
   (proceed), gray (pause and ask; the consent lives inside that goal with its reason and does
   not carry over), hard line (illegal, deceptive or harmful: no consent unlocks it).
9. Every element has a deterministic check, enforced outside the model.
10. It runs inside Claude Code (and every host Aladdin runs on).

**Calls on the Proposed items** (one line each; changeable with evidence):
- Name: "mission layer" in docs, `aloop mission` on the command line. Reason: plain words; the product keeps Aladdin.
- Fixed point: the mission, mode, budget caps and constraints change only with a new human approval of the new content; goals change every cycle. Reason: it makes `mission check` deterministic.
- Roles: Market Researcher, Product Tester, State Assessor, Evaluator, Mission Planner, Ethics Reviewer; each writes one schema-checked artifact; a builder never grades its own work. Reason: the spec's structure, enforceable by path guards.
- Baseline: a script plus pinned versions, measured twice within a tolerance; frozen and versioned once confirmed; a replacement needs evidence that the old one is invalid; kept apart from the target. Reason: requirement 4's reproducibility, made checkable.
- Baseline contents: benchmark scores and feature comparisons against rivals, keyed apart. Reason: the Product Tester's results are the rival half of "best".
- Journeys: scripted steps, pass criteria, events per step; existing journeys frozen; new ideas arrive as new journeys; retiring or weakening one needs evidence and a human approval event. Reason: the journeys are the mission's RED test, and weakening a test is never the agent's call (§7). This also settles the open "who approves weakening" question.
- The confirmed definition of best is the mission's RED: a baseline whose journeys all pass already is refused. Reason: the technical thesis, one level up.
- Subjective UX: judges on a pinned model id with a hashed rubric, at least 3 runs, scored by agreement (default 2 of 3). Reason: the gate checks the setup, never the verdict.
- Redefining best: research proposes a new baseline version with its evidence; in autonomous mode it applies after a veto window (default 24h) unless the human vetoes; in spoon-fed mode it waits for confirmation. Reason: set-and-forget has to move, and the old version stays archived, so it is reversible.
- Gap list: autonomous mode sends it straight to the Mission Planner; spoon-fed mode shows the human ranked options. Reason: the two modes' difference (Raise the Bar).
- Approval mode per goal: an autonomous mission runs its goals in Aladdin's autonomous mode, bounded by the authority record's scope (the approved mission); spoon-fed runs reviewed mode, one intent at a time. Reason: it maps the spec's modes onto §4.1 without a new mode.
- Whose "best": the app's end users, as researched; the human confirms the definition. Reason: the spec's "in terms of user's perspective", with the human confirming direction only.
- Cadence default 6h. Reason: the slower of the spoken examples (3h, 6h); fewer tokens per day.
- Budget: a USD and/or token cap is required at `mission init`, an optional deadline; the loop stops at a cap. Reason: "capped by budget", enforced outside the model.
- Telemetry: instrumentation is part of done once telemetry is consented (no journey ships without its events); usage leads goal derivation only past a minimum sample (default 50 users over 28 days) and research keeps at least a 30% share; cron runs read the Realtime API, goal decisions read settled data (≥48h old); GA4 thresholding is treated as "below the minimum sample". Reason: small numbers are noise, and GA hides them anyway.
- GA4 setup: the agents create the property, web data stream, Measurement Protocol secret and key events through the Admin API and read fixed report definitions through the Data API; the one human step is authorizing a Google account once. Opt-in consent is an event in the authority record; once other people use the app, a consent banner (Consent Mode v2) becomes a journey requirement. Reason: requirement 6 with the fewest human steps.
- Principles: Own the Outcome; Ship Small, Ship Often; Verify, Don't Assume; Raise the Bar; Disagree, Then Commit (a decision record with a reason and evidence refs); Leave It Better ("log it, don't fix it"); Know When to Stop and Ask (the §4.1 clarification table); plus Grow Adoption (real value: journeys completed, users returning; never time-on-app tricks) and Keep It Simple; the IBM loop is the engine. Tension pairs: Customer Obsession vs Frugality, Bias for Action vs Dive Deep, Think Big vs Deliver Results, Grow Adoption vs Keep It Simple. No principle can relax an invariant. Reason: the spec's list, with the edits it proposed for Aladdin.
- Deterministic checks: every row of the spec's table becomes a gate (below). Reason: requirement 9; a gate checks structure and evidence, never whether a judgment is right.
- Calls made while building M9.2/M9.3/M9.10: a tolerance is at most 25% (wider cannot tell a change from noise); a replacement baseline keeps every active journey (dropping one is a retirement); an automatic replacement never loosens the tolerance; a proposed (never frozen) journey retires with evidence alone; the gap list orders regressions first, then open gaps closest to their target (ship small), then unscored ones; human decisions are mirrored to a committed ledger. Reason for each: it closes a way to weaken the evaluation without a human, or keeps the loop cheap.
- Calls from the adversarial review of M9.2/M9.3/M9.10 (43 findings, 4 lenses, each reproduced, each now a test): gates read from the committed, hash-chained `ledger.jsonl` and never from a `status` field (it is outside the digest); the current baseline is the one the newest confirmation record names; a mission or baseline counts only under its newest approval (restoring older approved content is a rollback); confirm and apply need a `baseline.measured` record for the exact content and the veto window starts at that record; a replacement must supersede the current version; a veto binds the content, so it cannot come back under a new id; an autonomous application is recorded as the mission's, not a human's; `baseline restore` lets the human reinstate an archived version; a journey never replaces itself, its replacement is pinned at the content its retirement recorded, and an approval covers one exact (old, replacement) pair; the runner's script is pinned by sha256 and at least one tool by version (matched whole); a run that leaves out a step or scripted metric, or a NaN, is not a measurement; the RED needs a journey that demonstrably fails (a pending judge does not count); evidence must resolve. Reason: each closed a reproduced way to weaken the evaluation with no human.
- Calls from the second adversarial review of M9.0/M9.2/M9.3/M9.10 (31 unique findings, 4 lenses, each now a test): the records decide what a file cannot. A confirmation carries the journeys it froze and a retirement its replacement, so hiding or editing a file never unfreezes, unpins or un-retires a journey; a state counts only with its `state.assessed` record and a goal only with `goal.derived`/`goal.approved`; nothing decides from a broken ledger or an edited current baseline; a veto binds content in any order, and a version once confirmed comes back only by `baseline restore`. The runner pins every project file its command runs and refuses links, inline code and scripts outside the project; a judged metric pins its rubric by sha256; an unknown flag exits 64. Reason: each was a reproduced way to weaken the evaluation with no human, or a traceback where the contract is 0/2/3/64. Known gap, with M9.9 and M9.17: on a checkout without the run log, removing the newest ledger rows is not detected.

**Open questions for the owner** (not blocking; the default is in use): native voice mode (the
statement was cut off: unscoped until defined, M9.15); consent once other people use the app
(default: a consent banner journey, above); the rest of the standing principles (default: the
list above); the "my example" the first goal text refers to (not in the transcript).

**Deterministic checks** (each one `aloop mission <area> check`, exit 0 pass · 2 block · 3 needs the human):

| Element | Check |
|---|---|
| Mission | `docs/agents/mission/mission.json` is sealed (sha256); any content without a `mission.approved` event for that exact sha fails |
| Research | every claim has a source ref; every source has an archived snapshot whose sha256 matches |
| Baseline | the measure script runs twice on pinned versions and the results agree within tolerance; a confirmed version is frozen |
| Journeys and metrics | each metric has a query definition and each journey step its event; a diff that removes or weakens one needs a `journey.approved` event |
| Role separation | path guards: builders cannot write `docs/agents/mission/`, evaluators and assessors cannot write source |
| LLM judges | the setup, not the verdict: pinned model id, hashed rubric, ≥3 runs, agreement threshold |
| Telemetry | every journey step emits its event (static check), a test hit to GA's debug endpoint passes, the minimum-sample rule is code |
| Ethics | a review artifact on the mission and on every goal, a consent record bound to the goal's sha256 whenever the tier is gray, and a hard line blocks always |
| Budget and cadence | `mission tick` stops at a cap (the token ledger) and acts only when the cadence is due; per-attempt and sprint budgets stay Aladdin's |

**The loop** (`aloop mission tick`, one action from facts, like `sweep`; first match wins):
`SET_MISSION` → `CONFIRM_MISSION` (human) → `ETHICS_REVIEW` (mission) → `STOP_BUDGET` → `RESEARCH`
→ `DEFINE_JOURNEYS` → `BASELINE` → `CONFIRM_BASELINE` (human) → `ASSESS` → `EVALUATE` →
goal met? `RESEARCH` (raise the bar) : `DERIVE_GOAL` → `ETHICS_REVIEW` (goal; gray → `ASK_CONSENT`) →
`PLAN_INTENTS` (spoon-fed: `OFFER_OPTIONS`) → `SPRINT` (Aladdin) → `WAIT` until the sprint closes or
the cadence is due → `ASSESS`. A host routine runs `aloop mission due && aloop mission tick`, so a
tick that is not due spends no tokens.

**Layout.** `docs/agents/mission/`: `mission.json`, `research/`, `baseline/`, `journeys/`,
`state/`, `eval/`, `goals/`, `ethics/`, `judges/`, `telemetry/` (one JSON file per artifact,
named by its harness-issued id; schemas in `plugin/mission/schemas/`). Code: one entry point
`plugin/bin/mission` with the core in `plugin/bin/_mission.py`; each area is its own module
`plugin/mission/<area>.py`, discovered by name, and its tests are `scripts/test/mission-<area>.test.py`,
run by a Makefile glob. Every `mission.*` event type is registered in `_events.py` up front.
So parallel tracks add files and never edit a shared list.

**Tasks.** *Parallel-safe* tasks own disjoint files and can run in separate threads at once.
Each task flips only its own row here and in TRACKING.md.

| Task | What | Needs | Owns (files) | Parallel-safe | Status |
|---|---|---|---|---|---|
| M9.0 | Core: `mission` entry point, `_mission.py` (ids, canonical sha256, schema subset, approvals, snapshot), `mission init / check / approve / show / status`, event types, the test glob | — | `plugin/bin/{mission,_mission.py}`, `plugin/mission/{README.md,schemas/mission.json}`, `scripts/test/mission-core.test.py`, `_events.py` types, Makefile + `check-tracking.py` glob | — | done: `mission-core.test.py` (12 checks; every record a gate decides from is committed to the hash-chained `docs/agents/mission/ledger.jsonl`, so a fresh checkout keeps it and an edited, removed or backdated row fails the check; nothing decides from or records past a broken ledger; the lost-row cross-check is per branch; text is UTF-8 in any locale; `NO_ARGS` areas run bare) |
| M9.1 | Research: claims with source refs, archived source snapshots, `research new / claim / archive / check` | M9.0 | `plugin/mission/research.py`, `schemas/research.json`, `scripts/test/mission-research.test.py` | yes | open |
| M9.2 | Baseline: re-runnable spec (script + pinned versions), `measure` twice within tolerance, `confirm` freezes a version, `propose` a replacement with evidence, veto window | M9.0 | `plugin/mission/baseline.py`, `schemas/baseline.json`, `scripts/test/mission-baseline.test.py` | yes | done: `mission-baseline.test.py` (13 checks: forged measurements, stale or rolled-back versions, vetoed content under a new id or in another order, NaN, restore that sticks, a moved or edited current version, a broken ledger) |
| M9.3 | Journeys and metrics: steps, pass criteria, events, metric query definitions, source refs; `diff` blocks removing or weakening without approval; `run` through a pinned runner; all-green-at-baseline refused | M9.0 | `plugin/mission/journeys.py`, `schemas/journey.json`, `scripts/test/mission-journeys.test.py`, `plugin/mission/_runner.py` | yes | done: `mission-journeys.test.py` (11 checks: self-replacement, pinned replacements and retirements read from the records, hand-set status, runner pins that cover every file the command runs (no links, inline code or outside scripts), rubric pins, unknown flags) |
| M9.4 | Ethics: review per mission and goal, three tiers, gray consent bound to the goal's sha256, hard line never unlocked | M9.0 | `plugin/mission/ethics.py`, `schemas/ethics.json`, `scripts/test/mission-ethics.test.py` | yes | open |
| M9.5 | Judges: rubric setup check and an agreement verdict from recorded runs | M9.0 | `plugin/mission/judge.py`, `schemas/rubric.json`, `scripts/test/mission-judge.test.py` | yes | open |
| M9.6 | Telemetry (GA4): opt-in consent, Admin API setup, Measurement Protocol debug hit, fixed Data API reports (+ realtime), minimum-sample rule, static emit check; network faked in tests | M9.0 | `plugin/mission/telemetry.py`, `schemas/telemetry.json`, `scripts/test/mission-telemetry.test.py`, `scripts/test/fixtures/fake-ga.py` | yes | open |
| M9.7 | Principles in two variants with tension pairs; `principles show --mode`, hashed and injected into goals | M9.0 | `plugin/mission/principles.{md,py}`, `scripts/test/mission-principles.test.py` | yes | open |
| M9.8 | Loop: `tick` (the state machine above), `due` (zero-token cadence check), `cron` (host routine or crontab line), budget caps from the token ledger | M9.0 | `plugin/mission/{tick,due,cron}.py`, `scripts/test/mission-tick.test.py` | yes | open |
| M9.9 | Roles and wiring: the six personas, hook matchers, model routes, host install, eval suites, role counts in docs; `gate-mission-scope` (role path guards) and `gate-mission-artifact` (a role's artifact passes its check at stop) | M9.0 | `plugin/agents/` (6 new files), `plugin/hooks/hooks.json`, `plugin/hosts/`, `plugin/evals/`, `plugin/bin/gate-mission-*`, role counts in README / OVERVIEW / plugin docs | yes (the only track touching shared lists) | open |
| M9.10 | Assess and evaluate: the State Assessor's report (numbers only, from pinned scripts) and the Evaluator's gap list against the baseline and rivals, with `goal_met` | M9.2, M9.3, M9.5 | `plugin/mission/{assess,evaluate}.py`, `schemas/{state,eval}.json`, `scripts/test/mission-evaluate.test.py` | after its needs | done: `mission-evaluate.test.py` (8 checks); judged metrics come from `judge.verdict(cwd, rubric, journey_id, rubric_sha256=…)` (M9.5) for the rubric content the journey pinned and stay pending until a pass or fail verdict; every input is the content its record covers (state `state.assessed`, baseline confirmation, frozen journeys, goal `goal.derived`/`goal.approved`). Open: rival values keyed per benchmark in the baseline, so the gap list compares against rivals (review finding) |
| M9.11 | Goals and the Aladdin handoff: top gaps → a goal → intents (`intent` source `mission`), ethics and principles applied, mode → autonomy; `events trace` reaches goal → mission | M9.4, M9.7, M9.10 | `plugin/mission/goal.py`, `schemas/goal.json`, `plugin/bin/intent` (source list), `plugin/bin/events` (trace) | after its needs | blocked |
| M9.12 | The `/aloop:mission` skill: the supervisor's playbook for the tick loop | M9.8, M9.9 | `plugin/skills/mission/` | after its needs | blocked |
| M9.13 | End to end: an offline scripted to-do-app mission (mission → baseline → eval → goal → intent, zero tokens), then a live `make e2e-mission YES=1` | M9.10–M9.12 | `scripts/test/mission-e2e.test.py`, `scripts/smoke/e2e-mission.sh` | after its needs | blocked |
| M9.14 | GA4 live on the owner's account (one Google sign-in) | M9.6 | — | — | needs the owner |
| M9.15 | Native voice mode | the owner's scope (spec cut off) | — | — | open question |
| M9.16 | Docs: OVERVIEW, README, CHANGELOG, site | M9.13 | shared docs | — | blocked |
| M9.17 | Approval provenance: an approval names the captured human prompt (the UserPromptSubmit log), so a supervisor cannot approve its own mission change; the same for a well-formed row appended to `ledger.jsonl` (the chain catches edits, removals and backdating, not a forged append). Reason: found while building M9.0 and in the M9.2/M9.3 review, an approval is only as strong as `--by` | M9.0 | `plugin/bin/_mission.py` (`approve`), its test | yes | open |

**Parallel tracks** (one thread each, from M9.0 on master): (1) Research + Judges, M9.1 + M9.5;
(2) Baseline + Journeys → Assess/Evaluate, M9.2 + M9.3 → M9.10; (3) Ethics + Principles → Goals,
M9.4 + M9.7 → M9.11; (4) Telemetry, M9.6 → M9.14; (5) Loop → skill, M9.8 → M9.12; (6) Roles and
wiring, M9.9. M9.13 and M9.16 close the milestone. **Exit:** M9.13's offline run passes in CI and
one live mission cycle is measured in `docs/evidence/`.

## 5. Delivery sessions

Each session starts from accepted git state and its own contract, ends with accepted commits plus a handoff, and rotates context.

| # | Session | Exit criterion |
|---|---|---|
| 1 | Conformance fixtures around current behaviour, plus canonical IDs and the reference remap (§4.5) | Existing behaviour passes; accepted artifacts have stable identity |
| 2 | Budget semantics changes (§3.1–3.3, 3.5) and the sprint aggregate budget / admission control (§4.2) | Synthetic workers hit every threshold with the expected state and event transition |
| 3 | Canonical handoff, compaction detection, rotation and resume rule (§4.4) | Sprint N+1 succeeds in a fresh session from artifacts and the handoff only |
| 4 | Breach classification, failure envelope, grace rule and nudge, lesson candidates (§3.4, §4.3) | A hard breach never ordinary-retries; the nudge expires on schedule |
| 5 | Intent Contract, clarification policy and autonomous mode (§4.1) | A one-line intent runs in both modes |
| 6 | Semantic execution map (§4.6) | A worker starts from `target`/`context`, including for a file that doesn't exist yet |
| 7 | Event stream, rationale, authority and lineage query (§4.7) | Commit → intent traced without transcripts |
| 8 | Provider seam (§4.8) | Planner consumes external evidence; running without the provider still works |
| 9 | HostAdapter extraction, capability matrix, portable config, host swap, metrics (§4.9–4.10) | Two or more hosts pass the same conformance suite |
| 10 | Rebrand, README, site, migration docs (§4.11) | Public claims are backed by measured runs |

## 6. Conformance suite (every host)

1. An intent is admitted and normalized.
2. Planner artifacts are schema-valid.
3. RED is rejected if its tests pass at first run.
4. RED cannot write production code.
5. GREEN cannot suppress tests.
6. A failed gate blocks acceptance.
7. Worktrees stay isolated.
8. An accepted GREEN is mergeable.
9. Hard-breached work is discarded.
10. Transcript and event refs exist.
11. The lineage is complete.
12. Usage is exact or explicitly marked estimated.
13. Hard time and token limits stop the worker.
14. Admission closes deterministically.
15. A breached task is re-decomposed.
16. The handoff is produced and context rotated.
17. Compaction is used only when advertised.
18. The model route is observable.
19. Memory injection is equivalent across hosts.
20. Semantic capability gaps are explicit.

`make conformance` covers 19 of these 20 items. Items 2–9 run the real gates on a fixture crate. Items 1 and 10–14 and 16–20 run the focused test that drives the harness tool holding the behaviour. Item 15 is planner behaviour, measured by live runs (the re-plans in `docs/evidence/README.md`).

## 7. Invariants — never weakened

Existing invariants:
- RED fails for the expected behavioural reason and never implements production code.
- GREEN never weakens the RED proof.
- Writers are isolated.
- Failed gates are never accepted.
- Partial hard-budget work is never merged.
- The existing schema gate stays authoritative.
- The supervisor never bypasses the worker/gate path.
- Memory cannot relax an invariant.
- A missing deterministic backend is never described as full enforcement.

New with this roadmap:
- Canonical IDs are issued outside model text.
- The approval mode is explicit.
- The model and provider are recorded.
- Intent ancestry is preserved.
- Host limitations are explicit.
- A soft token limit never becomes acceptance or cancellation logic.
- Hard limits are enforced by the supervisor, not the worker.
- The sprint hard budget closes admission and rolls work forward.
- No sprint relies on hidden conversational context.
- Rotation is the boundary when compaction is unavailable.
- Semantic targets never modify the accepted tree.
- Deep failure evidence is on demand, not injected.
- External evidence is source-referenced and never treated as an invariant.

## 8. Tracking

Live status: [TRACKING.md](TRACKING.md), checked by `scripts/check-tracking.py` in `make test-hosts`. When the work completes, report each requirement as `IMPLEMENTED_TESTED` (with evidence), `IMPLEMENTED_UNTESTED`, `BLOCKED_HOST` or `NOT_IMPLEMENTED`.
