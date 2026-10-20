// conflicts.rs — duplicate / parallel-implementation / orphan detection over the
// SOURCE TREE. This reimplements the duplicate-collision CONCEPT from ctxconfig's
// src/plan/conflicts.rs (which operated on the plan-DSL `Index`) against the AST
// symbol tree instead — per the harvest rules, the concept is taken, not the code.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::Result;
use sha2::{Digest, Sha256};

use crate::ast::{AstParser, SymbolDef};
use crate::search::collect_source_files;
use crate::types::IncludeMode;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Severity {
    /// Foundation-poisoning: a real structural defect (gate may exit 2).
    High,
    /// Advisory: heuristic finding worth surfacing, not blocking.
    Low,
}

impl Severity {
    pub fn as_str(&self) -> &'static str {
        match self {
            Severity::High => "HIGH",
            Severity::Low => "LOW",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Finding {
    /// One symbol name defined more than once across the tree.
    DuplicateDefinition {
        name: String,
        locations: Vec<(PathBuf, usize)>,
    },
    /// Two+ distinct names with byte-identical normalized bodies (parallel impl / dup helper).
    DuplicateBody { names: Vec<(PathBuf, String)> },
    /// A declared symbol whose name is never referenced anywhere else (heuristic orphan).
    OrphanSymbol { name: String, file: PathBuf },
}

impl Finding {
    pub fn severity(&self) -> Severity {
        match self {
            Finding::DuplicateDefinition { .. } => Severity::High,
            Finding::DuplicateBody { .. } => Severity::High,
            Finding::OrphanSymbol { .. } => Severity::Low,
        }
    }

    pub fn message(&self) -> String {
        match self {
            Finding::DuplicateDefinition { name, locations } => {
                let locs: Vec<String> = locations
                    .iter()
                    .map(|(p, off)| format!("{}@{}", p.display(), off))
                    .collect();
                format!("duplicate definition of `{name}` ({})", locs.join(", "))
            }
            Finding::DuplicateBody { names } => {
                let n: Vec<String> = names
                    .iter()
                    .map(|(p, nm)| format!("{}::{}", p.display(), nm))
                    .collect();
                format!("identical body under multiple names ({})", n.join(", "))
            }
            Finding::OrphanSymbol { name, file } => {
                format!("`{name}` in {} is never referenced", file.display())
            }
        }
    }
}

/// Minimum minified body length to consider for duplicate-body detection (avoid trivial collisions).
const MIN_BODY_LEN: usize = 64;

/// Collect every (file, SymbolDef) across the tree.
fn index_tree(root: &Path) -> Result<Vec<(PathBuf, String, SymbolDef)>> {
    let mut parser = AstParser::new()?;
    let mut out = Vec::new();
    for file in collect_source_files(root)? {
        let ext = file
            .extension()
            .and_then(|e| e.to_str())
            .unwrap_or("")
            .to_string();
        let source = match fs::read_to_string(&file) {
            Ok(s) => s,
            Err(_) => continue,
        };
        for sym in parser.collect_symbols(&source, &ext)? {
            out.push((file.clone(), source.clone(), sym));
        }
    }
    Ok(out)
}

/// Count the definitions of `symbol` across the tree (file, byte-offset of each).
pub fn count_symbol(root: &Path, symbol: &str) -> Result<Vec<(PathBuf, usize)>> {
    let mut out = Vec::new();
    for (file, _src, sym) in index_tree(root)? {
        if sym.name == symbol {
            out.push((file, sym.start));
        }
    }
    Ok(out)
}

/// Detect duplicate definitions, duplicate bodies, and heuristic orphans.
pub fn detect_all(root: &Path) -> Result<Vec<Finding>> {
    let index = index_tree(root)?;
    let mut out = Vec::new();
    out.extend(detect_duplicate_definitions(root, &index));
    out.extend(detect_duplicate_bodies(root, &index)?);
    out.extend(detect_orphans(root, &index)?);
    out.sort_by_key(|f| if f.severity() == Severity::High { 0 } else { 1 });
    Ok(out)
}

/// A file under a `tests/` directory: in Rust each such file is its own integration-test crate.
fn in_test_crate(root: &Path, file: &Path) -> bool {
    file.strip_prefix(root)
        .unwrap_or(file)
        .components()
        .any(|c| c.as_os_str() == "tests")
}

/// A `const` / `static` item: a value, not an implementation.
fn is_value_def(kind: &str) -> bool {
    kind.contains("const") || kind.contains("static")
}

fn detect_duplicate_definitions(
    root: &Path,
    index: &[(PathBuf, String, SymbolDef)],
) -> Vec<Finding> {
    let mut by_name: BTreeMap<String, Vec<(PathBuf, usize)>> = BTreeMap::new();
    for (file, _src, sym) in index {
        // a constant in a test crate pins an expected value; it cannot shadow the code
        if is_value_def(&sym.kind) && in_test_crate(root, file) {
            continue;
        }
        by_name
            .entry(sym.name.clone())
            .or_default()
            .push((file.clone(), sym.start));
    }
    by_name
        .into_iter()
        .filter(|(_, locs)| locs.len() >= 2)
        // the same name once per integration-test file is one per crate: no collision
        .filter(|(_, locs)| {
            let mut files: Vec<&PathBuf> = locs.iter().map(|(f, _)| f).collect();
            files.sort();
            files.dedup();
            !(files.len() == locs.len() && files.iter().all(|f| in_test_crate(root, f)))
        })
        .map(|(name, mut locations)| {
            locations.sort();
            Finding::DuplicateDefinition { name, locations }
        })
        .collect()
}

fn detect_duplicate_bodies(
    root: &Path,
    index: &[(PathBuf, String, SymbolDef)],
) -> Result<Vec<Finding>> {
    let mut parser = AstParser::new()?;
    let mut by_hash: BTreeMap<String, Vec<(PathBuf, String)>> = BTreeMap::new();

    for (file, src, sym) in index {
        // Only function-like declarations carry comparable bodies.
        if !sym.kind.contains("function") && !sym.kind.contains("method") {
            continue;
        }
        let slice = src.get(sym.start..sym.end.min(src.len())).unwrap_or("");
        let ext = file.extension().and_then(|e| e.to_str()).unwrap_or("");
        let normalized = parser
            .process_content(slice, Path::new(&format!("x.{ext}")), IncludeMode::Minified)
            .unwrap_or_else(|_| slice.to_string());
        // Skip scaffold stubs (panic+TODO) and trivially short bodies.
        if normalized.contains("SUB-AGENT-TODO") || normalized.len() < MIN_BODY_LEN {
            continue;
        }
        let mut hasher = Sha256::new();
        hasher.update(normalized.as_bytes());
        let hash = format!("{:x}", hasher.finalize());
        by_hash
            .entry(hash)
            .or_default()
            .push((file.clone(), sym.name.clone()));
    }

    let _ = root;
    Ok(by_hash
        .into_values()
        .filter(|members| {
            // distinct names only — same name in 2 places is a DuplicateDefinition already
            let mut names: Vec<&String> = members.iter().map(|(_, n)| n).collect();
            names.sort();
            names.dedup();
            members.len() >= 2 && names.len() >= 2
        })
        .map(|names| Finding::DuplicateBody { names })
        .collect())
}

/// Heuristic orphan: a symbol name that never appears as a bare token outside its
/// own definition. Cheap, language-agnostic, advisory only (Severity::Low).
fn detect_orphans(root: &Path, index: &[(PathBuf, String, SymbolDef)]) -> Result<Vec<Finding>> {
    // Concatenate all source once; count word-boundary occurrences of each name.
    let mut corpus = String::new();
    for file in collect_source_files(root)? {
        if let Ok(s) = fs::read_to_string(&file) {
            corpus.push_str(&s);
            corpus.push('\n');
        }
    }

    let mut out = Vec::new();
    for (file, _src, sym) in index {
        // Skip common entrypoints that are referenced by the toolchain, not by code.
        if matches!(sym.name.as_str(), "main" | "new" | "default" | "Default") {
            continue;
        }
        let occurrences = count_token(&corpus, &sym.name);
        // 1 occurrence == only the definition itself -> never used.
        if occurrences <= 1 {
            out.push(Finding::OrphanSymbol {
                name: sym.name.clone(),
                file: file.clone(),
            });
        }
    }
    Ok(out)
}

/// Count whole-token occurrences of `token` in `haystack` (identifier boundaries).
fn count_token(haystack: &str, token: &str) -> usize {
    if token.is_empty() {
        return 0;
    }
    let bytes = haystack.as_bytes();
    let tb = token.as_bytes();
    let mut count = 0;
    let mut i = 0;
    while let Some(pos) = haystack[i..].find(token) {
        let abs = i + pos;
        let before_ok = abs == 0 || !is_ident_byte(bytes[abs - 1]);
        let after = abs + tb.len();
        let after_ok = after >= bytes.len() || !is_ident_byte(bytes[after]);
        if before_ok && after_ok {
            count += 1;
        }
        i = abs + tb.len();
    }
    count
}

fn is_ident_byte(b: u8) -> bool {
    b.is_ascii_alphanumeric() || b == b'_'
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write(root: &Path, rel: &str, body: &str) {
        let p = root.join(rel);
        if let Some(parent) = p.parent() {
            std::fs::create_dir_all(parent).unwrap();
        }
        std::fs::write(p, body).unwrap();
    }

    #[test]
    fn count_symbol_counts_across_files() {
        let dir = tempfile::tempdir().unwrap();
        write(dir.path(), "a.rs", "pub fn dup() {}\n");
        write(dir.path(), "b.rs", "pub fn dup() {}\n");
        let locs = count_symbol(dir.path(), "dup").unwrap();
        assert_eq!(locs.len(), 2);
    }

    #[test]
    fn detect_duplicate_definition_is_high() {
        let dir = tempfile::tempdir().unwrap();
        write(dir.path(), "a.rs", "pub fn dup() {}\n");
        write(dir.path(), "b.rs", "pub fn dup() {}\n");
        let findings = detect_all(dir.path()).unwrap();
        assert!(findings
            .iter()
            .any(|f| matches!(f, Finding::DuplicateDefinition { .. })
                && f.severity() == Severity::High));
    }

    #[test]
    fn same_name_in_separate_integration_test_crates_is_not_a_duplicate() {
        // Each tests/*.rs is its own crate: `mod common;` and a local helper in two of them
        // cannot collide (live e2e run 8 halted the chain 57 times on exactly this).
        let dir = tempfile::tempdir().unwrap();
        let t = "mod common;\nfn map() -> u32 { 1 }\n#[test]\nfn t() { assert_eq!(map(), 1); }\n";
        write(dir.path(), "tests/count.rs", t);
        write(dir.path(), "tests/top.rs", t);
        write(dir.path(), "tests/common/mod.rs", "pub fn shared() {}\n");
        let findings = detect_all(dir.path()).unwrap();
        assert!(
            !findings
                .iter()
                .any(|f| matches!(f, Finding::DuplicateDefinition { .. })),
            "{findings:?}"
        );
    }

    #[test]
    fn a_test_crate_redefining_a_source_symbol_is_still_high() {
        let dir = tempfile::tempdir().unwrap();
        write(dir.path(), "src/lib.rs", "pub fn top_n() {}\n");
        write(dir.path(), "tests/top.rs", "fn top_n() {}\n");
        let findings = detect_all(dir.path()).unwrap();
        assert!(findings
            .iter()
            .any(|f| matches!(f, Finding::DuplicateDefinition { .. })
                && f.severity() == Severity::High));
    }

    #[test]
    fn a_test_constant_pinning_a_source_constant_is_not_a_duplicate() {
        // An integration test that pins the expected output in its own `const` is asserting
        // the contract, not reimplementing it (live e2e run 45 halted the chain on `USAGE`).
        let dir = tempfile::tempdir().unwrap();
        write(
            dir.path(),
            "src/main.rs",
            "const USAGE: &str = \"usage: minwc\";\nfn main() { eprintln!(\"{USAGE}\"); }\n",
        );
        write(dir.path(), "tests/cli.rs", "const USAGE: &str = \"usage: minwc\";\n#[test]\nfn t() { assert!(!USAGE.is_empty()); }\n");
        let findings = detect_all(dir.path()).unwrap();
        assert!(
            !findings
                .iter()
                .any(|f| matches!(f, Finding::DuplicateDefinition { .. })),
            "{findings:?}"
        );
    }

    #[test]
    fn a_source_constant_defined_twice_in_source_is_still_high() {
        let dir = tempfile::tempdir().unwrap();
        write(dir.path(), "src/a.rs", "pub const LIMIT: u32 = 1;\n");
        write(dir.path(), "src/b.rs", "pub const LIMIT: u32 = 1;\n");
        let findings = detect_all(dir.path()).unwrap();
        assert!(findings
            .iter()
            .any(|f| matches!(f, Finding::DuplicateDefinition { .. })
                && f.severity() == Severity::High));
    }

    #[test]
    fn unique_symbol_has_no_duplicate_finding() {
        let dir = tempfile::tempdir().unwrap();
        write(
            dir.path(),
            "a.rs",
            "pub fn only_one() { let x = 1; }\npub fn caller() { only_one(); }\n",
        );
        let findings = detect_all(dir.path()).unwrap();
        assert!(!findings
            .iter()
            .any(|f| matches!(f, Finding::DuplicateDefinition { .. })));
    }

    #[test]
    fn scaffold_stubs_do_not_trigger_duplicate_body() {
        let dir = tempfile::tempdir().unwrap();
        write(
            dir.path(),
            "a.rs",
            "pub fn one() { panic!(\"SUB-AGENT-TODO: a\"); }\npub fn two() { panic!(\"SUB-AGENT-TODO: b\"); }\n",
        );
        let findings = detect_all(dir.path()).unwrap();
        assert!(!findings
            .iter()
            .any(|f| matches!(f, Finding::DuplicateBody { .. })));
    }
}
