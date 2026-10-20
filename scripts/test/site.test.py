#!/usr/bin/env python3
"""ROADMAP §4.11: the site (docs/index.html, served by GitHub Pages from /docs). It is one
self-contained page: no external script, style or image; an original inline lamp mark; the
RED explainer; the audit tree; and every public number in it is one that ROADMAP §0 records.
    python3 scripts/test/site.test.py
"""
import os
import re
from html.parser import HTMLParser

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
PAGE = os.path.join(ROOT, "docs", "index.html")
PASS = [0]


def ok(name):
    PASS[0] += 1
    print("ok -", name)


class P(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ext, self.stack, self.ids, self.bad = [], [], set(), []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "link", "img", "iframe") and (a.get("src") or a.get("href", "")).startswith(("http", "//")):
            self.ext.append((tag, a.get("src") or a.get("href")))
        if a.get("id"):
            self.ids.add(a["id"])
        if tag not in ("meta", "br", "stop", "path", "ellipse", "circle", "rect", "line"):
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        elif tag in self.stack:
            self.bad.append(tag)


html = open(PAGE).read()
p = P()
p.feed(html)
assert not p.ext, "external resources: %r" % p.ext
assert not p.bad and not p.stack, (p.bad, p.stack)
ok("docs/index.html is self-contained and well-formed")

assert "lamp" in p.ids and "<svg" in html and 'aria-label="Aladdin' in html and "Disney" not in html
ok("an original inline lamp mark (no borrowed visual language)")

assert {"red", "gates", "audit", "evidence"} <= p.ids
assert "Give the agent something to make false." in html and "aloop events trace" in html
assert "Models explore. Gates decide." in html
ok("the RED explainer, the gates table and the audit tree are on the page")

roadmap = open(os.path.join(ROOT, "docs", "ROADMAP.md")).read()
for claim in re.findall(r"(\d+m\d+s) for (\$\d+\.\d+)", html):
    assert all(c in roadmap for c in claim), "unsourced number %r (not in ROADMAP §0)" % (claim,)
ok("every time/cost number on the page is one ROADMAP §0 records")

readme = open(os.path.join(ROOT, "README.md")).read()
for anchor in re.findall(r"adeelahmad/(?:aloop|agentic-agile)#([\w-]+)", html):
    slug = anchor
    assert any(re.sub(r"[^\w\s-]", "", h).strip().lower().replace(" ", "-") == slug for h in re.findall(r"^##+ (.+)$", readme, re.M)), anchor
assert re.findall(r"adeelahmad/(?:aloop|agentic-agile)#([\w-]+)", html), "the page links the README install section"
ok("README anchors the page links to exist")

print("\n%d checks passed" % PASS[0])
