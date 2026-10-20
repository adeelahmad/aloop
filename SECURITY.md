# Security policy

aloop's gates are its security boundary: a hook decides what an agent may write, run or merge.
So a way around a gate is a security bug, even when no secret is involved.

## Report a vulnerability

Please report privately through GitHub:
**[Report a vulnerability](https://github.com/adeelahmad/aloop/security/advisories/new)**.
If that form does not open for you, open an issue titled "Security contact request" with no
details of the problem, and the maintainer will set up a private channel. Never put the details
of a vulnerability in a public issue.

Include what you ran, what the gate should have blocked, and what happened instead. A failing
test (a `scripts/test/*.test.py` case or a conformance fixture) is the best report: it becomes
the regression test of the fix.

## In scope

- A worker writing outside its worktree or its task's files without a BLOCK.
- A stop or a tool call that passes although its gate failed, crashed or was skipped.
- A BLOCK downgraded to a WARN, or a gate that fails open.
- A forged or edited record in the mission ledger (`docs/agents/mission/ledger.jsonl`) that a
  check accepts, or an approval that no human gave.
- Anything that makes the supervisor commit on main, dispatch on another model, or write
  production code during a sprint.

## Supported versions

Fixes land on `master` and ship in the next release. Only the latest release is supported.
