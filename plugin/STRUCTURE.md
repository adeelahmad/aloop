# aloop plugin — structure

Read this file, then walk `pipeline/`. The tree is the documentation.

## Layout

    .claude-plugin/plugin.json   installable manifest (entrypoint)
    pipeline/                    THE SOURCE OF TRUTH, organized phase -> activity
      planning/                  interactive, human-gated
        00-intake/
        01-standards/
        02-planner/
      execution/                 autonomous, hook-enforced
        00-red/
        01-scaffold/
        02-green/
        03-structural-review/
        04-final-gate/
    schemas/                     md-db KDL schemas — what `md-db validate` enforces
    hooks/hooks.json             wiring: which gate fires on which platform event
    bin/                         gate scripts (thin wrappers over `md-db validate` + greps)
    agents/                      plugin-standard agent files, ASSEMBLED from pipeline/*/persona.md

## Every activity folder holds the same four files

    persona.md         WHO  — the orchestrator persona dispatched for this activity
    init.template.md   IN   — the init.md contract template handed to that agent
                              (orchestrator/scaffolder fills it; read-only to the agent)
    artifacts.md       I/O  — what this activity READS, WRITES, and what VALIDATES each
    gate.md            CHECK— the orchestrator's acceptance checklist + failure verbs
                              + which hook enforces it

So: to understand RED, open `pipeline/execution/00-red/` and read four short files.

## The two phases (mapped to ICE: Intent · Context · Expectations)

- PLANNING is interactive; the human is present. intake -> standards -> planner.
    Intent       = intake.md + stories.md (the five-part outcome)
    Expectations = validate.md + Definition of Done (user-terms done-ness)
    Context      = standards.md, fed progressively via init.md / scaffold
- EXECUTION is autonomous; no human. Per wave: RED -> SCAFFOLD -> GREEN ->
  STRUCTURAL-REVIEW, then once per sprint FINAL-GATE.
    The human owns Intent + Expectations at planning; HOOKS enforce them per
    attempt during execution — presence-in-the-loop, made mechanical.
- Failures the supervisor can't resolve in the revision loop surface at the
  NEXT planning session (see DESIGN.md §4 / §9).

## Validation model (important — know the tool boundary)

`md-db validate <dir> --schema schemas/<x>.kdl` enforces YAML frontmatter, field
types, enums, patterns, required sections, and cross-doc reference integrity.
It CANNOT enumerate dynamically-named sections (the playbook's `## Tn — Title`
headings). So validation is two-layered:
  - md-db (schemas/*.kdl) : frontmatter + fixed sections + refs    [structural]
  - bin/ grep-gates        : the dynamic `Tn` body shape + invariants [pattern]
init.md and output.md are NEW artifacts with fixed sections -> fully md-db-validated.

## v0.2 additions

    .claude-plugin/plugin.json   the installable manifest (now present)
    skills/aloop/SKILL.md the supervisor's playbook (canonical)
    pipeline/planning/retrospective/  runs FIRST every planning session (the archivist)
    agents/archivist.md          read-only; distills memory from the transcripts
    agents/{market-researcher,product-tester,state-assessor,evaluator,mission-planner,ethics-reviewer}.md
                                 the mission layer's six fixed roles (ROADMAP §4.12; mission/roles.json)
    schemas/memory.kdl           validates docs/agents/memory.md
    bin/transcripts                  full capture (stage-in / record / prompt / snapshot / stop)
    tools/ctx-symbols/           the symbol backend (build + install)

Transcripts: a global append-only `global.jsonl` + per-task `events.jsonl` (full tool
payloads + prompts) + `transcript.jsonl` (the complete session snapshot). Each sub-agent
gets a READ-ONLY task slice staged into its worktree (`.transcripts/`) at SubagentStart;
every tool call and prompt is recorded; the full session is snapshotted on stop. The
supervisor reads the global store; the retrospective distills it into memory.md, which
is injected (role-scoped) into each init.md `# Memory` section.
The gates read their per-task contract from `.agentic/state/tasks/<TASK_ID>.env` (supervisor-written, main tree).
