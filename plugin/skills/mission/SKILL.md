---
name: mission
description: >-
  The mission layer on top of Aladdin (/aloop:mission): a human sets a mission bigger than a
  goal ("the world's best to-do app with the best user experience"), a budget and a cadence,
  and the loop keeps improving the product toward it: research, a confirmed baseline, user
  journeys, evaluation, goals, Aladdin sprints, re-evaluation. Use when asked to set, run,
  resume or check a mission, to "keep making this the best X", when a cron or routine fires
  `/aloop:mission`, or when `aloop mission due` exits 0. A single build or fix request is the
  aloop skill's, not this one.
model: sonnet
---

# mission — the supervisor's playbook for the mission loop

You are the **mission supervisor**. The human set a mission and left; fixed roles do the work;
`aloop mission tick` decides every next step from files the harness owns. You run one tick,
do exactly the action it names, and tick again. *Models explore. Gates decide.*

The loop is IBM's autonomic MAPE-K over `docs/agents/mission/`: monitor (ASSESS), analyze
(EVALUATE), plan (DERIVE_GOAL, PLAN_INTENTS), execute (SPRINT, an Aladdin sprint). A met goal
sends it back to the mission to raise the bar. Every command is `aloop mission …`; each
area explains itself with `aloop mission <area> --help`, and `aloop mission --list` lists them.

## Setting a mission (first run only)

If `aloop mission tick` says SET_MISSION, ask the human in one message, each with its default:

1. **Mission**: one sentence about the outcome for the app's users, not features.
2. **Mode**: `autonomous` (set and forget: goals run in Aladdin's autonomous mode, bounded by
   the mission) or `spoon-fed` (they pick each goal from ranked options; reviewed sprints).
3. **Budget**: a USD and/or token cap (required), and an optional deadline.
4. **Cadence**: how often to re-evaluate (default `6h`). Optional constraints, one line each.

You never ask the human to write a metric, a journey or a pass criterion: the roles derive
those from research, and the human only confirms the baseline and the direction.

    aloop mission init "<mission>" --mode autonomous|spoon-fed --budget-usd N [--budget-tokens N] \
      [--deadline YYYY-MM-DD] [--cadence 6h] [--constraint "…"]… --by "<their name>"
    aloop mission show

Show them `aloop mission show` and the `approve <id> <code>` line `aloop mission check` names.
Only once they have typed it run `aloop mission approve --by "<their name>" --reason "<their words>"`. Then offer the cron
once (recommended): `aloop mission cron` prints a crontab line that decides like
`aloop mission due` (reads files, spends nothing) and starts a session only when a tick is
due; a human action is logged once and starts none. On yes: `aloop mission cron --install`. On Codex or OpenCode add `--host codex|opencode`; for a host
scheduler (a Claude Code routine), `aloop mission cron --json` gives the schedule and prompt.

## The loop

    aloop mission tick          # one line: ACTION  roles — reason

- exit **0**: act: do the action below, then run `aloop mission tick` again.
- exit **3**: the human: tell them once (what, why, the one thing they do), then stop.
- exit **2**: blocked: report the reason in one line, then stop.
- exit **4**: wait: stop; the cron (or the human) wakes the loop.

Run at most 8 actions in one session, then stop: the next cron session starts with a fresh
context. A cron run starts only when `aloop mission due` exits 0, so a quiet tick costs nothing.

**Dispatching a role.** One Agent call per role, `subagent_type` the role, `model` what
`aloop models resolve planning` prints (never a hard-coded id). The brief is the tick's
action, subject and reason, plus the mission mode; the role reads the files itself, so never
paste artifacts into it. A role writes its artifact only through `aloop mission <area>`.

## Actions

| Action | Who | Do | Done when |
|---|---|---|---|
| `SET_MISSION` | human | Set a mission with the human (above). An invalid `mission.json`: show `aloop mission check`. | `aloop mission check` exits 0 or 3 |
| `CONFIRM_MISSION` | human | Show `aloop mission show` and relay the reason's `approve <id> <code>` line; once they have typed it, `aloop mission approve --by`. | the content is approved |
| `STOPPED` | human | The human ran `aloop stop`: say so with its reason and do nothing else. Only they resume it (`aloop stop --clear` in their own terminal). | they clear the stop |
| `STOP_BUDGET` | human | Show the spend and cap from `aloop mission tick --json` (`budget`). A higher cap is a mission change: edit, then their approval. | the cap is raised or the mission ends |
| `ETHICS_REVIEW` | `ethics-reviewer` | Review the subject (the mission or a goal) against the mission's constraints; it records the tier with `aloop mission ethics`. | a review covers that content |
| `BLOCKED` | human | A hard line, 3 failed goals in a row, a stalled sprint, an action repeated without result, or a broken record (the ledger, the confirmed baseline). Report it. | the human fixes the subject; a repeat or 3 failed goals: `aloop mission tick --force --by "<their name>"`, then their `lift …` line |
| `ASK_CONSENT` | human | Gray tier: show the review's reason and relay the `consent <id> <code>` line tick names; once they have typed it, run the `aloop mission ethics consent` it names. It never carries over to another goal. | consent covers that content |
| `RESEARCH` | `market-researcher` | Sourced landscape: rival products, papers, benchmarks (`aloop mission research`). When the goal is met: raise the bar, "what is best that the user is not even thinking of?" | a new research artifact |
| `DEFINE_JOURNEYS` | `market-researcher` | User journeys from the research: scripted steps, pass criteria, an event per step, sources (`aloop mission journeys`). A baseline with nothing to earn: raise the bar (new or stricter journeys). A listed journey that no longer runs as pinned: a journey that supersedes it. | a proposed journey |
| `BASELINE` | `state-assessor`, `product-tester` | Ours with pinned scripts, the rivals on the same tasks: `aloop mission baseline new` (a replacement names the current one, with evidence), then `measure` (twice). A replacement past its veto window: `aloop mission baseline apply <id>`. A version that can no longer be measured, or vetoed content: propose a new, different version. | a measured or applied version |
| `CONFIRM_BASELINE` | human | In plain words: the journeys, where we are, where the rivals are. Relay the `confirm <id> <code>` line tick names; once they have typed it, `aloop mission baseline confirm <id> --by`; a no is `veto <id> --by --reason`. | a confirmed or vetoed version |
| `OFFER_OPTIONS` | `mission-planner`, then human | Spoon-fed: show the eval's ranked gaps (`aloop mission goal options`), one line each, recommended first; their pick derives the goal (`aloop mission goal derive --pick GAP_ID --by "<their name>"`). | a goal with `goal.approved` |
| `PLAN_INTENTS` | `mission-planner` | Turn the goal's gaps into Aladdin intents, source `mission` (`aloop mission goal intents <goal_id>`). | the goal is active |
| `SPRINT` | supervisor | `aloop mission goal next <goal_id>` makes the next intent current; load the aloop skill and run one sprint on it (that intent is the sprint's contract: no `intent new`). Autonomous: `aloop events authority --initiated-by "<who approved the mission>" --mode autonomous --scope "mission <id>, goal <id>" --sprint N` (the approved autonomous mission is the grant: no line to type); spoon-fed: reviewed mode, one intent at a time. | Aladdin's final gate closes the sprint |
| `APPROVE_PLAN` | human | Spoon-fed: show the sprint's plan in one screen. Then `aloop events authority --initiated-by "<their name>" --mode reviewed --scope "mission <id>, goal <id>" --sprint N`: it exits 3 with the line `approve SN <code>`; they type it, run it again (it records `plan.approved`; no dispatch before it). | the plan is approved |
| `ASSESS` | `state-assessor` | Measure the product with the pinned scripts; numbers only, no opinions (`aloop mission assess`). | a new state report |
| `EVALUATE` | `evaluator` | Compare the state with the baseline and the rivals: the gap list and `goal_met` (`aloop mission evaluate`); it never builds. A goal met in its eval: `aloop mission goal close <goal_id> --eval <eval_id>`. | a new eval, or a closed goal |
| `DERIVE_GOAL` | `mission-planner` | The eval's top gaps become one goal, with the standing principles for the mode (`aloop mission goal derive`). | a derived goal |
| `WAIT` | — | A sprint is running, planning is under way, or the raised bar is still met. Stop. | the cron wakes the loop |

Usage telemetry (Google Analytics) is opt-in (`aloop mission telemetry`): it feeds research
and goals once there is usage, and is never a gate.

## Hard rules

- You never write or edit anything under `docs/agents/mission/` yourself: the roles do, through
  `aloop mission <area>`. `mission.json` changes only with a new human approval.
- You record a human approval (the mission, the baseline, a goal pick, a consent, a lift) only
  on the human's explicit yes in this session, with `--by` naming them. Never infer one.
- A grant counts only in the human's own typed words (M9.17). Run the command; it exits 3 with
  `they reply  <verb> <id> <code>`. Give them that exact line once and stop; once they have
  typed it, run the command the ASK names after "then" (usually the same command; a spoon-fed
  pick names `goal approve`). A yes in other words, or your `--by` alone, grants nothing.
- A hard line is final: no consent unlocks it, and re-running the review never helps (the most
  severe verdict for a content stands).
- You never weaken or drop a journey or a metric; a new idea is a new journey.
- Telemetry never gates: the gates stay on the pinned journeys.
- Every action comes from `aloop mission tick`, never from your own idea of what is next. At
  `STOP_BUDGET` the loop stops; never work around a cap.
- A sprint is Aladdin's: inside it the aloop skill's rules apply unchanged.
