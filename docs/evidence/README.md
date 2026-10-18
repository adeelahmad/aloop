# Evidence: the measured runs behind every public number

Every time or cost figure in the [README](../../README.md), on the [site](../index.html) and in
[ROADMAP §0](../ROADMAP.md#0-at-a-glance) is a row here. Each row is one live run of the whole
loop (`scripts/smoke/e2e-live.sh`): a one-line intent through intake, standards, planning,
RED/SCAFFOLD/GREEN per task, structural review and the final gate, to merged, tested code in a
real host session (Claude Code, or Codex/OpenCode with `HOST=`) with the approval given up front. The numbers come from the harness
(`run-summary` reads the session's `result` lines and the gate log), never from the model.
`scripts/test/evidence.test.py` fails if a public figure has no row, or a row has no reproduce
command.

**Reproduce:** install the plugin, then run the command in the last column (spends about a
dollar, 11–22 minutes). The run leaves `summary.json`, `summary.md` and a ready-to-paste row in
its run dir; add the row here. Intents: `semver` (one story) and `wordfreq` (two dependent
stories). `HOST=codex` / `HOST=opencode` run the loop on those hosts (`aloop install <host>` into the
run repo, then `codex exec --json` / `opencode run --format json`); their CLIs print no cost line, so
the cost cell reads — and the intent cell names the host. What each run found and fixed is in ROADMAP M0.8.

| run | intent | plugin | checks | wall | cost | wakes | plugin reads | gate blocks | reproduce |
|---|---|---|---|---|---|---|---|---|---|
| run 6 | semver | v0.10.x (master, 2026-10-17) | 11/11 | 11m10s | $0.65 | — | 2 | — | `make e2e-live YES=1 INTENT=semver` |
| run 9 | wordfreq | v0.10.x (master, 2026-10-17) | 12/12 | 19m42s | $1.56 | — | — | 29 | `make e2e-live YES=1 INTENT=wordfreq` |
| run 14 | wordfreq | v0.11.x (master, 2026-10-18) | 12/12 | 13m47s | $0.88 | 2 | — | 11 | `make e2e-live YES=1 INTENT=wordfreq` |
| run 15 | wordfreq | v0.11.x (master, 2026-10-18) | 13/13 | 13m13s | $1.42 | 16 | 6 | 6 | `make e2e-live YES=1 INTENT=wordfreq` |
| run 16 | wordfreq | v0.11.x (master, 2026-10-18) | 12/13 | 13m04s | $0.79 | 4 | 4 | 9 | `make e2e-live YES=1 INTENT=wordfreq` |
| run 17 | wordfreq | v0.11.x (master, 2026-10-18) | 13/13 | 12m58s | $0.95 | 2 | 0 | 6 | `make e2e-live YES=1 INTENT=wordfreq` |
| run 18 | wordfreq | v0.12.0 (fd1fd31) | 13/13 | 21m21s | $1.03 | 6 | 2 | 6 | `make e2e-live YES=1 INTENT=wordfreq` |
| run 19 | wordfreq | v0.12.0 + #29 (b93e6fd) | 13/13 | 15m24s | $1.01 | 1 | 0 | 3 | `make e2e-live YES=1 INTENT=wordfreq` |
| run 20 | semver | v0.12.0 + #31 (915810c) | 12/12 | 10m47s | $2.79 | 1 | 2 | 1 | `make e2e-live YES=1 INTENT=semver` |
| run 21 | semver | v0.12.0 + #33 (1ad94e2) | 12/12 | 8m17s | $0.66 | 1 | 0 | 3 | `make e2e-live YES=1 INTENT=semver` |
| run 22 | wordfreq | v0.12.0 + #33 (7417bb9) | 13/13 | 10m58s | $0.76 | 4 | 1 | 0 | `make e2e-live YES=1 INTENT=wordfreq` |

Columns: *checks* are the e2e script's own assertions (12 for `semver`, 13 for `wordfreq` since
the commit-trace check); *wakes* are supervisor turns that ended and resumed (each re-reads
context; the main cost driver); *plugin reads* are sub-agent tool calls into the plugin's own
source; *gate blocks* are `exit 2` verdicts in the run's `gates.jsonl` (host hooks and
self-checks). A dash means the run predates that counter.
Run 20 cost $2.79 because the supervisor dispatched planners on opus and workers on sonnet against a
project set to haiku; `gate-dispatch` now refuses that (#33), and run 21 is the same intent on haiku.
Run 19's reads were first reported as 5: all five were the planner's `md-db describe --schema` on
the plugin's schemas through a shell variable, which the counter now recognises as schema use.
