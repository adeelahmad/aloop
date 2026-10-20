#!/usr/bin/env python3
"""ROADMAP §4.12 (M9.1): research. Every claim cites a verbatim quote from an archived,
content-addressed source snapshot (or earlier claims); every snapshot's bytes match the
sha256 recorded for it; a quote that is not in its snapshot is refused at claim time and
blocked by the check; a hand edit breaks the seal; citations (rsr_<id>#cN) resolve only to
claims of a recorded report that passes the check. Network: a local http.server only.
    python3 scripts/test/mission-research.test.py
"""
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
AREAS = os.path.join(ROOT, "mission")
T = os.path.realpath(tempfile.mkdtemp(prefix="aloop-research-"))
ENV = dict(os.environ, HOME=os.path.join(T, "home"), no_proxy="127.0.0.1,localhost", NO_PROXY="127.0.0.1,localhost")
ENV.pop("AGENTIC_HOST", None)
PASS = [0]
sys.path[:0] = [BIN, AREAS]
import _mission as M  # noqa: E402
import research  # noqa: E402


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def run(*args):
    return subprocess.run(args, cwd=T, env=ENV, capture_output=True, text=True)


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


class Site(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/old":
            self.send_response(302)
            self.send_header("Location", "/page.html")
            self.end_headers()
            return
        if self.path == "/evil":
            self.send_response(302)
            self.send_header("Location", "file:///etc/passwd")
            self.end_headers()
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
r = run(*R, "check", RID)
assert r.returncode == 2 and "BLOCK[research]" in r.stderr and "no claims" in r.stderr, r.stderr
assert run(MI, "init", "the best to-do app", "--mode", "autonomous", "--budget-usd", "5").returncode == 0
r = run(*R, "new", "Benchmarks", "--question", "how fast are they?", "--role", "product-tester")
assert r.returncode == 0, r.stderr
P2 = os.path.realpath(r.stdout.strip())
doc2 = load(P2)
RID2 = doc2["research_id"]
assert doc2["mission_id"] == load(M.path_of(T, "mission"))["mission_id"] and doc2["role"] == "product-tester"
ok("research new: question + title required; a sealed recorded report with a harness id and the mission's id")

# 3) archive over http: content-addressed snapshot, HTML title, dedupe on same URL + bytes
r = run(*R, "archive", RID, BASE + "/page.html")
assert r.returncode == 0 and r.stdout.strip() == "s1", (r.stdout, r.stderr)
doc = load(P)
s1 = doc["sources"][0]
sha = hashlib.sha256(PAGE).hexdigest()
assert s1["sha256"] == sha and s1["snapshot"] == "research/snapshots/" + sha and s1["bytes"] == len(PAGE)
assert s1["media_type"] == "text/html" and s1["url"] == BASE + "/page.html" and s1["title"] == "Rival Notes & More"
assert read(snap(sha)) == PAGE and s1["retrieved_at"].endswith("Z")
assert doc["sha256"] == M.digest(doc)
assert not [f for f in os.listdir(os.path.dirname(P)) if f.endswith(".json") and f.startswith(sha[:8])]
r = run(*R, "archive", RID, BASE + "/page.html")                          # same URL, same bytes
assert r.returncode == 0 and r.stdout.strip() == "s1" and len(load(P)["sources"]) == 1, r.stderr
assert len(events("archive")) == 1
r = run(*R, "archive", RID, BASE + "/old", "--title", "Redirected")    # redirects are followed
assert r.returncode == 0 and r.stdout.strip() == "s2", r.stderr
assert load(P)["sources"][1]["title"] == "Redirected" and load(P)["sources"][1]["sha256"] == sha
PAGE_V2 = PAGE.replace(b"$4 per month", b"$5 per month")
SITE["/page.html"] = (200, "text/html; charset=utf-8", PAGE_V2)
r = run(*R, "archive", RID, BASE + "/page.html")                          # same URL, changed bytes
assert r.returncode == 0 and r.stdout.strip() == "s3", r.stderr
assert load(P)["sources"][2]["sha256"] == hashlib.sha256(PAGE_V2).hexdigest()
ev = events("archive")
assert len(ev) == 3 and ev[-1]["data"]["source_id"] == "s3" and ev[-1]["refs"]["snapshot"].startswith("docs/agents/mission/research/snapshots/")
ok("archive over http: a sha256-named snapshot, the page title, redirects followed, same URL+bytes reused, new bytes a new id")

# 4) archive refusals: non-http(s), empty, oversized, fetch errors, unsafe redirects, unknown report
before = load(P)
for url, why in (("file:///etc/passwd", "http"), ("ftp://example.com/x", "http"), ("not a url", "http"),
                 (BASE + "/empty", "empty"), (BASE + "/missing", "404"), (BASE + "/evil", "redirect")):
    r = run(*R, "archive", RID, url)
    assert r.returncode == 2 and "BLOCK[research]" in r.stderr and why in r.stderr.lower(), (url, r.returncode, r.stderr)
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
r = run(*R, "archive", "rsr_000000000000deadbeef", BASE + "/page.html")
assert r.returncode == 2 and "no research report" in r.stderr, r.stderr
assert run(*R, "archive", RID).returncode == 64
assert load(P) == before and len(events("archive")) == 3
ok("archive refuses non-http(s) URLs, empty or oversized bodies, failed fetches, a redirect off http(s), unknown reports")

# 5) archive --file: an agent-saved text form, and a binary (PDF) snapshot
with open(os.path.join(T, "paper.txt"), "w") as f:
    f.write("Abstract\nUsers   abandon to-do apps\nwithin two weeks when capture takes more than 3 taps.\n")
r = run(*R, "archive", RID, "https://arxiv.org/abs/2601.00001", "--file", "paper.txt", "--title", "Abandonment study")
assert r.returncode == 0 and r.stdout.strip() == "s4", r.stderr
s4 = load(P)["sources"][3]
assert s4["media_type"] == "text/plain" and s4["url"] == "https://arxiv.org/abs/2601.00001" and s4["title"] == "Abandonment study"
PDF = b"%PDF-1.4\n\x00\x01\x02binary stream users abandon to-do apps\n%%EOF\n"
with open(os.path.join(T, "paper.pdf"), "wb") as f:
    f.write(PDF)
r = run(*R, "archive", RID, "https://arxiv.org/pdf/2601.00001", "--file", os.path.join(T, "paper.pdf"))
assert r.returncode == 0 and r.stdout.strip() == "s5", r.stderr
s5 = load(P)["sources"][4]
assert s5["media_type"] == "application/pdf" and s5["title"] == "https://arxiv.org/pdf/2601.00001"
assert read(snap(s5["sha256"])) == PDF
ok("archive --file: the saved bytes are the snapshot (text form or binary), media type from the file")

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
cases = [
    (("--source", "s1", "--quote", "hidden script text here"), 2, ["s1", "hidden script text here"]),
    (("--source", "s1", "--quote", "a hidden comment quote"), 2, ["s1", "a hidden comment quote"]),
    (("--source", "s1", "--quote", "Pricing starts at $5"), 2, ["s1", "Pricing starts at $5"]),    # that is s3
    (("--source", "s1", "--quote", "the"), 2, ["too short"]),
    (("--source", "s9", "--quote", "Pricing starts at $4"), 2, ["s9"]),
    (("--source", "s5", "--quote", "users abandon to-do apps"), 2, ["binary", "--file"]),
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
ok("claim: quotes found in the snapshot pass; script/comment text, wrong bytes, trivial or binary-backed quotes, bad ids refused")

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
ok("normalize(): HTML tags/script/style/comments dropped, entities, NFKC, curly quotes, dashes, nbsp, ellipsis, casefold")

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
p = forged(lambda d: d.update(claims=[]))
assert has(p, "no claims"), p
p = forged(lambda d: d.update(status="retired"))
assert has(p, "retired"), p
p = forged(lambda d: d.update(research_id="rsr_madeup"))
assert has(p, "not issued by the harness"), p
junk = os.path.join(os.path.dirname(P), "rsr_0000000000000000ffff.json")
write(junk, b"{not json")
r = run(*R, "check")
assert r.returncode == 2 and "rsr_0000000000000000ffff" in r.stderr, r.stderr
os.remove(junk)
ok("check blocks: changed or missing snapshot bytes, a hand-edited report, and re-sealed forgeries of the evidence")

# 9) check over all reports, by id or path; an empty recorded report blocks; usage
r = run(*R, "check")
assert r.returncode == 2 and RID2 in r.stderr and "no claims" in r.stderr and RID + ":" not in r.stderr, r.stderr
r = run(*R, "check", "--json")
out = json.loads(r.stdout)
assert out["exit"] == 2 and sorted(out["checked"]) == sorted([RID, RID2]), out
assert run(*R, "check", P).returncode == 0
r = run(*R, "check", "rsr_000000000000deadbeef")
assert r.returncode == 2 and "no research report" in r.stderr, r.stderr
assert run(*R, "check", "--bogus").returncode == 64
ok("check: every report (or the named ones, by id or path); a recorded report needs at least one claim")

# 10) resolve: citation refs to claims of recorded reports that pass the check
r = run(*R, "resolve", RID + "#c1", RID + "#c3")
assert r.returncode == 0, r.stderr
r = run(*R, "resolve", RID + "#c2", "--json")
out = json.loads(r.stdout)
assert r.returncode == 0 and out["exit"] == 0 and out["problems"] == [] and out["claims"][RID + "#c2"]["kind"] == "rival", out
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
ok("resolve: rsr_<id>#cN resolves only to a claim of a recorded report that passes the check")

# 11) retire: a reason; the report is retired, sealed, refuses writes, and no longer resolves
assert run(*R, "retire", RID2).returncode == 64
r = run(*R, "retire", RID2, "--reason", "superseded by the benchmark study")
assert r.returncode == 0, r.stderr
d2 = load(P2)
assert d2["status"] == "retired" and d2["retired"]["reason"] == "superseded by the benchmark study"
assert d2["retired"]["at"].endswith("Z") and d2["sha256"] == M.digest(d2) and M.problems(d2, "research", P2) == []
assert run(*R, "retire", RID2, "--reason", "again").returncode == 2
r = run(*R, "archive", RID2, "https://example.com/x", "--file", "paper.txt")
assert r.returncode == 2 and "retired" in r.stderr, r.stderr
r = run(*R, "claim", RID2, "x", "--from", "c1")
assert r.returncode == 2 and "retired" in r.stderr, r.stderr
r = run(*R, "check")
assert r.returncode == 0, r.stderr                 # a retired report needs no claims
d = load(P)
os.makedirs(os.path.join(T, "keep"), exist_ok=True)
shutil.copy(P, os.path.join(T, "keep", "r1.json"))
assert run(*R, "retire", RID, "--reason", "stale prices").returncode == 0
r = run(*R, "resolve", RID + "#c1")
assert r.returncode == 2 and "retired" in r.stderr, r.stderr
assert research.cite_problems(T, [RID + "#c1"]) and research.resolve(T, RID + "#c1")[0] is None
shutil.copy(os.path.join(T, "keep", "r1.json"), P)  # back to recorded (git restore)
assert run(*R, "check").returncode == 0
assert [e["data"]["reason"] for e in events("retire")] == ["superseded by the benchmark study", "stale prices"]
ok("retire: status retired + retired{reason, at}, sealed; writes refused; a retired report's claims no longer resolve")

# 12) list and show
r = run(*R, "list")
assert r.returncode == 0 and RID in r.stdout and RID2 in r.stdout and "To-do app rivals" in r.stdout, r.stdout
rows = {x["research_id"]: x for x in json.loads(run(*R, "list", "--json").stdout)}
assert rows[RID] == {"research_id": RID, "status": "recorded", "title": "To-do app rivals", "sources": 5, "claims": 5}
assert rows[RID2]["status"] == "retired" and rows[RID2]["claims"] == 0
r = run(*R, "show", RID)
assert r.returncode == 0 and "who are the best to-do apps" in r.stdout and "c1" in r.stdout and "s4" in r.stdout, r.stdout
out = json.loads(run(*R, "show", RID, "--json").stdout)
assert out["research"]["research_id"] == RID and out["check"] == {"exit": 0, "problems": []}, out
assert run(*R, "show", "rsr_000000000000deadbeef").returncode == 2 and run(*R, "show").returncode == 64
ok("list (id, status, title, sources, claims) and show, as text and JSON")

# 13) events: every write is one research.recorded event with its action, the report ref and its id
ev = events()
acts = [e["data"]["action"] for e in ev]
assert acts.count("new") == 2 and acts.count("archive") == 5 and acts.count("claim") == 5 and acts.count("retire") == 2, acts
for e in ev:
    assert e["refs"]["research"].startswith("docs/agents/mission/research/rsr_") and e["data"]["research_id"]
assert {e["role"] for e in ev} == {"market-researcher", "product-tester"}
ok("events: research.recorded with action new|archive|claim|retire, the report ref and the role")

# 14) the Python API other areas import (journeys, rubrics cite research)
claim, probs = research.resolve(T, RID + "#c2")
assert probs == [] and claim["claim_id"] == "c2" and claim["kind"] == "rival"
assert research.resolve(T, "nope")[0] is None and research.resolve(T, "nope")[1]
assert research.cite_problems(T, []) == ["no research source cited"]
assert research.cite_problems(T, [RID + "#c1", RID + "#c5"]) == []
p = research.cite_problems(T, [RID + "#c1", RID + "#c42", "rsr_bad"])
assert len(p) == 2 and any("c42" in x for x in p) and any("rsr_bad" in x for x in p), p
assert research.check_report(T, P, load(P)) == [] and research.check_report(T, P2, load(P2)) == []
ok("Python API: check_report, resolve, cite_problems (an empty citation list is a problem)")

srv.shutdown()
srv.server_close()
shutil.rmtree(T, ignore_errors=True)
print("mission-research: %d checks passed" % PASS[0])
