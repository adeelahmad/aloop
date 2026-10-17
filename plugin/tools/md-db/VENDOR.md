# Vendored: md-db

This directory is a **vendored copy** of `md-db` (Markdown-as-Database CLI/library),
built into the `md-db` binary the aloop gates shell out to for KDL-schema
artifact validation. It is built from source by `../install.sh`.

- **Upstream:** https://github.com/decisiongraph/md-db-rs
- **Pinned commit:** `41b4eaefa76039f5aae274a37da998a7065a7ade`
- **License:** AGPL-3.0-or-later — see [`LICENSE`](LICENSE). The aloop plugin
  itself is MIT and links to none of this code; it invokes the standalone binary.
  Redistributing this repository carries the AGPL-3.0 obligation for this subtree.

## Re-vendoring a newer upstream

```bash
cd /tmp && rm -rf md-db-rs && git clone https://github.com/decisiongraph/md-db-rs
cd /path/to/aloop
DST=plugin/tools/md-db
rm -rf "$DST" && mkdir -p "$DST"
( cd /tmp/md-db-rs && cp -R Cargo.toml Cargo.lock .cargo crates tests LICENSE README.md SPEC.md llms.txt "$OLDPWD/$DST/" )
printf 'target/\n' > "$DST/.gitignore"
( cd "$DST" && cargo build --release )      # confirm it builds (needs Rust >= 1.85)
# then update the pinned commit above to: git -C /tmp/md-db-rs rev-parse HEAD
```

Excluded from the vendor copy: `.git/`, `target/`, and upstream CI scripts (`scripts/`).
