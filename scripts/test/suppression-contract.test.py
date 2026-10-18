#!/usr/bin/env python3
"""Models explore, gates decide: the final-gate persona's suppression check is the gate's.
`assert_no_suppression` in _gatelib.sh is the one definition of a test suppression; the
final-gate persona must name exactly its patterns and must not fail a sprint on a pattern
the gate accepts. Live e2e run 21: the persona grepped its own list, failed the sprint on a
`#![allow(dead_code)]` in tests/common/mod.rs (a shared test-helper module, the Rust idiom),
and cost a second GREEN attempt and a final-gate retry.
    python3 scripts/test/suppression-contract.test.py
"""
import json
import os
import re

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
P = os.path.join(ROOT, "plugin")
PASS = [0]


def ok(name):
    PASS[0] += 1
    print("ok -", name)


lib = open(os.path.join(P, "bin", "_gatelib.sh")).read()
fn = lib[lib.index("assert_no_suppression() {"):]
fn = fn[:fn.index("\n}\n")]
listed = re.search(r'fail "test-suppression pattern present \(no (.*)\)"', fn).group(1)
patterns = [p.strip() for p in listed.split("/")]
assert patterns == ["#[ignore]", ".skip", "xit", "cfg(not(test))"], patterns

persona = open(os.path.join(P, "agents", "final-gate.md")).read()
mandate = persona[persona.index("## Mandate"):persona.index("## Tools")]
for p in ("#[ignore]", ".skip(", "xit(", "#[cfg(not(test))]"):
    assert "`%s`" % p in mandate, (p, mandate)
assert "selfcheck final-gate" in mandate, mandate
ok("final-gate persona names exactly the gate's suppression patterns and defers to `selfcheck final-gate`")

assert "allow(dead_code)" in mandate and "tests/common/" in mandate and "not a suppression" in mandate, mandate
assert "every suppression pattern" not in persona, "an open-ended list lets the model invent patterns"
ok("a lint allow in shared test helpers is named as not a suppression; no open-ended pattern list")

ev = json.load(open(os.path.join(P, "evals", "agents", "final-gate.json")))
case = [e for e in ev["evals"] if "allow(dead_code)" in e["prompt"] + e["context"]]
assert case and case[0]["kind"] == "edge" and any("certif" in a.lower() for a in case[0]["assertions"]), case
ok("final-gate eval suite covers the run-21 case (allow(dead_code) in tests/common/ with a green matrix)")

print("\n%d checks passed" % PASS[0])
