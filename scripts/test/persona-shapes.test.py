#!/usr/bin/env python3
"""The planner persona's "Artifact shapes" list is what the planner copies on its first write,
so each shape it gives must pass planning-artifacts.kdl as written. Live runs 38, 39 and 41: it
said `stage: 1|2`, the planner wrote a bare `stage: 1` (a YAML number), and every sprint plan.md
was refused once by gate-validate-artifact (F020: expected enum (string), got number).
    python3 scripts/test/persona-shapes.test.py
"""
import os
import re
import shutil
import subprocess
import tempfile

ROOT = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
PLUGIN = os.path.join(ROOT, "plugin")
T = tempfile.mkdtemp(prefix="aloop-shapes-")
n = 0

mddb = subprocess.run([os.path.join(PLUGIN, "bin", "ensure-tools"), "--resolve", "md-db"],
                      capture_output=True, text=True).stdout.strip()
if not mddb:
    print("skip - md-db not installed")
    raise SystemExit(0)

text = open(os.path.join(PLUGIN, "agents", "planner.md")).read()
block = text.split("## Artifact shapes", 1)[1].split("\n## ", 1)[0]
items = re.split(r"\n- (?=`)", "\n" + block.split("\n- ", 1)[1])
checked = []
for item in items:
    item = " ".join(item.split())
    m = re.match(r"`([^`]+\.md)`:\s*(.*)", item)
    if not m:
        continue
    rel, rest = m.groups()
    fields, _, sections = rest.partition("·")
    fm = []
    for kv in re.findall(r"`([a-z_]+:[^`]*)`", fields):
        k, v = kv.split(":", 1)
        v = v.strip().split("|")[0].strip()             # `stage: "1"|"2"` → the first choice
        fm.append("%s: %s" % (k, v.replace("SN-NN", "S1-01").replace("N", "1") if k != "type" else v))
    heads = []
    for h in re.findall(r"`(## [^`]+)`", sections):
        heads.append(h.replace("<title>", "first").replace("Tn", "T1"))
    path = os.path.join(T, "docs", "agents", rel.replace("sprintN", "sprint1").replace("sN-NN-<slug>", "s1-01-x"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write("---\n%s\n---\n# x\n\n%s\n" % ("\n".join(fm), "".join("%s\n\nx\n\n" % h for h in heads)))
    checked.append(path)
assert any(p.endswith("sprint1/plan.md") for p in checked), checked

r = subprocess.run([mddb, "validate", "--stdin-list", "--schema", os.path.join(PLUGIN, "schemas", "planning-artifacts.kdl")],
                   input="\n".join(p for p in checked if p.endswith("sprint1/plan.md")), capture_output=True, text=True)
assert '"ok": true' in r.stdout, (open([p for p in checked if p.endswith("sprint1/plan.md")][0]).read(), r.stdout[-1500:])
n += 1
print("ok - the planner persona's sprint plan.md shape (type, sprint, stage, sections) passes planning-artifacts.kdl as written")

shutil.rmtree(T, ignore_errors=True)
print("\n%d checks passed" % n)
