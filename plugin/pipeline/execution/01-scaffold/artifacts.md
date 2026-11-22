# Artifacts — 01-scaffold

| Direction | Artifact                              | Validated by                  |
|-----------|---------------------------------------|-------------------------------|
| IN        | plan-ready.md + verified RED tests    | agent-io.kdl / gate-red-verify|
| OUT       | canonical production stubs            | libexec/gate-scaffold-verify      |
| OUT       | init.md `# Scaffold` lines (appended) | agent-io.kdl (type init)      |
| OUT       | execution.log scaffold line           | libexec/gate-ledger-format        |

Stubs carry the per-symbol instructions inline as SUB-AGENT-TODO comments.
