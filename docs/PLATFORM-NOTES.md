# PLATFORM-NOTES — Claude Code mechanics this plugin relies on

A distilled reference of the platform facts the design depends on, so the builder
has them on hand. This is a working distillation, not a copy of the documentation.
The authoritative source is the official Claude Code docs at
**https://docs.claude.com** (Claude Code section); verify any detail there, since
the platform evolves. Topic pages referenced below: Plugins, Skills, Subagents,
Hooks (guide + reference), Worktrees, Workflows, Goal, Marketplaces, Agent teams,
Scheduled tasks.

================================================================================
## Plugins
- A plugin bundles skills, agents, commands, hooks, and bin scripts behind one
  install (`/plugin install`), optionally from a marketplace.
- Manifest: `.claude-plugin/plugin.json` (name, version, and paths to skills/
  agents/hooks).
- `${CLAUDE_PLUGIN_ROOT}` is available inside hook commands and resolves to the
  installed plugin directory — use it for all libexec/ and schemas/ paths.

## Skills
- A skill is instructions (a `SKILL.md`) the agent loads and follows. It is
  ADVISORY: the model may or may not comply. This is exactly why determinism in
  this design comes from hooks, not from the skill.
- The orchestrating ("supervisor") agent is the one that loads the skill.

## Subagents
- Dispatched via the Task tool with a `subagent_type` (matches an `agents/<name>.md`)
  and may run with `isolation: "worktree"`.
- CRITICAL LIMITATION: a subagent provided BY A PLUGIN ignores `hooks`,
  `mcpServers`, and `permissionMode` in its own frontmatter. Therefore per-agent
  gates MUST live in the top-level `hooks.json`, matched by `agent_type`.

## Hooks (the determinism)
- Hooks fire deterministically on lifecycle/tool events, regardless of the model.
- Exit-code contract used throughout this plugin:
    exit 0  = pass / allow
    exit 2  = BLOCK the event and send stderr back to the agent (the feedback)
    other non-zero = surfaced as an error
- Events this design uses:
    PostToolUse        after a tool runs (e.g., Write) — cannot undo the action,
                       so it validates and blocks-forward, not retroactively.
    SubagentStart      a subagent begins.
    SubagentStop       a subagent finished — the main gate hook point; matched by
                       agent_type to run the right gate.
    Stop               the agent is trying to stop — the `/goal`-style "keep going
                       until verified" hook point (used for final-gate).
    PreToolUse         before a tool runs — can block/allow/ask (used for
                       bounded-cost-style guards if added).
    (worktree lifecycle is handled by the harness's built-in `isolation: "worktree"`;
     `WorktreeCreate`/`WorktreeRemove` are user-settings.json-only hooks, not plugin events.)
- A hook returning exit 2 on SubagentStop blocks the stop and hands stderr to the
  supervisor; the supervisor (not the hook) owns the retry-or-escalate decision.

## Worktrees
- `isolation: "worktree"` gives each dispatched subagent its own git worktree, so a
  bad RED/GREEN run never pollutes the integration branch. Merge on gate pass;
  abandon the worktree on HALT. This is what makes "revert on halt" clean.

## Workflows (autonomous runs)
- A dynamic workflow (triggered by a keyword such as `ultracode`) runs
  autonomously and takes NO mid-run user input — only an agent permission prompt
  can pause one. This is consistent with this design's "human only at planning."

## Goal
- `/goal` keeps the agent working until a stated condition is verified (a Stop-hook
  loop). The final-gate "sprint is green" condition fits this shape.

## Marketplaces
- Plugins can be published to / installed from a marketplace. Relevant only at
  distribution time; not required to build or run locally.

## Agent teams (experimental)
- Provide worker<->worker messaging (SendMessage / mailbox). NOT required by this
  design — the supervisor mediates all coordination. Listed for awareness only.

================================================================================
## The three facts this design leans on hardest
1. Hooks fire regardless of model behavior, and exit 2 = block + feedback. (Hooks
   are the substitute for the human attention deliberately absent during execution.)
2. Plugin-provided subagents ignore their own hook frontmatter -> all gates go in
   top-level hooks.json by agent_type.
3. Worktree isolation is built in -> per-task isolation, merge-on-pass, and
   clean abandon-on-halt require no custom machinery.
