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
- A human approval is an event, never a field: `approve(cwd, event, subject_ref, sha, by, reason)`
  records it and `approved(cwd, event, subject_ref, sha)` finds the one covering exactly that
  content. The approval events are `APPROVAL_EVENTS`.
- Exit codes, like every aloop check: `0` pass · `2` block (with `BLOCK[<area>]:` lines on
  stderr) · `3` needs the human (`ASK[<area>]:`) · `64` usage.
- Every `mission.*`, `research.*`, `baseline.*`, `journey.*`, `state.*`, `eval.*`, `goal.*`,
  `ethics.*`, `telemetry.*` and `judge.*` event type is already registered in `_events.py`.
- `snapshot(cwd)` is what `mission tick` decides from: per kind, the count by status and the
  latest id. It reads only the contract fields, so the loop never depends on an area module.

A gate checks structure and evidence, never whether a judgment is right.
