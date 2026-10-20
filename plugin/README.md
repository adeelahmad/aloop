# aloop

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

See `skills/aloop/SKILL.md` for the full supervisor playbook and
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
| Mission layer (§4.12), planning route: market-researcher, evaluator, mission-planner, ethics-reviewer | `claude-opus-5-5` |
| Mission layer, worker route: product-tester, state-assessor | `sonnet` |

Every execution-worker attempt is time-boxed on **tokens and wall clock**, each with a
soft limit (the worker is warned inside its tool results) and a hard limit (the worker
is stopped, and its task is split by the planner mid-sprint instead of retried).
Defaults: **150k / 250k tokens, 20 / 30 min** (real TDD attempts use 60–190k). At the hard limit the hook
itself stops the worker (PostToolUse `continue: false`) — no model decides the kill.

## Init, layout, stats and the handoff

`/aloop:init` shows the defaults and asks you to confirm: the limits, the
models, adding `.agentic/` + `.transcripts/` to `.gitignore`, whether `docs/agents/` is
tracked in git (default **no**), and the commit author (default: **your** git identity,
no Claude co-author trailers — `gate-commit-author` enforces it). The harness saves the
answers in `docs/agents/defaults.md`.

Run data lives in `.aladdin/` (transcripts, budget, token ledger, gate log, state),
resolved in `bin/_paths.sh` / `bin/_paths.py` and never committed: `.aladdin/.gitignore`
keeps only `config.yaml`. A project that still has `.agentic/` keeps using it until
`aloop layout migrate`. When FINAL-GATE
passes the harness writes `docs/agents/sprintN/stats.md` (tasks, attempts, gate blocks,
budget stops, re-plans, tokens for the sprint / session / project, human messages) and
`docs/agents/NEXT.md`; it shows you the totals and asks for `/clear`, and the
SessionStart hook reloads `NEXT.md` into the fresh context. `stats show` prints token
totals any time.

## Install the plugin

From a marketplace (this repo ships `.claude-plugin/marketplace.json` at its root):

```
/plugin marketplace add adeelahmad/aloop
/plugin install aloop@aloop-marketplace
```

(For local dev, point `marketplace add` at your checkout: `./path/to/repo`.)

### Codex and OpenCode

The same plugin runs on **Codex** and **OpenCode** with every feature: skills, the 9
role agents, all gates, the time box and hard stop, worktree isolation, the token
ledger, sprint stats and the post-`/clear` handoff. Run the installer from a clone (or
from `aloop --root` once installed anywhere):

```bash
git clone https://github.com/adeelahmad/aloop && cd your-project

# Codex — Codex reads this repo's .claude-plugin manifests + hooks/hooks.json natively
/path/to/aloop/plugin/bin/aloop install codex      # .codex/agents/*.toml + ~/.local/bin/aloop
codex plugin marketplace add adeelahmad/aloop
codex plugin add aloop@aloop-marketplace
#   then in Codex: /hooks → trust the aloop hooks (Codex runs only trusted hooks)

# OpenCode — no bundle format, so the installer renders everything into .opencode/
/path/to/aloop/plugin/bin/aloop install opencode   # agents, skills, /aloop-init, plugin
#   restart opencode; start with /aloop-init
```

`--global` installs into `~/.codex` / `~/.config/opencode` instead of the project;
`--uninstall` removes what was generated; `aloop install status` shows what's where.

| Capability | Claude Code | Codex | OpenCode | DeepSeek Harness (dsh) |
|---|---|---|---|---|
| Hooks | `hooks/hooks.json` | the same file (native) | plugin → the same scripts | native plugin `hosts/dsh/aloop.mjs` → the same scripts |
| Agents | `agents/*.md` | generated `.codex/agents/*.toml` | generated `.opencode/agents/*.md` | agent presets `aloop-<role>` in the generated `aloop` profile |
| Worker isolation | `isolation: "worktree"` | `aloop task-worktree` + `aloop bind` (hook-enforced) | same (plugin rewrites into the worktree) | same, dispatched with `cwd: <worktree>` (hook-enforced: dsh cannot rewrite a call) |
| Tools on PATH | plugin `bin/` | `~/.local/bin/aloop` | plugin `shell.env` | `~/.local/bin/aloop` |
| Token source | transcripts | rollouts (`.jsonl`/`.zst`) | plugin-written usage log | plugin-written usage log |
| Hard stop | PostToolUse `continue:false` | same | `session.abort` | `agent.cancel` |

Models: Codex agents use the session's model at `high` (planning) / `medium` (workers)
reasoning effort unless `MODEL_*_CODEX` is set; OpenCode agents use
`anthropic/claude-opus-5-5` / `anthropic/claude-sonnet-5` unless `MODEL_*_OPENCODE` is set
(all in `docs/agents/defaults.md`; `aloop init --apply` re-renders installed hosts).
Both are covered in CI by `make test-hosts` (Codex hook-runner emulation + OpenCode
plugin against a mock client).

### DeepSeek Harness (dsh)

[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (`dsh`, a developer
preview; tested with `@deepseek-ai/dsh@0.2.1-rc.2`) loads plugins from profiles, so the
installer writes an `aloop` profile and a native plugin runs the gates:

```bash
npm install -g @deepseek-ai/dsh@0.2.1-rc.2
/path/to/aloop/plugin/bin/aloop install dsh        # $DSH_HOME/profiles/aloop + .dsh/skills + ~/.local/bin/aloop
export DEEPSEEK_API_KEY=sk-...                     # or DEEPSEEK_API_KEY=... in $DSH_HOME/.env
dsh --profile aloop "Use the aloop skill. Intent: <one line>"
```

- **Profile** (`$DSH_HOME/profiles/aloop/`, `DSH_HOME` defaults to `~/.dsh`): the 15 roles as
  agent presets `aloop-<role>` (each role's prompt is its agent file), `subagent` for the
  execution roles and `subagent_planning` for the planning roles, each with its group's model,
  and the plugin `hosts/dsh/aloop.mjs`, loaded in place. dsh has no project-scoped plugin
  layer, so the profile is global and its tools hold the models of the project it was rendered
  for (the plugin then routes each role to its own project's models, below); the skills go to
  `.dsh/skills` (or `$DSH_HOME/skills` with `--global`).
- **Models**: the supervisor runs on `$ALOOP_DSH_MODEL` (default `deepseek-v4-pro`; dsh has no
  `--model` flag); the roles use `deepseek-v4-pro` (strongest/strong) or `deepseek-flash`
  (economical) unless `MODEL_*_DSH` is set. Each role runs on the models of the project the
  session is in (the plugin routes its requests to `aloop models resolve <role>`), so one install
  serves every project; another project needs `aloop install dsh` there only for its skills.
- **Gates**: every block uses one of dsh's own decision points, so `enforce_gate` is native. A
  tool call is denied with the gate's reason; an invalid artifact write comes back as the
  gate's feedback; a role's stop gate steers the child back to work before dsh settles it,
  up to 3 rounds, and a block past them (or a STUCK or NEEDS-HUMAN release) is injected into the supervisor.
- **Messages and compaction**: a role is not offered `send_message` (the plugin scopes it out of
  the role's tools at its start), so its result reaches the supervisor through its stop gate and
  dsh's settlement notice; the supervisor's `send_message` reaches a running role at its next step.
  dsh compacts a session in place, and the plugin records `context.compacted` and injects the
  handoff again.
- **Cost**: `run-summary --host dsh` prices each logged call from the dated DeepSeek table in
  `bin/_prices.py`; a model with no published price leaves the cost unmeasured.
- **Unattended runs**: dsh asks before commands unless `DSH_PERMISSION_MODE=danger-full-access`
  (no sandbox, no approvals); use that only in a throw-away repo, as `make e2e-live HOST=dsh` does.
  The profile disables dsh's session-log upload (`session-log-deepseek`); set
  `DSH_TELEMETRY_DISABLED=1` to stop its telemetry too.

Covered in CI by `make test-hosts`: `dsh-plugin.test.mjs` (the plugin against an emulated
dsh context) and `dsh-runtime.test.mjs` (the real dsh runtime, pinned, driven by a scripted
model: no network, no tokens).

## Invoking it

The `aloop` skill is the supervisor — it dispatches the 9 sub-agents and
gates; you don't call them directly. Start it either way:

- **Implicitly** — it's model-invoked, so asking to build/ship/implement/fix something
  via a sprint or TDD auto-triggers it (e.g. *"build a rate limiter as a sprint, tests
  first"*).
- **Explicitly** — `/aloop:init` (a thin entry-point alias; the underlying
  skill is also directly invokable as `/aloop:aloop`).

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
skills/aloop/SKILL.md the supervisor playbook (canonical)
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
  caught by the RED-invariant + diff-scope, not by grep. See skills/aloop/reference.md
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
  `BASE_REF`, `BUDGET_*`) from `.aladdin/state/tasks/<TASK_ID>.env` in the main tree
  (`.agentic/state/tasks/` in a legacy project; both are searched), written by the supervisor
  before dispatch (a worktree `.aladdin/task.env`, or `.agentic/` in a legacy project, must match
  it). Run data (`.aladdin/`, by its own `.gitignore`, or `.agentic/`) and `.transcripts/` are
  git-ignored and never merged.
