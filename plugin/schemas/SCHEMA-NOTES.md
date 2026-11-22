# schema notes

## Three schemas, three scopes
- agent-io.kdl          story-bound, APPEND-ONLY comms channel (init.md, output.md).
                        md-db validates the static top frontmatter; the dynamic
                        `## <task> · attempt N · <role> · <ts>` blocks are grep-checked
                        by `validate_comms` (md-db can't enumerate dynamic sections).
- planning-artifacts.kdl the playbook's stories/tasks/validate/plan/plan-ready/
                        sprint-plan. md-db validates frontmatter + fixed sections.
- ledger.kdl            execution.log line ledger. Line format enforced by libexec/.

## Tool boundary (don't fight it)
md-db can validate: frontmatter fields/types/enums/patterns, NAMED required
sections, tables, and cross-doc refs. md-db CANNOT enumerate dynamically-named
sections — so the playbook's `## Tn — Title` task headings, and the
"one bullet = one test, has a real path + fn name" rule, are checked by
libexec/gate-plan-shape (grep), not by md-db.

## DECISION required (OPEN-7)
planning-artifacts.kdl assumes YAML frontmatter on stories.md/plan.md/etc.
The playbook's current artifacts have NO frontmatter. Either:
  (a) add `--- type: stories / sprint: N ---` frontmatter to planning artifacts
      (enables md-db structural validation), or
  (b) keep planning artifacts frontmatter-free and validate them with grep-gates
      only (md-db reserved for init.md/output.md).
Not decided. agent-io.kdl works either way.
