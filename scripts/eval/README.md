# Eval harness

Runs the eval suites (the skill suite and the 9 per-agent suites) the
[agentskills.io](https://agentskills.io/llms.txt) way: each test case is run **twice**
— once `with_skill` (the agent/skill instructions injected) and once `without_skill`
(bare prompt) — so you can measure what the instructions actually buy.

## Suites

| Suite | File |
|-------|------|
| skill | `plugin/skills/aloop/evals/evals.json` |
| agents (×9) | `plugin/evals/agents/<role>.json` |

## Pieces

| Script | Spends tokens? | Does |
|--------|----------------|------|
| `run-evals.sh` | **yes** (2 `claude -p` per eval) | runs both arms, writes `outputs/` + `timing.json` |
| `grade.py` | yes (LLM judge) — unless `--dry-run` | grades each arm's outputs vs assertions → `grading.json` |
| `aggregate.py` | no | rolls an iteration up into `benchmark.json` |
| `_extract.py` | no | claude JSON envelope → `result.md` + `timing.json` |

`run-evals.sh` and `grade.py` (without `--dry-run`) call the `claude` CLI and cost
credits. `aggregate.py` and `grade.py --dry-run` are pure file I/O — safe anytime.

## Workspace layout

```
eval-workspace/<suite>/iteration-N/
  eval-<id>/
    with_skill/    { outputs/, timing.json, grading.json }
    without_skill/ { outputs/, timing.json, grading.json }
  benchmark.json
```

The only files you author by hand are the `evals.json` suites. Everything under
`eval-workspace/` is generated and git-ignored.

## Workflow

```bash
# 1) Plan only (no tokens) — prints the call count and exits:
scripts/eval/run-evals.sh --suite plugin/evals/agents/red-worker.json

# 2) Execute both arms (spends tokens — opt in explicitly):
scripts/eval/run-evals.sh --suite plugin/evals/agents/red-worker.json --yes
#    or: make eval SUITE=plugin/evals/agents/red-worker.json

# 3) Grade — start with --dry-run to verify plumbing (no tokens), then drop it:
for d in eval-workspace/red-worker/iteration-1/eval-*/*/; do
  id=$(basename "$(dirname "$d")" | sed 's/eval-//')
  python3 scripts/eval/grade.py --suite plugin/evals/agents/red-worker.json \
    --eval-id "$id" --arm-dir "$d" --dry-run
done

# 4) Aggregate (no tokens):
python3 scripts/eval/aggregate.py eval-workspace/red-worker/iteration-1
```

## Methodology notes

- **with vs without** is implemented by injecting the agent/skill `.md` via
  `claude --append-system-prompt` for the `with_skill` arm only. This A/Bs the
  *instructions* reproducibly without depending on whether the plugin is installed.
- **Grading is strict** by design — the judge must cite concrete evidence for a PASS
  (see `grade.py`'s system prompt). Label-without-substance is a FAIL.
- **Iterate**: read failed assertions + the `result.md` transcripts, improve the
  agent/skill `.md`, then re-run into a fresh `iteration-N+1/`. Drop assertions that
  pass in *both* arms (they don't measure the instructions); investigate ones that
  fail in both (likely a broken assertion or too-hard case).
- **Negative cases carry the most signal** here: every agent suite's third case asks
  the agent to cross a hard limit. A baseline (`without_skill`) model will often
  comply; a good agent `.md` makes the `with_skill` arm refuse. That delta is the point.
