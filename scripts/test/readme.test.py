#!/usr/bin/env python3
"""ROADMAP §4.11: the README reads product-first and makes no claim the repo cannot back.
    python3 scripts/test/readme.test.py
Checks the section order, that every `aladdin` / `aloop <tool>` command it names exists in
plugin/bin, that the install lines name the real marketplace and plugin, that every relative
link resolves, and that every demo time/cost figure is one ROADMAP §0 records.
"""
import json
import os
import re

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
BIN = os.path.join(ROOT, "plugin", "bin")
PASS = [0]


def ok(name):
    PASS[0] += 1
    print("ok -", name)


readme = open(os.path.join(ROOT, "README.md")).read()
h2 = re.findall(r"^## (.+)$", readme, re.M)

# 1) product-first order (§4.11): intent → demo → TDD agents → loop → gates → install → hosts → evidence → audit → architecture → limitations
want = ["Demo", "Test-driven agents", "How the loop works", "Why failing first matters", "Models explore. Gates decide.",
        "Install / run", "Supported harnesses", "Evidence", "Audit", "Architecture", "Limitations"]
assert [h for h in h2 if h in want] == want, h2
assert re.search(r"^# .*intent is all you need", readme, re.M | re.I), "the title states the thesis"
assert readme.index("# Aladdin") < readme.index("## Demo") < readme.index("### Quick start"), "intent before demo before install"
ok("sections run product-first in the §4.11 order")

# 2) every command the README names exists: `aladdin <sub>` (bin/aladdin) and `aloop <tool>` (bin/<tool>)
tools = set(os.listdir(BIN))
aladdin = open(os.path.join(BIN, "aladdin")).read()
missing = []
for cmd in re.findall(r"`aloop ([a-z][\w-]*)", readme):
    if cmd not in tools and not re.search(r"\b%s\b" % re.escape(cmd), aladdin):
        missing.append("aloop " + cmd)
for sub in re.findall(r"`aladdin ([a-z][\w-]*)", readme):
    if not re.search(r"^\s*[\w|-]*\b%s\b[\w|-]*\)" % re.escape(sub), aladdin, re.M) and sub not in tools:
        missing.append("aladdin " + sub)
assert not missing, "README names tools that do not exist: %r" % missing
ok("every `aloop <tool>` / `aladdin <sub>` the README names exists in plugin/bin")

# 3) the install lines name the real marketplace and plugin
plugin = json.load(open(os.path.join(ROOT, "plugin", ".claude-plugin", "plugin.json")))["name"]
market = json.load(open(os.path.join(ROOT, ".claude-plugin", "marketplace.json")))["name"]
assert "/plugin install %s@%s" % (plugin, market) in readme, (plugin, market)
assert "/plugin marketplace add adeelahmad/" in readme
ok("the quick start installs the plugin and marketplace this repo defines")

# 4) every relative link resolves to a file (anchors to README headings must exist too)
slugs = {re.sub(r"[^\w\s-]", "", h).strip().lower().replace(" ", "-") for h in re.findall(r"^##+ (.+)$", readme, re.M)}
bad = []
for text, target in re.findall(r"\[([^\]]*)\]\(([^)\s]+)\)", readme):
    if target.startswith(("http://", "https://", "mailto:")):
        continue
    path, _, anchor = target.partition("#")
    if path and not os.path.exists(os.path.join(ROOT, path)):
        bad.append(target)
    elif not path and anchor and anchor not in slugs:
        bad.append(target)
assert not bad, "dangling links: %r" % bad
ok("every relative link and in-page anchor resolves")

# 5) every demo time/cost figure is one ROADMAP §0 records (the numbers come from the harness, not the model)
roadmap = open(os.path.join(ROOT, "docs", "ROADMAP.md")).read()
rows = re.findall(r"^\| .+ \| .+ \| (\d+m\d+s) \| (\$\d+\.\d+) \|$", readme, re.M)
assert rows, "the demo table has no measured rows"
for t, c in rows:
    assert t in roadmap and c in roadmap, "unsourced demo figure %r (not in ROADMAP §0)" % ((t, c),)
ok("every demo time/cost figure is one ROADMAP §0 records")

# 6) the offline suite count the README quotes is the Makefile's (docs match the code, CLAUDE.md "done means")
make = open(os.path.join(ROOT, "Makefile")).read()
n = len(set(re.findall(r"scripts/test/(\S+\.test\.(?:py|mjs))", make)))   # python3 … and the `@if … node …` OpenCode line
m = re.search(r"(\d+) Python/Node suites", readme)
assert m and int(m.group(1)) == n, "README says %s suites, the Makefile runs %d" % (m and m.group(1), n)
ok("the README's offline suite count is the Makefile's (%d)" % n)

print("\n%d checks passed" % PASS[0])
