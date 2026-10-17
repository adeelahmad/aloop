// ctx-symbols — minimal AST symbol resolver for the aloop gates.
//
//   ctx-symbols count   <symbol> --tree .   # prints how many times <symbol> is DEFINED (gate: == 1)
//   ctx-symbols locate  <symbol> --tree .   # prints file:line:col for each definition
//   ctx-symbols search  <symbol> --tree .   # like locate, with the node kind
//   ctx-symbols conflicts        --tree .   # prints "<SEVERITY>\t<message>" findings
//        add --fail-on-high to exit 2 when any HIGH (foundation-poisoning) finding exists
//
// Exit codes: 0 success, 2 = HIGH finding with --fail-on-high, 3 = usage/runtime error.

mod ast;
mod conflicts;
// Harvested symbol-resolution API (resolve_symbol / Resolution). Kept as the
// documented backend surface and exercised by tests; not all called by the CLI.
#[allow(dead_code)]
mod resolver;
mod search;
#[allow(dead_code)]
mod types;

use std::path::{Path, PathBuf};
use std::process::ExitCode;

use anyhow::Result;

use ast::AstParser;
use search::collect_source_files;

const USAGE: &str = "usage:
  ctx-symbols count   <symbol> [--tree DIR]
  ctx-symbols locate  <symbol> [--tree DIR]
  ctx-symbols search  <symbol> [--tree DIR]
  ctx-symbols conflicts        [--tree DIR] [--fail-on-high]";

fn main() -> ExitCode {
    match run() {
        Ok(code) => code,
        Err(e) => {
            eprintln!("ctx-symbols: {e:#}");
            ExitCode::from(3)
        }
    }
}

/// Pull `--tree DIR` (default ".") and `--fail-on-high` out of the positional args.
fn parse_opts(args: &[String]) -> (PathBuf, bool, Vec<String>) {
    let mut tree = PathBuf::from(".");
    let mut fail_on_high = false;
    let mut positional = Vec::new();
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--tree" => {
                if i + 1 < args.len() {
                    tree = PathBuf::from(&args[i + 1]);
                    i += 2;
                } else {
                    eprintln!("ctx-symbols: --tree requires a directory argument");
                    std::process::exit(3);
                }
            }
            "--fail-on-high" => {
                fail_on_high = true;
                i += 1;
            }
            other => {
                positional.push(other.to_string());
                i += 1;
            }
        }
    }
    (tree, fail_on_high, positional)
}

fn run() -> Result<ExitCode> {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    if argv.is_empty() || argv[0] == "-h" || argv[0] == "--help" {
        println!("{USAGE}");
        return Ok(ExitCode::SUCCESS);
    }
    if argv[0] == "--version" || argv[0] == "-V" {
        println!("ctx-symbols {}", env!("CARGO_PKG_VERSION"));
        return Ok(ExitCode::SUCCESS);
    }

    let cmd = argv[0].clone();
    let (tree, fail_on_high, positional) = parse_opts(&argv[1..]);

    match cmd.as_str() {
        "count" => {
            let symbol = require_symbol(&positional)?;
            let locs = conflicts::count_symbol(&tree, &symbol)?;
            println!("{}", locs.len());
            Ok(ExitCode::SUCCESS)
        }
        "locate" => {
            let symbol = require_symbol(&positional)?;
            for (path, line, col, _kind) in definitions(&tree, &symbol)? {
                println!("{}:{}:{}", path.display(), line, col);
            }
            Ok(ExitCode::SUCCESS)
        }
        "search" => {
            let symbol = require_symbol(&positional)?;
            for (path, line, col, kind) in definitions(&tree, &symbol)? {
                println!("{}:{}:{}:{}", path.display(), line, col, kind);
            }
            Ok(ExitCode::SUCCESS)
        }
        "conflicts" => {
            let findings = conflicts::detect_all(&tree)?;
            let mut any_high = false;
            for f in &findings {
                if f.severity() == conflicts::Severity::High {
                    any_high = true;
                }
                println!("{}\t{}", f.severity().as_str(), f.message());
            }
            if fail_on_high && any_high {
                Ok(ExitCode::from(2))
            } else {
                Ok(ExitCode::SUCCESS)
            }
        }
        other => {
            eprintln!("unknown command: {other}\n{USAGE}");
            Ok(ExitCode::from(3))
        }
    }
}

fn require_symbol(positional: &[String]) -> Result<String> {
    positional
        .first()
        .cloned()
        .ok_or_else(|| anyhow::anyhow!("missing <symbol>\n{USAGE}"))
}

/// Every definition of `symbol`: (file, line, col, node kind).
fn definitions(tree: &Path, symbol: &str) -> Result<Vec<(PathBuf, usize, usize, String)>> {
    let mut parser = AstParser::new()?;
    let mut out = Vec::new();
    for file in collect_source_files(tree)? {
        let ext = file.extension().and_then(|e| e.to_str()).unwrap_or("");
        let source = match std::fs::read_to_string(&file) {
            Ok(s) => s,
            Err(_) => continue,
        };
        for sym in parser.collect_symbols(&source, ext)? {
            if sym.name == symbol {
                let (line, col) = AstParser::offset_to_line_col(&source, sym.start);
                out.push((file.clone(), line, col, sym.kind));
            }
        }
    }
    Ok(out)
}
