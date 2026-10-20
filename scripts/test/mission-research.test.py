#!/usr/bin/env python3
"""ROADMAP §4.12 (M9.1): research. Every claim cites a verbatim quote from an archived,
content-addressed source snapshot (or earlier claims); every snapshot's bytes match the
sha256 recorded for it; a quote that is not in its snapshot is refused at claim time and
blocked by the check; a hand edit breaks the seal, and a re-sealed one no longer matches the
event log; citations (rsr_<id>#cN) resolve only to claims of a recorded report that passes the
check. Network: a local http.server only.
    python3 scripts/test/mission-research.test.py
"""
import contextlib
import hashlib
import http.server
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
AREAS = os.path.join(ROOT, "mission")
T = os.path.realpath(tempfile.mkdtemp(prefix="aloop-research-"))
OUT = os.path.realpath(tempfile.mkdtemp(prefix="aloop-research-outside-"))   # a dir outside the project
ENV = dict(os.environ, HOME=os.path.join(T, "home"), no_proxy="127.0.0.1,localhost", NO_PROXY="127.0.0.1,localhost")
ENV.pop("AGENTIC_HOST", None)
PASS = [0]
HEX64 = re.compile(r"^[0-9a-f]{64}$")
sys.path[:0] = [BIN, AREAS]
import _mission as M  # noqa: E402
import research  # noqa: E402


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def run(*args, cwd=None, timeout=120):
    """The CLI never crashes: whatever the input, it answers with an exit code and BLOCK lines."""
    r = subprocess.run(args, cwd=cwd or T, env=ENV, capture_output=True, text=True, timeout=timeout)
    assert "Traceback" not in r.stderr and "internal error" not in r.stderr, (args, r.stderr)
    return r


def cli(*args):
    """research.main in this process (fast): (exit, stdout, stderr); never an internal error."""
    o, e = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
        rc = research.main(list(args), T)
    assert "internal error" not in e.getvalue(), (args, e.getvalue())
    return rc, o.getvalue(), e.getvalue()


def events(action=None):
    p = os.path.join(T, ".aladdin", "logs", "events.jsonl")
    evs = [json.loads(line) for line in read(p).decode().splitlines()] if os.path.exists(p) else []
    return [e for e in evs if e["event"] == "research.recorded" and (action is None or e["data"].get("action") == action)]


def read(p):
    with open(p, "rb") as f:
        return f.read()


def write(p, data):
    with open(p, "wb") as f:
        f.write(data if isinstance(data, bytes) else json.dumps(data).encode())


def load(p):
    return json.loads(read(p))


def snap(sha):
    return os.path.join(T, "docs", "agents", "mission", "research", "snapshots", sha)


subprocess.run(["git", "init", "-q"], cwd=T, check=True)
MI = os.path.join(BIN, "mission")
R = (MI, "research")

# ── a local web server: the "internet" of this test ───────────────────────────────────
PAGE = ("<!doctype html><html><head><title>Rival  Notes &amp; More</title>"
        "<script>var s = \"hidden script text here\";</script><style>.x { color: red }</style></head>"
        "<body><div><p>Todoist&nbsp;supports “natural language” dates&mdash;it&#39;s fast…</p>"
        "<!-- a hidden comment quote --><p>Pricing starts at $4 per month.</p></div></body></html>").encode()
SITE = {"/page.html": (200, "text/html; charset=utf-8", PAGE), "/empty": (200, "text/plain", b""),
        "/missing": (404, "text/plain", b"not here")}
REDIRECTS = {"/old": "/page.html", "/evil": "file:///etc/passwd", "/ftp": "ftp://127.0.0.1:1/x"}


class Site(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in REDIRECTS:
            self.send_response(302)
            self.send_header("Location", REDIRECTS[self.path])
            self.end_headers()
            return
        if self.path == "/garbage":                      # not an HTTP response at all
            self.wfile.write(b"garbage not http\r\n\r\n")
            return
        if self.path == "/short":                        # says 1000 bytes, sends 36, closes
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "1000")
            self.end_headers()
            self.wfile.write(b"Pricing starts at $4 per month. Tru")
            return
        if self.path == "/chunked-cut":                  # a chunk cut short
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.write(b"64\r\nonly ten b")
            return
        if self.path == "/notype":                       # no Content-Type: the bytes are sniffed
            body = b"<html><body><p>Untyped pages are sniffed for markup.</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        code, ctype, body = SITE.get(self.path, (404, "text/plain", b"no"))
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % srv.server_address[1]

# 1) nothing yet: check passes with nothing to check; list is empty; the area is discovered
r = run(*R, "check")
assert r.returncode == 0 and "no research yet" in r.stderr, (r.returncode, r.stderr)
r = run(*R, "check", "--json")
assert r.returncode == 0 and json.loads(r.stdout) == {"exit": 0, "problems": [], "checked": []}, r.stdout
r = run(*R, "list", "--json")
assert r.returncode == 0 and json.loads(r.stdout) == [], r.stdout
assert "research" in run(MI, "--list").stdout.split()
r = run(*R, "--help")
assert r.returncode == 0 and "Usage:" in r.stdout and "mission research claim" in r.stdout, r.stdout
assert run(*R, "frobnicate").returncode == 64
ok("no research yet: check passes with nothing to check; `mission research` is a discovered area")

# 2) new: a title and a question; a sealed, recorded report with a harness-issued id
assert run(*R, "new", "Rivals", "--role", "market-researcher").returncode == 64          # no --question
assert run(*R, "new", "--question", "who are the rivals?").returncode == 64               # no title
assert run(*R, "new", "Rivals", "--question", "q?", "--role", "builder").returncode == 64  # unknown role
assert run(*R, "new", "Rivals", "--question", "q?", "--bogus", "1").returncode == 64       # unknown option
rc, _, err = cli("new", b"caf\xe9".decode("utf-8", "surrogateescape"), "--question", "q?")   # not UTF-8: usage
assert rc == 64 and "BLOCK[research]" in err and "UTF-8" in err, err     # (the `mission` entry decodes argv itself)
assert not os.path.isdir(os.path.join(T, "docs", "agents", "mission", "research"))
r = run(*R, "new", "To-do app rivals", "--question", "who are the best to-do apps and why?")
assert r.returncode == 0, r.stderr
P = os.path.realpath(r.stdout.strip())
assert os.path.dirname(P) == os.path.join(T, "docs", "agents", "mission", "research"), P
doc = load(P)
RID = doc["research_id"]
assert M.ID.match(RID) and RID.startswith("rsr_") and os.path.basename(P) == RID + ".json"
assert doc["status"] == "recorded" and doc["role"] == "market-researcher" and doc["mission_id"] is None
assert doc["sources"] == [] and doc["claims"] == [] and doc["sha256"] == M.digest(doc)
assert M.problems(doc, "research", P) == []
ev = events("new")
assert len(ev) == 1 and ev[0]["data"]["research_id"] == RID and ev[0]["refs"]["research"].endswith(RID + ".json")
assert ev[0]["data"]["report_sha256"] == doc["sha256"] and ev[0]["data"]["status"] == "recorded", ev[0]
r = run(*R, "check", RID)
assert r.returncode == 2 and "BLOCK[research]" in r.stderr and "no claims" in r.stderr, r.stderr
assert run(MI, "init", "the best to-do app", "--mode", "autonomous", "--budget-usd", "5").returncode == 0
r = run(*R, "new", "Benchmarks", "--question", "how fast are they?", "--role", "product-tester")
assert r.returncode == 0, r.stderr
P2 = os.path.realpath(r.stdout.strip())
doc2 = load(P2)
RID2 = doc2["research_id"]
assert doc2["mission_id"] == load(M.path_of(T, "mission"))["mission_id"] and doc2["role"] == "product-tester"
ok("research new: question + title required (UTF-8); a sealed recorded report with a harness id and the mission's id")

# 3) archive over http: content-addressed snapshot, HTML title, dedupe on same URL + bytes, provenance
r = run(*R, "archive", RID, BASE + "/page.html")
assert r.returncode == 0 and r.stdout.strip() == "s1", (r.stdout, r.stderr)
doc = load(P)
s1 = doc["sources"][0]
sha = hashlib.sha256(PAGE).hexdigest()
assert s1["sha256"] == sha and s1["snapshot"] == "research/snapshots/" + sha and s1["bytes"] == len(PAGE)
assert s1["media_type"] == "text/html" and s1["url"] == BASE + "/page.html" and s1["title"] == "Rival Notes & More"
assert s1["via"] == "fetch" and s1["final_url"] == BASE + "/page.html" and "file" not in s1, s1
assert read(snap(sha)) == PAGE and s1["retrieved_at"].endswith("Z")
assert doc["sha256"] == M.digest(doc)
assert not [f for f in os.listdir(os.path.dirname(P)) if f.endswith(".json") and f.startswith(sha[:8])]
r = run(*R, "archive", RID, BASE + "/page.html")                          # same URL, same bytes
assert r.returncode == 0 and r.stdout.strip() == "s1" and len(load(P)["sources"]) == 1, r.stderr
assert len(events("archive")) == 1
r = run(*R, "archive", RID, BASE + "/old", "--title", "Redirected")    # redirects are followed
assert r.returncode == 0 and r.stdout.strip() == "s2", r.stderr
s2 = load(P)["sources"][1]
assert s2["title"] == "Redirected" and s2["sha256"] == sha
assert s2["url"] == BASE + "/old" and s2["final_url"] == BASE + "/page.html", s2   # who really served the bytes
PAGE_V2 = PAGE.replace(b"$4 per month", b"$5 per month")
SITE["/page.html"] = (200, "text/html; charset=utf-8", PAGE_V2)
r = run(*R, "archive", RID, BASE + "/page.html")                          # same URL, changed bytes
assert r.returncode == 0 and r.stdout.strip() == "s3", r.stderr
assert load(P)["sources"][2]["sha256"] == hashlib.sha256(PAGE_V2).hexdigest()
ev = events("archive")
assert len(ev) == 3 and ev[-1]["data"]["source_id"] == "s3" and ev[-1]["refs"]["snapshot"].startswith("docs/agents/mission/research/snapshots/")
assert ev[1]["data"]["via"] == "fetch" and ev[1]["data"]["final_url"] == BASE + "/page.html", ev[1]["data"]
assert ev[-1]["data"]["report_sha256"] == load(P)["sha256"]
ok("archive over http: a sha256-named snapshot, the page title, redirects followed (final URL recorded), "
   "same URL+bytes reused, new bytes a new id")

# 4) archive refusals: non-http(s) or malformed URLs, empty, short or broken bodies, fetch errors, unsafe
#    redirects, files outside the project or not regular, unknown report
before = load(P)
for url, why in (("file:///etc/passwd", "http"), ("ftp://example.com/x", "http"), ("not a url", "http"),
                 ("http://[::1", "http"), ("https://[bad/x", "http"), ("http://127.0.0.1:abc/", "http"),
                 ("http://:80/x", "http"),
                 (BASE + "/empty", "empty"), (BASE + "/missing", "404"), (BASE + "/evil", "redirect"),
                 (BASE + "/ftp", "a redirect off http(s) is refused"),       # the module's own redirect guard
                 (BASE + "/garbage", "could not fetch"), (BASE + "/chunked-cut", "could not fetch"),
                 (BASE + "/short", "incomplete body")):
    r = run(*R, "archive", RID, url)
    assert r.returncode == 2 and "BLOCK[research]" in r.stderr and why in r.stderr.lower(), (url, r.returncode, r.stderr)
r = run(*R, "archive", RID, "http://[::1", "--file", "page.txt")
assert r.returncode == 2 and "http" in r.stderr, r.stderr
big = os.path.join(T, "big.txt")
with open(big, "wb") as f:
    f.write(b"a" * (5 * 1024 * 1024 + 1))
r = run(*R, "archive", RID, "https://example.com/big", "--file", big)
assert r.returncode == 2 and "5 MB" in r.stderr, r.stderr
write(os.path.join(T, "empty.txt"), b"")
r = run(*R, "archive", RID, "https://example.com/e", "--file", "empty.txt")
assert r.returncode == 2 and "empty" in r.stderr, r.stderr
r = run(*R, "archive", RID, "https://example.com/e", "--file", "nope.txt")
assert r.returncode == 2 and "nope.txt" in r.stderr, r.stderr
secret = os.path.join(OUT, "credentials")                                  # --file only inside the project
write(secret, b"API_TOKEN=sk-live-0123456789 do not archive me\n")
os.symlink(secret, os.path.join(T, "innocent.txt"))
os.mkfifo(os.path.join(T, "pipe.txt"))
os.makedirs(os.path.join(T, "adir.txt"))
for f, why in ((secret, "outside the project"), (os.path.relpath(secret, T), "outside the project"),
               ("innocent.txt", "outside the project"), ("/dev/zero", "outside the project"),
               ("pipe.txt", "regular file"), ("adir.txt", "regular file")):
    r = run(*R, "archive", RID, "https://docs.example/page", "--file", f, timeout=30)
    assert r.returncode == 2 and "BLOCK[research]" in r.stderr and why in r.stderr, (f, r.returncode, r.stderr)
if os.path.exists("/proc/self/environ"):
    r = run(*R, "archive", RID, "https://docs.example/env", "--file", "/proc/self/environ")
    assert r.returncode == 2 and "outside the project" in r.stderr, r.stderr
assert not any(read(os.path.join(dp, x)).startswith(b"API_TOKEN") for dp, _, xs in
               os.walk(os.path.join(T, "docs", "agents", "mission", "research", "snapshots")) for x in xs)
r = run(*R, "archive", "rsr_000000000000deadbeef", BASE + "/page.html")
assert r.returncode == 2 and "no research report" in r.stderr, r.stderr
assert run(*R, "archive", RID).returncode == 64
assert load(P) == before and len(events("archive")) == 3
ok("archive refuses non-http(s) or malformed URLs, empty, oversized, short or broken bodies, failed fetches, "
   "a redirect off http(s), --file outside the project or not a regular file, unknown reports")

# 5) archive --file: an agent-saved text form, and a binary (PDF) snapshot; both marked as read from a file
with open(os.path.join(T, "paper.txt"), "w") as f:
    f.write("Abstract\nUsers   abandon to-do apps\nwithin two weeks when capture takes more than 3 taps.\n")
r = run(*R, "archive", RID, "https://arxiv.org/abs/2601.00001", "--file", "paper.txt", "--title", "Abandonment study")
assert r.returncode == 0 and r.stdout.strip() == "s4", r.stderr
s4 = load(P)["sources"][3]
assert s4["media_type"] == "text/plain" and s4["url"] == "https://arxiv.org/abs/2601.00001" and s4["title"] == "Abandonment study"
assert s4["via"] == "file" and s4["file"] == "paper.txt" and "final_url" not in s4, s4
ev = events("archive")[-1]["data"]
assert ev["via"] == "file" and ev["file"] == "paper.txt", ev
PDF = b"%PDF-1.4\n\x00\x01\x02binary stream users abandon to-do apps\n%%EOF\n"
with open(os.path.join(T, "paper.pdf"), "wb") as f:
    f.write(PDF)
r = run(*R, "archive", RID, "https://arxiv.org/pdf/2601.00001", "--file", os.path.join(T, "paper.pdf"))
assert r.returncode == 0 and r.stdout.strip() == "s5", r.stderr
s5 = load(P)["sources"][4]
assert s5["media_type"] == "application/pdf" and s5["title"] == "https://arxiv.org/pdf/2601.00001"
assert s5["via"] == "file" and s5["file"] == "paper.pdf", s5
assert read(snap(s5["sha256"])) == PDF
ok("archive --file: the saved bytes are the snapshot (text form or binary), media type from the file, "
   "provenance via=file with the project-relative path")

# 6) claim: quotes are verified against the snapshot (normalized), else refused and nothing is written
r = run(*R, "claim", RID, "Todoist parses natural-language dates", "--source", "s1",
        "--quote", 'Todoist supports "natural language" dates-it\'s fast...', "--kind", "feature")
assert r.returncode == 0 and r.stdout.strip() == "c1", (r.stdout, r.stderr)
c1 = load(P)["claims"][0]
assert c1 == {"claim_id": "c1", "text": "Todoist parses natural-language dates", "kind": "feature",
              "evidence": [{"source": "s1", "quote": 'Todoist supports "natural language" dates-it\'s fast...'}]}, c1
r = run(*R, "claim", RID, "Todoist is cheap", "--source", "s1", "--quote", "PRICING   starts at $4", "--source", "s4",
        "--quote", "users abandon to-do apps within two weeks", "--kind", "rival")
assert r.returncode == 0 and r.stdout.strip() == "c2", r.stderr
assert [e["source"] for e in load(P)["claims"][1]["evidence"]] == ["s1", "s4"]
ev = events("claim")
assert len(ev) == 2 and ev[-1]["data"]["claim_id"] == "c2" and ev[-1]["data"]["research_id"] == RID
before, raw = load(P), read(P)
NOT_IN = ["s1", "not in its snapshot"]
cases = [
    (("--source", "s1", "--quote", "hidden script text here"), 2, ["s1", "hidden script text here"]),
    (("--source", "s1", "--quote", "a hidden comment quote"), 2, ["s1", "a hidden comment quote"]),
    (("--source", "s1", "--quote", "Pricing starts at $5"), 2, ["s1", "Pricing starts at $5"]),    # that is s3
    (("--source", "s1", "--quote", "the"), 2, ["too short"]),
    (("--source", "s9", "--quote", "Pricing starts at $4"), 2, ["s9"]),
    (("--source", "s5", "--quote", "users abandon to-do apps"), 2, ["binary", "--file"]),
    # a quote's own markup is text to find, never stripped: made-up words inside a tag, comment or script fail
    (("--source", "s1", "--quote", "Pricing starts at $4 <div Todoist is free forever and beats everyone> per month"),
     2, NOT_IN),
    (("--source", "s1", "--quote", "<p>Pricing starts at $4 <!-- invented: rivals are bankrupt --> per month"), 2, NOT_IN),
    (("--source", "s1", "--quote", "<script>rivals are bankrupt</script>Pricing starts at $4 per month"), 2, NOT_IN),
    (("--source", "s1", "--quote", '<p title="Acme has 10 million paying customers">pricing starts at $4 per month'),
     2, NOT_IN),
    (("--source", "s4", "--quote", "<div><script>Acme was acquired</script>users abandon to-do apps"), 2, ["s4", "not in"]),
    # whole words only, and never across two blocks of a page
    (("--source", "s1", "--quote", "ricing starts at $4"), 2, NOT_IN),
    (("--source", "s1", "--quote", "Pricing starts at $4 per mont"), 2, NOT_IN),
    (("--source", "s4", "--quote", "sers abandon to-do"), 2, ["s4", "not in"]),
    (("--source", "s1", "--quote", "fast... Pricing starts"), 2, NOT_IN),
    (("--source", "s1", "--source", "s4", "--quote", "Pricing starts at $4"), 64, ["--source", "--quote"]),
    (("--kind", "rival"), 64, ["--source"]),
    (("--source", "s1", "--quote", "Pricing starts at $4", "--from", "c1"), 64, ["--from"]),
    (("--source", "s1", "--quote", "Pricing starts at $4", "--kind", "gossip"), 64, ["--kind"]),
    (("--from", "c3"), 2, ["c3"]),                      # c3 would be this claim: not earlier
    (("--from", "c1", "c9"), 2, ["c9"]),
]
for args, rc, words in cases:
    r = run(*R, "claim", RID, "a claim", *args)
    assert r.returncode == rc and "BLOCK[research]" in r.stderr, (args, r.returncode, r.stderr)
    for w in words:
        assert w in r.stderr, (args, w, r.stderr)
assert run(*R, "claim", RID, "--source", "s1", "--quote", "Pricing starts at $4").returncode == 64   # no text
def undecodable(b):
    return b.decode("utf-8", "surrogateescape")      # argv as Python gets bytes that are not UTF-8


rc, _, err = cli("claim", RID, undecodable(b"caf\xe9 claim"), "--source", "s1", "--quote", "Pricing starts at $4")
assert rc == 64 and "UTF-8" in err, err
rc, _, err = cli("claim", RID, "a claim", "--source", "s1", "--quote", undecodable(b"Pricing starts at $4 \xe9"))
assert rc == 64 and "UTF-8" in err, err
assert read(P) == raw and len(events("claim")) == 2
r = run(*R, "claim", RID, "Price is the rival's edge", "--from", "c1", "c2", "--kind", "other")
assert r.returncode == 0 and r.stdout.strip() == "c3", r.stderr
assert load(P)["claims"][2]["from_claims"] == ["c1", "c2"] and "evidence" not in load(P)["claims"][2]
r = run(*R, "claim", RID, "Pricing rose", "--source", "s3", "--quote", "Pricing starts at $5 per month", "--from", "c3")
assert r.returncode == 64
r = run(*R, "claim", RID, "Pricing rose to $5", "--source", "s3", "--quote", "Pricing starts at $5 per month.")
assert r.returncode == 0 and r.stdout.strip() == "c4", r.stderr
r = run(*R, "claim", RID, "Rivals change prices", "--from", "c4", "--from", "c2")
assert r.returncode == 0 and r.stdout.strip() == "c5" and load(P)["claims"][4]["from_claims"] == ["c4", "c2"], r.stderr
r = run(*R, "check", RID)
assert r.returncode == 0 and "ok" in r.stderr, r.stderr
ok("claim: quotes found in the snapshot pass; script/comment text, markup inside a quote, part-words, quotes across "
   "blocks, wrong bytes, trivial or binary-backed quotes, bad ids and non-UTF-8 arguments refused")

# 7) normalization: tags, script/style/comments, entities, curly quotes, dashes, nbsp, ellipsis, case, spaces
N = research.normalize
html_page = b"<HTML><body><P>Caf\xc3\xa9&nbsp;\xe2\x80\x98quoted\xe2\x80\x99 &ldquo;x&rdquo; a&ndash;b \xe2\x88\x92 c\xe2\x80\xa6</P>" \
    b"<SCRIPT type='x'>alert('no')</SCRIPT><style>p{}</style><!-- gone --></body></HTML>"
assert N(html_page, "") == "café 'quoted' \"x\" a-b - c...", N(html_page, "")
assert N(b"a <b>bold</b> word", "text/html") == "a bold word"
assert N(b"if a < b and c > d then", "text/plain") == "if a < b and c > d then"
assert N(b"\xff\xfeodd bytes", "text/plain").endswith("odd bytes")
assert N("„Low“ prices — ‚really‛ ‟so‟") == "\"low\" prices - 'really' \"so\""
assert N("  MANY\n\tspaces  ") == "many spaces"
assert N(b"<p>x</p><script>never here</script>", "text/html").find("never") == -1
# block-level tags separate (a quote can't span two blocks); inline tags are a space
assert N(b"<p>Welcome to the\n user forum.</p><p>Read the docs.</p>", "text/html") == "welcome to the user forum.\nread the docs."
# a quote is plain text: its markup is kept, so it must be in the page as text
assert N("Pricing <div fake> per month", markup=False) == "pricing <div fake> per month"
assert N("<p>Pricing</p> &amp; more", markup=False) == "<p>pricing</p> & more"
# escaped tags in a page are text; a plain-text snapshot keeps tag-like text even when it mentions <div>
lit = b"<p>Wrap each paragraph in a &lt;p&gt; element, never a &lt;div&gt;.</p>"
assert N(lit, "text/html") == "wrap each paragraph in a <p> element, never a <div>."
plain = b"Use <div> for layout.\nif a<b then c>d holds for all inputs\n"
assert N(plain, "text/plain") == "use <div> for layout. if a<b then c>d holds for all inputs"
assert N(plain, "") != N(plain, "text/plain")     # with no media type recorded, markup is sniffed
assert N(b"<p>x</p>", 5) == N(b"<p>x</p>", "") and N(b"<p>x</p>", ["text/html"]) == "x"   # junk media types
# linear time on hostile pages: unclosed comments, scripts, styles and tag-like runs (was quadratic)
for name, data in (("unclosed comments", b"<p>x</p>" + b"<!-- pad pad pad " * 62000),
                   ("unclosed scripts", b"<p>x</p>" + b"<script pad " * 88000),
                   ("unclosed styles", b"<p>x</p>" + b"<style x " * 110000),
                   ("tag-like text", b"<p>x</p>" + b"a<b " * 260000),
                   ("unclosed tags", b"<p>x</p>" + b"<x" * 520000)):
    t0 = time.time()
    N(data, "")
    dt = time.time() - t0
    assert dt < 3, (name, len(data), dt)
t0 = time.time()
assert research.page_title(b"<title" * 11000, "text/html") == ""
assert time.time() - t0 < 1
ok("normalize(): HTML tags/script/style/comments dropped (linear time), blocks separated, entities, NFKC, curly "
   "quotes, dashes, nbsp, ellipsis, casefold; a quote's markup and a plain-text snapshot's tag-like text are text")

# 8) the check: tampered or missing snapshots, hand edits, and re-sealed forgeries are all blocked
sha3 = load(P)["sources"][2]["sha256"]
good = read(snap(sha3))
with open(snap(sha3), "wb") as f:
    f.write(good.replace(b"$5", b"$9"))
r = run(*R, "check", RID)
assert r.returncode == 2 and "s3" in r.stderr and "sha256" in r.stderr, r.stderr
r = run(*R, "claim", RID, "x", "--source", "s3", "--quote", "Pricing starts at $9 per month")
assert r.returncode == 2 and "s3" in r.stderr, r.stderr
os.remove(snap(sha3))
r = run(*R, "check", RID, "--json")
out = json.loads(r.stdout)
assert r.returncode == 2 and out["exit"] == 2 and out["checked"] == [RID], r.stdout
assert any("s3" in p and "missing" in p for p in out["problems"]), out
r = run(*R, "archive", RID, BASE + "/page.html")          # re-archiving the same bytes restores the snapshot
assert r.returncode == 0 and r.stdout.strip() == "s3" and read(snap(sha3)) == good, r.stderr
assert run(*R, "check", RID).returncode == 0
# a snapshot that is a FIFO or a symlink (e.g. to /dev/zero) is reported, never read or waited on
os.remove(snap(sha3))
os.mkfifo(snap(sha3))
r = run(*R, "check", RID, timeout=30)
assert r.returncode == 2 and "s3" in r.stderr and "regular file" in r.stderr, r.stderr
r = run(*R, "resolve", RID + "#c4", timeout=30)
assert r.returncode == 2 and "regular file" in r.stderr, r.stderr
os.remove(snap(sha3))
os.symlink("/dev/zero", snap(sha3))
r = run(*R, "check", RID, timeout=30)
assert r.returncode == 2 and "s3" in r.stderr and "regular file" in r.stderr, r.stderr
assert research.cite_problems(T, [RID + "#c4"])
r = run(*R, "archive", RID, BASE + "/page.html")          # re-archiving replaces the planted file
assert r.returncode == 0 and r.stdout.strip() == "s3" and not os.path.islink(snap(sha3)) and read(snap(sha3)) == good
assert run(*R, "check", RID).returncode == 0
raw = read(P)
d = load(P)
d["claims"][0]["text"] = "Todoist is the best app ever"
write(P, d)
r = run(*R, "check", RID)
assert r.returncode == 2 and "sha256 does not match the content" in r.stderr, r.stderr
r = run(*R, "claim", RID, "laundering", "--from", "c1")
assert r.returncode == 2 and "restore" in r.stderr, r.stderr     # a CLI write never reseals a hand edit
r = run(*R, "archive", RID, "https://example.com/x", "--file", "paper.txt")
assert r.returncode == 2
write(P, raw)
assert run(*R, "check", RID).returncode == 0
# a hand edit that is re-sealed and saved: the seal holds, but the event log remembers the last CLI write
n_events = len(events())
d = load(P)
d["claims"][1]["text"] = "Rival is unusable; users rate it 1/5"
d["sources"][0]["url"] = "https://www.nngroup.com/articles/rival"
M.seal(d)
write(P, d)
assert M.problems(d, "research", P) == []
r = run(*R, "check", RID)
assert r.returncode == 2 and "event log" in r.stderr, r.stderr
r = run(*R, "resolve", RID + "#c2")
assert r.returncode == 2 and "event log" in r.stderr, r.stderr
assert research.cite_problems(T, [RID + "#c2"]) and research.resolve(T, RID + "#c2")[0] is None
r = run(*R, "claim", RID, "laundering", "--from", "c1")
assert r.returncode == 2 and "event log" in r.stderr, r.stderr   # nor is a re-sealed one written over
assert len(events()) == n_events
write(P, raw)
assert run(*R, "check", RID).returncode == 0


def forged(edit):
    """A hand edit that is re-sealed: the seal holds, so only the evidence checks can catch it."""
    d = load(P)
    edit(d)
    M.seal(d)
    return research.check_report(T, P, d)


def has(probs, *words):
    return any(all(w in p for w in words) for p in probs)


assert research.check_report(T, P, load(P)) == []
p = forged(lambda d: d["claims"][0]["evidence"][0].update(quote="Todoist supports voice input"))
assert has(p, "c1", "s1", "not in"), p
p = forged(lambda d: d["claims"][1]["evidence"][0].update(quote="PRICING starts at $4 <div invented> per month"))
assert has(p, "c2", "s1", "not in"), p
p = forged(lambda d: d["claims"][1]["evidence"][0].update(quote="<!-- rivals are bankrupt -->PRICING starts at $4"))
assert has(p, "c2", "s1", "not in"), p
p = forged(lambda d: d["claims"][1]["evidence"][0].update(quote="ricing starts at $4"))
assert has(p, "c2", "s1", "not in"), p
p = forged(lambda d: d["claims"][0]["evidence"][0].update(source="s5"))
assert has(p, "c1", "binary", "--file"), p
p = forged(lambda d: d["claims"][0]["evidence"][0].update(source="s7"))
assert has(p, "c1", "s7"), p
p = forged(lambda d: d["claims"][2].update(from_claims=["c4"]))
assert has(p, "c3", "c4", "earlier"), p
p = forged(lambda d: d["claims"][0].pop("evidence"))
assert has(p, "c1", "no evidence"), p
p = forged(lambda d: d["claims"][0].update(from_claims=["c2"]))
assert has(p, "c1"), p
p = forged(lambda d: d["sources"][1].update(source_id="s9"))
assert has(p, "s9", "s2"), p
p = forged(lambda d: d["claims"].reverse())
assert has(p, "c5", "c1"), p
p = forged(lambda d: d["sources"][0].update(snapshot="research/snapshots/../../../../etc/passwd"))
assert has(p, "snapshot"), p
p = forged(lambda d: d["sources"][0].update(sha256="0" * 64, snapshot="research/snapshots/" + "0" * 64))
assert has(p, "s1", "missing"), p
p = forged(lambda d: d["sources"][0].update(bytes=3))
assert has(p, "s1", "bytes"), p
p = forged(lambda d: d["sources"][0].pop("via"))
assert has(p, "via", "missing"), p
p = forged(lambda d: d["sources"][0].update(via="file"))
assert has(p, "s1", "file"), p
p = forged(lambda d: d["sources"][3].update(via="fetch"))
assert has(p, "s4", "final_url"), p
p = forged(lambda d: d.update(claims=[]))
assert has(p, "no claims"), p
p = forged(lambda d: d.update(status="retired"))
assert has(p, "retired"), p
p = forged(lambda d: d.update(research_id="rsr_madeup"))
assert has(p, "not issued by the harness"), p
p = forged(lambda d: d["claims"][0].update(text="Todoist is the best app ever"))
assert has(p, "event log"), p                       # re-sealed: only the log can tell
junk = os.path.join(os.path.dirname(P), "rsr_0000000000000000ffff.json")
write(junk, b"{not json")
r = run(*R, "check")
assert r.returncode == 2 and "rsr_0000000000000000ffff" in r.stderr, r.stderr
os.remove(junk)
# wrong-typed fields in a hand-edited report: BLOCK lines and exit 2 from check/resolve, never a crash; show and
# list still answer; the Python API returns problems; one bad report does not hide the others' problems
MALFORMED = (
    lambda d: d["sources"][0].update(media_type=5),
    lambda d: d["sources"][0].update(media_type=["text/plain"]),
    lambda d: d["sources"][0].update(source_id=["s1"]),
    lambda d: d["sources"][0].update(sha256=["x"], snapshot={"a": 1}, bytes="3"),
    lambda d: d["sources"][0].update(via=["fetch"], final_url=5),
    lambda d: d["claims"][0]["evidence"][0].update(source=["s1"]),
    lambda d: d["claims"][0]["evidence"][0].update(quote=["Todoist"]),
    lambda d: d["claims"][0].update(evidence=["s1"]),
    lambda d: d["claims"][0].update(evidence=5),
    lambda d: d["claims"][0].update(claim_id=["c1"]),
    lambda d: d["claims"][2].update(from_claims=[["c1"]]),
    lambda d: d["claims"][2].update(from_claims=[{"c": 1}]),
    lambda d: d["claims"][2].update(from_claims="c1"),
    lambda d: d.update(sources=5),
    lambda d: d.update(claims=5),
    lambda d: d.update(sources={"s1": 1}, claims="c1"),
    lambda d: d["sources"].append("s9"),
    lambda d: d["claims"].append(["c9"]),
    lambda d: d.update(retired="x"),
    lambda d: d.update(status=["recorded"], research_id=["x"], title=5, sha256=7),
)
for i, edit in enumerate(MALFORMED):
    d = json.loads(raw)
    edit(d)
    write(P, d)
    r = run(*R, "check", RID)
    assert r.returncode == 2 and "BLOCK[research]" in r.stderr, (i, r.returncode, r.stderr)
    r = run(*R, "resolve", RID + "#c1", "--json")
    assert r.returncode == 2 and json.loads(r.stdout)["exit"] == 2, (i, r.stdout, r.stderr)
    rc, _, err = cli("check")
    assert rc == 2 and RID2 in err and "no claims" in err, (i, err)
    assert cli("show", RID)[0] == 0 and cli("show", RID, "--json")[0] == 0, i
    assert cli("list")[0] == 0 and cli("list", "--json")[0] == 0, i
    assert cli("claim", RID, "x", "--from", "c1")[0] == 2, i
    p = research.check_report(T, P, load(P))
    assert p and not has(p, "could not be checked"), (i, p)
    assert research.cite_problems(T, [RID + "#c1"]), i
write(P, raw)
assert run(*R, "check", RID).returncode == 0
ok("check blocks: changed, missing or planted (FIFO, symlink) snapshots, a hand-edited report, re-sealed forgeries "
   "of the evidence or of the claims (event log), and wrong-typed fields (exit 2, never a crash)")

# 9) check over all reports, by id or path; an empty recorded report blocks; usage; paths outside the reports dir
r = run(*R, "check")
assert r.returncode == 2 and RID2 in r.stderr and "no claims" in r.stderr and RID + ":" not in r.stderr, r.stderr
r = run(*R, "check", "--json")
out = json.loads(r.stdout)
assert out["exit"] == 2 and sorted(out["checked"]) == sorted([RID, RID2]), out
assert run(*R, "check", P).returncode == 0
assert run(*R, "check", os.path.relpath(P, T)).returncode == 0
r = run(*R, "check", "rsr_000000000000deadbeef")
assert r.returncode == 2 and "no research report" in r.stderr, r.stderr
assert run(*R, "check", "--bogus").returncode == 64
shutil.copy(P, os.path.join(T, "copy.json"))             # a copy elsewhere is not the report
r = run(*R, "check", "copy.json")
assert r.returncode == 2 and "no research report" in r.stderr, r.stderr
r = run(*R, "claim", "copy.json", "x", "--from", "c1")
assert r.returncode == 2 and "no research report" in r.stderr, r.stderr
# from a linked worktree (where workers build): ids name the main worktree's report; the worktree's copy is refused
git = ("git", "-C", T, "-c", "user.name=t", "-c", "user.email=t@example.com")
subprocess.run(git + ("commit", "-q", "--allow-empty", "-m", "init"), check=True)
WT = os.path.join(OUT, "wt")
subprocess.run(git + ("worktree", "add", "-q", WT), check=True)
WP = os.path.join(WT, "docs", "agents", "mission", "research")
os.makedirs(WP)
shutil.copy(P, os.path.join(WP, RID + ".json"))
write(os.path.join(WT, "s2.txt"), b"Pricing starts at $4 per month for teams.\n")
rel = os.path.join("docs", "agents", "mission", "research", RID + ".json")
r = run(*R, "archive", rel, "https://example.com/s2", "--file", "s2.txt", cwd=WT)
assert r.returncode == 2 and "no research report" in r.stderr, r.stderr
r = run(*R, "check", rel, cwd=WT)
assert r.returncode == 2 and "no research report" in r.stderr, r.stderr
assert run(*R, "check", RID, cwd=WT).returncode == 0
assert load(P)["sources"] == load(os.path.join(WP, RID + ".json"))["sources"] and len(events("archive")) == 5
ok("check: every report (or the named ones, by id or a path in the reports dir); a recorded report needs at least "
   "one claim; a path elsewhere (a copy, a worktree's copy) is not a report")

# 10) resolve: citation refs to claims of recorded reports that pass the check
r = run(*R, "resolve", RID + "#c1", RID + "#c3")
assert r.returncode == 0, r.stderr
r = run(*R, "resolve", RID + "#c2", "--json")
out = json.loads(r.stdout)
assert r.returncode == 0 and out["exit"] == 0 and out["problems"] == [] and out["claims"][RID + "#c2"]["kind"] == "rival", out
assert [(s["source_id"], s["via"]) for s in out["sources"][RID + "#c2"]] == [("s1", "fetch"), ("s4", "file")], out
assert "s4" in r.stderr and "file" in r.stderr, r.stderr          # evidence read from a file is pointed out
for ref, word in ((RID + "#c99", "no claim c99"), ("rsr_nope#c1", "not a research citation"),
                  ("rsr_000000000000deadbeef#c1", "no research report"), (RID, "not a research citation"),
                  (RID2 + "#c1", "no claim")):
    r = run(*R, "resolve", ref)
    assert r.returncode == 2 and "BLOCK[research]" in r.stderr and word in r.stderr, (ref, r.stderr)
assert run(*R, "resolve").returncode == 64
with open(snap(sha3), "wb") as f:
    f.write(b"tampered")
r = run(*R, "resolve", RID + "#c1")
assert r.returncode == 2 and "fails its check" in r.stderr, r.stderr
write(snap(sha3), good)
ok("resolve: rsr_<id>#cN resolves only to a claim of a recorded report that passes the check; it names file sources")

# 11) retire: a reason; the report is retired, sealed, refuses writes
assert run(*R, "retire", RID2).returncode == 64
r = run(*R, "retire", RID2, "--reason", "superseded by the benchmark study")
assert r.returncode == 0, r.stderr
d2 = load(P2)
assert d2["status"] == "retired" and d2["retired"]["reason"] == "superseded by the benchmark study"
assert d2["retired"]["at"].endswith("Z") and d2["sha256"] == M.digest(d2) and M.problems(d2, "research", P2) == []
assert events("retire")[-1]["data"]["status"] == "retired" and events("retire")[-1]["data"]["report_sha256"] == d2["sha256"]
assert run(*R, "retire", RID2, "--reason", "again").returncode == 2
r = run(*R, "archive", RID2, "https://example.com/x", "--file", "paper.txt")
assert r.returncode == 2 and "retired" in r.stderr, r.stderr
r = run(*R, "claim", RID2, "x", "--from", "c1")
assert r.returncode == 2 and "retired" in r.stderr, r.stderr
r = run(*R, "check")
assert r.returncode == 0, r.stderr                 # a retired report needs no claims
ok("retire: status retired + retired{reason, at}, sealed; writes refused")

# 12) list and show
r = run(*R, "list")
assert r.returncode == 0 and RID in r.stdout and RID2 in r.stdout and "To-do app rivals" in r.stdout, r.stdout
rows = {x["research_id"]: x for x in json.loads(run(*R, "list", "--json").stdout)}
assert rows[RID] == {"research_id": RID, "status": "recorded", "title": "To-do app rivals", "sources": 5, "claims": 5}
assert rows[RID2]["status"] == "retired" and rows[RID2]["claims"] == 0
r = run(*R, "show", RID)
assert r.returncode == 0 and "who are the best to-do apps" in r.stdout and "c1" in r.stdout and "s4" in r.stdout, r.stdout
assert "from file paper.txt" in r.stdout and "fetched" in r.stdout and BASE + "/old" in r.stdout, r.stdout
out = json.loads(run(*R, "show", RID, "--json").stdout)
assert out["research"]["research_id"] == RID and out["check"] == {"exit": 0, "problems": []}, out
assert run(*R, "show", "rsr_000000000000deadbeef").returncode == 2 and run(*R, "show").returncode == 64
ok("list (id, status, title, sources, claims) and show (with each source's provenance), as text and JSON")

# 13) events: every write is one research.recorded event with its action, the report ref, its id, and the
#     report's sealed digest and status after the write
ev = events()
acts = [e["data"]["action"] for e in ev]
assert acts.count("new") == 2 and acts.count("archive") == 5 and acts.count("claim") == 5 and acts.count("retire") == 1, acts
for e in ev:
    assert e["refs"]["research"].startswith("docs/agents/mission/research/rsr_") and e["data"]["research_id"]
    assert HEX64.match(e["data"]["report_sha256"]) and e["data"]["status"] in ("recorded", "retired"), e
assert {e["role"] for e in ev} == {"market-researcher", "product-tester"}
for rid, p in ((RID, P), (RID2, P2)):
    last = [e for e in ev if e["data"]["research_id"] == rid][-1]["data"]
    assert last["report_sha256"] == load(p)["sha256"] and last["status"] == load(p)["status"], (rid, last)
ok("events: research.recorded with action new|archive|claim|retire, the report ref, the role, and the post-write digest")

# 14) the Python API other areas import (journeys, rubrics cite research)
claim, probs = research.resolve(T, RID + "#c2")
assert probs == [] and claim["claim_id"] == "c2" and claim["kind"] == "rival"
assert research.resolve(T, "nope")[0] is None and research.resolve(T, "nope")[1]
assert research.resolve(T, ["not", "a", "ref"])[0] is None
assert research.cite_problems(T, []) == ["no research source cited"]
assert research.cite_problems(T, [RID + "#c1", RID + "#c5"]) == []
p = research.cite_problems(T, [RID + "#c1", RID + "#c42", "rsr_bad"])
assert len(p) == 2 and any("c42" in x for x in p) and any("rsr_bad" in x for x in p), p
assert research.check_report(T, P, load(P)) == [] and research.check_report(T, P2, load(P2)) == []
assert research.check_report(T, P, [1, 2]) and research.check_report(T, P, None)
ok("Python API: check_report, resolve, cite_problems (an empty citation list is a problem)")

# 15) more archives: a page with no Content-Type is sniffed; escaped or tag-like text in pages and plain-text
#     snapshots is quotable verbatim; scripts written without spaces quote mid-sentence
r = run(*R, "new", "Markup in quotes", "--question", "can tag-like text be quoted?")
assert r.returncode == 0, r.stderr
P3 = os.path.realpath(r.stdout.strip())
RID3 = load(P3)["research_id"]
r = run(*R, "archive", RID3, BASE + "/notype")
assert r.returncode == 0 and r.stdout.strip() == "s1" and load(P3)["sources"][0]["media_type"] == "text/html", r.stderr
write(os.path.join(T, "lit.html"), b"<html><body><p>Wrap each paragraph in a &lt;p&gt; element, never a &lt;div&gt;.</p>"
                                   b"</body></html>")
write(os.path.join(T, "lit.txt"), b"Use <div> for layout.\nif a<b then c>d holds for all inputs\n")
write(os.path.join(T, "cjk.txt"), "价格从每月4美元起，团队版更贵。\n".encode())
for f, sid in (("lit.html", "s2"), ("lit.txt", "s3"), ("cjk.txt", "s4")):
    r = run(*R, "archive", RID3, "https://docs.example/" + f, "--file", f)
    assert r.returncode == 0 and r.stdout.strip() == sid, (f, r.stderr)
assert [s["media_type"] for s in load(P3)["sources"]] == ["text/html", "text/html", "text/plain", "text/plain"]
for src, quote in (("s1", "Untyped pages are sniffed for markup."),
                   ("s2", "Wrap each paragraph in a <p> element, never a <div>."),
                   ("s3", "if a<b then c>d holds for all inputs"),
                   ("s4", "从每月4美元起，团队版")):
    r = run(*R, "claim", RID3, "quotable: " + quote, "--source", src, "--quote", quote)
    assert r.returncode == 0, (src, quote, r.stderr)
r = run(*R, "claim", RID3, "x", "--source", "s2", "--quote", "Wrap each paragraph in a element, never a")
assert r.returncode == 2 and "not in" in r.stderr, r.stderr
assert run(*R, "check", RID3).returncode == 0
ok("archive sniffs an untyped page; escaped tags, tag-like plain text and unspaced scripts are quotable verbatim")

# 16) concurrent writers: every claim and every source gets its own id, none is lost
n0, e0 = len(load(P3)["claims"]), len(events("claim"))
procs = [subprocess.Popen(R + ("claim", RID3, "parallel claim %d" % i, "--source", "s3", "--quote",
                               "if a<b then c>d holds"), cwd=T, env=ENV, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True) for i in range(8)]
outs = [p.communicate(timeout=120) for p in procs]
assert all(p.returncode == 0 for p in procs), outs
printed = [o.strip() for o, _ in outs]
claims = load(P3)["claims"]
assert len(set(printed)) == 8 and len(claims) == n0 + 8 and len(events("claim")) == e0 + 8, (printed, len(claims))
assert [c["claim_id"] for c in claims] == ["c%d" % (i + 1) for i in range(len(claims))]
by_text = {c["text"]: c["claim_id"] for c in claims}
assert all(by_text["parallel claim %d" % i] == printed[i] for i in range(8)), (by_text, printed)
for i in range(6):
    write(os.path.join(T, "par%d.txt" % i), ("parallel source number %d of six\n" % i).encode())
procs = [subprocess.Popen(R + ("archive", RID3, "https://docs.example/par%d" % i, "--file", "par%d.txt" % i), cwd=T,
                          env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(6)]
outs = [p.communicate(timeout=120) for p in procs]
assert all(p.returncode == 0 for p in procs), outs
srcs = load(P3)["sources"]
assert len(srcs) == 10 and len({o.strip() for o, _ in outs}) == 6, (outs, len(srcs))
assert {s["url"]: s["source_id"] for s in srcs if "par" in s["url"]} == \
    {"https://docs.example/par%d" % i: outs[i][0].strip() for i in range(6)}
assert run(*R, "check", RID3).returncode == 0
ok("concurrent claims and archives on one report: distinct ids, each printed id is its own stored entry, none lost")

# 17) a retired report stays retired: restoring the old file, a re-sealed un-retire, or flipping the unhashed
#     status back are all blocked, and no write goes through
shutil.copy(P, os.path.join(OUT, "r1.json"))
assert run(*R, "retire", RID, "--reason", "stale prices").returncode == 0
r = run(*R, "resolve", RID + "#c1")
assert r.returncode == 2 and "retired" in r.stderr, r.stderr
assert research.cite_problems(T, [RID + "#c1"]) and research.resolve(T, RID + "#c1")[0] is None
retired_raw = read(P)
shutil.copy(os.path.join(OUT, "r1.json"), P)               # the pre-retire file (a git restore)
assert M.problems(load(P), "research", P) == []
r = run(*R, "check", RID)
assert r.returncode == 2 and "event log" in r.stderr and "retired" in r.stderr, r.stderr
assert run(*R, "resolve", RID + "#c1").returncode == 2
d = json.loads(retired_raw)                                # a re-sealed un-retire
d.pop("retired")
d["status"] = "recorded"
M.seal(d)
write(P, d)
r = run(*R, "check", RID)
assert r.returncode == 2 and "event log" in r.stderr, r.stderr
assert run(*R, "resolve", RID + "#c1").returncode == 2
d = json.loads(retired_raw)                                # status is unhashed: flip it, the seal still holds
d["status"] = "recorded"
write(P, d)
assert M.problems(d, "research", P) == []
n_events, flipped = len(events()), read(P)
r = run(*R, "archive", RID, "https://example.com/x", "--file", "paper.txt")
assert r.returncode == 2 and "retired" in r.stderr, r.stderr
r = run(*R, "claim", RID, "x", "--from", "c1")
assert r.returncode == 2 and "retired" in r.stderr, r.stderr
r = run(*R, "retire", RID, "--reason", "again")
assert r.returncode == 2 and "retired" in r.stderr, r.stderr
assert read(P) == flipped and len(events()) == n_events
assert run(*R, "check", RID).returncode == 2 and run(*R, "resolve", RID + "#c1").returncode == 2
write(P, retired_raw)
assert run(*R, "check", RID).returncode == 0
assert [e["data"]["reason"] for e in events("retire")] == ["superseded by the benchmark study", "stale prices"]
ok("a retired report stays retired: a restored pre-retire file, a re-sealed un-retire and a status flip are blocked")

# 18) a fresh clone: the git-ignored event log is gone, so a report HEAD holds exactly needs no local event;
#     an uncommitted hand edit, even re-sealed, still blocks
subprocess.run(git + ("add", "-A", "docs"), check=True)
subprocess.run(git + ("commit", "-q", "-m", "research"), check=True)
log = os.path.join(T, ".aladdin", "logs", "events.jsonl")
os.rename(log, log + ".away")
try:
    assert run(*R, "check", RID, RID3).returncode == 0
    assert research.cite_problems(T, [RID3 + "#c1"]) == []
    P3_saved = read(P3)
    d = load(P3)
    d["claims"][0]["text"] = "Every rival is worse"            # re-sealed, not committed
    M.seal(d)
    write(P3, d)
    r = run(*R, "check", RID3)
    assert r.returncode == 2 and "nor committed" in r.stderr, r.stderr
    assert research.cite_problems(T, [RID3 + "#c1"])
    write(P3, P3_saved)
    assert run(*R, "check", RID3).returncode == 0
finally:
    os.rename(log + ".away", log)
ok("a fresh clone: committed reports need no local event; an uncommitted re-sealed edit still blocks")

srv.shutdown()
srv.server_close()
subprocess.run(("git", "-C", T, "worktree", "remove", "--force", WT), capture_output=True)
shutil.rmtree(T, ignore_errors=True)
shutil.rmtree(OUT, ignore_errors=True)
print("mission-research: %d checks passed" % PASS[0])
