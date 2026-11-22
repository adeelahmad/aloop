# Gate — 02-planner  (the planning -> execution boundary)

Checks (libexec/gate-stage2-complete):
- [ ] Every story in stories.md has a sN-NN-<slug>/ dir with all three files.
- [ ] No "TBW" remains anywhere.
- [ ] sprintN/plan.md references every story; references no non-existent story.
- [ ] Every plan.md bullet has a concrete test file path AND fn name (gate-plan-shape).
- [ ] No story appears in two waves.
- [ ] Sprint containment: no reference to a LATER sprint's story IDs; no task defers
      work into the next/a later sprint (context is cleared between sprints).

Failure verb: RE-PLAN (back to this persona; human is present).
Enforced by: [OPEN-3] hook at handoff OR supervisor self-check. This is the single
most important gate — it is the fix for "the sprint plan went missing."

Anti-patterns (extends the playbook's planning anti-patterns — reject the planning PR):
- A story whose Intent is missing failure-scenarios — the agent will invent the
  failure boundary.
- A story missing its connections list — a change becomes untraceable to what it breaks.
(Enforces the five-part Intent, carried from intake.md into stories.md.)

Self-check (v0.2): the worker runs `selfcheck` (this same gate) BEFORE reporting
done — nothing advances past a failing self-check. The SubagentStop hook runs the same
gate as the backstop.
