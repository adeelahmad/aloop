# Aloop: standing goal

**What it is.** agentic-agile (becoming **Aladdin / aloop**) is a plugin for Claude Code, Codex and OpenCode that turns any intent into planned, test-first, gate-verified software. A human approves the plan, cheap workers build it in isolated worktrees, and deterministic hooks decide what is accepted. *Models explore. Gates decide.*

**North star.** A one-line intent becomes merged, tested, auditable code on any host, with no human help beyond approval, at the lowest token cost.

**Done means.** Every milestone's exit criterion is proven by a test or a measured run, CI is green, and `docs/OVERVIEW.md` and `docs/ROADMAP.md` match the code.

**Flywheel (every agent, every turn):**
1. **Orient:** read `docs/OVERVIEW.md`, ROADMAP §0, and open PRs.
2. **Pick** the highest-value unblocked item. Red CI and bugs come first, then the current milestone.
3. **Ship small:** branch, write tests first, open a draft PR, and drive it green.
4. **Measure:** put the evidence (tests, run stats) in the PR.
5. **Re-plan:** update ROADMAP status. Add any gap, risk or idea you found as an item with a one-line reason. Requirements are yours to maintain, not only the user's.
6. **Repeat** without waiting to be asked.

**Judgment.** Decide reversible things yourself and record the call. Ask only about irreversible, security-sensitive or goal-changing choices: one question, with a recommended option. Never weaken a gate, an invariant (ROADMAP §7) or a test to make progress. Prefer evidence to claims. Keep user-facing replies to 1–2 words unless more is needed.
