# Contributing

Start with [docs/OVERVIEW.md](docs/OVERVIEW.md) (what the project is) and
[docs/ROADMAP.md](docs/ROADMAP.md) (where it is going).

## Before you open a PR

```bash
make hooks      # install the tracked pre-commit / pre-push hooks
make ci         # what CI runs, except the live `claude plugin validate`
make validate   # `claude plugin validate --strict` (needs the claude CLI)
```

## Rules of the road

- **Gates are the product.** A change that weakens a gate, or turns a BLOCK into a WARN,
  needs a stated reason and a test showing the old failure is still caught.
- **Edit `plugin/pipeline/` first.** Agent files in `plugin/agents/` are assembled from it.
- **Versions move together.** `plugin.json`, the `ctx-symbols` `Cargo.toml` and
  `CHANGELOG.md` must agree (`make version-check`). See the versioning policy in the
  [CHANGELOG](CHANGELOG.md).
- **`plugin/tools/md-db/` is vendored AGPL-3.0 code.** Keep changes there minimal and
  note them in `plugin/tools/md-db/VENDOR.md`.

By contributing you agree your work is licensed under the repository's MIT license
(AGPL-3.0 for the `md-db` subtree).
