---
type: init
agent_role: green-worker
task_id: S<N>-<NN>/T<k>
attempt: 1
wave: <K>
issued_by: "@supervisor"
issued_at: <ISO8601>
allowed_tools: [read, write]
allowed_paths: ["<production files from tasks.md>", "<test-file-path>"]
---
# Mandate
Make task T<k>'s tests pass by filling the scaffolded SUB-AGENT-TODO bodies.

# Scope
## May
Edit production files named in tasks.md; delete obsolete shims in the test file;
touch a two-way production file the plan missed by declaring it under `### Scope exemptions`
in your output.md block (`- <path>: <reason>`, at most 3).
## May Not
Add tests; refactor unforced code; touch out-of-scope files; suppress a test.

# Inputs
- sN-NN-<slug>/plan-ready.md  (this task's section)
- the scaffolded stubs (SUB-AGENT-TODO bodies)

# Acceptance
This task's tests all PASS; no regression; diff in tasks.md scope; fmt/lint/test/
coverage gates green.
