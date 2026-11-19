---
name: init
description: >-
  Explicit entry point for the aloop workflow — set up the project (limits,
  models, .gitignore, commit author, whether docs/agents is tracked) from the detected
  defaults with no questions (--ask to choose them), then start sprint planning. Invoke with /aloop:init.
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

## 1. Project settings — apply the defaults, ask nothing (M11.6)

Settings are two-way doors: the loop picks them and the human changes any one later by saying
so. Run:

    aloop init --show

and, unless the human asked to choose them (`/aloop:init --ask`, or "ask me"), apply them as
they are in step 2 with no arguments and show the table once, as information, with one line:
"say a value to change it". The defaults: limits 150k / 250k tokens, 20 / 30 minutes, re-plan
cap 2; routes planning `strongest`, workers `strong` (`aloop models routes`); `.gitignore`
yes (run data never reaches GitHub); `docs/agents/` not tracked; commit author the identity
`--show` detected from git config, no Claude trailers.

With `--ask`: one question per topic, the default first (Claude Code: `AskUserQuestion`;
OpenCode: the `question` tool; Codex and dsh: ask in chat and wait): limits, models,
`.gitignore`, tracking `docs/agents/`, the commit author and trailers.

## 2. Save them — the harness writes the file, not you

    aloop init --apply                     # the defaults (or keep the current values)
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

If `--show` reported that `docs/agents/` is already tracked, keep tracking it
(`TRACK_DOCS=yes`); never run `git rm -r --cached docs/agents` unasked.

## 3. Start planning

Load the canonical supervisor playbook — the `aloop` skill in this plugin — adopt
the supervisor role and plan from the human's intent (retrospective → intake → standards →
planner → door-classifier). Autonomous is the default: the human is asked only about the
plan's one-way doors; reviewed mode (opt-in) waits for their `approve SN <code>`.

If `docs/agents/NEXT.md` exists, the previous sprint closed and this is a resume: the
SessionStart hook has already loaded it — follow its "Do this now" section.
