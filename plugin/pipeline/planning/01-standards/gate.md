# Gate — 01-standards (orchestrator acceptance)

Checks:
- [ ] Detected language matches the repo's actual sources.
- [ ] Every rule line cites a real file (CLAUDE.md Rn, rules/<lang>/...).
- [ ] The cross-cutting gate matrix is present (fmt/lint/test/coverage/etc.).

Failure verb: RETRY (missing/incorrect citations).
Enforced by: libexec/gate-standards-cited (grep for dangling citations).

Self-check (v0.2): the worker runs `selfcheck` (this same gate) BEFORE reporting
done — nothing advances past a failing self-check. The SubagentStop hook runs the same
gate as the backstop.
