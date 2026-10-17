# agentic-agile plugin — Dev Log

## Working State
**Session:** 1 | **Date:** 2026-06-07

### Active Task
Build the `agentic-agile` Claude Code plugin from the DESIGN package.
- [x] Resolve OPEN-1/2/3/4/7/8 (all at recommended defaults)
- [x] TARGET = new Rust workspace at `./target-demo` (per user: "create new dir here")
- [x] Extract + install `ctx-symbols` (OPEN-8)
- [x] Populate `plugin/skills/agentic-agile/SKILL.md` (OPEN-2)
- [x] Fill 4 gate bodies + shared `bin/_gatelib.sh` (Rust commands)
- [x] Fix manifest (`skills` key); confirm hooks.json per OPEN-3
- [x] Dry-run one-story sprint through RED→SCAFFOLD→GREEN→STRUCTURAL→FINAL (pos+neg)
- [x] code review (rust-reviewer + code-reviewer) → fixed all CRITICAL/HIGH

### Key Files (current shape)
- **`plugin/skills/agentic-agile/SKILL.md`** — canonical supervisor playbook: 2-stage
  planning, 2-phase TDD, data-plane writers, §12 corrections, escalation, guardrails.
- **`plugin/tools/ctx-symbols/`** — harvested Rust CLI (`count`/`locate`/`search`/`conflicts`).
  17 tests green, clippy clean, pinned to Rust 1.77. Installed at `~/.local/bin`.
- **`plugin/bin/_gatelib.sh`** — shared gate helpers (md-db fallback, standards-matrix
  runner, ctx-symbols detection, diff-scope, suppression grep).
- **`plugin/bin/gate-{red,scaffold,green,structural-integrity,final}-verify`** — filled
  for Rust; matrix read from standards.md, not hardcoded.
- **`target-demo/`** — Rust crate + `docs/agents/sprint1/` sample sprint used for the dry-run.

### Decisions (resolved OPENs, all defaults)
- OPEN-2 replace · OPEN-1 dispatched scaffolder + body-guard · OPEN-3 supervisor
  self-check (`gate-stage2-complete`) · OPEN-4 per-task `T<k>/attempt-K/` · OPEN-5 fresh
  output.md/attempt · OPEN-6 MAX=3 · OPEN-7 add frontmatter · OPEN-8 extract ctx-symbols.

### Verified (dry-run, target-demo)
- RED: exit 0 (compiles vs mod-common shim, fails by assertion); exit 2 on vacuous RED.
- SCAFFOLD: exit 0 (count==1, panic+TODO, no shim); exit 2 on duplicate def and on a real body.
- GREEN: exit 0 (standards matrix fmt+clippy+test green, in scope); exit 2 on suppression and out-of-scope.
- STRUCTURAL: exit 0 clean; exit 2 on HIGH duplicate (foundation-poisoning → HALT).
- FINAL: exit 0 (matrix + all ticked + no suppression); exit 2 on unticked box.
- stage2 / plan-shape / validate-artifact / ledger / standards-cited: all pass; md-db
  absent → WARN+fallback (never a false block, never a silent pass).

### Watch Out
- `md-db` is now **vendored** at `plugin/tools/md-db/` (AGPL-3.0) and built by
  `install.sh`; no longer an external prereq. `make install` builds both backends.
- Build now needs **Rust >= 1.85** (vendored md-db deps use edition 2024). Local
  toolchain upgraded 1.77.2 → 1.96.0 (Homebrew) this session.
- ctx-symbols must be on the hook's PATH (`~/.local/bin` or `~/.cargo/bin`).

---

### Session 6 — 2026-06-08: Subagents-doc audit → tool allowlists (v0.3.1)
**What:** Audited the 9 agents against the "Create custom subagents" doc. Compliant on
names (match hooks.json matchers), descriptions, models, and — correctly — no use of the
plugin-ignored `hooks`/`mcpServers`/`permissionMode` fields. Gap: no `tools` limits. Added
`tools: Read, Grep, Glob, Bash, Write` (omits Edit/MultiEdit) to the 5 read/verify/artifact
agents (intake, standards, structural-reviewer, final-gate, archivist). A `code-reviewer`
subagent pass caught that my first comments overclaimed ("No Edit → cannot mutate source")
while Bash remains — corrected to name the gates as the real enforcement (defense-in-depth
only). Left the 3 builders + planner unrestricted on purpose (must edit code / revise .md
artifacts); documented planner's exemption inline. archivist now drafts to output.md, never
writes memory.md directly. Bumped 0.3.0 → 0.3.1.
**Lesson:** tool allowlists are coarse — `Bash` can still write files, so they're a speed
bump, not a wall. The deterministic gates remain the guarantee; comments must not imply
otherwise.
**Files:** plugin/agents/{intake,standards,planner,structural-reviewer,final-gate,archivist}.md,
plugin.json, Cargo.toml, CHANGELOG.md.

---

### Session 5 — 2026-06-08: `/agentic-agile:init` entry-point skill (v0.3.0)
**What:** Added a second skill `plugin/skills/init/` so the workflow has a clean
explicit command, `/agentic-agile:init`. It's a thin pointer (`disable-model-invocation:
true`) that directs the supervisor to the canonical `agentic-agile` playbook — no
content duplication, no symlink/duplicate-name risk. The main skill still auto-triggers.
Bumped 0.2.1 → 0.3.0 (new skill = MINOR). **Lesson reconfirmed:** the version-keyed
plugin cache won't refresh on the same version string — the local-path reinstall kept
serving the stale 0.2.1 cache (only the old skill) until the bump; after 0.3.0 the cache
carried both skills. Verified install loads enabled with `agentic-agile` + `init`.
**Files:** plugin/skills/init/SKILL.md (new), plugin/.claude-plugin/plugin.json,
plugin/tools/ctx-symbols/Cargo.toml, CHANGELOG.md, README.md, plugin/README.md.

---

### Session 4 — 2026-06-08: Marketplace compliance review + publish v0.2.1
**What:** Audited against the Claude Code marketplace/plugin docs. `claude plugin
validate` (v2.1.168) passed the plugin clean and the marketplace with one warning
(no description) — fixed by adding a marketplace `description` and plugin
`homepage`/`repository`. A live `claude plugin install` smoke test then caught a
loader bug static validation missed: `plugin.json` declared `"hooks":
"./hooks/hooks.json"`, but the standard path auto-loads, so it loaded twice →
`Status: failed to load`. Removed the redundant `manifest.hooks`; reinstalled and
confirmed `Status: ✔ enabled`. Bumped 0.2.0 → 0.2.1 (0.2.0 shipped broken), replaced
the v0.2.0 tag with v0.2.1, pushed. Verified install from GitHub end to end.
**Files:** plugin/.claude-plugin/plugin.json, .claude-plugin/marketplace.json,
plugin/tools/ctx-symbols/Cargo.toml, CHANGELOG.md.
**Install:** `claude plugin marketplace add adeelahmad/agentic-agile` then
`claude plugin install agentic-agile@agentic-agile-marketplace`.

---

### Session 3 — 2026-06-08: Eval suites for the skill + all 9 agents
**What:** Added agentskills.io-style evals across the plugin. Expanded the skill suite
`plugin/skills/agentic-agile/evals/evals.json` from 3 → 7 cases (happy/edge/negative +
a spec-file fixture). Authored 9 per-agent suites at `plugin/evals/agents/<role>.json`
(3 cases each — happy/edge/negative, the negative case probing each agent's hard limit;
101 assertions total) and `plugin/evals/README.md`. Built a shared harness in
`scripts/eval/`: `run-evals.sh` (with_skill vs without_skill via `claude -p
--append-system-prompt`, gated behind `--yes`), `grade.py` (strict LLM judge, `--dry-run`
plumbing mode), `aggregate.py` → `benchmark.json`, `_extract.py`, and
`validate-suites.py`. Makefile gained `eval-validate` (no tokens) + `eval SUITE=…`.
**Decisions:** agent evals live in `plugin/evals/`, NOT `plugin/agents/` — avoids any
chance of a stray file being parsed as a bogus agent. Live runs NOT executed (token
cost; await user OK). File paths in evals.json are skill-root-relative (agentskills.io).
**Verified:** `make eval-validate` OK (10 suites); shellcheck clean; all .py compile;
grade(--dry-run)→aggregate plumbing proven on synthetic data; plan-mode run exits 0
without spending tokens. `eval-workspace/` git-ignored.
**Files:** plugin/evals/* (new), plugin/skills/agentic-agile/evals/{evals.json,files/spec.md},
scripts/eval/* (new), Makefile, .gitignore.

---

### Session 2 — 2026-06-08: Vendor md-db so `make install` is self-contained
**What:** `make install` failed (cargo 1.77 couldn't read a v4 Cargo.lock; md-db was an
unbuildable external prereq). Vendored `decisiongraph/md-db-rs` into `plugin/tools/md-db/`
(AGPL-3.0, own LICENSE), wired `install.sh` + `Makefile` to build+install it from source.
Upgraded Rust 1.77.2→1.96.0 (vendored deps need edition 2024); pinning to MSRV 1.77 was
whack-a-mole, so upgrade was the durable fix. All 4 plugin KDL schemas parse + validate
under the vendored binary; md-db unit tests green; shellcheck/json clean.
**Files:** plugin/tools/md-db/ (new, vendored), plugin/tools/install.sh, Makefile,
README.md, plugin/README.md.
**License note:** repo is MIT + one vendored AGPL-3.0 subtree (disclosed in README).
- Gate diff-scope uses the working tree unless `BASE_REF` is set; the supervisor should
  set `BASE_REF`, `SCOPE_GLOBS`, `TEST_GLOBS`, `SCAFFOLD_SYMBOLS`, `STANDARDS_FILE`,
  `ATTEMPT_DIR`, `REPO_DIR` per dispatch (contract documented in `bin/_gatelib.sh`).

---

## Review findings — disposition
**Fixed (all CRITICAL + HIGH):** impl-block over-count in `count` (broke count==1);
panicking `Default` impl removed; JS/TS const+function double-count; unguarded byte-slice;
`--tree` with no value now errors; `gate-standards-cited` `set -e` foot-gun + silent pass
(now a real citation check); `changed_paths_filter` pipefail `|| true`; `symbol_count`
fallback guard; awk heading-match consistency; `type:` checks scoped to the frontmatter block.
**Deferred (MEDIUM/LOW, non-blocking):** per-symbol source clone perf (small trees);
`is_excluded` substring on dirs named dist/build/target (documented); body-slice standalone
re-parse stability; JSON path sed escaping (impossible on unix paths). assert_no_suppression
syntactic gap is documented as a known limitation in SKILL.md.

## Milestones
- [x] Plugin builds, ctx-symbols installs, all gates verified pos+neg on a dry-run sprint.
- [ ] Install `md-db` and re-verify artifact validation with the real backend.
- [ ] Live Claude Code session test of the hook wiring (offline gate tests done).

## v0.2 — lineage, retrospective/memory, review fixes

Review dispositions (from the gap analysis):
- Added the two missing manifests (plugin.json + marketplace.json) — the install blocker.
- Rewired final-gate to SubagentStop (was Stop; it is dispatched as a sub-agent).
- gate-scaffold-verify: symbols now come from .agentic/scaffold-symbols, and shim
  detection uses the `// agentic:shim` marker (no longer collides with tests/common).
- Added .agentic/task.env so the gates get their per-task contract for real (closes the
  env-injection gap; _gatelib sources it).

New capability:
- Task-scoped lineage (bin/lineage) + the every-planning retrospective/memory loop
  (archivist + memory.kdl). Lineage feeds memory; memory feeds init.md.

## End-to-end self-check dry run (v0.2)
Walked a throwaway `calc` crate through the full lifecycle, running each gate twice —
on good work (expect PASS) and on planted bad work (expect BLOCK). 18/18 matched.
Two real defects caught and fixed: (1) gate scripts shipped non-executable; (2) the
diff-scope filter counted the plugin's `.agentic/` control dir as worker scope.
Dynamic cargo/md-db/ctx-symbols branches WARN-degrade (no toolchain in the harness);
all structural checks ran for real.

## Rename to aloop (v0.11.0)
Decisions (reversible; recorded per CLAUDE.md):
- User-facing names move now (plugin, marketplace, skill, CLI, OpenCode file, docs); the
  ROADMAP had the rebrand last, but the owner asked for it first.
- Old names stay as aliases (`agentic`, `agentic init`, `agentic-init`, `agentic bind`) so
  existing prompts, scripts and worker transcripts keep working.
- On-disk layout (`.agentic/`, `docs/agents/`, `agentic/<task>` branches, `AGENTIC_*` keys)
  is NOT renamed here: it would change live projects without conformance fixtures to prove
  parity. Moved to M1, after those fixtures exist.
- GitHub repo slug stays `adeelahmad/agentic-agile` until the owner renames it (GitHub
  redirects the old URL, so docs keep working either way).
