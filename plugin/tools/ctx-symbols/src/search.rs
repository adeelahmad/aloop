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
    // A host's worktree checkouts live INSIDE the main tree (Claude Code: .claude/worktrees/
    // agent-<id>/), so a stale one duplicates every symbol of main (live e2e run 12: 20
    // false HIGH findings). The plugin's own state and transcripts hold no source either.
    "/.claude/worktrees/",
    "/.agentic/",
    "/.transcripts/",
];

const SUPPORTED_EXTS: &[&str] = &["rs", "py", "js", "ts", "tsx", "go"];

/// Walk `root` and return every supported source file, skipping default-excluded dirs.
pub fn collect_source_files(root: &Path) -> Result<Vec<PathBuf>> {
    let mut out = Vec::new();
    for entry in WalkDir::new(root)
        .follow_links(false)
        .into_iter()
        .filter_entry(|e| {
            // judge the path below the walk root: a root that itself sits inside an excluded
            // fragment (a gate scanning .claude/worktrees/agent-<id>) must still be walked
            !is_excluded(e.path().strip_prefix(root).unwrap_or_else(|_| e.path()))
        })
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

    #[test]
    fn collect_skips_host_worktree_checkouts() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        std::fs::create_dir_all(root.join("src")).unwrap();
        std::fs::write(root.join("src/lib.rs"), "pub fn count_words() {}").unwrap();
        for d in [
            ".claude/worktrees/agent-a1b2/src",
            ".agentic/x",
            ".transcripts",
        ] {
            std::fs::create_dir_all(root.join(d)).unwrap();
            std::fs::write(root.join(d).join("lib.rs"), "pub fn count_words() {}").unwrap();
        }
        let files = collect_source_files(root).unwrap();
        assert_eq!(files.len(), 1, "only the main tree's file: {:?}", files);
        assert!(files[0].ends_with("src/lib.rs"));
    }

    #[test]
    fn collect_walks_a_root_inside_an_excluded_fragment() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join(".claude/worktrees/agent-a1b2");
        std::fs::create_dir_all(root.join("src")).unwrap();
        std::fs::write(root.join("src/lib.rs"), "pub fn count_words() {}").unwrap();
        let files = collect_source_files(&root).unwrap();
        assert_eq!(files.len(), 1, "{:?}", files);
    }
}
