# Gate — 00-intake  (orchestrator acceptance)

Checks (the five-part Intent must be complete — a missing part is a hole the agent fills):
- [ ] What's wanted is one paragraph, in the user's terms, and stack-free.
- [ ] Constraints listed.
- [ ] Failure scenarios listed (where the result is NOT done).
- [ ] Success scenarios listed (where it IS).
- [ ] Connections listed (what a change here would affect).
- [ ] In-scope / out-of-scope explicit; no blocking open question.

Failure verb: RE-PLAN (back to the human).
Enforced by: the human. Planning is interactive; no hook fires here.

Self-check (v0.2): the worker runs `selfcheck` (this same gate) BEFORE reporting
done — nothing advances past a failing self-check. The SubagentStop hook runs the same
gate as the backstop.

An unanswered `ask` question in the Intent Contract is not a hole the agent fills: the
hook releases its stop at once as NEEDS-HUMAN (not a pass), and the supervisor asks the
human and re-dispatches intake with the answers.
