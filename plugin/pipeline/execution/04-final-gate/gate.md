# Gate — 04-final-gate  (sprint sign-off)

Checks (libexec/gate-final):
- [ ] Full matrix (fmt/lint/test/deny/coverage/typecheck) green, zero exceptions.
- [ ] Suppression grep count == 0 (#[ignore], .skip, xit, cfg(not(test)) over asserts).
- [ ] Every story's plan-ready.md is fully [x].

Failure verbs:
  ESCALATE — a test cannot pass without a scope change -> next planning session
  ABORT    — matrix red after revision budget exhausted
Enforced by: Stop(final-gate) -> libexec/gate-final (exit 2 blocks sprint close).

Remediation (added): a FIXABLE final failure (red test, unticked box, or a matrix
failure that is NOT a scope issue) re-dispatches the failing GREEN task — it is NOT
filed as a scope change. ESCALATE is reserved for genuine scope/plan defects;
ABORT for budget exhaustion.

Self-check (v0.2): the worker runs `selfcheck` (this same gate) BEFORE reporting
done. A PASS there means this hook will pass — the hook is the backstop, not the first
line of defense.
