// ast.rs — AST parsing via tree-sitter.
// Harvested from ctxconfig src/ast.rs (AstParser::{locate_symbol, search, process_content});
// `collect_symbols` is added here to enumerate ALL declarations for count/conflicts.

use anyhow::{Context, Result};
use std::path::Path;
use tree_sitter::{Language, Parser, Query, QueryCursor, Tree};

use crate::types::{IncludeMode, SearchResult};

/// AST parser for multiple languages.
pub struct AstParser {
    parser: Parser,
    languages: Vec<(String, Language)>,
}

impl AstParser {
    pub fn new() -> Result<Self> {
        let parser = Parser::new();

        let languages = vec![
            ("rs".to_string(), tree_sitter_rust::language()),
            ("py".to_string(), tree_sitter_python::language()),
            ("js".to_string(), tree_sitter_javascript::language()),
            (
                "ts".to_string(),
                tree_sitter_typescript::language_typescript(),
            ),
            ("tsx".to_string(), tree_sitter_typescript::language_tsx()),
            ("go".to_string(), tree_sitter_go::language()),
            ("c".to_string(), tree_sitter_c::language()),
            ("h".to_string(), tree_sitter_c::language()),
        ];

        Ok(Self { parser, languages })
    }

    /// Get language for file extension.
    pub fn get_language(&self, extension: &str) -> Option<Language> {
        self.languages
            .iter()
            .find(|(ext, _)| ext == extension)
            .map(|(_, lang)| *lang)
    }

    /// Parse source code into AST.
    pub fn parse(&mut self, source: &str, language: Language) -> Result<Tree> {
        self.parser
            .set_language(language)
            .context("Failed to set language")?;

        self.parser
            .parse(source, None)
            .context("Failed to parse source code")
    }

    /// Process content based on include mode (used here to normalize bodies before hashing).
    pub fn process_content(
        &mut self,
        source: &str,
        path: &Path,
        mode: IncludeMode,
    ) -> Result<String> {
        let extension = path.extension().and_then(|e| e.to_str()).unwrap_or("");

        let language = match self.get_language(extension) {
            Some(lang) => lang,
            None => return Ok(source.to_string()),
        };

        let tree = self.parse(source, language)?;

        match mode {
            IncludeMode::Full => Ok(source.to_string()),
            IncludeMode::Minified => self.minify(source, &tree, extension),
            IncludeMode::Stubbed => self.stub(source, &tree, extension),
            IncludeMode::AstOnly => Ok(self.ast_to_string(&tree, source)),
        }
    }

    /// Minify code by removing comments and extra whitespace.
    fn minify(&self, source: &str, tree: &Tree, extension: &str) -> Result<String> {
        let language = match self.get_language(extension) {
            Some(lang) => lang,
            None => return Ok(source.to_string()),
        };

        let comment_query = match extension {
            "rs" => "[(line_comment) (block_comment)] @comment",
            "py" => "(comment) @comment",
            "js" | "ts" | "tsx" => "(comment) @comment",
            "go" | "c" | "h" => "(comment) @comment",
            _ => return Ok(source.to_string()),
        };

        let query = match Query::new(language, comment_query) {
            Ok(q) => q,
            Err(_) => return Ok(source.to_string()),
        };

        let mut cursor = QueryCursor::new();

        let mut comment_ranges: Vec<std::ops::Range<usize>> = Vec::new();
        for m in cursor.matches(&query, tree.root_node(), source.as_bytes()) {
            for capture in m.captures {
                comment_ranges.push(capture.node.byte_range());
            }
        }

        comment_ranges.sort_by_key(|r| r.start);

        let source_bytes = source.as_bytes();
        let mut result = Vec::new();
        let mut pos = 0;

        for range in &comment_ranges {
            if range.start > pos {
                result.extend_from_slice(&source_bytes[pos..range.start]);
            }
            pos = range.end;
        }
        if pos < source_bytes.len() {
            result.extend_from_slice(&source_bytes[pos..]);
        }

        let result = String::from_utf8_lossy(&result).to_string();

        let lines: Vec<&str> = result
            .lines()
            .map(|line| line.trim())
            .filter(|line| !line.is_empty())
            .collect();

        Ok(lines.join("\n"))
    }

    /// Create stub by extracting function signatures.
    fn stub(&self, source: &str, tree: &Tree, extension: &str) -> Result<String> {
        let language = match self.get_language(extension) {
            Some(lang) => lang,
            None => {
                let lines: Vec<&str> = source.lines().take(20).collect();
                return Ok(format!("{}\n// ... (truncated)", lines.join("\n")));
            }
        };

        let stub_query = match extension {
            "rs" => "(function_item) @function",
            "py" => "(function_definition) @function",
            "js" | "ts" | "tsx" => "(function_declaration) @function",
            "go" => "(function_declaration) @function",
            _ => {
                let lines: Vec<&str> = source.lines().take(20).collect();
                return Ok(format!("{}\n// ... (truncated)", lines.join("\n")));
            }
        };

        let query = match Query::new(language, stub_query) {
            Ok(q) => q,
            Err(_) => {
                let lines: Vec<&str> = source.lines().take(20).collect();
                return Ok(format!("{}\n// ... (truncated)", lines.join("\n")));
            }
        };

        let mut cursor = QueryCursor::new();
        let mut stubs = Vec::new();

        for m in cursor.matches(&query, tree.root_node(), source.as_bytes()) {
            for capture in m.captures {
                let node = capture.node;
                let text = node.utf8_text(source.as_bytes()).unwrap_or("");
                let sig_line = text.lines().next().unwrap_or(text).trim();
                let sig_clean = sig_line.trim_end_matches('{').trim();
                stubs.push(format!("{} {{ /* stub */ }}", sig_clean));
            }
        }

        if stubs.is_empty() {
            let lines: Vec<&str> = source.lines().take(20).collect();
            Ok(format!("{}\n// ... (truncated)", lines.join("\n")))
        } else {
            Ok(stubs.join("\n\n"))
        }
    }

    /// Convert AST to string representation.
    fn ast_to_string(&self, tree: &Tree, source: &str) -> String {
        let mut result = Vec::new();
        self.walk_tree(&tree.root_node(), source, "", true, &mut result);
        result.join("\n")
    }

    fn walk_tree(
        &self,
        node: &tree_sitter::Node,
        source: &str,
        prefix: &str,
        is_last: bool,
        result: &mut Vec<String>,
    ) {
        let connector = if prefix.is_empty() {
            ""
        } else if is_last {
            "└─ "
        } else {
            "├─ "
        };

        let node_text = if node.child_count() == 0 {
            node.utf8_text(source.as_bytes()).unwrap_or("").trim()
        } else {
            ""
        };

        let display = if node_text.is_empty() || node_text.len() > 50 {
            format!("{}{}", connector, node.kind())
        } else {
            format!(
                "{}{}: \"{}\"",
                connector,
                node.kind(),
                node_text.replace('\n', "\\n")
            )
        };

        result.push(format!("{}{}", prefix, display));

        let child_prefix = format!("{}{}", prefix, if is_last { "   " } else { "│  " });

        let child_count = node.child_count();
        for i in 0..child_count {
            if let Some(child) = node.child(i) {
                let is_last_child = i == child_count - 1;
                self.walk_tree(&child, source, &child_prefix, is_last_child, result);
            }
        }
    }

    /// Search for AST nodes matching a tree-sitter query. (Harvested API; used by tests.)
    #[allow(dead_code)]
    pub fn search(
        &mut self,
        source: &str,
        language: Language,
        query_str: &str,
    ) -> Result<Vec<SearchResult>> {
        let tree = self.parse(source, language)?;

        let query = Query::new(language, query_str).context("Failed to parse query")?;

        let mut cursor = QueryCursor::new();
        let mut results = Vec::new();

        for m in cursor.matches(&query, tree.root_node(), source.as_bytes()) {
            for capture in m.captures {
                let node = capture.node;
                let start = node.start_position();
                let end = node.end_position();
                let text = node.utf8_text(source.as_bytes()).unwrap_or("");

                results.push(SearchResult {
                    path: std::path::PathBuf::new(),
                    node_type: node.kind().to_string(),
                    node_name: text.lines().next().unwrap_or("").to_string(),
                    line_start: start.row + 1,
                    line_end: end.row + 1,
                    snippet: text.lines().take(5).collect::<Vec<_>>().join("\n"),
                });
            }
        }

        Ok(results)
    }

    /// Per-language declaration patterns. `@name` is the symbol name, `@item` the node.
    fn symbol_patterns(extension: &str) -> &'static [&'static str] {
        match extension {
            "rs" => &[
                r#"(struct_item name: (type_identifier) @name) @item"#,
                r#"(enum_item name: (type_identifier) @name) @item"#,
                r#"(trait_item name: (type_identifier) @name) @item"#,
                r#"(type_item name: (type_identifier) @name) @item"#,
                r#"(union_item name: (type_identifier) @name) @item"#,
                r#"(function_item name: (identifier) @name) @item"#,
                r#"(mod_item name: (identifier) @name) @item"#,
                r#"(const_item name: (identifier) @name) @item"#,
                r#"(static_item name: (identifier) @name) @item"#,
                r#"(macro_definition name: (identifier) @name) @item"#,
                // NOTE: `impl_item` is deliberately NOT matched — an impl block is
                // not a *definition* of the type; counting it would make every type
                // with impl blocks read as a duplicate and break the count==1 gate.
            ],
            "py" => &[
                r#"(function_definition name: (identifier) @name) @item"#,
                r#"(class_definition name: (identifier) @name) @item"#,
            ],
            "js" => &[
                r#"(function_declaration name: (identifier) @name) @item"#,
                r#"(class_declaration name: (identifier) @name) @item"#,
                r#"(method_definition name: (property_identifier) @name) @item"#,
                // NOTE: plain `variable_declarator` is NOT matched — it would
                // double-count a name shared by a `const` and a `function`.
            ],
            "ts" | "tsx" => &[
                r#"(function_declaration name: (identifier) @name) @item"#,
                r#"(class_declaration name: (type_identifier) @name) @item"#,
                r#"(interface_declaration name: (type_identifier) @name) @item"#,
                r#"(type_alias_declaration name: (type_identifier) @name) @item"#,
                r#"(enum_declaration name: (identifier) @name) @item"#,
                r#"(method_definition name: (property_identifier) @name) @item"#,
            ],
            "go" => &[
                r#"(function_declaration name: (identifier) @name) @item"#,
                r#"(method_declaration name: (field_identifier) @name) @item"#,
                r#"(type_declaration (type_spec name: (type_identifier) @name)) @item"#,
                r#"(const_declaration (const_spec name: (identifier) @name)) @item"#,
                r#"(var_declaration (var_spec name: (identifier) @name)) @item"#,
            ],
            // C: only a function DEFINITION (`function_definition` = declarator + compound
            // body). A prototype is a `declaration` and a call is a `call_expression`, so
            // neither matches. The declarator nests under one `pointer_declarator` per `*`
            // in the return type (`void *f(...)`, `char **f(...)`).
            "c" | "h" => &[
                r#"(function_definition declarator: (function_declarator declarator: (identifier) @name)) @item"#,
                r#"(function_definition declarator: (pointer_declarator declarator: (function_declarator declarator: (identifier) @name))) @item"#,
                r#"(function_definition declarator: (pointer_declarator declarator: (pointer_declarator declarator: (function_declarator declarator: (identifier) @name)))) @item"#,
            ],
            _ => &[],
        }
    }

    /// Locate a named symbol; returns the byte range of its declaration node (first hit).
    /// (Harvested API; used by the resolver and tests.)
    #[allow(dead_code)]
    pub fn locate_symbol(
        &mut self,
        source: &str,
        extension: &str,
        symbol: &str,
    ) -> Result<Option<(usize, usize)>> {
        let language = match self.get_language(extension) {
            Some(lang) => lang,
            None => return Ok(None),
        };

        let tree = self.parse(source, language)?;

        for pat in Self::symbol_patterns(extension) {
            let query = match Query::new(language, pat) {
                Ok(q) => q,
                Err(_) => continue,
            };

            let name_idx = query.capture_index_for_name("name");
            let item_idx = query.capture_index_for_name("item");
            let (Some(name_idx), Some(item_idx)) = (name_idx, item_idx) else {
                continue;
            };

            let mut cursor = QueryCursor::new();
            for m in cursor.matches(&query, tree.root_node(), source.as_bytes()) {
                let name_node = m.captures.iter().find(|c| c.index == name_idx);
                let item_node = m.captures.iter().find(|c| c.index == item_idx);

                let (Some(name_cap), Some(item_cap)) = (name_node, item_node) else {
                    continue;
                };

                let name_text = name_cap.node.utf8_text(source.as_bytes()).unwrap_or("");
                if name_text == symbol {
                    let r = item_cap.node.byte_range();
                    return Ok(Some((r.start, r.end)));
                }
            }
        }

        Ok(None)
    }

    /// Enumerate EVERY top-level declaration in `source`: (name, start_byte, end_byte).
    /// This is the basis for "defined exactly once" counting and duplicate detection.
    pub fn collect_symbols(&mut self, source: &str, extension: &str) -> Result<Vec<SymbolDef>> {
        let language = match self.get_language(extension) {
            Some(lang) => lang,
            None => return Ok(Vec::new()),
        };

        let tree = self.parse(source, language)?;
        let mut out: Vec<SymbolDef> = Vec::new();

        for pat in Self::symbol_patterns(extension) {
            let query = match Query::new(language, pat) {
                Ok(q) => q,
                Err(_) => continue,
            };
            let name_idx = query.capture_index_for_name("name");
            let item_idx = query.capture_index_for_name("item");
            let (Some(name_idx), Some(item_idx)) = (name_idx, item_idx) else {
                continue;
            };

            let mut cursor = QueryCursor::new();
            for m in cursor.matches(&query, tree.root_node(), source.as_bytes()) {
                let name_node = m.captures.iter().find(|c| c.index == name_idx);
                let item_node = m.captures.iter().find(|c| c.index == item_idx);
                let (Some(name_cap), Some(item_cap)) = (name_node, item_node) else {
                    continue;
                };
                let name = name_cap
                    .node
                    .utf8_text(source.as_bytes())
                    .unwrap_or("")
                    .to_string();
                if name.is_empty() {
                    continue;
                }
                let r = item_cap.node.byte_range();
                let kind = item_cap.node.kind().to_string();
                out.push(SymbolDef {
                    name,
                    kind,
                    start: r.start,
                    end: r.end,
                });
            }
        }

        // Two patterns can match the same node (e.g. impl blocks); dedup by (name, start).
        out.sort_by(|a, b| (a.start, &a.name).cmp(&(b.start, &b.name)));
        out.dedup_by(|a, b| a.start == b.start && a.name == b.name);
        Ok(out)
    }

    /// Convenience: convert a byte offset to (line, column), 1-indexed.
    pub fn offset_to_line_col(source: &str, offset: usize) -> (usize, usize) {
        let offset = offset.min(source.len());
        let mut line = 1usize;
        let mut last_nl = 0usize;
        for (i, b) in source.as_bytes().iter().enumerate().take(offset) {
            if *b == b'\n' {
                line += 1;
                last_nl = i + 1;
            }
        }
        let col = offset.saturating_sub(last_nl) + 1;
        (line, col)
    }
}

/// One declared symbol with its byte range and node kind.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SymbolDef {
    pub name: String,
    pub kind: String,
    pub start: usize,
    pub end: usize,
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    #[test]
    fn get_language_returns_some_for_supported() {
        let parser = AstParser::new().unwrap();
        for ext in ["rs", "py", "js", "ts", "tsx", "go", "c", "h"] {
            assert!(parser.get_language(ext).is_some(), "ext={ext}");
        }
        assert!(parser.get_language("rb").is_none());
    }

    #[test]
    fn locate_symbol_finds_rust_struct() {
        let mut parser = AstParser::new().unwrap();
        let src = "pub struct Foo { a: i32 }\n";
        let r = parser.locate_symbol(src, "rs", "Foo").unwrap();
        assert!(r.is_some());
    }

    #[test]
    fn locate_symbol_finds_go_function() {
        let mut parser = AstParser::new().unwrap();
        let src = "package main\nfunc MyFunc() {}\n";
        assert!(parser.locate_symbol(src, "go", "MyFunc").unwrap().is_some());
    }

    #[test]
    fn locate_symbol_returns_none_for_missing() {
        let mut parser = AstParser::new().unwrap();
        let src = "fn foo() {}\n";
        assert!(parser.locate_symbol(src, "rs", "bar").unwrap().is_none());
    }

    #[test]
    fn collect_symbols_enumerates_all_rust_decls() {
        let mut parser = AstParser::new().unwrap();
        let src = "pub fn a() {}\npub fn b() {}\nstruct C;\n";
        let syms = parser.collect_symbols(src, "rs").unwrap();
        let names: Vec<&str> = syms.iter().map(|s| s.name.as_str()).collect();
        assert!(names.contains(&"a"));
        assert!(names.contains(&"b"));
        assert!(names.contains(&"C"));
    }

    #[test]
    fn impl_blocks_do_not_inflate_type_count() {
        // A struct with two impl blocks must count as exactly ONE definition.
        let mut parser = AstParser::new().unwrap();
        let src = "pub struct Foo { a: i32 }\nimpl Foo { fn a(&self) {} }\nimpl std::fmt::Debug for Foo { fn fmt(&self, _f: &mut std::fmt::Formatter) -> std::fmt::Result { Ok(()) } }\n";
        let syms = parser.collect_symbols(src, "rs").unwrap();
        let n = syms.iter().filter(|s| s.name == "Foo").count();
        assert_eq!(n, 1, "Foo defined once despite impl blocks; got {n}");
    }

    #[test]
    fn js_const_and_function_same_name_not_double_counted() {
        let mut parser = AstParser::new().unwrap();
        let src = "const foo = 1;\nfunction foo() { return 2; }\n";
        let syms = parser.collect_symbols(src, "js").unwrap();
        let n = syms.iter().filter(|s| s.name == "foo").count();
        assert_eq!(n, 1, "only the function declaration counts; got {n}");
    }

    #[test]
    fn collect_symbols_dedups_overlapping_matches() {
        let mut parser = AstParser::new().unwrap();
        // a single fn must not be double-counted by two patterns
        let src = "fn only() {}\n";
        let syms = parser.collect_symbols(src, "rs").unwrap();
        let n = syms.iter().filter(|s| s.name == "only").count();
        assert_eq!(n, 1);
    }

    #[test]
    fn minify_strips_comments_for_body_hashing() {
        let mut parser = AstParser::new().unwrap();
        let src = "// c\nfn foo() { let x = 1; }\n";
        let out = parser
            .process_content(src, Path::new("f.rs"), IncludeMode::Minified)
            .unwrap();
        assert!(!out.contains("// c"));
        assert!(out.contains("fn foo"));
    }
}
