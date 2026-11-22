# Gate — 03-structural-review  (supervisor verification)

Checks (libexec/gate-structural-integrity):
- [ ] Zero orphan modules.
- [ ] Zero parallel implementations of a single abstraction.
- [ ] Zero duplicate helpers under different names.

Failure verbs:
  ESCALATE — a finding poisons a shared foundation -> supervisor HALTs the
             dependency chain and surfaces it at the next planning session (§4)
  REJECT   — an isolated finding -> supervisor CONTINUEs other work, collects it
Enforced by: SubagentStop(structural-reviewer) -> libexec/gate-structural-integrity.

Remediation (added): an ISOLATED + FIXABLE finding re-spawns the implicated GREEN
task with feedback via init.md (RETRY) — it does NOT just "continue." Only a
foundation-poisoning finding HALTs the chain; only a truly cosmetic/accepted
isolated finding is collected-and-continued.

Self-check (v0.2): the worker runs `selfcheck` (this same gate) BEFORE reporting
done. A PASS there means this hook will pass — the hook is the backstop, not the first
line of defense.
