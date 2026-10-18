# Artifacts — 02-planner

| Direction | Artifact                                  | Validated by                          |
|-----------|-------------------------------------------|---------------------------------------|
| IN        | intake.md, standards.md                   | —                                     |
| OUT       | sprintN/stories.md                        | planning-artifacts.kdl (type stories) |
| OUT       | sN-NN-<slug>/tasks.md                      | planning-artifacts.kdl (type tasks)   |
| OUT       | sN-NN-<slug>/validate.md                   | planning-artifacts.kdl (type validate)|
| OUT       | sN-NN-<slug>/plan.md  (tests-only)         | kdl + bin/gate-plan-shape             |
| OUT       | sprintN/plan.md                            | planning-artifacts.kdl (sprint-plan)  |
| OUT       | sN-NN-<slug>/targets.json (optional)       | `targets check` (via gate-stage2)     |

Handoff to execution is blocked by bin/gate-stage2-complete.
