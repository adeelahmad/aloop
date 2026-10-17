# agentic-agile

A Claude Code plugin that packages **two-stage agile planning** (interactive,
human-gated) and **two-phase TDD execution** (autonomous, hook-enforced) into one
install. Determinism comes from hooks, not from model goodwill: every sub-agent
stop is intercepted by a gate that can block the stop and feed the failure reason
back to the supervisor.

- **Planning** (human present): intake → standards → planner produce the sprint
  contract + per-story `tasks.md` / `validate.md` / `plan.md`.
- **Execution** (human absent): per wave, RED → SCAFFOLD → GREEN →
  STRUCTURAL-REVIEW, then once per sprint a FINAL-GATE. One worktree-isolated
  sub-agent per task; merge on pass, abandon the chain on a foundation-poisoning halt.

See `skills/agentic-agile/SKILL.md` for the full supervisor playbook and
`STRUCTURE.md` for the design source layout.

## Prerequisites

| Tool | Required? | What it does | Install |
|------|-----------|--------------|---------|
| **ctx-symbols** | recommended | symbol uniqueness (`count==1`) + duplicate/parallel/orphan detection for the scaffold & structural gates | built from `tools/ctx-symbols` (see below) |
| **md-db** | recommended | validates `.md` artifacts against `schemas/*.kdl` | built from vendored `tools/md-db` (AGPL-3.0; see below) |
| **Rust toolchain** | required to install | builds both backends + runs the target repo's `cargo fmt/clippy/test/coverage` matrix | rustup (>= 1.85) |

**You don't install the backends yourself.** A `SessionStart` hook (`bin/ensure-tools`)
builds `md-db` + `ctx-symbols` from the bundled source into the plugin's data dir the
first time a session starts (in the background, ~1 min; needs a Rust toolchain), and
rebuilds them when the plugin version changes. The plugin's `bin/` is on PATH in every
session and sub-agent, and `bin/md-db` / `bin/ctx-symbols` are shims that run the real
binaries — so agents call every tool by bare name and never go looking for it.
Opt out with `AGENTIC_NO_AUTOINSTALL=1`; force a build with `ensure-tools --sync`.

If a backend is missing, planning gates WARN and fall back to grep; execution is
blocked by `gate-tooling` (which first tries to install them itself).

### Installing the backends manually (optional)

```bash
./tools/install.sh            # builds + installs ctx-symbols AND md-db to ~/.local/bin
                              # (both from source; needs a Rust toolchain >= 1.85)
```
A copy on PATH or in `~/.local/bin` is used instead of building a second one.

## Models and time boxes

| Role | Default model |
|---|---|
| Orchestrator (the skill, main session) | `sonnet` |
| Planning: intake, standards, planner, archivist | `claude-opus-5-5` |
| Execution: red-worker, scaffolder, green-worker, structural-reviewer, final-gate | `sonnet` |

Every execution-worker attempt is time-boxed on **tokens and wall clock**, each with a
soft limit (the worker is warned inside its tool results) and a hard limit (the worker
is stopped, and its task is split by the planner mid-sprint instead of retried).
Defaults: **150k / 250k tokens, 20 / 30 min** (real TDD attempts use 60–190k). At the hard limit the hook
itself stops the worker (PostToolUse `continue: false`) — no model decides the kill.

## Init, layout, stats and the handoff

`/agentic-agile:init` shows the defaults and asks you to confirm: the limits, the
models, adding `.agentic/` + `.transcripts/` to `.gitignore`, whether `docs/agents/` is
tracked in git (default **no**), and the commit author (default: **your** git identity,
no Claude co-author trailers — `gate-commit-author` enforces it). The harness saves the
answers in `docs/agents/defaults.md`.

Run data lives at one fixed path, `.agentic/` (transcripts, budget, token ledger, gate
log, state), defined once in `bin/_paths.sh` and always git-ignored. When FINAL-GATE
passes the harness writes `docs/agents/sprintN/stats.md` (tasks, attempts, gate blocks,
budget stops, re-plans, tokens for the sprint / session / project, human messages) and
`docs/agents/NEXT.md`; it shows you the totals and asks for `/clear`, and the
SessionStart hook reloads `NEXT.md` into the fresh context. `stats show` prints token
totals any time.

## Install the plugin

From a marketplace (this repo ships `.claude-plugin/marketplace.json` at its root):

```
/plugin marketplace add adeelahmad/agentic-agile
/plugin install agentic-agile@agentic-agile-marketplace
```

(For local dev, point `marketplace add` at your checkout: `./path/to/repo`.)

### Codex and OpenCode

The same plugin runs on **Codex** and **OpenCode** with every feature: skills, the 9
role agents, all gates, the time box and hard stop, worktree isolation, the token
ledger, sprint stats and the post-`/clear` handoff. Run the installer from a clone (or
from `agentic --root` once installed anywhere):

```bash
git clone https://github.com/adeelahmad/agentic-agile && cd your-project

# Codex — Codex reads this repo's .claude-plugin manifests + hooks/hooks.json natively
/path/to/agentic-agile/plugin/bin/agentic install codex      # .codex/agents/*.toml + ~/.local/bin/agentic
codex plugin marketplace add adeelahmad/agentic-agile
codex plugin add agentic-agile@agentic-agile-marketplace
#   then in Codex: /hooks → trust the agentic-agile hooks (Codex runs only trusted hooks)

# OpenCode — no bundle format, so the installer renders everything into .opencode/
/path/to/agentic-agile/plugin/bin/agentic install opencode   # agents, skills, /agentic-init, plugin
#   restart opencode; start with /agentic-init
```

`--global` installs into `~/.codex` / `~/.config/opencode` instead of the project;
`--uninstall` removes what was generated; `agentic install status` shows what's where.

| Capability | Claude Code | Codex | OpenCode |
|---|---|---|---|
| Hooks | `hooks/hooks.json` | the same file (native) | plugin → the same scripts |
| Agents | `agents/*.md` | generated `.codex/agents/*.toml` | generated `.opencode/agents/*.md` |
| Worker isolation | `isolation: "worktree"` | `agentic task-worktree` + `agentic bind` (hook-enforced) | same (plugin rewrites into the worktree) |
| Tools on PATH | plugin `bin/` | `~/.local/bin/agentic` | plugin `shell.env` |
| Token source | transcripts | rollouts (`.jsonl`/`.zst`) | plugin-written usage log |
| Hard stop | PostToolUse `continue:false` | same | `session.abort` |

Models: Codex agents use the session's model at `high` (planning) / `medium` (workers)
reasoning effort unless `MODEL_*_CODEX` is set; OpenCode agents use
`anthropic/claude-opus-5-5` / `anthropic/claude-sonnet-5` unless `MODEL_*_OPENCODE` is set
(all in `docs/agents/defaults.md`; `agentic init --apply` re-renders installed hosts).
Both are covered in CI by `make test-hosts` (Codex hook-runner emulation + OpenCode
plugin against a mock client).

## Invoking it

The `agentic-agile` skill is the supervisor — it dispatches the 9 sub-agents and
gates; you don't call them directly. Start it either way:

- **Implicitly** — it's model-invoked, so asking to build/ship/implement/fix something
  via a sprint or TDD auto-triggers it (e.g. *"build a rate limiter as a sprint, tests
  first"*).
- **Explicitly** — `/agentic-agile:init` (a thin entry-point alias; the underlying
  skill is also directly invokable as `/agentic-agile:agentic-agile`).

Planning is human-gated: the skill stops for your Stage-2 approval before it begins the
autonomous RED → SCAFFOLD → GREEN → STRUCTURAL → FINAL execution run.

## Targeting your repo

The execution gates ship wired for a **Rust** target (cargo fmt/clippy/test). The
GREEN and FINAL matrix is **read from your `standards.md`**, not hardcoded — so the
fastest way to retarget is to declare your matrix there. For a non-Rust stack, adapt
the language-specific bits of `bin/gate-red-verify` (compile-then-fail check) and the
default matrix in `bin/_gatelib.sh`.

### Gate contract (env the supervisor sets per dispatch)

All optional, each with a safe fallback (see `bin/_gatelib.sh`):
`STORY_DIR`, `REPO_DIR`, `STANDARDS_FILE`, `BASE_REF`, `TEST_GLOBS`,
`SCOPE_GLOBS`, `SCAFFOLD_SYMBOLS`, `GATE_RUN_TESTS`, `GATE_RUN_MATRIX`.

## What's in here

```
.claude-plugin/plugin.json   manifest
skills/agentic-agile/SKILL.md the supervisor playbook (canonical)
agents/<role>.md             9 dispatchable personas
hooks/hooks.json             gate wiring by agent_type (SubagentStop/Stop/PostToolUse)
bin/                         gate bodies + _gatelib.sh + log-execution
schemas/*.kdl                md-db schemas (agent-io / planning-artifacts / ledger)
tools/ctx-symbols/           the symbol backend (Rust source) + install.sh
pipeline/                    design source of truth (persona/init/artifacts/gate per activity)
```

## Status & limitations

- Gate bodies are **verified offline** (positive + negative) against a sample
  one-story sprint. The hook *wiring* should be smoke-tested in a live Claude Code
  session before relying on it in anger.
- Suppression detection is syntactic; a test weakened by deleting an assertion is
  caught by the RED-invariant + diff-scope, not by grep. See SKILL.md
  "Known gate limitations."

## License

MIT — see `LICENSE`.

## Transcripts & memory (v0.2)

- `bin/transcripts` keeps a global append-only `global.jsonl` + per-task transcripts.
  `SubagentStart` stages a READ-ONLY task slice into the worktree (`.transcripts/`),
  `PostToolUse *` records every call, `SubagentStop *` removes the slice. These hooks
  never block. The supervisor reads the global store; the planning **retrospective**
  (the `archivist`) distills it into `docs/agents/memory.md`, which is injected
  (role-scoped) into each `init.md` `# Memory` section.
- The gates read their per-task contract (`TASK_ID`, `SCOPE_GLOBS`, `SCAFFOLD_SYMBOLS`,
  `BASE_REF`, `BUDGET_*`) from `.agentic/task.env`, written by the supervisor
  into each worktree at dispatch. `.agentic/` and `.transcripts/` are git-ignored and never
  merged.
