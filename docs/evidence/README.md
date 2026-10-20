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
its run dir; add the row here. Intents: `semver` (one story), `slugify` (one story, Python) and `wordfreq` (two dependent
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
| run 22 | wordfreq | v0.12.0 + #37 (361f8d7) | 13/13 | 16m18s | $1.14 | 2 | 0 | 3 | `make e2e-live YES=1 INTENT=wordfreq` |
| run 23 | wordfreq | v0.12.0 + #33 (7417bb9) | 13/13 | 10m58s | $0.76 | 4 | 1 | 0 | `make e2e-live YES=1 INTENT=wordfreq` |
| run 24 | wordfreq, haiku supervisor | v0.12.0 + #39 (9826566) | 10/13 | 19m09s | $0.66 | 4 | 1 | 6 | `make e2e-live YES=1 INTENT=wordfreq MODEL=haiku` |
| run 25 | wordfreq | v0.12.0 + #40 (9c6a16c) | 13/13 | 16m13s | $0.98 | 3 | 0 | 5 | `make e2e-live YES=1 INTENT=wordfreq` |
| run 26 | wordfreq | v0.12.0 + #41 (685a48b), trimmed playbook | 13/13 | 16m01s | $0.95 | 2 | 1 | 7 | `make e2e-live YES=1 INTENT=wordfreq` |
| run 27 | wordfreq | v0.12.0 + #43 (b983f92) | 13/13 | 9m54s | $0.70 | 1 | 1 | 3 | `make e2e-live YES=1 INTENT=wordfreq` |
| run 28 | wordfreq | v0.12.0 + #47 (dd2e2b6) | 13/13 | 13m14s | $1.15 | 3 | 1 | 4 | `make e2e-live YES=1 INTENT=wordfreq` |
| run 29 | wordfreq | v0.12.0 + #49 (7e57460), `budget contract` | 13/13 | 12m55s | $0.91 | 1 | 0 | 0 | `make e2e-live YES=1 INTENT=wordfreq` |
| run 30 | wordfreq | v0.12.0 + #50 (08d5c92) | 13/13 | 11m27s | $0.70 | 2 | 0 | 4 | `make e2e-live YES=1 INTENT=wordfreq` |
| run 31 | semver | v0.12.0 + #52 (b33c932) | 12/12 | 10m24s | $0.91 | 2 | 1 | 5 | `make e2e-live YES=1` |
| run 32 | semver | v0.12.0 + #53 (5e9a0a9), 11m05s to the final gate then a stale wakeup | 12/12 | 23m30s | $0.90 | 3 | 2 | 2 | `make e2e-live YES=1` |
| run 33 | wordfreq | v0.12.0 + #54 (002e4ff) | 13/13 | 12m20s | $0.84 | 2 | 0 | 5 | `make e2e-live YES=1 INTENT=wordfreq` |
| run 34 | semver | v0.12.0 + #55 (2ceeada) | 12/12 | 10m23s | $0.74 | 2 | 1 | 1 | `make e2e-live YES=1` |
| run 35 | wordfreq, clean env | v0.12.0 + #56 (cc40637) | 13/13 | 12m59s | $0.91 | 2 | 0 | 2 | `make e2e-live YES=1 INTENT=wordfreq` |
| run 36 | slugify (Python), first run | v0.12.0 + #60 (e490d65) | 11/12 | 11m37s | $0.83 | 5 | 0 | 15 | `make e2e-live YES=1 INTENT=slugify` |
| run 37 | slugify (Python), on the run-36 fixes | v0.12.0 + #61 (d830c71) | 12/12 | 9m42s | $0.69 | 1 | 2 | 11 | `make e2e-live YES=1 INTENT=slugify` |
| run 38 | roman (Go), first run | v0.12.0 + #61 (1c7e19b) | 12/12 | 14m15s | $1.02 | 3 | 0 | 9 | `make e2e-live YES=1 INTENT=roman` |
| run 39 | roman (Go), on the init.md fix | v0.12.0 + #62 (0b31961) | 12/12 | 7m52s | $0.56 | 3 | 2 | 5 | `make e2e-live YES=1 INTENT=roman` |
| run 40 | roman (Go), on the GREEN scope fix | v0.12.0 + #62 (5694903) | 12/12 | 13m03s | $0.75 | 1 | 0 | 5 | `make e2e-live YES=1 INTENT=roman` |
| run 41 | camel (JS), first run | v0.12.0 + #63 (3752458) | 12/12 | 6m45s | $0.56 | 1 | 0 | 2 | `make e2e-live YES=1 INTENT=camel` |
| run 42 | extend (brownfield Rust), first run | v0.12.0 + #66 (859661d) | 13/13 | 7m11s | $0.67 | 2 | 4 | 2 | `make e2e-live YES=1 INTENT=extend` |
| run 43 | modify (brownfield Rust), first run | v0.12.0 + #67 (f16433d) | 13/13 | 9m10s | $0.67 | 1 | 0 | 6 | `make e2e-live YES=1 INTENT=modify` |
| run 44 | cli (Rust binary), first run | v0.12.0 + #68 (216dd4c) | 13/13 | 24m55s | $1.54 | 3 | 0 | 6 | `make e2e-live YES=1 INTENT=cli` |
| run 45 | cli (Rust binary), on the task-sizing rule | v0.12.0 + #76 (eb00a74) | 13/13 | 16m22s | $1.07 | 3 | 0 | 8 | `make e2e-live YES=1 INTENT=cli` |

Columns: *checks* are the e2e script's own assertions (12 for `semver`, 13 for `wordfreq` since
the commit-trace check); *wakes* are supervisor turns that ended and resumed (each re-reads
context; the main cost driver); *plugin reads* are sub-agent tool calls into the plugin's own
source; *gate blocks* are `exit 2` verdicts in the run's `gates.jsonl` (host hooks and
self-checks). A dash means the run predates that counter.
Run 20 cost $2.79 because the supervisor dispatched planners on opus and workers on sonnet against a
project set to haiku; `gate-dispatch` now refuses that (#33), and run 21 is the same intent on haiku.
Run 24 is an experiment, not a public number: a haiku supervisor cost the same as sonnet in twice
the turns and planned the sprint in the run-data dir (now refused by the gates).
Run 19's reads were first reported as 5: all five were the planner's `md-db describe --schema` on
the plugin's schemas through a shell variable, which the counter now recognises as schema use.
Run 28's supervisor made the task worktrees by hand (`aloop task-worktree add`) instead of with
`isolation: "worktree"`, and wrote only the main-tree contract. RED's `aloop bind` therefore failed,
and its stop blocked twice in the shared tree. Since then, `gate-dispatch` copies the contract into a
task worktree on every dispatch. 2 of its 4 blocks were self-check catches.
Run 42 is the first change to existing code: the seed crate's `parse` and its three tests were
left as they were (the merge adds 10 lines to `src/lib.rs` and a new `tests/compare.rs`), and the
seed's tests still pass on main (the extra 13th check).
Run 43 changed an existing, tested function for the first time: `parse` gained 3 lines (trim, then
strip a leading `v`/`V`), its signature and seed tests were kept, and 6 of 6 blocks were the
workers' own self-checks (2 of them RED's `cargo fmt` on its new tests).
Run 44 is the first application: the merged `minwc` binary counted stdin and exited 2 on a bad
flag (the extra check runs it). It cost the most of any one-intent run because the planner made
2 stories and 7 tasks (one per count, one per flag behaviour) for a small tool, so the
supervisor ran 7 RED/SCAFFOLD/GREEN cycles in 56 turns. A first try at 18:32 UTC stopped in
planning on the account's weekly usage limit and is not counted.
Run 45 (the planner sizing tasks up to the budget) planned the same tool as 3 tasks (from 7):
$1.07 and 16m22s (from $1.54 and 24m55s). 6 of its 8 blocks were one false positive:
`ctx-symbols conflicts` read the test's own `const USAGE` (the expected usage line) as a HIGH
duplicate of the binary's, so the structural reviewer halted twice. A constant in a test crate
no longer counts as a duplicate definition; a test function redefining a source one still does.
