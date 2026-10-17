# ctx-symbols

Minimal AST symbol resolver that backs two aloop gates
(`gate-scaffold-verify`, `gate-structural-integrity`). Harvested from the
`ctxconfig` code-intelligence layer — `src/ast.rs` (`AstParser::{locate_symbol,
search, process_content}`), `src/plan/resolver.rs` (`resolve_symbol`),
`src/search.rs`, and the duplicate-detection *concept* from
`src/plan/conflicts.rs` reimplemented over the AST symbol tree. None of the
orchestration engine, SQLite store, plan-DSL, IIIF, LSP, or TUI was taken.

Languages: Rust, Python, JavaScript, TypeScript (+TSX), Go (tree-sitter 0.20).

## Build & install

```bash
cargo build --release
cp target/release/ctx-symbols ~/.local/bin/    # or: cargo install --path .
# ensure ~/.local/bin (or ~/.cargo/bin) is on PATH
```

Toolchain note: pinned for Rust 1.77 (`tempfile = "=3.8.1"`, no clap) to avoid
crates that require the unstable `edition2024` feature.

## Commands

```bash
ctx-symbols count   <symbol> --tree .   # prints how many times <symbol> is DEFINED
ctx-symbols locate  <symbol> --tree .   # file:line:col per definition
ctx-symbols search  <symbol> --tree .   # file:line:col:kind per definition
ctx-symbols conflicts        --tree .   # "<SEVERITY>\t<message>" per finding
ctx-symbols conflicts --tree . --fail-on-high   # exit 2 if any HIGH finding
```

Findings:
- `DuplicateDefinition` (HIGH) — one name defined more than once across the tree.
  This is the "defined exactly once" check `gate-scaffold-verify` relies on
  (`count == 1`) and the foundation-poisoning signal for the structural gate.
- `DuplicateBody` (HIGH) — distinct names with byte-identical normalized bodies
  (parallel implementation / duplicate helper). Scaffold `panic!("SUB-AGENT-TODO …")`
  stubs are ignored so the structural gate is only meaningful after GREEN.
- `OrphanSymbol` (LOW) — a declared name never referenced elsewhere (heuristic;
  advisory, never blocks).

Exit codes: `0` success · `2` HIGH finding with `--fail-on-high` · `3` usage/runtime error.

The gates degrade to `grep` with a logged "weaker check" warning if `ctx-symbols`
is not on PATH — the plugin never hard-fails on a missing backend.
