---
name: init
description: >-
  Explicit entry point for the aloop workflow — set up the project (limits,
  models, .gitignore, commit author, whether docs/agents is tracked) with the human's
  confirmation, then start sprint planning. Invoke with /aloop:init.
disable-model-invocation: true
---

# aloop — init (entry point)

**Do this now, in order.**

## 0. Host

On Claude Code nothing to do. On **Codex**, **OpenCode** or **DeepSeek Harness** (`dsh`), the
human runs `aloop install codex` / `aloop install opencode` / `aloop install dsh` once (it
generates the agents, skills and — for OpenCode and dsh — the plugin; see the aloop skill's
host table). If `aloop`
is not found, the plugin isn't installed for this host yet — say so and stop.

## 1. Project settings — show the defaults, get the human's confirmation

Run:

    aloop init --show

It prints a table of the proposed settings (the current ones if
`docs/agents/defaults.md` already exists). Present that table to the human **as is**, then
ask — one question per topic, the default first (Claude Code: `AskUserQuestion`; OpenCode: the
`question` tool; Codex and dsh: ask in chat and wait):

1. **Limits** — per worker attempt: tokens soft/hard (default 150k / 250k), minutes
   soft/hard (20 / 30), re-plan cap (2). Keep the defaults, or which values?
2. **Models** — a route per group: planning agents `strongest`, workers `strong` (the
   orchestrator runs on `strong`). Routes resolve per host (`aloop models routes`: on Claude
   Code `claude-opus-5-5` / `sonnet`); a pinned id is accepted too. Keep, or which?
3. **`.gitignore`** — may I add `.agentic/` and `.transcripts/` (run data: transcripts,
   token ledger, logs — never meant for GitHub)? Default **yes**. (Run data lives in
   `.aladdin/`, guarded by its own `.gitignore`; `.agentic/` covers a legacy project.)
4. **Track `docs/agents/` in git?** Default **no** (it is then git-ignored too).
5. **Commit author** — every commit is authored by *the human*, not Claude: default is the
   identity `aloop-init --show` detected from git config (name + email). Confirm or
   give another. And: allow Claude co-author / "Generated with Claude Code" trailers?
   Default **no**.

Do not assume answers and do not skip a question because a default exists — the point is
that the human sees and confirms them.

## 2. Save them — the harness writes the file, not you

    aloop init --apply TOKENS_SOFT=… TOKENS_HARD=… TIME_SOFT_MIN=… TIME_HARD_MIN=… \
      MAX_REPLANS=… MODEL_PLANNING=… MODEL_WORKER=… GITIGNORE=yes|no TRACK_DOCS=no|yes \
      COMMIT_AUTHOR_NAME="…" COMMIT_AUTHOR_EMAIL="…" CLAUDE_COAUTHOR=no|yes

This writes `docs/agents/defaults.md` (never hand-edit it — a hook blocks that), applies
the `.gitignore` lines, and sets the repo's local `git config user.name/email` to the
confirmed author. From then on:
- the time-box hooks read the limits from it;
- `gate-commit-author` blocks any commit with another author or a Claude trailer (unless
  allowed);
- the SessionStart hook re-applies the `.gitignore` lines every session.

If `--show` reported that `docs/agents/` is already tracked and the human chose not to
track it, ask before running `git rm -r --cached docs/agents` — never run it unasked.

## 3. Start planning

Load the canonical supervisor playbook — the `aloop` skill in this plugin — adopt
the supervisor role and begin **Stage-1 planning** with the human (retrospective → intake →
standards → planner). Do **not** start the autonomous RED → SCAFFOLD → GREEN →
STRUCTURAL-REVIEW → FINAL-GATE run until the human approves the completed Stage-2 plan.

If `docs/agents/NEXT.md` exists, the previous sprint closed and this is a resume: the
SessionStart hook has already loaded it — follow its "Do this now" section.
