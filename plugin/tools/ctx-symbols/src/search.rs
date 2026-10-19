// search.rs — file walking (harvested from ctxconfig src/search.rs, trimmed) +
// a supported-source-file collector with sensible default excludes.

use anyhow::Result;
use std::path::{Path, PathBuf};
use walkdir::WalkDir;

/// Directory/path fragments excluded from every tree walk.
pub const DEFAULT_EXCLUDES: &[&str] = &[
    "/.git/",
    "/target/",
    "/node_modules/",
    "/.venv/",
    "/dist/",
    "/build/",
    "/.next/",
];

const SUPPORTED_EXTS: &[&str] = &["rs", "py", "js", "ts", "tsx", "go", "c", "h"];

/// Walk `root` and return every supported source file, skipping default-excluded dirs.
pub fn collect_source_files(root: &Path) -> Result<Vec<PathBuf>> {
    let mut out = Vec::new();
    for entry in WalkDir::new(root)
        .follow_links(false)
        .into_iter()
        .filter_entry(|e| !is_excluded(e.path()))
    {
        let entry = entry?;
        if !entry.file_type().is_file() {
            continue;
        }
        let path = entry.path();
        let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
        if SUPPORTED_EXTS.contains(&ext) {
            out.push(path.to_path_buf());
        }
    }
    out.sort();
    Ok(out)
}

/// True if any default-exclude fragment appears in the path.
pub fn is_excluded(path: &Path) -> bool {
    let s = format!("/{}/", path.to_string_lossy().trim_matches('/'));
    DEFAULT_EXCLUDES.iter().any(|frag| s.contains(frag))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn collect_finds_supported_skips_excluded() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        std::fs::write(root.join("a.rs"), "fn a() {}").unwrap();
        std::fs::write(root.join("b.txt"), "nope").unwrap();
        std::fs::create_dir_all(root.join("target")).unwrap();
        std::fs::write(root.join("target/c.rs"), "fn c() {}").unwrap();

        let files = collect_source_files(root).unwrap();
        let names: Vec<String> = files
            .iter()
            .map(|p| p.file_name().unwrap().to_string_lossy().to_string())
            .collect();
        assert!(names.contains(&"a.rs".to_string()));
        assert!(!names.contains(&"b.txt".to_string()));
        assert!(!names.contains(&"c.rs".to_string()), "target/ excluded");
    }
}
