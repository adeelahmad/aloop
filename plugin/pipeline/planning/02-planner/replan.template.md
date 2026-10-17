---
type: init
agent_role: planner
task_id: <sprint-id>
attempt: <N>
mode: replan
issued_by: "@orchestrator"
issued_at: <ISO8601>
allowed_tools: [read, write]
allowed_paths: ["docs/agents/sprint<N>/**"]
---
# Mandate
Sprint <N> is PAUSED mid-execution: the tasks below hit their HARD budget and were
killed. Confirm each task's breach class, then act on it (split, reframe, repair the
contract, reorder, adjust commands, isolate the service). Do not retry them as-is.

# Status
## Done (DO NOT TOUCH — tasks.md sections, plan.md bullets, plan-ready.md ticks stay as-is)
- <SN-NN Tk> — merged green

## Killed at hard limit (SPLIT these)
- <SN-NN Tk> — <tokens used>/<tokens_hard> tokens, <minutes>/<time_hard> min, role <red|green>;
  breach class (harness): <CLASS> — <evidence>   (`budget classify <task>`);
  failure envelope: `budget envelope <task>` (limit, observed, last gate, changed files, refs);
  last output.md block: <path>#<header>; transcript: .agentic/transcripts/<task>/

## Not started (may re-size if the split changes the dependency graph)
- <SN-NN Tk>

# Budget in force
tokens_soft=<n> tokens_hard=<n> time_soft=<n>m time_hard=<n>m

# Scope
## May
Edit tasks.md / validate.md / plan.md of the OPEN tasks' stories (only the OPEN tasks'
sections) and sprint<N>/plan.md waves.
## May Not
Touch a DONE task; move work into a later sprint; write code or tests.

# Acceptance
Every killed task is replaced by >= 2 smaller tasks, each with its own plan.md tests
and validate.md rubric; sprint<N>/plan.md waves updated; gate-stage2-complete passes.
