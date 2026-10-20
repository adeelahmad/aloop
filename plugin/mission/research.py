"""research — the Market Researcher's sourced report (ROADMAP §4.12, M9.1).

Every claim cites evidence: a verbatim quote from an archived source snapshot (the bytes,
content-addressed under docs/agents/mission/research/snapshots/<sha256>), or earlier claims
it infers from. Ids (rsr_…, s1…, c1…) are issued here, never by a model. The check is
structure and evidence, never whether a claim is right.

Usage:
  mission research new "<title>" --question TEXT [--role market-researcher|product-tester]
        write docs/agents/mission/research/<rsr_id>.json (recorded, sealed); prints its path
  mission research archive RSR URL [--file PATH] [--title T]
        archive a source: fetch URL (http/https, at most 5 MB) or take PATH as the bytes
        retrieved from URL (a saved page, pdftotext output). Prints its id (s1, s2, …);
        the same URL with the same bytes reuses its id, changed bytes get a new one
  mission research claim RSR "<text>" (--source sN --quote "<verbatim excerpt>")… | --from cN…
               [--kind rival|feature|benchmark|user-need|paper|practice|other]
        add a claim (prints c1, c2, …). Each quote must occur in its source's snapshot
        (normalized: tags, entities, quotes, dashes, spaces and case); --from cites earlier claims
  mission research check [RSR…] [--json]
        every claim has evidence; every snapshot exists and matches its sha256; every quote is
        in its snapshot; the report is sealed. Exit 0 ok (or no research yet) · 2 block · 64 usage
  mission research resolve REF… [--json]
        a citation is rsr_<id>#cN; exit 0 when each names a claim of a recorded report that passes
  mission research show RSR [--json]     the report and its check
  mission research list [--json]         id, status, title, sources, claims
  mission research retire RSR --reason TEXT

Python API: check_report(cwd, path, doc), resolve(cwd, ref), cite_problems(cwd, refs),
normalize(data, media_type).
"""
import glob
import hashlib
import html
import json
import mimetypes
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

_BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
if _BIN not in sys.path:
    sys.path.insert(0, _BIN)
import _mission as M  # noqa: E402

AREA = "research"
ROLES = ("market-researcher", "product-tester")
KINDS = ("rival", "feature", "benchmark", "user-need", "paper", "practice", "other")
CAP = 5 * 1024 * 1024
TIMEOUT = 30
MIN_QUOTE = 8
REF = re.compile(r"^(rsr_[0-9a-f]{20})#(c[1-9][0-9]*)$")
SNAP = re.compile(r"^research/snapshots/([0-9a-f]{64})$")
CLAIM_ID = re.compile(r"^c[1-9][0-9]*$")
HTMLISH = re.compile(r"<(?:html|body|p|div)\b", re.I)
FOLD = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"', "‟": '"',
                      "–": "-", "—": "-", "−": "-", " ": " ", "…": "..."})
FLAGS = {"new": {"question", "role"}, "archive": {"file", "title"}, "claim": {"source", "quote", "from", "kind"},
         "check": {"json"}, "resolve": {"json"}, "show": {"json"}, "list": {"json"}, "retire": {"reason"}}
MULTI, BOOL = {"source", "quote", "from"}, {"json"}


def block(msg):
    print("BLOCK[%s]: %s" % (AREA, msg), file=sys.stderr)


def short(q, n=80):
    q = " ".join(str(q).split())
    return repr(q if len(q) <= n else q[:n] + "…")


# ── normalization: the same function for the snapshot text and the quote ─────────────
def normalize(data, media_type=""):
    """Text for quote matching: tags (and script/style/comments) dropped when it looks like
    HTML, entities unescaped, NFKC, curly quotes/dashes/nbsp/ellipsis folded, spaces
    collapsed, casefolded."""
    s = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
    if "html" in (media_type or "").lower() or HTMLISH.search(s):
        s = re.sub(r"<script\b.*?</script\s*>", " ", s, flags=re.S | re.I)
        s = re.sub(r"<style\b.*?</style\s*>", " ", s, flags=re.S | re.I)
        s = re.sub(r"<!--.*?-->", " ", s, flags=re.S)
        s = re.sub(r"</?[A-Za-z!?][^>]*>", " ", s)
    s = unicodedata.normalize("NFKC", html.unescape(s)).translate(FOLD)
    return re.sub(r"\s+", " ", s).strip().casefold()


def snapshot_path(cwd, sha):
    return M.mdir(cwd, "research", "snapshots", sha)


def _source_text(cwd, s, out):
    """(normalized text, None) for a source's snapshot, (None, "binary"), or (None, "bad")
    with the problem appended to `out`."""
    sid, sha = s.get("source_id"), s.get("sha256")
    m = SNAP.match(str(s.get("snapshot") or ""))
    if not m or m.group(1) != sha:
        out.append("source %s: snapshot %s is not research/snapshots/<its sha256>" % (sid, short(s.get("snapshot"))))
        return None, "bad"
    try:
        with open(snapshot_path(cwd, sha), "rb") as f:
            data = f.read()
    except OSError:
        out.append("source %s: snapshot research/snapshots/%s is missing (archive the same URL and bytes again)"
                   % (sid, sha))
        return None, "bad"
    if hashlib.sha256(data).hexdigest() != sha:
        out.append("source %s: snapshot bytes do not match the recorded sha256 %s (the file changed)" % (sid, sha[:12]))
        return None, "bad"
    if len(data) != s.get("bytes"):
        out.append("source %s: snapshot is %d bytes, recorded %r" % (sid, len(data), s.get("bytes")))
    if b"\0" in data:
        return None, "binary"
    return normalize(data, s.get("media_type", "")), None


def _quote_problem(sid, quote, texts):
    """One problem line (or None) for a quote cited from source `sid`; texts: sid → (text, err)."""
    if sid not in texts:
        return "cites source %s, which is not in the report" % sid
    text, err = texts[sid]
    if err == "bad":
        return "source %s: its snapshot is not intact, so it can't back a quote" % sid
    if err == "binary":
        return ("source %s is a binary snapshot (e.g. a PDF) and can't back a quote: archive its text form "
                "with --file (e.g. pdftotext output) and cite that" % sid)
    q = normalize(quote or "")
    if len(q) < MIN_QUOTE:
        return "quote %s is too short to be evidence (at least %d characters)" % (short(quote), MIN_QUOTE)
    if q not in text:
        return "source %s: quote %s is not in its snapshot (quotes are verbatim, normalized)" % (sid, short(quote))
    return None


def check_report(cwd, path, doc):
    """Every problem of one report: the core contract (schema, harness id, status, seal), ids
    issued in order, ≥1 claim when recorded, every claim backed by quotes found in intact
    snapshots or by earlier claims."""
    out = M.problems(doc, "research", path)
    if not isinstance(doc, dict):
        return out
    st = doc.get("status")
    if st == "retired" and not isinstance(doc.get("retired"), dict):
        out.append("a retired report needs retired{reason, at} (mission research retire)")
    if st == "recorded" and "retired" in doc:
        out.append("a recorded report carries a retired field")
    sources = [s for s in doc.get("sources") or [] if isinstance(s, dict)] if isinstance(doc.get("sources"), list) else []
    claims = [c for c in doc.get("claims") or [] if isinstance(c, dict)] if isinstance(doc.get("claims"), list) else []
    if st == "recorded" and not claims:
        out.append("no claims yet: mission research claim RSR \"<text>\" --source sN --quote \"…\"")
    texts = {}
    for i, s in enumerate(sources):
        if s.get("source_id") != "s%d" % (i + 1):
            out.append("source #%d has id %r, expected s%d (ids are issued in order)" % (i + 1, s.get("source_id"), i + 1))
        if s.get("source_id") not in texts:
            texts[s.get("source_id")] = _source_text(cwd, s, out)
    earlier = set()
    for i, c in enumerate(claims):
        cid = c.get("claim_id")
        if cid != "c%d" % (i + 1):
            out.append("claim #%d has id %r, expected c%d (ids are issued in order)" % (i + 1, cid, i + 1))
        ev, fr = c.get("evidence"), c.get("from_claims")
        if not ev and not fr:
            out.append("claim %s has no evidence and no from_claims: it cites a source quote or earlier claims" % cid)
        elif ev and fr:
            out.append("claim %s has both evidence and from_claims: it cites one or the other" % cid)
        for e in ev if isinstance(ev, list) else []:
            p = _quote_problem(e.get("source"), e.get("quote"), texts) if isinstance(e, dict) else None
            if p:
                out.append("claim %s: %s" % (cid, p))
        for f in fr if isinstance(fr, list) else []:
            if f not in earlier:
                out.append("claim %s: from_claims %s is not an earlier claim" % (cid, f))
        earlier.add(cid)
    return out


# ── finding and loading reports ──────────────────────────────────────────────────────
def reports(cwd):
    return sorted(glob.glob(M.mdir(cwd, "research", "*.json")))


def find(cwd, ref):
    """The report file for an id or a path, or None."""
    if ref and os.path.isfile(os.path.join(cwd, ref)) and ref.endswith(".json"):
        return os.path.join(cwd, ref)
    if M.ID.match(ref or "") and ref.startswith("rsr_"):
        p = M.path_of(cwd, "research", ref)
        return p if os.path.isfile(p) else None
    return None


def _open(cwd, ref, write=False):
    """(path, doc) or (None, None) after a BLOCK line. A write needs a recorded report that
    still matches its seal, so a CLI write never re-seals a hand edit."""
    path = find(cwd, ref)
    if not path:
        block("no research report %r (mission research list)" % ref)
        return None, None
    doc, err = M.load(path)
    if not isinstance(doc, dict):
        block("%s is unreadable: %s" % (M.rel(cwd, path), err or "not an object"))
        return None, None
    if write:
        if doc.get("status") == "retired":
            block("%s is retired: start a new report (mission research new)" % doc.get("research_id"))
            return None, None
        bad = M.problems(doc, "research", path)
        if bad:
            for b in bad:
                block("%s: %s" % (doc.get("research_id"), b))
            block("%s fails its check: restore it from git (a CLI write never re-seals a hand edit)"
                  % M.rel(cwd, path))
            return None, None
    return path, doc


def _write(cwd, path, doc, action, **data):
    M.seal(doc)
    M.save(path, doc)
    refs = {"research": M.rel(cwd, path)}
    if data.get("sha256"):
        refs["snapshot"] = M.rel(cwd, snapshot_path(cwd, data["sha256"]))
    M.emit(cwd, "research.recorded", role=doc.get("role", ""), refs=refs, action=action,
           research_id=doc.get("research_id"), **data)


# ── archiving ────────────────────────────────────────────────────────────────────────
class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme.lower() not in ("http", "https"):
            raise urllib.error.URLError("a redirect off http(s) is refused: %s" % newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url):
    """(bytes, media type, None) or (None, None, error). Follows http(s) redirects and the
    proxy environment; reads at most CAP + 1 bytes."""
    req = urllib.request.Request(url, headers={"User-Agent": "aloop-research/1"})
    try:
        with urllib.request.build_opener(_Redirect).open(req, timeout=TIMEOUT) as r:
            return r.read(CAP + 1), r.headers.get_content_type(), None
    except urllib.error.HTTPError as e:
        return None, None, "HTTP %d %s" % (e.code, e.reason)
    except urllib.error.URLError as e:
        return None, None, str(e.reason)
    except (OSError, ValueError) as e:
        return None, None, str(e) or e.__class__.__name__


def sniff(data):
    if b"\0" in data:
        return "application/octet-stream"
    return "text/html" if HTMLISH.search(data[:65536].decode("utf-8", errors="replace")) else "text/plain"


def page_title(data, media_type):
    if "html" not in media_type:
        return ""
    m = re.search(r"<title[^>]*>(.*?)</title\s*>", data[:65536].decode("utf-8", errors="replace"), re.S | re.I)
    return " ".join(html.unescape(m.group(1)).split())[:200] if m else ""


def store(cwd, sha, data):
    """Content-addressed: write the bytes unless an intact copy is there already."""
    p = snapshot_path(cwd, sha)
    try:
        with open(p, "rb") as f:
            if hashlib.sha256(f.read()).hexdigest() == sha:
                return p
    except OSError:
        pass
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = "%s.tmp.%d" % (p, os.getpid())
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, p)
    return p


def cmd_archive(cwd, pos, o):
    if len(pos) != 2:
        block("usage: mission research archive RSR URL [--file PATH] [--title T]")
        return 64
    ref, url = pos
    path, doc = _open(cwd, ref, write=True)
    if not path:
        return 2
    u = urllib.parse.urlsplit(url)
    if u.scheme.lower() not in ("http", "https") or not u.netloc or any(c.isspace() for c in url):
        block("only http(s) URLs can be archived, not %s" % short(url))
        return 2
    if o.get("file"):
        fp = os.path.join(cwd, o["file"])
        try:
            with open(fp, "rb") as f:
                data = f.read(CAP + 1)
        except OSError as e:
            block("can't read --file %s: %s" % (o["file"], e.strerror or e))
            return 2
        media = mimetypes.guess_type(fp)[0] or mimetypes.guess_type(u.path)[0] or sniff(data)
    else:
        data, media, err = fetch(url)
        if err:
            block("could not fetch %s: %s" % (url, err))
            return 2
    if not data:
        block("%s is empty: nothing to archive" % url)
        return 2
    if len(data) > CAP:
        block("%s is over 5 MB: archive the part you cite (--file PATH)" % url)
        return 2
    sha = hashlib.sha256(data).hexdigest()
    for s in doc["sources"]:
        if s.get("url") == url and s.get("sha256") == sha:
            store(cwd, sha, data)
            print("[research] %s: %s already archived as %s" % (doc["research_id"], url, s["source_id"]),
                  file=sys.stderr)
            print(s["source_id"])
            return 0
    store(cwd, sha, data)
    sid = "s%d" % (len(doc["sources"]) + 1)
    title = " ".join((o.get("title") or "").split()) or page_title(data, media) or url
    doc["sources"].append({"source_id": sid, "url": url, "title": title, "retrieved_at": M.now(),
                           "media_type": media, "bytes": len(data), "sha256": sha,
                           "snapshot": "research/snapshots/" + sha})
    _write(cwd, path, doc, "archive", source_id=sid, url=url, sha256=sha, bytes=len(data))
    print(sid)
    return 0


# ── claims ───────────────────────────────────────────────────────────────────────────
def cmd_claim(cwd, pos, o):
    srcs, quotes, frm = o.get("source", []), o.get("quote", []), list(dict.fromkeys(o.get("from", [])))
    kind, bad = o.get("kind") or "other", []
    if len(pos) < 2 or not " ".join(pos[1:]).strip():
        bad.append('usage: mission research claim RSR "<text>" (--source sN --quote TEXT)… | --from cN…')
    if kind not in KINDS:
        bad.append("--kind must be one of %s" % ", ".join(KINDS))
    if len(srcs) != len(quotes):
        bad.append("--source and --quote come in pairs (%d --source, %d --quote)" % (len(srcs), len(quotes)))
    if not srcs and not quotes and not frm:
        bad.append("a claim cites evidence: --source sN --quote TEXT (repeatable), or --from cN (earlier claims)")
    if (srcs or quotes) and frm:
        bad.append("a claim cites quotes (--source/--quote) or earlier claims (--from), not both")
    for b in bad:
        block(b)
    if bad:
        return 64
    path, doc = _open(cwd, pos[0], write=True)
    if not path:
        return 2
    cid, text = "c%d" % (len(doc["claims"]) + 1), " ".join(pos[1:]).strip()
    claim = {"claim_id": cid, "text": text, "kind": kind}
    if srcs:
        by_id, texts, probs = {s.get("source_id"): s for s in doc["sources"]}, {}, []
        for sid in srcs:
            if sid in by_id and sid not in texts:
                texts[sid] = _source_text(cwd, by_id[sid], probs)
        bad = probs + [p for p in (_quote_problem(s, q, texts) for s, q in zip(srcs, quotes)) if p]
        claim["evidence"] = [{"source": s, "quote": q} for s, q in zip(srcs, quotes)]
    else:
        earlier = {c.get("claim_id") for c in doc["claims"]}
        bad = ["--from %s is not an earlier claim of %s (this claim would be %s)" % (f, doc["research_id"], cid)
               for f in frm if f not in earlier]
        claim["from_claims"] = frm
    for b in dict.fromkeys(bad):
        block(b)
    if bad:
        return 2
    doc["claims"].append(claim)
    _write(cwd, path, doc, "claim", claim_id=cid, kind=kind, sources=srcs, from_claims=frm)
    print(cid)
    return 0


# ── new / retire ─────────────────────────────────────────────────────────────────────
def cmd_new(cwd, pos, o):
    title, question, role = " ".join(pos).strip(), (o.get("question") or "").strip(), o.get("role") or ROLES[0]
    bad = []
    if not title:
        bad.append('the title is empty: mission research new "<title>" --question TEXT')
    if not question:
        bad.append("--question is required: what this research answers")
    if role not in ROLES:
        bad.append("--role must be one of %s" % ", ".join(ROLES))
    for b in bad:
        block(b)
    if bad:
        return 64
    mission, _ = M.load(M.path_of(cwd, "mission"))
    mid = mission.get("mission_id") if isinstance(mission, dict) else None
    doc = {"research_id": M.new_id("research"), "title": title, "question": question, "role": role,
           "mission_id": mid if isinstance(mid, str) and M.ID.match(mid) else None,
           "sources": [], "claims": [], "status": "recorded", "created_at": M.now()}
    path = M.path_of(cwd, "research", doc["research_id"])
    _write(cwd, path, doc, "new", title=title, mission_id=doc["mission_id"])
    print(path)
    return 0


def cmd_retire(cwd, pos, o):
    reason = (o.get("reason") or "").strip()
    if len(pos) != 1 or not reason or "\n" in reason:
        block("usage: mission research retire RSR --reason TEXT (one line)")
        return 64
    path, doc = _open(cwd, pos[0], write=True)
    if not path:
        return 2
    doc["status"], doc["retired"] = "retired", {"reason": reason, "at": M.now()}
    _write(cwd, path, doc, "retire", reason=reason)
    print("[research] %s retired: %s" % (doc["research_id"], reason), file=sys.stderr)
    return 0


# ── check / resolve ──────────────────────────────────────────────────────────────────
def check(cwd, refs=()):
    """(exit, problems, checked ids) over every report, or the named ones."""
    paths, out = [], []
    for ref in refs:
        p = find(cwd, ref)
        if p:
            paths.append(p)
        else:
            out.append("no research report %r (mission research list)" % ref)
    if not refs:
        paths = reports(cwd)
    checked = []
    for p in paths:
        rid = os.path.basename(p)[:-len(".json")]
        checked.append(rid)
        doc, err = M.load(p)
        if doc is None:
            out.append("%s: unreadable (%s)" % (rid, err))
            continue
        out += ["%s: %s" % (rid, x) for x in check_report(cwd, p, doc)]
    return (2 if out else 0), out, checked


def cmd_check(cwd, pos, o):
    rc, bad, checked = check(cwd, pos)
    if o.get("json"):
        print(json.dumps({"exit": rc, "problems": bad, "checked": checked}, indent=1))
    for b in bad:
        block(b)
    if rc == 0 and not o.get("json"):
        print("[research] %s" % ("%d report(s) ok" % len(checked) if checked else "no research yet: nothing to check"),
              file=sys.stderr)
    return rc


def resolve(cwd, ref, _cache=None):
    """(claim, []) when `ref` (rsr_<id>#cN) names a claim of a recorded report that passes its
    check; else (None, [problems])."""
    m = REF.match(str(ref or "").strip())
    if not m:
        return None, ["%s is not a research citation (rsr_<20 hex>#cN)" % short(ref)]
    rid, cid = m.groups()
    ref = m.group(0)
    path = M.path_of(cwd, "research", rid)
    if _cache is not None and path in _cache:
        doc, probs = _cache[path]
    else:
        doc, _ = M.load(path)
        probs = check_report(cwd, path, doc) if isinstance(doc, dict) else []
        if _cache is not None:
            _cache[path] = (doc, probs)
    if not isinstance(doc, dict):
        return None, ["%s: no research report %s" % (ref, rid)]
    bad = []
    if doc.get("status") != "recorded":
        bad.append("%s: report %s is %s (cite a recorded report)" % (ref, rid, doc.get("status")))
    bad += ["%s: report %s fails its check: %s" % (ref, rid, p) for p in probs]
    claims = doc.get("claims") if isinstance(doc.get("claims"), list) else []
    claim = next((c for c in claims if isinstance(c, dict) and c.get("claim_id") == cid), None)
    if claim is None:
        bad.append("%s: report %s has no claim %s" % (ref, rid, cid))
    return (None if bad else claim), bad


def cite_problems(cwd, refs):
    """[] when every citation resolves; an empty list of citations is itself a problem."""
    if not refs:
        return ["no research source cited"]
    cache, out = {}, []
    for ref in refs:
        out += resolve(cwd, ref, cache)[1]
    return out


def cmd_resolve(cwd, pos, o):
    if not pos:
        block("usage: mission research resolve rsr_<id>#cN…")
        return 64
    cache, bad, claims = {}, [], {}
    for ref in pos:
        claim, probs = resolve(cwd, ref, cache)
        bad += probs
        if claim is not None:
            claims[ref] = claim
    rc = 2 if bad else 0
    if o.get("json"):
        print(json.dumps({"exit": rc, "problems": bad, "claims": claims}, indent=1))
    for b in bad:
        block(b)
    return rc


# ── show / list ──────────────────────────────────────────────────────────────────────
def cmd_show(cwd, pos, o):
    if len(pos) != 1:
        block("usage: mission research show RSR [--json]")
        return 64
    path, doc = _open(cwd, pos[0])
    if not path:
        return 2
    bad = check_report(cwd, path, doc)
    if o.get("json"):
        print(json.dumps({"research": doc, "check": {"exit": 2 if bad else 0, "problems": bad}}, indent=1,
                         ensure_ascii=False))
        return 0
    print("Research %s (%s): %s\nQuestion: %s\nRole: %s%s" % (
        doc.get("research_id"), doc.get("status"), doc.get("title"), doc.get("question"), doc.get("role"),
        " · mission %s" % doc["mission_id"] if doc.get("mission_id") else ""))
    for s in doc.get("sources") or []:
        print("  %s %s — %s (%s, %s bytes, sha256 %s)" % (s.get("source_id"), s.get("url"), s.get("title"),
                                                         s.get("media_type"), s.get("bytes"), str(s.get("sha256"))[:12]))
    for c in doc.get("claims") or []:
        print("  %s [%s] %s" % (c.get("claim_id"), c.get("kind"), c.get("text")))
        for e in c.get("evidence") or []:
            print("      %s: %s" % (e.get("source"), short(e.get("quote"), 120)))
        if c.get("from_claims"):
            print("      from %s" % ", ".join(c["from_claims"]))
    if doc.get("retired"):
        print("Retired: %s" % doc["retired"].get("reason"))
    print("Check: %s" % ("ok" if not bad else "%d problem(s), first: %s" % (len(bad), bad[0])))
    return 0


def cmd_list(cwd, pos, o):
    rows = []
    for p in reports(cwd):
        d, _ = M.load(p)
        if isinstance(d, dict):
            rows.append({"research_id": d.get("research_id"), "status": d.get("status"), "title": d.get("title"),
                         "sources": len(d.get("sources") or []), "claims": len(d.get("claims") or [])})
    if o.get("json"):
        print(json.dumps(rows, indent=1, ensure_ascii=False))
        return 0
    if not rows:
        print("[research] no research yet", file=sys.stderr)
    for r in rows:
        print("%s  %-8s  %2d sources  %2d claims  %s" % (r["research_id"], r["status"], r["sources"], r["claims"],
                                                         r["title"]))
    return 0


# ── the command line ─────────────────────────────────────────────────────────────────
def parse(argv, allowed):
    """(opts, positionals, problems). --source/--quote/--from repeat; --from also takes
    several claim ids in a row (--from c1 c2) or comma-separated."""
    opts, pos, bad, k = {}, [], [], None
    for a in argv:
        if k == "from+":
            if CLAIM_ID.match(a):
                opts["from"].append(a)
                continue
            k = None
        if k:
            vals = [v for v in a.split(",") if v] if k == "from" else [a]
            if k in MULTI:
                opts.setdefault(k, []).extend(vals)
            else:
                opts[k] = a
            k = "from+" if k == "from" else None
        elif a.startswith("--") and len(a) > 2:
            k = a[2:].replace("-", "_")
            if k not in allowed:
                bad.append("unknown option %s" % a)
                k = None
            elif k in BOOL:
                opts[k], k = True, None
        else:
            pos.append(a)
    if k and k != "from+":
        bad.append("--%s needs a value" % k)
    return opts, pos, bad


COMMANDS = {"new": cmd_new, "archive": cmd_archive, "claim": cmd_claim, "check": cmd_check, "resolve": cmd_resolve,
            "show": cmd_show, "list": cmd_list, "retire": cmd_retire}


def main(argv, cwd):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    cmd = argv[0]
    if cmd not in COMMANDS:
        block("unknown command %r (mission research --help)" % cmd)
        return 64
    o, pos, bad = parse(argv[1:], FLAGS[cmd])
    for b in bad:
        block(b)
    if bad:
        return 64
    return COMMANDS[cmd](cwd, pos, o)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], os.getcwd()))
