#!/usr/bin/env python3
"""Validate every eval suite (skill + per-agent) parses and conforms. No tokens.

Checks each suite is valid JSON with the required shape, that referenced input
files exist, and (for agent suites) that the named gate is wired in hooks.json and
the agent .md exists. Exits non-zero on any problem — safe to run in CI.
"""
import glob
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLUGIN = os.path.join(REPO, "plugin")


def _err(errs, msg):
    errs.append(msg)


def _check_evals(suite_path, d, errs):
    evals = d.get("evals")
    if not isinstance(evals, list) or not evals:
        _err(errs, f"{suite_path}: 'evals' must be a non-empty list")
        return
    ids = set()
    # File paths are relative to the skill/suite root. When the suite lives in an
    # `evals/` dir (the agentskills.io convention), that root is its parent.
    sdir = os.path.dirname(suite_path)
    base = os.path.dirname(sdir) if os.path.basename(sdir) == "evals" else sdir
    for e in evals:
        eid = e.get("id")
        if eid in ids:
            _err(errs, f"{suite_path}: duplicate eval id {eid}")
        ids.add(eid)
        for field in ("prompt", "expected_output", "assertions"):
            if not e.get(field):
                _err(errs, f"{suite_path}: eval {eid} missing '{field}'")
        if not isinstance(e.get("assertions"), list) or not e.get("assertions"):
            _err(errs, f"{suite_path}: eval {eid} 'assertions' must be a non-empty list")
        for f in e.get("files", []):
            if not os.path.exists(os.path.join(base, f)):
                _err(errs, f"{suite_path}: eval {eid} input file missing: {f}")


def _hooks_gates():
    hooks = json.load(open(os.path.join(PLUGIN, "hooks", "hooks.json")))
    wired = set()
    for blocks in hooks.get("hooks", {}).values():
        for blk in blocks:
            for h in blk.get("hooks", []):
                wired.add(os.path.basename(h.get("command", "").split()[0].strip('"')))
    return wired


def main():
    errs = []
    gates = _hooks_gates()

    skill_suites = glob.glob(os.path.join(PLUGIN, "skills", "*", "evals", "evals.json"))
    agent_suites = sorted(glob.glob(os.path.join(PLUGIN, "evals", "agents", "*.json")))
    if not skill_suites:
        _err(errs, "no skill eval suite found under plugin/skills/*/evals/evals.json")
    agent_files = glob.glob(os.path.join(PLUGIN, "agents", "*.md"))
    if len(agent_suites) != len(agent_files):
        _err(errs, f"expected one per-agent suite per plugin/agents/*.md ({len(agent_files)}), found {len(agent_suites)}")

    for s in skill_suites:
        d = json.load(open(s))
        if not d.get("skill_name"):
            _err(errs, f"{s}: missing 'skill_name'")
        _check_evals(s, d, errs)

    for s in agent_suites:
        d = json.load(open(s))
        role = d.get("agent_role")
        gate = d.get("gate")
        if not role:
            _err(errs, f"{s}: missing 'agent_role'")
        elif not os.path.exists(os.path.join(PLUGIN, "agents", role + ".md")):
            _err(errs, f"{s}: agent file plugin/agents/{role}.md not found")
        if gate and gate not in gates:
            _err(errs, f"{s}: gate '{gate}' is not wired in hooks.json")
        _check_evals(s, d, errs)

    n = len(skill_suites) + len(agent_suites)
    if errs:
        print(f"FAIL: {len(errs)} problem(s) across {n} suite(s):", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"OK: {n} eval suites valid ({len(skill_suites)} skill + {len(agent_suites)} agent)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
