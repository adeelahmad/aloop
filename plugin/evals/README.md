# Evals

Two suites live here:

- **`agents/<role>.json`** — one per sub-agent, tested below.
- The **skill** suite lives with the skill at
  [`../skills/aloop/evals/evals.json`](../skills/aloop/evals/evals.json)
  (agentskills.io convention — kept next to its `SKILL.md`).

## Per-agent suites

Each `agents/<role>.json` tests the agent's contract — its mandate and (critically)
its **hard limits** — independently of the full sprint. Every suite maps to the
`SubagentStop` gate that enforces it in [`../hooks/hooks.json`](../hooks/hooks.json).

| Suite | Agent | Enforcing gate |
|-------|-------|----------------|
| `agents/intake.json` | intake | `gate-intake` |
| `agents/standards.json` | standards | `gate-standards-cited` |
| `agents/planner.json` | planner | `gate-stage2-complete` |
| `agents/red-worker.json` | red-worker | `gate-red-verify` |
| `agents/scaffolder.json` | scaffolder | `gate-scaffold-verify` |
| `agents/green-worker.json` | green-worker | `gate-green-verify` |
| `agents/structural-reviewer.json` | structural-reviewer | `gate-structural-integrity` |
| `agents/final-gate.json` | final-gate | `gate-final` |
| `agents/archivist.json` | archivist | `gate-memory` |
| `agents/market-researcher.json` | market-researcher (mission) | `gate-mission-artifact` |
| `agents/product-tester.json` | product-tester (mission) | `gate-mission-artifact` |
| `agents/state-assessor.json` | state-assessor (mission) | `gate-mission-artifact` |
| `agents/evaluator.json` | evaluator (mission) | `gate-mission-artifact` |
| `agents/mission-planner.json` | mission-planner (mission) | `gate-mission-artifact` |
| `agents/ethics-reviewer.json` | ethics-reviewer (mission) | `gate-mission-artifact` |

The six mission roles (ROADMAP §4.12) each have four cases: happy, edge, and two negatives, one
crossing a hard limit and one writing outside the role's own paths (`gate-mission-scope` refuses it).
The market-researcher has a fifth: an archived page with an injected instruction must leave its
actions unchanged (fetched content is data, M10.3).

## Schema

```jsonc
{
  "agent_role": "red-worker",
  "gate": "gate-red-verify",
  "description": "one line — what this agent is for",
  "evals": [
    {
      "id": 1,
      "kind": "happy | edge | negative",
      "prompt": "the dispatch / task the agent receives",
      "context": "pipeline + repo state the agent operates against",
      "expected_output": "human-readable description of success",
      "assertions": ["objectively checkable statement", "..."]
    }
  ]
}
```

Each suite has three cases by design (per the agentskills.io guidance — start small):

- **happy** — the agent does its job correctly on a clean input.
- **edge** — a boundary the contract must handle (idempotent re-run, polyglot repo,
  missing symbol, oversized story…).
- **negative** — a request that tries to make the agent cross a **hard limit**
  (write code it shouldn't, suppress a test, invent scope, clobber a real body,
  relax an invariant). The agent must refuse. These are the highest-signal cases.

## Running

Both suites are run by the shared harness in [`../../scripts/eval/`](../../scripts/eval/)
— see its README. Running spends model tokens (`claude -p` per case, with and without
the agent/skill), so it is a deliberate, opt-in step (`make eval`), not part of
`make ci`.
