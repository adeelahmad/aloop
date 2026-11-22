# Artifacts — 00-red

| Direction | Artifact                          | Validated by                   |
|-----------|-----------------------------------|--------------------------------|
| IN        | init.md (-> plan.md task section) | agent-io.kdl (type init)       |
| OUT       | <test files>, mod common shims    | libexec/gate-red-verify            |
| OUT       | output.md                         | agent-io.kdl (type output)     |

After all wave tasks report clean RED, the SUPERVISOR writes plan-ready.md.
