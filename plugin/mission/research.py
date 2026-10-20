"""research — the Market Researcher's sourced report (ROADMAP §4.12, M9.1).

Every claim cites evidence: a verbatim quote from an archived source snapshot (the bytes,
content-addressed under docs/agents/mission/research/snapshots/<sha256>), or earlier claims
it infers from. Ids (rsr_…, s1…, c1…) are issued here, never by a model. The check is
structure and evidence, never whether a claim is right.

Usage:
  mission research new "<title>" --question TEXT [--role market-researcher|product-tester]
        write docs/agents/mission/research/<rsr_id>.json (recorded, sealed); prints its path
  mission research archive RSR URL [--file PATH] [--title T]
        archive a source: fetch URL (http/https, at most 5 MB; the final URL after redirects is
        recorded) or take PATH, a regular file inside the project, as the bytes retrieved from
        URL (a saved page, pdftotext output; recorded as read from that file, not fetched).
        Prints its id (s1, s2, …); the same URL with the same bytes reuses its id
  mission research claim RSR "<text>" (--source sN --quote "<verbatim excerpt>")… | --from cN…
               [--kind rival|feature|benchmark|user-need|paper|practice|other]
        add a claim (prints c1, c2, …). Each quote must occur in its source's snapshot as whole
        words within one block of the page (normalized: tags, entities, quotes, dashes, spaces and
        case; the quote's own text is never stripped); --from cites earlier claims
  mission research check [RSR…] [--json]
        every claim has evidence; every snapshot exists and matches its sha256; every quote is
        in its snapshot; the report is sealed and is what its last CLI write recorded in the
        event log. Exit 0 ok (or no research yet) · 2 block · 64 usage
  mission research resolve REF… [--json]
        a citation is rsr_<id>#cN; exit 0 when each names a claim of a recorded report that passes
  mission research show RSR [--json]     the report (with each source's provenance) and its check
  mission research list [--json]         id, status, title, sources, claims
  mission research retire RSR --reason TEXT

Python API: check_report(cwd, path, doc), resolve(cwd, ref), cite_problems(cwd, refs),
normalize(data, media_type, markup=None).
"""
import contextlib
import errno
import glob
import hashlib
import html
import http.client
import json
import mimetypes
import os
import re
import stat
import subprocess
import sys
import traceback
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

try:
    import fcntl
except ImportError:  # not POSIX: CLI writes are not serialized
    fcntl = None

_BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
if _BIN not in sys.path:
    sys.path.insert(0, _BIN)
import _events  # noqa: E402
import _mission as M  # noqa: E402
import _paths  # noqa: E402

AREA = "research"
ROLES = ("market-researcher", "product-tester")
KINDS = ("rival", "feature", "benchmark", "user-need", "paper", "practice", "other")
CAP = 5 * 1024 * 1024
TIMEOUT = 30
MIN_QUOTE = 8
REF = re.compile(r"^(rsr_[0-9a-f]{20})#(c[1-9][0-9]*)$")
SNAP = re.compile(r"^research/snapshots/([0-9a-f]{64})$")
CLAIM_ID = re.compile(r"^c[1-9][0-9]*$")
HTTP_URL = re.compile(r"(?i)^https?://\S+$")
HTMLISH = re.compile(r"<(?:html|body|p|div)\b", re.I)
FOLD = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"', "‟": '"',
                      "–": "-", "—": "-", "−": "-", " ": " ", "…": "..."})
# markup, matched one tag at a time from a '<' (never across another '<'): linear on any input
TAG = re.compile(r"</?([A-Za-z!?][^\s/<>]*)[^<>]*>")
RAW = re.compile(r"<(script|style)\b", re.I)
RAW_END = {"script": re.compile(r"</script\s*>", re.I), "style": re.compile(r"</style\s*>", re.I)}
TITLE, TITLE_END = re.compile(r"<title\b[^<>]*>", re.I), re.compile(r"</title\s*>", re.I)
BLOCKS = frozenset("address article aside blockquote body br caption dd details dialog div dl dt fieldset "
                   "figcaption figure footer form h1 h2 h3 h4 h5 h6 head header hr html li main nav ol option p "
                   "pre section summary table tbody td tfoot th thead title tr ul".split())
SPACES = re.compile(r"\s+")
# a letter or digit of a script written with spaces between words (a quote must not start or end
# inside such a word); scripts written without them (Chinese, Japanese, Thai, …) quote mid-sentence
WORD = (r"[^\W฀-໿က-႟ក-៿⺀-⿟぀-ヿ㄀-ㄯ"
        r"ㇰ-ㇿ㐀-䶿一-鿿豈-﫿\U00020000-\U0003ffff]")
WORD_RE = re.compile(WORD)
FLAGS = {"new": {"question", "role"}, "archive": {"file", "title"}, "claim": {"source", "quote", "from", "kind"},
         "check": {"json"}, "resolve": {"json"}, "show": {"json"}, "list": {"json"}, "retire": {"reason"}}
MULTI, BOOL = {"source", "quote", "from"}, {"json"}


def block(msg):
    print("BLOCK[%s]: %s" % (AREA, msg), file=sys.stderr)


def short(q, n=80):
    q = " ".join(str(q).split())
    return repr(q if len(q) <= n else q[:n] + "…")


def _str(v):
    return v if isinstance(v, str) else None


def _items(v):
    """The object items of a list field (a hand edit may have put anything there)."""
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


# ── normalization: the same folding for the snapshot text and the quote ──────────────
def _visible(s):
    """The text of an HTML document: tags, comments, script and style dropped, entities
    unescaped, a block-level tag as a line break (a quote can't span two blocks), any other
    tag as a space. One left-to-right pass; an unclosed comment, script or style runs to the
    end, as a browser reads it."""
    out, start, i, n = [], 0, 0, len(s)
    while True:
        j = s.find("<", i)
        if j < 0:
            break
        if s.startswith("<!--", j):
            k = s.find("-->", j + 4)
            end, sep = (n if k < 0 else k + 3), " "
        else:
            m = RAW.match(s, j)
            if m:
                e = RAW_END[m.group(1).lower()].search(s, m.end())
                end, sep = (n if e is None else e.end()), " "
            else:
                m = TAG.match(s, j)
                if not m:
                    i = j + 1          # a '<' that opens no tag is text
                    continue
                end, sep = m.end(), ("\n" if m.group(1).lower() in BLOCKS else " ")
        out += [SPACES.sub(" ", html.unescape(s[start:j])), sep]
        start = i = end
    out.append(SPACES.sub(" ", html.unescape(s[start:])))
    return "".join(out)


def normalize(data, media_type="", markup=None):
    """Text for quote matching: entities unescaped, NFKC, curly quotes/dashes/nbsp/ellipsis
    folded, spaces collapsed, casefolded. Markup (tags, script/style, comments) is dropped
    when `markup` is true; None decides from the media type (html in it), or sniffs the text
    when none is recorded. A quote is always normalized with markup=False: its own text is
    what must be found, so a tag or comment in it can never hide words that were not checked."""
    s = data.decode("utf-8", errors="replace") if isinstance(data, (bytes, bytearray)) else str(data)
    if markup is None:
        mt = media_type if isinstance(media_type, str) else ""
        markup = "html" in mt.lower() if mt else bool(HTMLISH.search(s))
    s = _visible(s) if markup else SPACES.sub(" ", html.unescape(s))
    s = unicodedata.normalize("NFKC", s).translate(FOLD)
    lines = (" ".join(x.split()) for x in s.split("\n"))
    return "\n".join(x for x in lines if x).casefold()


def found(q, text):
    """A normalized quote occurs in a normalized text, as whole words at both ends."""
    pat = re.escape(q)
    if WORD_RE.match(q[0]):
        pat += "(?<!%s.{%d})" % (WORD, len(q))
    if WORD_RE.match(q[-1]):
        pat += "(?!%s)" % WORD
    return re.search(pat, text, re.S) is not None


def read_file(path, follow=False):
    """(bytes, None) for a regular file of at most CAP bytes, else (None, why). Never follows a
    final symlink (unless `follow`), never blocks on a FIFO or reads a device."""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | (0 if follow else getattr(os, "O_NOFOLLOW", 0))
    try:
        fd = os.open(path, flags)
    except OSError as e:
        if e.errno == errno.ENOENT:
            return None, "missing"
        if e.errno == errno.ELOOP:
            return None, "a symlink, not a regular file"
        return None, e.strerror or str(e)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_size > CAP:
        os.close(fd)
        return None, "not a regular file" if not stat.S_ISREG(st.st_mode) else "over 5 MB"
    with os.fdopen(fd, "rb") as f:
        try:
            data = f.read(CAP + 1)
        except OSError as e:
            return None, e.strerror or str(e)
    return (None, "over 5 MB") if len(data) > CAP else (data, None)


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
    data, why = read_file(snapshot_path(cwd, sha))
    if data is None:
        if why == "missing":
            out.append("source %s: snapshot research/snapshots/%s is missing (archive the same URL and bytes again)"
                       % (sid, sha))
        else:
            out.append("source %s: snapshot research/snapshots/%s is %s (archive the same URL and bytes again)"
                       % (sid, sha, why))
        return None, "bad"
    if hashlib.sha256(data).hexdigest() != sha:
        out.append("source %s: snapshot bytes do not match the recorded sha256 %s (the file changed)" % (sid, sha[:12]))
        return None, "bad"
    if len(data) != s.get("bytes"):
        out.append("source %s: snapshot is %d bytes, recorded %r" % (sid, len(data), s.get("bytes")))
    if b"\0" in data:
        return None, "binary"
    return normalize(data, s.get("media_type")), None


def _quote_problem(sid, quote, texts):
    """One problem line (or None) for a quote cited from source `sid`; texts: sid → (text, err)."""
    if not isinstance(sid, str):
        return "evidence source %s is not a source id (s1, s2, …)" % short(sid)
    if not isinstance(quote, str):
        return "the quote cited from %s is not text: %s" % (sid, short(quote))
    if sid not in texts:
        return "cites source %s, which is not in the report" % sid
    text, err = texts[sid]
    if err == "bad":
        return "source %s: its snapshot is not intact, so it can't back a quote" % sid
    if err == "binary":
        return ("source %s is a binary snapshot (e.g. a PDF) and can't back a quote: archive its text form "
                "with --file (e.g. pdftotext output) and cite that" % sid)
    q = normalize(quote, markup=False)
    if len(q) < MIN_QUOTE:
        return "quote %s is too short to be evidence (at least %d characters)" % (short(quote), MIN_QUOTE)
    if not found(q, text):
        return ("source %s: quote %s is not in its snapshot (quotes are verbatim, normalized, whole words, "
                "within one block of the page)" % (sid, short(quote)))
    return None


# ── the event log remembers each CLI write's sealed digest ───────────────────────────
_LOG = {}


def last_writes(cwd):
    """research_id → data of its latest research.recorded event (cached on the log's size and mtime)."""
    p = _events.path(cwd)
    try:
        st = os.stat(p)
    except OSError:
        return {}
    key = (p, st.st_size, st.st_mtime_ns)
    if _LOG.get("key") != key:
        last = {}
        for e in _events.read(cwd):
            d = e.get("data") if isinstance(e, dict) and e.get("event") == "research.recorded" else None
            if isinstance(d, dict) and isinstance(d.get("research_id"), str):
                last[d["research_id"]] = d
        _LOG.clear()
        _LOG.update(key=key, last=last)
    return _LOG["last"]


def committed(cwd, path):
    """The file as the project's HEAD holds it (bytes), or None. The event log is git-ignored run
    data, so what arrived through git (a fresh clone, a pull) is vouched for by history instead."""
    try:
        p = subprocess.run(["git", "-C", _paths.project(cwd), "show", "HEAD:" + M.rel(cwd, path)],
                           capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 else None


def _committed_doc(cwd, path):
    data = committed(cwd, path)
    try:
        return json.loads(data) if data is not None else None
    except ValueError:
        return None


def _log_problems(cwd, path, doc):
    """The report must be what its last CLI write sealed (the seal is unkeyed: a hand edit can be
    re-sealed, but not written into the log), or exactly what HEAD holds (it came through git)."""
    rid = os.path.basename(path)[:-len(".json")]
    d = last_writes(cwd).get(rid) or {}
    sha = d.get("report_sha256")
    if isinstance(sha, str) and doc.get("sha256") == sha and doc.get("status") == d.get("status"):
        return []
    if _committed_doc(cwd, path) == doc:
        return []
    if not isinstance(sha, str):
        return ["the report is neither written here by `mission research` (no research.recorded event) nor "
                "committed as it stands: reports are written only through the CLI"]
    return ["the report is not what its last CLI write recorded in the event log (%s: sha256 %s…, status %s) nor "
            "what HEAD holds: a hand edit, even a re-sealed one, is refused; a retired report stays retired"
            % (d.get("action"), sha[:12], d.get("status"))]


def _status_problems(doc):
    st, out = doc.get("status"), []
    if st == "retired" and not isinstance(doc.get("retired"), dict):
        out.append("a retired report needs retired{reason, at} (mission research retire)")
    if st == "recorded" and "retired" in doc:
        out.append("a recorded report carries a retired field")
    return out


def _via_problems(s):
    via, sid = s.get("via"), s.get("source_id")
    if via == "fetch" and ("file" in s or not s.get("final_url")):
        return ["source %s: a fetched source records its final_url and no file" % sid]
    if via == "file" and ("final_url" in s or not s.get("file")):
        return ["source %s: a source read from a file records that file and no final_url" % sid]
    return []


def check_report(cwd, path, doc):
    """Every problem of one report: the core contract (schema, harness id, status, seal), the
    event log's record of its last CLI write, ids issued in order, ≥1 claim when recorded, every
    claim backed by quotes found in intact snapshots or by earlier claims. Never raises: a report
    that can't be checked is a problem."""
    try:
        return _check_report(cwd, path, doc)
    except Exception as e:  # fail closed
        return ["could not be checked (%s: %s)" % (e.__class__.__name__, e)]


def _check_report(cwd, path, doc):
    out = M.problems(doc, "research", path)
    if not isinstance(doc, dict):
        return out
    out += _status_problems(doc) + _log_problems(cwd, path, doc)
    sources, claims = _items(doc.get("sources")), _items(doc.get("claims"))
    if doc.get("status") == "recorded" and not claims:
        out.append("no claims yet: mission research claim RSR \"<text>\" --source sN --quote \"…\"")
    texts = {}
    for i, s in enumerate(sources):
        sid = s.get("source_id")
        if sid != "s%d" % (i + 1):
            out.append("source #%d has id %r, expected s%d (ids are issued in order)" % (i + 1, sid, i + 1))
        out += _via_problems(s)
        if isinstance(sid, str) and sid not in texts:
            texts[sid] = _source_text(cwd, s, out)
    earlier = set()
    for i, c in enumerate(claims):
        cid = c.get("claim_id")
        if cid != "c%d" % (i + 1):
            out.append("claim #%d has id %r, expected c%d (ids are issued in order)" % (i + 1, cid, i + 1))
        label = cid if isinstance(cid, str) else "#%d" % (i + 1)
        ev, fr = c.get("evidence"), c.get("from_claims")
        if not ev and not fr:
            out.append("claim %s has no evidence and no from_claims: it cites a source quote or earlier claims" % label)
        elif ev and fr:
            out.append("claim %s has both evidence and from_claims: it cites one or the other" % label)
        for e in ev if isinstance(ev, list) else []:
            p = (_quote_problem(e.get("source"), e.get("quote"), texts) if isinstance(e, dict)
                 else "evidence %s is not {source, quote}" % short(e))
            if p:
                out.append("claim %s: %s" % (label, p))
        for f in fr if isinstance(fr, list) else []:
            if not isinstance(f, str) or f not in earlier:
                f = f if isinstance(f, str) else short(f)
                out.append("claim %s: from_claims %s is not an earlier claim" % (label, f))
        if isinstance(cid, str):
            earlier.add(cid)
    return out


# ── finding and loading reports ──────────────────────────────────────────────────────
def reports(cwd):
    return sorted(glob.glob(M.mdir(cwd, "research", "*.json")))


def find(cwd, ref):
    """The report file for an id, or for a path to it in docs/agents/mission/research/ of the
    project (the main worktree, where every id resolves); else None. A copy elsewhere, e.g. in a
    linked worktree, is not the report."""
    ref = _str(ref) or ""
    if M.ID.match(ref) and ref.startswith("rsr_"):
        p = M.path_of(cwd, "research", ref)
        return p if os.path.isfile(p) else None
    if ref.endswith(".json"):
        p = os.path.realpath(os.path.join(cwd, ref))
        rid = os.path.basename(p)[:-len(".json")]
        if (os.path.dirname(p) == os.path.realpath(M.mdir(cwd, "research")) and M.ID.match(rid)
                and rid.startswith("rsr_") and os.path.isfile(p)):
            return p
    return None


def _open(cwd, ref, write=False):
    """(path, doc) or (None, None) after a BLOCK line. A write needs a recorded report that
    still matches its seal and its last CLI write, so a CLI write never re-seals a hand edit."""
    path = find(cwd, ref)
    if not path:
        block("no research report %r (mission research list)" % ref)
        return None, None
    doc, err = M.load(path)
    if not isinstance(doc, dict):
        block("%s is unreadable: %s" % (M.rel(cwd, path), err or "not an object"))
        return None, None
    if write:
        rid = os.path.basename(path)[:-len(".json")]
        if doc.get("status") == "retired" or "retired" in doc:
            block("%s is retired: start a new report (mission research new)" % rid)
            return None, None
        bad = M.problems(doc, "research", path) + _status_problems(doc) + _log_problems(cwd, path, doc)
        if bad:
            for b in bad:
                block("%s: %s" % (rid, b))
            block("%s fails its check: restore it from git (a CLI write never re-seals a hand edit)"
                  % M.rel(cwd, path))
            return None, None
    return path, doc


@contextlib.contextmanager
def locked(cwd, shared=False):
    """Serialize CLI writes (load → issue an id → seal → save → emit), so two agents writing at
    once never get the same id, lose a write, or log writes out of order; a reader (shared) never
    sees a report saved but not yet logged. Best effort where no lock file can be made."""
    try:
        f = open(os.path.join(_paths.home(cwd, "state"), "research.lock"), "a")
    except OSError:
        f = None
    try:
        if f and fcntl:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        yield
    finally:
        if f:
            f.close()  # releases the lock


def _write(cwd, path, doc, action, **data):
    M.seal(doc)
    M.save(path, doc)
    refs = {"research": M.rel(cwd, path)}
    if data.get("sha256"):
        refs["snapshot"] = M.rel(cwd, snapshot_path(cwd, data["sha256"]))
    if not M.emit(cwd, "research.recorded", role=doc.get("role", ""), refs=refs, action=action,
                  research_id=doc.get("research_id"), report_sha256=doc["sha256"], status=doc.get("status"), **data):
        print("[research] warning: the event log could not record this write of %s; its check will block until "
              "the log is writable and the report is restored" % doc.get("research_id"), file=sys.stderr)


# ── archiving ────────────────────────────────────────────────────────────────────────
class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme.lower() not in ("http", "https"):
            raise urllib.error.URLError("a redirect off http(s) is refused: %s" % newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url):
    """(bytes, media type, final URL, None) or (None, None, None, error). Follows http(s)
    redirects and the proxy environment; reads at most CAP + 1 bytes; a body shorter than its
    Content-Length is an error, never a snapshot."""
    req = urllib.request.Request(url, headers={"User-Agent": "aloop-research/1"})
    try:
        with urllib.request.build_opener(_Redirect).open(req, timeout=TIMEOUT) as r:
            data = r.read(CAP + 1)
            left = getattr(r, "length", None)
            if isinstance(left, int) and left > 0 and len(data) <= CAP:
                return None, None, None, ("incomplete body (got %d bytes, Content-Length said %d)"
                                          % (len(data), len(data) + left))
            media = r.headers.get_content_type() if r.headers.get("Content-Type") else sniff(data)
            return data, media, r.geturl(), None
    except urllib.error.HTTPError as e:
        return None, None, None, "HTTP %d %s" % (e.code, e.reason)
    except urllib.error.URLError as e:
        return None, None, None, str(e.reason)
    except (OSError, ValueError, http.client.HTTPException) as e:
        return None, None, None, "%s %s" % (e.__class__.__name__, e)


def sniff(data):
    if b"\0" in data:
        return "application/octet-stream"
    return "text/html" if HTMLISH.search(data[:65536].decode("utf-8", errors="replace")) else "text/plain"


def page_title(data, media_type):
    if "html" not in media_type:
        return ""
    s = data[:65536].decode("utf-8", errors="replace")
    m = TITLE.search(s)
    e = TITLE_END.search(s, m.end()) if m else None
    return " ".join(html.unescape(s[m.end():e.start()]).split())[:200] if e else ""


def store(cwd, sha, data):
    """Content-addressed: write the bytes unless an intact copy is there already (anything else
    at that path, a planted symlink or FIFO included, is replaced)."""
    p = snapshot_path(cwd, sha)
    old, _ = read_file(p)
    if old is not None and hashlib.sha256(old).hexdigest() == sha:
        return p
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = "%s.tmp.%d" % (p, os.getpid())
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, p)
    return p


def _url_ok(url):
    try:
        u = urllib.parse.urlsplit(url)
        u.port  # a malformed port raises ValueError
    except ValueError:
        return False
    return (u.scheme.lower() in ("http", "https") and bool(u.hostname) and url.isprintable()
            and not any(c.isspace() for c in url))


def _roots(cwd):
    """Where --file may read: the project (main worktree) and the worktree cwd is in."""
    out = [_paths.project(cwd)]
    try:
        top = subprocess.run(("git", "-C", cwd, "rev-parse", "--show-toplevel"), capture_output=True, text=True,
                             timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        top = ""
    return out + [top or cwd]


def cmd_archive(cwd, pos, o):
    if len(pos) != 2:
        block("usage: mission research archive RSR URL [--file PATH] [--title T]")
        return 64
    ref, url = pos
    with locked(cwd):                         # refuse before any fetch; re-read under the lock below
        if not _open(cwd, ref, write=True)[0]:
            return 2
    if not _url_ok(url):
        block("only http(s) URLs can be archived, not %s" % short(url))
        return 2
    if o.get("file"):
        fp = os.path.realpath(os.path.join(cwd, o["file"]))
        if not any(_paths.under(fp, r) for r in _roots(cwd)):
            block("--file %s is outside the project: --file reads only a file inside it (save the page there)"
                  % o["file"])
            return 2
        data, why = read_file(fp)
        if data is None and why != "over 5 MB":
            block("can't read --file %s: %s" % (o["file"], why))
            return 2
        media = mimetypes.guess_type(fp)[0] or mimetypes.guess_type(urllib.parse.urlsplit(url).path)[0] or (
            sniff(data) if data else "")
        via = {"via": "file", "file": M.rel(cwd, fp)}
    else:
        data, media, final, err = fetch(url)
        if not err and not HTTP_URL.match(final or ""):
            err = "it ended at %s, not an http(s) URL" % short(final)
        if err:
            block("could not fetch %s: %s" % (url, err))
            return 2
        via = {"via": "fetch", "final_url": final}
    if data is None or len(data) > CAP:
        block("%s is over 5 MB: archive the part you cite (--file PATH)" % url)
        return 2
    if not data:
        block("%s is empty: nothing to archive" % url)
        return 2
    sha = hashlib.sha256(data).hexdigest()
    with locked(cwd):
        path, doc = _open(cwd, ref, write=True)
        if not path:
            return 2
        for s in doc["sources"]:
            if (s.get("url"), s.get("sha256"), s.get("via"), s.get("final_url")) == (
                    url, sha, via["via"], via.get("final_url")):
                store(cwd, sha, data)
                print("[research] %s: %s already archived as %s" % (doc["research_id"], url, s["source_id"]),
                      file=sys.stderr)
                print(s["source_id"])
                return 0
        store(cwd, sha, data)
        sid = "s%d" % (len(doc["sources"]) + 1)
        title = " ".join((o.get("title") or "").split()) or page_title(data, media) or url
        doc["sources"].append(dict({"source_id": sid, "url": url, "title": title, "retrieved_at": M.now(),
                                    "media_type": media, "bytes": len(data), "sha256": sha,
                                    "snapshot": "research/snapshots/" + sha}, **via))
        _write(cwd, path, doc, "archive", source_id=sid, url=url, sha256=sha, bytes=len(data), **via)
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
    with locked(cwd):
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
    with locked(cwd):
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
    with locked(cwd, shared=True):
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
    m = REF.match(ref.strip() if isinstance(ref, str) else "")
    if not m:
        return None, ["%s is not a research citation (rsr_<20 hex>#cN)" % short(ref)]
    rid, cid = m.groups()
    ref = m.group(0)
    path = M.path_of(cwd, "research", rid)
    if _cache is not None and path in _cache:
        doc, probs = _cache[path]
    else:
        with locked(cwd, shared=True):
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
    claim = next((c for c in _items(doc.get("claims")) if c.get("claim_id") == cid), None)
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


def cited_sources(cwd, ref):
    """The source entries a resolved claim's quotes cite (its url, how the bytes were got)."""
    m = REF.match(ref.strip())
    doc, _ = M.load(M.path_of(cwd, "research", m.group(1)))
    by_id = {s.get("source_id"): s for s in _items(doc.get("sources"))} if isinstance(doc, dict) else {}
    claim = next((c for c in _items(doc.get("claims")) if c.get("claim_id") == m.group(2)), {})
    sids = dict.fromkeys(e.get("source") for e in _items(claim.get("evidence")))
    return [by_id[s] for s in sids if s in by_id]


def cmd_resolve(cwd, pos, o):
    if not pos:
        block("usage: mission research resolve rsr_<id>#cN…")
        return 64
    cache, bad, claims, sources = {}, [], {}, {}
    for ref in pos:
        claim, probs = resolve(cwd, ref, cache)
        bad += probs
        if claim is not None:
            claims[ref] = claim
            sources[ref] = cited_sources(cwd, ref)
            for s in sources[ref]:
                if s.get("via") == "file":
                    print("[research] %s: %s was read from file %s, not fetched from %s"
                          % (ref, s.get("source_id"), s.get("file"), s.get("url")), file=sys.stderr)
    rc = 2 if bad else 0
    if o.get("json"):
        print(json.dumps({"exit": rc, "problems": bad, "claims": claims, "sources": sources}, indent=1))
    for b in bad:
        block(b)
    return rc


# ── show / list ──────────────────────────────────────────────────────────────────────
def _provenance(s):
    if s.get("via") == "file":
        return "from file %s (not fetched)" % s.get("file")
    if s.get("via") == "fetch":
        fin = s.get("final_url")
        return "fetched" + (" → %s" % fin if fin and fin != s.get("url") else "")
    return "provenance unknown"


def cmd_show(cwd, pos, o):
    if len(pos) != 1:
        block("usage: mission research show RSR [--json]")
        return 64
    with locked(cwd, shared=True):
        path, doc = _open(cwd, pos[0])
        bad = check_report(cwd, path, doc) if path else []
    if not path:
        return 2
    if o.get("json"):
        print(json.dumps({"research": doc, "check": {"exit": 2 if bad else 0, "problems": bad}}, indent=1,
                         ensure_ascii=False))
        return 0
    print("Research %s (%s): %s\nQuestion: %s\nRole: %s%s" % (
        doc.get("research_id"), doc.get("status"), doc.get("title"), doc.get("question"), doc.get("role"),
        " · mission %s" % doc["mission_id"] if doc.get("mission_id") else ""))
    for s in _items(doc.get("sources")):
        print("  %s %s — %s (%s, %s bytes, sha256 %s) %s" % (
            s.get("source_id"), s.get("url"), s.get("title"), s.get("media_type"), s.get("bytes"),
            str(s.get("sha256"))[:12], _provenance(s)))
    for c in _items(doc.get("claims")):
        print("  %s [%s] %s" % (c.get("claim_id"), c.get("kind"), c.get("text")))
        for e in _items(c.get("evidence")):
            print("      %s: %s" % (e.get("source"), short(e.get("quote"), 120)))
        fr = c.get("from_claims")
        if fr:
            print("      from %s" % (", ".join(map(str, fr)) if isinstance(fr, list) else short(fr)))
    ret = doc.get("retired")
    if ret:
        print("Retired: %s" % (ret.get("reason") if isinstance(ret, dict) else short(ret)))
    print("Check: %s" % ("ok" if not bad else "%d problem(s), first: %s" % (len(bad), bad[0])))
    return 0


def cmd_list(cwd, pos, o):
    rows = []
    for p in reports(cwd):
        d, _ = M.load(p)
        if isinstance(d, dict):
            n = {k: len(d[k]) if isinstance(d.get(k), list) else 0 for k in ("sources", "claims")}
            rows.append({"research_id": d.get("research_id"), "status": d.get("status"), "title": d.get("title"),
                         "sources": n["sources"], "claims": n["claims"]})
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
    for a in argv:
        try:
            a.encode("utf-8")
        except UnicodeEncodeError:
            block("argument %s is not valid UTF-8: re-type it, or convert the text to UTF-8" % ascii(a))
            return 64
    cmd = argv[0]
    if cmd not in COMMANDS:
        block("unknown command %r (mission research --help)" % cmd)
        return 64
    o, pos, bad = parse(argv[1:], FLAGS[cmd])
    for b in bad:
        block(b)
    if bad:
        return 64
    try:
        return COMMANDS[cmd](cwd, pos, o)
    except Exception as e:  # fail closed: an unexpected error blocks (exit 2), never passes as a crash
        where = traceback.extract_tb(e.__traceback__)[-1]
        block("internal error in mission research %s: %s: %s (%s:%d)"
              % (cmd, e.__class__.__name__, e, os.path.basename(where.filename), where.lineno))
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], os.getcwd()))
