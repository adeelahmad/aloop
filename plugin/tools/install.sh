#!/usr/bin/env bash
# install.sh — build + install the aloop gate backends onto PATH.
#
#   ctx-symbols : built from source in this repo (tools/ctx-symbols).
#   md-db       : built from vendored source in this repo (tools/md-db, AGPL-3.0).
#
# Both are built from source, so a working Rust toolchain is required:
#   rustc/cargo >= 1.82 (the kdl dep in Cargo.lock is pinned to 6.5.0; newer
#   kdl 6.6+ would raise the MSRV to 1.95 — do not `cargo update -p kdl`).
#   macOS:  brew install rust   ·   any:  https://rustup.rs
#
# Usage:  ./plugin/tools/install.sh [BIN_DIR]
#   BIN_DIR defaults to ~/.local/bin (must be on your PATH).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${1:-$HOME/.local/bin}"
mkdir -p "$BIN_DIR"

command -v cargo >/dev/null 2>&1 || {
  echo "ERROR: cargo not found. Install Rust (>=1.85): https://rustup.rs or 'brew install rust'." >&2
  exit 1
}

echo "==> Building ctx-symbols (release)"
( cd "$HERE/ctx-symbols" && cargo build --release )
cp "$HERE/ctx-symbols/target/release/ctx-symbols" "$BIN_DIR/ctx-symbols"
echo "    installed: $BIN_DIR/ctx-symbols ($("$BIN_DIR/ctx-symbols" --version))"

echo
echo "==> Building md-db (release, vendored AGPL-3.0)"
( cd "$HERE/md-db" && cargo build --release )
cp "$HERE/md-db/target/release/md-db" "$BIN_DIR/md-db"
echo "    installed: $BIN_DIR/md-db ($("$BIN_DIR/md-db" --help >/dev/null 2>&1 && echo ok))"

echo
case ":$PATH:" in
  *":$BIN_DIR:"*) echo "PATH OK: $BIN_DIR is on PATH." ;;
  *) echo "WARNING: $BIN_DIR is NOT on your PATH. Add it, e.g.:"; echo "  export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac
echo "Done."
