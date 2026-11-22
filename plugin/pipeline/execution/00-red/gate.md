# Gate — 00-red  (supervisor verification)

Checks (libexec/gate-red-verify):
- [ ] Every new test is in the FAIL list.
- [ ] No previously-passing test flipped to fail.
- [ ] git diff --stat touches only test files + the shim module.

Failure verbs:
  REJECT  — wrote production code, or a new test passed (exercises nothing)
  RETRY   — transient/incomplete; re-spawn with feedback appended to init.md
Enforced by: SubagentStop(red-worker) -> libexec/gate-red-verify (exit 2 blocks).

Note (v0.2): RED compile shims carry `// agentic:shim`; the scaffold gate keys on that
marker, so a normal `tests/common/mod.rs` helper is never mistaken for a shim.

Self-check (v0.2): the worker runs `selfcheck` (this same gate) BEFORE reporting
done. A PASS there means this hook will pass — the hook is the backstop, not the first
line of defense.
