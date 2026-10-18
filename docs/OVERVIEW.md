# What aloop is

*Product definition as of v0.11.0 (2026-10-17), written from the code, not the design docs.
**aloop** is "Aladdin's Loop", renamed from agentic-agile in v0.11.0; the old command names
still work as aliases. The 1.0 target is **Aladdin**; see [ROADMAP.md](ROADMAP.md).*

## In one sentence

**aloop is a coding-agent plugin that turns a request into a planned, test-first,
gate-verified sprint: a human approves the plan, then cheap worker agents build it in
isolated git worktrees while deterministic hooks decide what is accepted.**

> *Models explore. Gates decide.*

## The problem it solves

Coding agents are good at producing code and bad at proving it is right. Left alone they
skip tests, weaken assertions, wander out of scope, rewrite each other's work and say
"done" when they are not. Prompting harder does not fix this, because a prompt is advice.

aloop moves the rules out of the prompt and into **hooks**: small scripts the host
runs on every agent event, which can block a stop or a tool call and return the reason.
The model can ignore a skill; it cannot ignore an exit code.

## Who it is for

- Developers using **Claude Code** (reference host), **Codex** or **OpenCode** who want an
  agent to deliver a multi-story feature or a whole service, not a single edit.
- People who want to stay in the loop for *what* gets built and be absent for *how*.
- Teams that care about cost: most of the work runs on cheaper models in narrow context.

## How it works

```
 you ──► INIT ──► PLANNING (human-gated, Opus) ──► approve ──► EXECUTION (autonomous, Sonnet) ──► FINAL-GATE ──► stats + NEXT.md ──► /clear ──► next sprint
                  retrospective → intake →                     per wave, per task, own worktree:
                  standards → planner                          RED → SCAFFOLD → GREEN → STRUCTURAL-REVIEW
```

1. **Init** (`/aloop:init`) confirms limits, models, `.gitignore`, whether
   `docs/agents/` is tracked and the commit author, then writes `docs/agents/defaults.md`.
2. **Planning** — with the human present. The supervisor *dispatches* (never writes):
   - `archivist` (retrospective) distills recurring failures into `memory.md`;
   - `intake` captures the five-part intent;
   - `standards` produces a cited coding standard;
   - `planner` writes the sprint contract and per-story `tasks.md` / `validate.md` / `plan.md`.
   The supervisor then **stops for approval**.
3. **Execution** — human absent. For each task, a worker runs in its own git worktree:
   - **RED** writes tests that must fail *by assertion* and touch no production code;
   - **SCAFFOLD** creates each planned symbol exactly once, as a stub;
   - **GREEN** makes the tests pass inside the task's scope, with zero suppressions;
   - **STRUCTURAL-REVIEW** looks for orphan, parallel or duplicate code.
   Work merges only on a passing gate. A failed gate re-prompts the worker; repeated
   failure goes to the supervisor.
4. **Time box.** Every worker attempt has soft and hard limits on tokens and minutes. At
   the hard limit the worker is stopped and the planner **splits** the task (up to
   `MAX_REPLANS`) instead of retrying it.
5. **Close.** `FINAL-GATE` runs the full matrix once per sprint. The harness writes
   `sprintN/stats.md` and `docs/agents/NEXT.md`, context is cleared, and the next session
   reloads from the handoff. Sprints are planned to be self-contained.

## What is in the box

| Part | Where | What it is |
|---|---|---|
| Supervisor playbook | [`plugin/skills/aloop/SKILL.md`](../plugin/skills/aloop/SKILL.md) | The skill the main agent runs; `init` is its entry point |
| 9 roles | [`plugin/agents/`](../plugin/agents/) | `archivist`, `intake`, `standards`, `planner` (Opus) · `red-worker`, `scaffolder`, `green-worker`, `structural-reviewer`, `final-gate` (Sonnet) |
| Pipeline source | [`plugin/pipeline/`](../plugin/pipeline/) | Per activity: `persona.md`, `init.template.md`, `artifacts.md`, `gate.md` |
| Hook wiring | [`plugin/hooks/hooks.json`](../plugin/hooks/hooks.json) | SessionStart, PreToolUse, PostToolUse, SubagentStart/Stop, Stop, UserPromptSubmit |
| Gates | [`plugin/bin/gate-*`](../plugin/bin/README.md) | 15 bash gates; exit `0` pass, `2` block with reason |
| Harness tools | [`plugin/bin/`](../plugin/bin/README.md) | `budget` (time box), `stats` (token ledger), `transcripts` (full capture), `host-adapter`, `install`, `aloop-init`, worktree helpers |
| Schemas | [`plugin/schemas/*.kdl`](../plugin/schemas/) | What `md-db validate` enforces on every planning and comms artifact |
| `ctx-symbols` | [`plugin/tools/ctx-symbols/`](../plugin/tools/ctx-symbols/) | Rust, tree-sitter symbol resolver (Rust, Python, JS/TS, Go): count, locate, conflicts |
| `md-db` | [`plugin/tools/md-db/`](../plugin/tools/md-db/) | Vendored Rust markdown-as-database validator (AGPL-3.0 subtree) |
| Other hosts | [`plugin/hosts/opencode/`](../plugin/hosts/opencode/), `bin/install` | Generates agents/skills for Codex and OpenCode; same gates via `host-adapter` |
| Evals | [`plugin/evals/`](../plugin/evals/), [`scripts/eval/`](../scripts/eval/) | Per-agent eval suites + grader/aggregator |

## What it leaves on disk

| Artifact | Scope | Purpose |
|---|---|---|
| `.aladdin/config.yaml` | project | The portable config (budgets, context, semantic, models, provenance, git); authoritative when present (`aloop config migrate`) |
| `docs/agents/defaults.md` | project | Limits, models, author — set at init, or rendered from `.aladdin/config.yaml` |
| `docs/agents/memory.md` | project | Lessons promoted by the retrospective; injected into contracts |
| `stories.md`, `sprintN/plan.md` | sprint | Stage-1 contract |
| `sN-NN-<slug>/{tasks,validate,plan}.md` | story | Stage-2 plan |
| `plan-ready.md` | story | Static RED → GREEN handoff |
| `init.md` ⇄ `output.md` | story | Append-only dispatch contract and agent report |
| `execution.log` | sprint | One-line state ledger |
| `sprintN/stats.md`, `docs/agents/NEXT.md` | sprint | Harness-written stats and the handoff |
| `.agentic/` | local | Transcripts, budgets, token ledger, worktree state (git-ignored) |

## Where it stands

**Working and tested:**
- The full planning → execution loop on Claude Code; Codex and OpenCode via generated
  agents and the same gates.
- CI on every PR: Rust fmt/clippy/tests for both backends, shellcheck on every gate, the
  ROADMAP §6 conformance fixtures (real gates on a fixture crate, 56 checks), the host tests
  (budget/worktree, Codex hooks emulation, supervisor scope, hook matchers, dispatch,
  ensure-tools, fail-closed: 73 checks; OpenCode end-to-end: 20), `claude plugin validate
  --strict`, eval-suite and version-lockstep checks.
- The north-star run, `make e2e-live YES=1 [INTENT=wordfreq]`: a one-line intent becomes
  planned, test-first, gate-verified, merged code in a real Claude Code session with the
  approval given up front. Two intents pass every check (semver parser: 11/11, ~$0.65–1.15,
  11–19 min; word frequencies with two dependent stories: 12/12, $0.86–1.56, 13–45 min). Every run so
  far has found a gate false positive that unit tests missed; ROADMAP M0.8 lists them.
- One measured real run (*TheNoteBook*, a Go backend): ~17K LOC, 322 tests, 74 commits in 6
  sprints for ~1.55M output tokens, 76% of it on Sonnet. See the
  [README](../README.md#token-efficiency-a-measured-run).

**Known limits:**
- Suppression detection is syntactic; a weakened assertion is caught only indirectly
  (RED invariant + diff scope).
- Without `ctx-symbols` / `md-db` the planning gates degrade to grep (WARN, never silent).
- The default standards matrix targets Rust; other stacks need `standards.md` and gate edits.
- The soft token limit tells workers to wrap up, budgets meter only the three build roles,
  and every hard breach is treated as "task too large". The roadmap changes all three.
- One public measured run; no live-session test in CI.

## The docs, and which ones are current

| Doc | Status | Read it for |
|---|---|---|
| [README](../README.md) | current | Install, usage, measured run |
| **OVERVIEW.md** (this) | current | What the project is |
| [ROADMAP.md](ROADMAP.md) | current | Where it is going (aloop → Aladdin 1.0) |
| [plugin/README.md](../plugin/README.md), [plugin/bin/README.md](../plugin/bin/README.md) | current | Operating the plugin; gate contract |
| [plugin/STRUCTURE.md](../plugin/STRUCTURE.md) | current | How the plugin tree is organised |
| [CHANGELOG.md](../CHANGELOG.md) | current | What changed per version |
| [DESIGN.md](DESIGN.md), [ARCHITECTURE.md](ARCHITECTURE.md), [KICKSTART.md](KICKSTART.md) | historical (pre-v0.2) | The original design package; numbers and paths are out of date |
| [PLATFORM-NOTES.md](PLATFORM-NOTES.md) | reference | Host mechanics the design relies on |
| [DEVLOG.md](../DEVLOG.md) | historical | Build journal through v0.2 |
