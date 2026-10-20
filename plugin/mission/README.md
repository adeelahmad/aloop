# plugin/mission/ — the mission layer's areas

The mission layer (ROADMAP §4.12, milestone M9) sits on top of Aladdin: a human sets a
mission, a budget and a cadence; fixed roles build a baseline and an evaluation layer from
user journeys; goals derived from the gap list become Aladdin intents. `aloop mission …`
is the one entry point (`plugin/bin/mission`); the shared core is `plugin/bin/_mission.py`.

## Adding an area (one parallel track = one area)

An area is `plugin/mission/<area>.py`. `mission --list` discovers it by name, so a track
adds files and never edits a shared list:

| File | What |
|---|---|
| `plugin/mission/<area>.py` | the module: a docstring (it is `mission <area> --help`) and `main(argv, cwd) -> int` |
| `plugin/mission/schemas/<kind>.json` | the artifact's schema (the JSON Schema subset `_mission.validate` reads) |
| `scripts/test/mission-<area>.test.py` | its test; `make test-hosts` runs every `scripts/test/mission-*.test.py` |

Then flip the task's own row in ROADMAP §4.12 and its row in `docs/TRACKING.md`.

## The contract (`_mission.py`)

- Artifacts are JSON under `docs/agents/mission/<LAYOUT[kind]>/<id>.json` (the mission itself
  is `mission.json`). Each has `<kind>_id`, `status` (one of `STATUSES[kind]`) and `created_at`.
- Ids come from `new_id(kind)`, never from a model; `problems(doc, kind, path)` refuses one
  that does not match the file name.
- `seal(doc)` sets `sha256` over the content (minus `sha256`, `status`, `approval`), so a file
  that changes after it was sealed is detected.
- A human approval is a ledger record, never a field: `approve(cwd, event, subject_ref, sha, by, reason)`
  records it and `approved(cwd, event, subject_ref, sha)` returns the subject's **newest** human
  approval when it covers exactly that content (restoring older approved content is a rollback, not
  approved). The approval events are `APPROVAL_EVENTS`.
- `status` is outside the digest, so it proves nothing: a gate reads what an artifact *is* from the
  ledger (`records`, `newest`, `covered`) and reports a status field that disagrees.
- Exit codes, like every aloop check: `0` pass · `2` block (with `BLOCK[<area>]:` lines on
  stderr) · `3` needs the human (`ASK[<area>]:`) · `64` usage.
- Every `mission.*`, `research.*`, `baseline.*`, `journey.*`, `state.*`, `eval.*`, `goal.*`,
  `ethics.*`, `telemetry.*` and `judge.*` event type is already registered in `_events.py`.
- `snapshot(cwd)` is what `mission tick` decides from: per kind, the count by status and the
  latest id. It reads only the contract fields, so the loop never depends on an area module.

A gate checks structure and evidence, never whether a judgment is right.

## Shared helpers

- `M.mission(cwd)` → `(doc, sha256, approval record)`; the record is `None` while the mission is a
  draft or changed since its newest approval.
- **The ledger** `docs/agents/mission/ledger.jsonl` holds every record a gate decides from
  (`LEDGER_EVENTS`: the human decisions, `baseline.measured`, `journey.retired`, `ethics.reviewed`,
  `ethics.blocked`, `goal.derived`, `goal.met`), committed with the artifacts.
  `M.record(cwd, event, subject, role, **data)` appends one (and emits the event); `M.record_human` /
  `M.approve` are the human-only forms. The event stream is git-ignored run data, so a fresh checkout
  reads the ledger only. Each row carries the hash of the row it follows: an edited, removed,
  backdated or junk row, or a row the run log recorded **on this branch** and the ledger lost, makes
  `M.ledger_problems` fail. Nothing decides from a broken ledger: a deciding command starts with
  `M.ledger_block(cwd, area)` (prints `BLOCK` lines, returns 2), and `M.record` raises
  `M.LedgerBroken` rather than chain a row past a broken one. Recovery is
  `git checkout -- docs/agents/mission/ledger.jsonl`. `mission init` marks it `merge=union`.
  **Not detectable here:** a forged row appended with a valid hash, and, on a checkout without the run
  log, the newest rows removed. M9.9 denies roles direct writes to the ledger and M9.17 ties each human
  record to a captured prompt.
- A confirmation record carries the journeys it froze (`data.journeys`), and a retirement record its
  replacement (`replaced_by`, `replaced_by_sha256`): `journeys.pins` and `journeys.lifecycle` read
  those records, so hiding or editing a file never unfreezes, unpins or un-retires a journey.
  `baseline.current` is the version the newest `baseline.confirmed` record names.
- `plugin/mission/_runner.py` runs a pinned script: each `pinned` tool's probe must print its
  version, then the command's last stdout line is the result
  `{"steps": {id: bool}, "metrics": {id: number|bool}}` (NaN and Infinity are refused). The files the
  command runs are pinned by sha256 (`runner.files`, filled by `journeys new` and `baseline new`;
  `journeys check` refuses a map that misses a file the command runs, an empty one, a changed file, a
  symlinked script, inline code such as `python3 -c`, and a script outside the project), and a
  pinned version must appear whole in the probe's output (`3.1` never matches `3.12`). `drift(a, b)` is
  the relative difference (infinite for a non-finite number).
- Flags: `--k=v` is `--k v`, and an area exits 64 on a flag it does not know (`J.parse(…, known=)` and
  `J.unknown_flags`), so a typo never records a broader decision than the human typed.
- Text is UTF-8 whatever the locale: `M.cli_text(argv)` recovers argv, files are read and written as
  UTF-8, and a lone surrogate in a file is a `BLOCK`, never a crash.
- An area that must run with no arguments (`aloop mission due`) sets `NO_ARGS = True`.
- A research source ref is `<research_id>#<claim_id>`, resolved against the research artifact's
  `claims: [{"claim_id", …}]` (M9.1 owns that schema). Evidence (`--evidence`, `evidence_refs`) is a
  research claim or an existing `evl_`, `sta_` or `rsr_` id: a placeholder string is refused.
- A journey's judged metric pins its rubric's content (`query.rubric_sha256` = `M.digest(rubric)`), so a
  weaker rubric is a change to the journey that its approval does not cover. It is scored by
  `judge.verdict(cwd, rubric, journey_id, rubric_sha256=…)` (M9.5): only a `pass` or `fail` verdict fills
  the value; anything else (or a judge that cannot check the pin) leaves the criterion pending, and a
  pending criterion is never a baseline's RED.
- A gap list (`evl_…`, sealed) is `{eval_id, state_id, baseline_id, goal_id, goal_met, regressions,
  gaps, earned, metrics}`; each gap is `{gap_id: "<journey_id>#<criterion_id>", journey_id,
  criterion_id, kind: regression|open|unscored, now, baseline, target, distance}`, regressions first,
  then by distance. `eval.completed` carries `eval_id` and `sha256`. Every input is the content its
  record covers: the state a `state.assessed` ledger record (only `mission assess` measures), the
  baseline a confirmation, each journey the content the baseline froze, and the goal a `goal.derived`
  or `goal.approved` record (the newest such goal with no later `goal.met` record is the active one; a
  status field activates nothing) whose targets are criteria the baseline froze. Anything else blocks
  `evaluate` and `evaluate check`.

## Notes for the tracks (from mapping the repo, 2026-10-20)

- **M9.8 loop.** `budget.usd` cannot come from the token ledger, which holds tokens only; USD
  exists only in `claude -p` result lines, so the cron runner records each run's cost (or a price
  table converts tokens). In-host schedulers (CronCreate, `/loop`, cloud scheduled tasks) spend
  tokens on every fire; a zero-token tick needs a system cron line that runs `aloop mission cron --fire`
  in the persistent checkout (built: `tick` prices tokens per model, `PRICES`).
- **M9.9 roles.** A role name lives in about 12 lists: `hooks.json` (its SubagentStop gate and the
  `budget hook stop` matcher), `gate-dispatch` (the aloop-role allowlist and the no-TASK_ID
  exemption: mission roles are not sprint tasks), `_routes.GROUPS`, `install.PLANNING`, `budget`
  `ROLE_PROFILES`, `selfcheck`, `hosts/opencode/aloop.js` GATES, the `gate-supervisor-scope`
  AGENT_ROLE fallback, the `agent-io.kdl` enums and `_gatelib.sh`. Only `hook-matchers.test.py`
  (ROLES) and `validate-suites.py` (exactly 9 suites) notice a miss, so add a test that every list
  names all six. The aloop `SKILL.md` has about 150 bytes left of its 50,000-byte budget: mission
  text goes in the mission skill. A supervisor Write under `docs/agents/` arms the sprint lock;
  exempt `docs/agents/mission/`. Mission-role gates need their own stuck key (`_gate_stuck` keys
  on TASK_ID and ATTEMPT). Block `mission approve`, `baseline confirm|veto`, `journeys approve`
  and `events authority` for sub-agents, plus `baseline restore` and any direct Write/Edit of
  `docs/agents/mission/ledger.jsonl` (only the CLI appends to it).
  *Done in M9.9:* every list above names the six, or leaves them out on purpose (the worker-only
  lists, `_gate_stuck` and the AGENT_ROLE fallback; `agent-io.kdl` too, since a mission role writes
  no init.md or output.md); `mission-roles.test.py` checks each wired list.
- **M9.11 goals.** Add `mission` to the `intent` SOURCES. `gate-intake` checks the newest
  `int_*.json` by mtime and `ids freeze` links a plan to the last `intent.normalized` anywhere, so
  queued mission intents need an explicit link (built: `goal next` makes the intent it hands off the
  newest file before normalizing it). `record_authority` also approves the latest plan:
  record a goal's authority only after its sprint's `ids freeze`. `aladdin "<intent>"` refuses a
  bare intent containing `/`; use `aladdin run`.
- **M9.17 approvals.** Any session can run `mission approve --by anyone` or `events authority`, or
  append a well-formed ledger row; an approval has to cite a captured human prompt before it means a
  human approved.
- **Rivals (M9.1 → M9.10).** A baseline's `rivals` are claim refs today; the Product Tester's values per
  benchmark and metric belong in a keyed object so `evaluate` can compare against them.
- **M9.6 telemetry.** `journeys.GA4_RESERVED_NAMES` is the one reserved-name list (the union of both
  tracks' lists plus `session_start_with_rollout`); import it.
- **M9.6 telemetry (GA4 facts, 2026-10-20).** A GA account needs a human ToS acceptance (no
  `accounts.create`); below it the Admin API does everything (properties, a web stream with its
  measurement id, `acknowledgeUserDataCollection` with the exact string, the MP secret, key events).
  gcloud's default OAuth client is being blocked for analytics scopes: use `--client-id-file` or
  service-account impersonation, and send `x-goog-user-project`. The Data API needs
  `analytics.readonly` (not `analytics.edit`). The Measurement Protocol answers 2xx to bad payloads
  and the debug endpoint skips the secret, so prove a setup with `runRealtimeReport` (last 30 min;
  `mission telemetry verify` does that round-trip);
  standard reports lag 2–48 h and threshold small user counts (`metadata.subjectToThresholding`).

## What the loop reads (`mission tick`, M9.8)

`tick` decides from the contract fields above and these facts only, so an area that wants the
loop to move on records them:

| Fact | Recorded as |
|---|---|
| a human approval | a ledger row from `approve(...)` (role human, `--by`) with the subject's project-relative path (`rel`) and `digest` of its content: `mission.approved`, `goal.approved` (spoon-fed pick), `ethics.consent` (gray; mission or goal; it names the review: `review`, `review_sha256`). The subject's newest approval decides |
| an ethics verdict | `ethics.reviewed` {subject, sha256, tier: clear · gray · hard, ethics_id, review_sha256}; `ethics.blocked` {subject, sha256} is a hard line. The most severe verdict for a content wins; a goal stays hard |
| the baseline | as `baseline.py` decides it: `current` (the newest `baseline.confirmed` row), `measured_ok`, `vetoed`, `evaluate`; a replacement applies after the mission's `veto_window_h` from its `baseline.measured` row |
| journeys defined | a journey that `journeys.lifecycle` does not call retired |
| a state report or eval | its `baseline_id` (another than the current one: re-assess, re-evaluate) and the eval's boolean `goal_met` |
| a goal's progress | status `derived` → `approved` → `active` (intents handed to Aladdin) → `met` · `retired` · `blocked` |
| a goal's sprints | `intent.received` {goal_id, intent_id} → `plan.created` {sprint: "S<N>", intent_id}; with no linked intent, the sprints planned since the loop handed out its `SPRINT`. Closes: `.aladdin/state/sprints.json`; `plan.approved` (spoon-fed) |

Time is the ms in each harness id, so artifacts order exactly; an event stamp kept to the second
reads as the end of that second (a tie counts as after).
