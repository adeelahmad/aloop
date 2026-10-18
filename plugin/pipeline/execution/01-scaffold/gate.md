# Gate — 01-scaffold  (supervisor verification)

Checks (bin/gate-scaffold-verify):
- [ ] Every symbol the RED tests reference now exists as exactly ONE definition.
- [ ] Every stub body is panic + SUB-AGENT-TODO (no real implementation).
- [ ] No `mod common` shim remains.
- [ ] No duplicate definition of any symbol anywhere in the tree.

Failure verbs:
  REJECT — a body was implemented, or a symbol was defined twice
Enforced by: SubagentStop(scaffolder) -> bin/gate-scaffold-verify (exit 2 blocks).

Added check: scaffolding is idempotent — a symbol that already has a real body is
left untouched (no clobber); only new symbols are stubbed.

Fixed (v0.2): symbol set comes from the worktree's `.aladdin/scaffold-symbols` (or `.agentic/`; scaffolder-written),
not from plan-ready.md test paths; shim detection keys on the `// agentic:shim` marker,
not any `mod common`.

Self-check (v0.2): the worker runs `selfcheck` (this same gate) BEFORE reporting
done. A PASS there means this hook will pass — the hook is the backstop, not the first
line of defense.
