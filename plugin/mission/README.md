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
  (`LEDGER_EVENTS`: the human decisions, `baseline.measured`, `journey.retired`), committed with the
  artifacts. `M.record(cwd, event, subject, role, **data)` appends one (and emits the event);
  `M.record_human` / `M.approve` are the human-only forms. The event stream is git-ignored run data,
  so a fresh checkout reads the ledger only. Each row carries the hash of the row it follows: an
  edited, removed, backdated or junk row, or a ledger row the run log has and the ledger lost, makes
  `M.ledger_problems` (and so `mission check`, `baseline check`, `journeys check`) fail. Recovery is
  `git checkout -- docs/agents/mission/ledger.jsonl`. `mission init` marks it `merge=union`. A forged
  row appended with a valid hash is not detectable here: M9.9 denies roles direct writes to it and
  M9.17 ties each human record to a captured prompt.
- `M.covered(cwd, kind, event)` → every artifact whose current content its newest `event` record
  covers; `baseline.current` is the version the newest `baseline.confirmed` record names, and
  `journeys.lifecycle` / `journeys.pins` say what a journey is and which content it may not leave.
- `plugin/mission/_runner.py` runs a pinned script: each `pinned` tool's probe must print its
  version, then the command's last stdout line is the result
  `{"steps": {id: bool}, "metrics": {id: number|bool}}` (NaN and Infinity are refused). The files the
  command runs are pinned by sha256 (`runner.files`, filled by `journeys new` and `baseline new`), and a
  pinned version must appear whole in the probe's output (`3.1` never matches `3.12`). `drift(a, b)` is
  the relative difference (infinite for a non-finite number).
- An area that must run with no arguments (`aloop mission due`) sets `NO_ARGS = True`.
- A research source ref is `<research_id>#<claim_id>`, resolved against the research artifact's
  `claims: [{"claim_id", …}]` (M9.1 owns that schema). Evidence (`--evidence`, `evidence_refs`) is a
  research claim or an existing `evl_`, `sta_` or `rsr_` id: a placeholder string is refused.
- A journey's judged metric is scored by `judge.verdict(cwd, rubric, journey_id)` (M9.5): only a `pass`
  or `fail` verdict fills the value; anything else leaves the criterion pending, and a pending
  criterion is never a baseline's RED.

## Notes for the tracks (from mapping the repo, 2026-10-20)

- **M9.8 loop.** `budget.usd` cannot come from the token ledger, which holds tokens only; USD
  exists only in `claude -p` result lines, so the cron runner records each run's cost (or a price
  table converts tokens). In-host schedulers (CronCreate, `/loop`, cloud scheduled tasks) spend
  tokens on every fire; a zero-token tick needs a system cron line that runs `aloop mission cron run`
  in the persistent checkout.
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
- **M9.11 goals.** Add `mission` to the `intent` SOURCES. `gate-intake` checks the newest
  `int_*.json` by mtime and `ids freeze` links a plan to the last `intent.normalized` anywhere, so
  queued mission intents need an explicit link. `record_authority` also approves the latest plan:
  record a goal's authority only after its sprint's `ids freeze`. `aladdin "<intent>"` refuses a
  bare intent containing `/`; use `aladdin run`.
- **M9.17 approvals.** Any session can run `mission approve --by anyone` or `events authority`, or
  append a well-formed ledger row; an approval has to cite a captured human prompt before it means a
  human approved.
- **Rivals (M9.1 → M9.10).** A baseline's `rivals` are claim refs today; the Product Tester's values per
  benchmark and metric belong in a keyed object so `evaluate` can compare against them.
- **M9.6 telemetry (GA4 facts, 2026-10-20).** A GA account needs a human ToS acceptance (no
  `accounts.create`); below it the Admin API does everything (properties, a web stream with its
  measurement id, `acknowledgeUserDataCollection` with the exact string, the MP secret, key events).
  gcloud's default OAuth client is being blocked for analytics scopes: use `--client-id-file` or
  service-account impersonation, and send `x-goog-user-project`. The Data API needs
  `analytics.readonly` (not `analytics.edit`). The Measurement Protocol answers 2xx to bad payloads
  and the debug endpoint skips the secret, so prove a setup with `runRealtimeReport` (last 30 min;
  `mission telemetry verify` does that round-trip);
  standard reports lag 2–48 h and threshold small user counts (`metadata.subjectToThresholding`).
