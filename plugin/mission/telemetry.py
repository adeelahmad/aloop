"""telemetry — usage from Google Analytics 4: opt-in, informs goals, never a gate (ROADMAP §4.12, M9.6).

Real usage drives most of a later goal once there is enough of it, so it is captured from day
one through GA4, which the agents set up through Google's APIs: no server to host, nothing to
buy. Nothing reaches the network until a human consents to the exact terms below, and a
revoke stops the agents' use of it. Agents see aggregate counts of the consented stream only.

Usage:
  mission telemetry init --uri URL [--name NAME] [--time-zone TZ] [--force]
        draft the terms (what is measured, why, what agents see, GA's user-data acknowledgement,
        the credential) as a sealed record. Nothing leaves the machine. --force re-drafts the
        terms of the existing record; changed terms need a new consent.
  mission telemetry consent --by NAME [--reason TEXT]
        the human opts in to exactly these terms: telemetry.consent names their sha256
  mission telemetry revoke --by NAME [--reason TEXT]
        opt out: setup, debug-hit and report refuse, usage stops driving goals, and the local
        Measurement Protocol secret is deleted. The app's own GA tag is the app's code: check
        names the files that still carry it.
  mission telemetry auth
        the one-time Google sign-in, as pastable steps (the only human step)
  mission telemetry setup [--account accounts/N] [--property properties/N]
        Admin API, idempotent: find or create the property (--account picks or replaces it),
        acknowledge user-data collection, a web data stream for the uri, a Measurement
        Protocol secret (kept in .aladdin/state/, never in docs/) and a key event for each
        active journey's last step
  mission telemetry debug-hit
        confirm the local secret is live on the stream (Admin API; Google's validation server
        does not check secrets), then send every active journey step's event to that server
  mission telemetry verify
        the live proof: one probe event through the real Measurement Protocol endpoint must
        show in the stream's realtime report (MP answers 2xx even when it drops a hit)
  mission telemetry report [--realtime] [--json]
        the fixed Data API reports, scoped to the stream: settled (28 days ending 3 days ago,
        so ≥48h old) or realtime (a quick signal only); written to docs/agents/mission/telemetry/reports/
  mission telemetry share [--json]
        how much of the next goal usage may drive, from the latest settled report: 0 below
        50 users over 28 days or when GA thresholded the data, else 0.7 (research keeps 0.3)
  mission telemetry show [--json]
  mission telemetry check [--json]
        the structural check: consent covers the current terms; once consented, every active
        journey step's event is emitted by non-test code in this checkout; once set up, the
        app carries the stream's measurement id, the key events match the journeys and a
        passing debug hit covers the current events. Consent and revoke are read from the
        committed ledger, active journeys from their records. Usage numbers never change its exit.
        Exit 0 ok (or off) · 2 block · 3 needs the human · 64 usage

Environment: ALADDIN_GA_ACCESS_TOKEN (an OAuth access token), ALADDIN_GA_TOKEN_COMMAND (default
`gcloud auth application-default print-access-token`), ALADDIN_GA_QUOTA_PROJECT (sent as
x-goog-user-project, for user credentials), ALADDIN_GA_VERIFY_WAIT (seconds verify waits, default
180), ALADDIN_GA_BASE (a loopback fake, tests only).
"""
import glob
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit

import _mission as M
import _paths
import journeys as J

SCOPES = ("https://www.googleapis.com/auth/analytics.edit", "https://www.googleapis.com/auth/analytics.readonly")
# Google's verbatim text for properties.acknowledgeUserDataCollection, which a Measurement Protocol
# secret needs; the human consents to it as part of the terms
ACKNOWLEDGEMENT = ("I acknowledge that I have the necessary privacy disclosures and rights from my end users for the "
                   "collection and processing of their data, including the association of such data with the "
                   "visitation information Google Analytics collects from my site and/or app property.")
CREDENTIAL = ("a service account the human adds to their Analytics account as Editor; agents mint its tokens with "
              "gcloud, carrying only the scopes below. Signing in also leaves the human's gcloud credentials "
              "(cloud-platform) on this machine, as any gcloud use does")
HOSTS = {"admin": "https://analyticsadmin.googleapis.com", "data": "https://analyticsdata.googleapis.com",
         "mp": "https://www.google-analytics.com"}
LOOPBACK = ("127.0.0.1", "localhost", "::1")
TOKEN_COMMAND = "gcloud auth application-default print-access-token"

# the minimum-sample rule (§4.12 call): small numbers are noise, and GA hides them anyway
MIN_USERS, WINDOW_DAYS, USAGE_SHARE, RESEARCH_FLOOR = 50, 28, 0.7, 0.3
SETTLED = {"startDate": "30daysAgo", "endDate": "3daysAgo"}   # 28 days; the newest is ≥48h old, so settled
# the fixed report definitions (hashed into every report); request() scopes each to the stream.
# Aggregate dimensions only: agents never see a user or client id
AGGREGATE_DIMENSIONS = {"eventName", "date", "newVsReturning", "deviceCategory"}
REPORTS = {
    "users": {"api": "runReport", "no_probe": True, "body": {"dateRanges": [SETTLED], "metrics": [
        {"name": "totalUsers"}, {"name": "newUsers"}, {"name": "activeUsers"}]}},
    "journeys": {"api": "runReport", "events": True, "body": {"dateRanges": [SETTLED], "dimensions": [
        {"name": "eventName"}], "metrics": [{"name": "eventCount"}, {"name": "totalUsers"}]}},
    "realtime": {"api": "runRealtimeReport", "events": True, "body": {"dimensions": [{"name": "eventName"}],
                                                                       "metrics": [{"name": "eventCount"}, {"name": "activeUsers"}]}},
}
SETTLED_REPORTS = ("users", "journeys")

# GA4 event names, conservatively: an ASCII letter, then letters, digits or _, at most 40
EVENT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
# Measurement Protocol's reserved names (plus the app-only ones) and Firebase's reserved prefixes,
# never fewer than journeys (M9.3) refuses
RESERVED = {"ad_activeview", "ad_click", "ad_exposure", "ad_impression", "ad_query", "ad_reward", "adunit_exposure",
            "app_background", "app_clear_data", "app_exception", "app_install", "app_remove", "app_store_refund",
            "app_store_subscription_cancel", "app_store_subscription_convert", "app_store_subscription_renew",
            "app_update", "app_upgrade", "dynamic_link_app_open", "dynamic_link_app_update",
            "dynamic_link_first_open", "error", "firebase_campaign", "firebase_in_app_message_action",
            "firebase_in_app_message_dismiss", "firebase_in_app_message_impression", "first_open", "first_visit",
            "in_app_purchase", "notification_dismiss", "notification_foreground", "notification_open",
            "notification_receive", "notification_send", "os_update", "screen_view", "session_start",
            "session_start_with_rollout", "user_engagement"} | set(J.GA4_RESERVED_NAMES)
RESERVED_PREFIXES = tuple(sorted({"_", "firebase_", "ga_", "google_"} | set(J.GA4_RESERVED)))
MP_BATCH = 25                     # events per Measurement Protocol request (the validator does not enforce it)
DEBUG_CLIENT_ID = "1000000001.1000000001"   # the debug hit's and probe's client id (GA's random.timestamp form): no user's
PROBE_EVENT = "aloop_probe"       # the debug hit's event while no journey has events; verify's live probe
VERIFY_WAIT, VERIFY_EVERY, VERIFY_MAX = 180, 15, 900   # seconds: realtime data usually shows within a minute
SECRET_NAME = "aloop"

# the emit check reads this checkout's code, not docs, data or tests
CODE_EXT = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".svelte", ".astro", ".html", ".htm", ".py",
            ".rb", ".erb", ".go", ".rs", ".java", ".kt", ".swift", ".dart", ".php", ".cs", ".cshtml", ".razor",
            ".ex", ".exs", ".heex", ".m", ".ejs", ".hbs", ".handlebars", ".njk", ".twig", ".liquid", ".pug",
            ".haml", ".slim", ".jinja", ".j2", ".mustache"}
SKIP_DIRS = {"docs", "test", "tests", "__tests__", "__mocks__", "spec", "specs", "fixtures", "cypress", "e2e",
             "playwright", "testdata", "node_modules", "vendor", "dist", "build"}
TEST_FILE = re.compile(r"(^test_|^tests\.py$|^conftest\.py$|_(test|spec)\.[^.]+$|\.(test|spec|cy|e2e)\.[^.]+$"
                       r"|(Test|Tests|Spec)\.[^.]+$)")
SCAN_LIMIT = 2 * 1024 * 1024      # bytes read per file

AUTH_STEPS = """One-time Google sign-in (the only human step). Paste in a terminal with gcloud installed:
  gcloud auth login
  P=aloop-ga-$RANDOM; gcloud projects create $P && gcloud config set project $P
  gcloud services enable analyticsadmin.googleapis.com analyticsdata.googleapis.com iam.googleapis.com iamcredentials.googleapis.com
  gcloud iam service-accounts create aloop-ga --display-name="aloop GA"
  SA=aloop-ga@$P.iam.gserviceaccount.com
  gcloud iam service-accounts add-iam-policy-binding $SA --member="user:$(gcloud config get-value account)" --role=roles/iam.serviceAccountTokenCreator
  gcloud auth application-default login --impersonate-service-account=$SA --scopes=https://www.googleapis.com/auth/analytics.edit,https://www.googleapis.com/auth/analytics.readonly
  echo $SA
Then in a browser: https://analytics.google.com -> Admin -> Account access management -> + -> Add users ->
paste that service-account email, role Editor -> Add. (No Analytics account yet? Admin -> Create account first.)
Then: aloop mission telemetry setup"""


class Usage(Exception):
    pass


class Help(Exception):
    pass


class GAError(Exception):
    def __init__(self, code, status, message):
        super().__init__("%s %s: %s" % (code, status, message))
        self.code, self.status, self.message = code, status, message


def say(kind, msg):
    print("%s[telemetry]: %s" % (kind, msg), file=sys.stderr)


# ── terms, consent and the record ────────────────────────────────────────────────────
def terms_sha(doc):
    """What a consent covers: this record's terms, nothing it learns later (property, stream)."""
    return M.digest({"telemetry_id": doc.get("telemetry_id"), "terms": doc.get("terms")}, drop=())


def current(cwd):
    """(path, doc) of the project's telemetry record, or (None, None): one per project."""
    recs = M.artifacts(cwd, "telemetry")
    return recs[-1] if recs else (None, None)


def consent_state(cwd, path, doc):
    """none | broken | revoked | stale (the newest consent covers other terms) | ok: the record's
    newest human consent or revoke in the committed ledger decides, so a fresh checkout keeps it.
    Newest is by time, not file order (a union merge appends a branch's older rows after newer
    ones). A row any other role wrote is not a decision (approval provenance itself is M9.17), and
    while the ledger fails its own check (an edited or lost row may be a revoke) nothing is decided."""
    if M.ledger_problems(cwd):
        return "broken", None
    ref = M.rel(cwd, path)
    mine = [(r["ts"], i, r) for i, r in enumerate(M.ledger_rows(cwd)[0])
            if r["event"] in ("telemetry.consent", "telemetry.revoked") and r.get("role") == "human"
            and r["data"].get("subject") == ref]
    last = max(mine, key=lambda x: x[:2])[2] if mine else None
    if last is None:
        return "none", None
    if last["event"] == "telemetry.revoked":
        return "revoked", last
    return ("ok" if last["data"].get("sha256") == terms_sha(doc) else "stale"), last


def bound(doc):
    """Set up for the current terms: a property and a stream measuring exactly the consented uri."""
    return bool(doc.get("property") and (doc.get("stream") or {}).get("default_uri") == doc["terms"]["uri"])


def derive_status(cwd, path, doc):
    state = consent_state(cwd, path, doc)[0]
    if state == "revoked":
        return "revoked"
    if state != "ok":
        return "off"            # none, stale or broken
    return "configured" if bound(doc) else "consented"


OPT_IN = ("telemetry is opt-in and off: `aloop mission telemetry init --uri URL`, then a human runs "
          "`aloop mission telemetry consent --by NAME`")


def consented(cwd):
    """(path, doc, 0) when the human's consent covers the current terms; else asks and returns rc 3
    (or 2 for a record that fails its own check). Every network command starts here."""
    path, doc = current(cwd)
    if doc is None:
        say("ASK", OPT_IN)
        return None, None, 3
    bad = M.problems(doc, "telemetry", path) + ledger_problems(cwd)
    if bad:
        for b in bad:
            say("BLOCK", b)
        return path, doc, 2
    state, ev = consent_state(cwd, path, doc)
    if state == "ok":
        return path, doc, 0
    if state == "revoked":
        say("ASK", "telemetry was revoked by %s: a human runs `aloop mission telemetry consent --by NAME` to resume"
            % (ev.get("data") or {}).get("by"))
    elif state == "stale":
        say("ASK", "the consent does not cover the current terms (they changed): a human reviews "
            "`aloop mission telemetry show` and runs `aloop mission telemetry consent --by NAME`")
    else:
        say("ASK", "not consented yet: a human reviews `aloop mission telemetry show` and runs "
            "`aloop mission telemetry consent --by NAME`")
    return path, doc, 3


def ledger_problems(cwd):
    """The ledger holds the consent: while it fails its own check, no decision read from it holds."""
    return ["%s (recover: `git checkout -- %s`)" % (b, M.rel(cwd, M.ledger(cwd))) for b in M.ledger_problems(cwd)]


def store(path, doc):
    M.seal(doc)
    M.save(path, doc)


def secret_file(cwd, telemetry_id):
    """The Measurement Protocol secret lives in the git-ignored run-data dir, never in docs/."""
    return os.path.join(_paths.home(cwd, "state"), "telemetry", telemetry_id + ".json")


def read_secret(cwd, telemetry_id):
    d, _ = M.load(secret_file(cwd, telemetry_id))
    return d if isinstance(d, dict) and d.get("api_secret") else None


def write_secret(cwd, telemetry_id, data):
    p = secret_file(cwd, telemetry_id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        os.fchmod(f.fileno(), 0o600)
        json.dump(data, f)
    return p


def local_time_zone():
    """The machine's IANA zone (the property's reporting zone), or Google's example zone."""
    tz = os.environ.get("TZ", "").lstrip(":")
    if "/" not in tz:
        try:
            tz = os.path.realpath("/etc/localtime").split("zoneinfo/", 1)[1]
        except (OSError, IndexError):
            tz = ""
    if tz.startswith(("posix/", "right/")):
        tz = tz.split("/", 1)[1]
    ok = re.match(r"^[A-Z][A-Za-z_]+/[A-Za-z0-9_+\-/]+$", tz) and not tz.startswith("Etc/")
    return tz if ok else "America/Los_Angeles"


# ── journeys (M9.3's records) and the emit check ─────────────────────────────────────
def journeys(cwd):
    """[(title, [step event, …], drifted)] of every journey the records bind, oldest first: one a
    confirmed baseline froze, or a frozen one's recorded replacement, and not retired; never the
    hand-editable status field. drifted: its content is not what the records pinned."""
    frozen, pinned = J.pins(cwd)
    out = []
    for p, d in M.artifacts(cwd, "journey"):
        jid = d.get("journey_id")
        if not isinstance(jid, str) or jid not in pinned or J.lifecycle(cwd, p, d, frozen) == "retired":
            continue
        steps = d.get("steps") if isinstance(d.get("steps"), list) else []
        evs = [s["event"] for s in steps if isinstance(s, dict) and isinstance(s.get("event"), str)]
        out.append((d["title"] if isinstance(d.get("title"), str) else jid, evs,
                    M.digest(J.unretired(d)) != pinned[jid]))
    return out


def journey_events(cwd):
    return sorted({e for _, evs, _ in journeys(cwd) for e in evs})


def key_events(cwd):
    """The key event of each active journey: its last step's event."""
    return sorted({evs[-1] for _, evs, _ in journeys(cwd) if evs and not name_problem(evs[-1])})


def events_sha(evs):
    return M.digest({"events": sorted(evs)}, drop=())


def name_problem(name):
    if not EVENT_NAME.fullmatch(name):
        return "must start with a letter and use only letters, digits and _ (at most 40)"
    if name in RESERVED or name.lower().startswith(RESERVED_PREFIXES):
        return "is reserved by Google Analytics"
    if name == PROBE_EVENT:
        return "is aloop's own probe event (a journey step that sent it would make verify prove nothing)"
    return None


def checkout(cwd):
    """The source tree being checked: cwd's own checkout (a worker's linked worktree included),
    while journeys and the record stay in the main worktree's docs/agents/mission/."""
    try:
        r = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                           timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return _paths.project(cwd)


def source_files(root, code_only=True):
    """Non-test, non-doc files of the checkout (tracked or new, never git-ignored)."""
    try:
        out = subprocess.run(["git", "-C", root, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, text=True, timeout=60)
        files = [f for f in out.stdout.split("\0") if f] if out.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        files = None
    if files is None:
        files = [os.path.relpath(os.path.join(d, f), root) for d, _, fs in os.walk(root) for f in fs]
    for f in sorted(set(files)):
        parts = f.replace(os.sep, "/").split("/")
        if any(p.startswith(".") or p in SKIP_DIRS for p in parts[:-1]) or TEST_FILE.search(parts[-1]):
            continue
        if not code_only or os.path.splitext(f)[1].lower() in CODE_EXT:
            yield f


def scan(cwd, patterns, code_only=True):
    """{key: "file:line"} for each pattern's first hit in the checkout's files."""
    root, found = checkout(cwd), {}
    for f in source_files(root, code_only):
        if len(found) == len(patterns):
            break
        try:
            with open(os.path.join(root, f), errors="ignore") as fh:
                text = fh.read(SCAN_LIMIT)
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for k, rx in patterns.items():
                if k not in found and rx.search(line):
                    found[k] = "%s:%d" % (f, i)
    return found


def emitted(cwd, names):
    """{event: "file:line"} for each name found as a quoted literal ('x', "x" or `x`) in code."""
    return scan(cwd, {n: re.compile(r"(['\"`])%s\1" % re.escape(n)) for n in names})


def tagged(cwd, measurement_id):
    """Where the checkout carries the stream's measurement id (code, config or env file), or None."""
    hit = scan(cwd, {"mid": re.compile(r"(?<![A-Za-z0-9-])%s(?![A-Za-z0-9])" % re.escape(measurement_id))},
               code_only=False)
    return hit.get("mid")


# ── the network: three Google hosts, or one loopback fake ────────────────────────────
def endpoint(api):
    base = _paths.env("GA_BASE")
    if not base:
        return HOSTS[api]
    u = urlsplit(base)
    if u.scheme not in ("http", "https") or u.hostname not in LOOPBACK or u.username or u.password:
        raise Usage("ALADDIN_GA_BASE must be a loopback URL (a test fake), not %r: a token never leaves for "
                    "another host" % base)
    return base.rstrip("/") + "/" + api


def token():
    t = _paths.env("GA_ACCESS_TOKEN")
    if t:
        return t.strip()
    try:
        r = subprocess.run(shlex.split(_paths.env("GA_TOKEN_COMMAND") or TOKEN_COMMAND),
                           capture_output=True, text=True, timeout=60)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    out = r.stdout.strip()
    return out if r.returncode == 0 and out and not any(c.isspace() for c in out) else None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Google's APIs never redirect; following one would carry the bearer token to another host."""

    def redirect_request(self, *args, **kwargs):
        return None


def call(method, api, path, body=None, tok=None, query=None):
    url = endpoint(api) + path + ("?" + urlencode(query) if query else "")
    headers = {"Content-Type": "application/json"}
    if tok:
        headers["Authorization"] = "Bearer " + tok
    if api != "mp" and _paths.env("GA_QUOTA_PROJECT"):
        headers["x-goog-user-project"] = _paths.env("GA_QUOTA_PROJECT")     # user credentials bill a project
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), headers=headers,
                                 method=method)
    # a loopback fake is reached directly, never through a proxy
    handlers = [_NoRedirect()] + ([urllib.request.ProxyHandler({})] if _paths.env("GA_BASE") else [])
    try:
        with urllib.request.build_opener(*handlers).open(req, timeout=60) as r:
            raw = r.read()
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            err = (json.loads(e.read() or b"{}") or {}).get("error") or {}
        except ValueError:
            err = {}
        raise GAError(e.code, err.get("status", ""), err.get("message") or str(e.reason))
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise GAError(0, "UNREACHABLE", str(e))


def listall(path, key, tok):
    """Every item of a paged Admin API list (each list has its own page-size cap, so none is sent)."""
    out, page = [], None
    while True:
        r = call("GET", "admin", path, tok=tok, query={"pageToken": page} if page else None)
        out += r.get(key) or []
        page = r.get("nextPageToken")
        if not page:
            return out


SIGN_IN = re.compile(r"insufficient authentication scopes|ACCESS_TOKEN_SCOPE_INSUFFICIENT|invalid_grant", re.I)
API_OFF = re.compile(r"has not been used|is disabled|SERVICE_DISABLED|accessNotConfigured")


def ga_failed(e):
    """Map a GA error to an exit: credentials and API access are the human's (3), the rest block (2)."""
    if e.code == 401 or (e.code == 403 and SIGN_IN.search(e.message)):
        say("ASK", "Google rejected the credentials (%s).\n%s" % (e.message, AUTH_STEPS))
        return 3
    if e.code == 403 and API_OFF.search(e.message):
        say("ASK", "an Analytics API is off in your Google Cloud project (%s). Paste:\n"
            "  gcloud services enable analyticsadmin.googleapis.com analyticsdata.googleapis.com" % e.message)
        return 3
    if e.code == 403:
        say("ASK", "the signed-in Google identity lacks access (%s): add it to the Analytics account as Editor "
            "(Admin -> Account access management), or pass --account / --property it can edit" % e.message)
        return 3
    say("BLOCK", "Google Analytics: %s" % e)
    return 2


def need_token():
    tok = token()
    if not tok:
        say("ASK", "no Google credentials yet.\n" + AUTH_STEPS)
    return tok


# ── commands ─────────────────────────────────────────────────────────────────────────
def cmd_init(cwd, o):
    uri = o.get("uri") or ""
    if not re.match(r"^https?://[^\s/]+", uri):
        raise Usage("--uri must be the app's http(s) URL (what is measured is part of the terms)")
    path, doc = current(cwd)
    if doc is not None and not o.get("force"):
        say("BLOCK", "%s exists; `aloop mission telemetry init --uri URL --force` re-drafts its terms "
            "(changed terms need a new consent)" % M.rel(cwd, path))
        return 2
    mission, _ = M.load(M.path_of(cwd, "mission"))
    mission = mission if isinstance(mission, dict) else {}
    name = o.get("name") or ("aloop: " + (mission.get("statement") or urlsplit(uri).hostname))[:100]
    terms = {"uri": uri,
             "purpose": "usage of %s informs research and goal-setting for the mission; it is never a gate" % uri,
             "collects": ["the app's Google Analytics 4 web measurement on this uri",
                          "one event per user-journey step (its name and engagement time)",
                          "a synthetic %s event from client id %s each time agents verify the setup"
                          % (PROBE_EVENT, DEBUG_CLIENT_ID)],
             "agents_see": "aggregate counts of this uri's stream only (event name, users, new users) from fixed "
                           "report definitions; never a user or client id",
             "acknowledgement": ACKNOWLEDGEMENT, "credential": CREDENTIAL, "scopes": list(SCOPES),
             "others": "once other people use the app, a consent banner (Consent Mode v2) is a journey requirement"}
    settings = {"display_name": name, "time_zone": o.get("time_zone") or local_time_zone(), "currency": "USD"}
    if doc is None:
        tid = M.new_id("telemetry")
        path = M.path_of(cwd, "telemetry", tid)
        doc = {"telemetry_id": tid, "status": "off", "created_at": M.now(), "provider": "ga4",
               "mission_id": mission.get("mission_id"), "terms": terms, "settings": settings, "account": None,
               "property": None, "acknowledged": False, "stream": None, "secret": None, "key_events": [],
               "debug_hit": None, "verified": None}
    else:
        if (doc.get("terms") or {}).get("uri") != uri:     # another uri: the old stream measures something else
            doc.update(stream=None, secret=None, key_events=[], debug_hit=None, verified=None)
        doc.update(terms=terms, settings=settings)
    doc["status"] = derive_status(cwd, path, doc)
    store(path, doc)
    print(path)
    if doc["status"] == "off":
        say("ASK", "terms drafted (nothing was sent): a human reviews `aloop mission telemetry show` and runs "
            "`aloop mission telemetry consent --by NAME`")
    return 0


def who(o, verb):
    by, reason = (o.get("by") or "").strip(), (o.get("reason") or "").strip()
    if not by:
        raise Usage("%s names the human (--by NAME)" % verb)
    if "\n" in reason or len(reason) > 200:
        raise Usage("a reason is one line of at most 200 characters")
    return by, reason


def cmd_consent(cwd, o):
    path, doc = current(cwd)
    if doc is None:
        say("ASK", OPT_IN)
        return 3
    by, reason = who(o, "consent")
    bad = M.problems(doc, "telemetry", path)
    for b in bad:
        say("BLOCK", b)
    if bad:
        return 2
    eid = M.approve(cwd, "telemetry.consent", M.rel(cwd, path), terms_sha(doc), by, reason,
                    telemetry_id=doc["telemetry_id"], uri=doc["terms"]["uri"])
    doc["status"] = derive_status(cwd, path, doc)
    doc["approval"] = {"event_id": eid, "by": by, "at": M.now()}
    store(path, doc)
    print("[telemetry] %s: %s consented to the terms (sha256 %s)" % (doc["telemetry_id"], by, terms_sha(doc)[:12]),
          file=sys.stderr)
    return 0


def cmd_revoke(cwd, o):
    path, doc = current(cwd)
    if doc is None:
        say("ASK", OPT_IN)
        return 3
    by, reason = who(o, "revoke")
    M.record_human(cwd, "telemetry.revoked", M.rel(cwd, path), by, reason, telemetry_id=doc.get("telemetry_id"))
    try:
        os.remove(secret_file(cwd, doc["telemetry_id"]))
    except OSError:
        pass
    doc["status"] = "revoked"
    M.save(path, doc)       # status is outside the digest: the seal still holds
    print("[telemetry] %s revoked by %s: agents stop setup, debug hits and usage reports, usage no longer drives "
          "goals, and the local Measurement Protocol secret is deleted. The app's own GA tag keeps sending until "
          "it is removed (`aloop mission telemetry check` names where it is). Commit the ledger so every "
          "checkout sees the revoke." % (doc["telemetry_id"], by),
          file=sys.stderr)
    return 0


def pick_property(doc, o, tok):
    """(account, property, rc): --property, else the recorded one (unless --account asks to pick
    again), else found by display name or created in the one reachable account. Which account
    is the human's call when there are none or several, or when the recorded one is gone (rc 3)."""
    if o.get("property"):
        call("GET", "admin", "/v1beta/" + o["property"], tok=tok)
        return doc.get("account") if o["property"] == doc.get("property") else None, o["property"], 0
    if doc.get("property") and not o.get("account"):
        try:
            call("GET", "admin", "/v1beta/" + doc["property"], tok=tok)
            return doc.get("account"), doc["property"], 0
        except GAError as e:
            if e.code not in (403, 404) or SIGN_IN.search(e.message) or API_OFF.search(e.message):
                raise
            say("ASK", "the recorded property %s is not reachable (%s): rerun with `--account accounts/N` to find or "
                "create one there, or `--property properties/N`" % (doc["property"], e.message))
            return None, None, 3
    summaries = listall("/v1beta/accountSummaries", "accountSummaries", tok)
    if o.get("account"):
        found = [a for a in summaries if a.get("account") == o["account"]]
        if not found:
            say("BLOCK", "%s is not an account the signed-in Google identity can reach (%s)"
                % (o["account"], ", ".join(a.get("account", "") for a in summaries) or "none"))
            return None, None, 2
    elif not summaries:
        say("ASK", "the signed-in Google identity has no Analytics account: create one at https://analytics.google.com "
            "(Admin -> Create account; Google needs you to accept its terms there) and add the service account "
            "to it, then rerun `aloop mission telemetry setup`")
        return None, None, 3
    elif len(summaries) > 1:
        say("ASK", "several Analytics accounts: choose one, e.g. `aloop mission telemetry setup --account %s` (%s)"
            % (summaries[0].get("account"), "; ".join("%s %s" % (a.get("account"), a.get("displayName", ""))
                                                       for a in summaries)))
        return None, None, 3
    else:
        found = summaries
    acct, name = found[0]["account"], doc["settings"]["display_name"]
    same = [p for p in found[0].get("propertySummaries") or [] if p.get("displayName") == name]
    if same:
        return acct, same[0]["property"], 0
    p = call("POST", "admin", "/v1beta/properties", {"parent": acct, "displayName": name,
                                                      "timeZone": doc["settings"]["time_zone"],
                                                      "currencyCode": doc["settings"]["currency"]}, tok)
    return acct, p["name"], 0


def cmd_setup(cwd, o):
    path, doc, rc = consented(cwd)
    if rc:
        return rc
    endpoint("admin")
    tok = need_token()
    if not tok:
        return 3
    tid, uri = doc["telemetry_id"], doc["terms"]["uri"]
    try:
        acct, prop, rc = pick_property(doc, o, tok)
        if rc:
            return rc
        if prop != doc.get("property"):
            doc.update(account=acct, property=prop, acknowledged=False, stream=None, secret=None, key_events=[],
                       debug_hit=None, verified=None)
        store(path, doc)                         # progress survives a later failure: a rerun reuses it
        if not doc.get("acknowledged"):
            call("POST", "admin", "/v1beta/%s:acknowledgeUserDataCollection" % prop,
                 {"acknowledgement": doc["terms"]["acknowledgement"]}, tok)
            doc["acknowledged"] = True
            store(path, doc)
        streams = listall("/v1beta/%s/dataStreams" % prop, "dataStreams", tok)
        s = next((x for x in streams if x.get("type") == "WEB_DATA_STREAM"
                  and (x.get("webStreamData") or {}).get("defaultUri") == uri), None)
        if s is None:
            s = call("POST", "admin", "/v1beta/%s/dataStreams" % prop,
                     {"type": "WEB_DATA_STREAM", "displayName": doc["settings"]["display_name"],
                      "webStreamData": {"defaultUri": uri}}, tok)
        stream = {"name": s["name"], "measurement_id": s["webStreamData"]["measurementId"], "default_uri": uri}
        if stream != doc.get("stream"):
            doc.update(stream=stream, secret=None, debug_hit=None, verified=None)
        store(path, doc)
        local = read_secret(cwd, tid)
        secrets = listall("/v1beta/%s/measurementProtocolSecrets" % s["name"], "measurementProtocolSecrets", tok)
        sec = next((x for x in secrets if local and x.get("name") == local.get("name") and x.get("secretValue")), None) \
            or next((x for x in secrets if x.get("displayName") == SECRET_NAME and x.get("secretValue")), None)
        if sec is None:
            sec = call("POST", "admin", "/v1beta/%s/measurementProtocolSecrets" % s["name"],
                       {"displayName": SECRET_NAME}, tok)
        if not sec.get("secretValue"):
            raise GAError(0, "NO_SECRET", "Google returned no Measurement Protocol secret value")
        write_secret(cwd, tid, {"telemetry_id": tid, "stream": s["name"], "name": sec["name"],
                                "api_secret": sec["secretValue"]})
        secret = {"name": sec["name"], "display_name": sec.get("displayName") or SECRET_NAME}
        if secret != doc.get("secret"):
            doc.update(secret=secret, verified=None)     # a new secret never round-tripped
        want = key_events(cwd)
        have = {k.get("eventName") for k in listall("/v1beta/%s/keyEvents" % prop, "keyEvents", tok)}
        for ev in sorted(set(want) - have):
            call("POST", "admin", "/v1beta/%s/keyEvents" % prop, {"eventName": ev, "countingMethod": "ONCE_PER_EVENT"},
                 tok)
        doc["key_events"] = want
        doc["status"] = derive_status(cwd, path, doc)
        store(path, doc)
    except GAError as e:
        return ga_failed(e)
    M.emit(cwd, "telemetry.setup", role="mission", refs={"telemetry": M.rel(cwd, path)}, action="setup",
           telemetry_id=tid, property=prop, measurement_id=stream["measurement_id"], key_events=doc["key_events"])
    print("[telemetry] %s configured: %s, stream %s, %d key event(s)"
          % (tid, prop, stream["measurement_id"], len(doc["key_events"])), file=sys.stderr)
    return 0


def secret_live(doc, sec, tok):
    """Google's validation server checks the payload, never the secret: prove it on the Admin API."""
    live = listall("/v1beta/%s/measurementProtocolSecrets" % doc["stream"]["name"], "measurementProtocolSecrets", tok)
    return any(x.get("name") == sec.get("name") and x.get("secretValue") == sec["api_secret"] for x in live)


def not_live(doc):
    return ("the local Measurement Protocol secret is not live on %s (deleted or for another stream): rerun "
            "`aloop mission telemetry setup`" % doc["stream"]["name"])


def ready(cwd, doc):
    """(secret, rc): the stream and its local secret, or a block naming setup."""
    sec = read_secret(cwd, doc["telemetry_id"])
    if not bound(doc) or not sec:
        say("BLOCK", "not set up for %s on this machine: `aloop mission telemetry setup` first" % doc["terms"]["uri"])
        return None, 2
    return sec, 0


def cmd_debug_hit(cwd, o):
    path, doc, rc = consented(cwd)
    if rc:
        return rc
    endpoint("mp")
    sec, rc = ready(cwd, doc)
    if rc:
        return rc
    evs = journey_events(cwd)
    bad = ["event %r %s" % (e, name_problem(e)) for e in evs if name_problem(e)]
    for b in bad:
        say("BLOCK", b)
    if bad:
        return 2
    tok = need_token()
    if not tok:
        return 3
    mid, msgs, send = doc["stream"]["measurement_id"], [], evs or [PROBE_EVENT]
    try:
        if not secret_live(doc, sec, tok):
            msgs.append({"fieldPath": "api_secret", "validationCode": "SECRET_NOT_LIVE",
                         "description": not_live(doc)})
        else:
            for i in range(0, len(send), MP_BATCH):
                body = {"client_id": DEBUG_CLIENT_ID, "validation_behavior": "ENFORCE_RECOMMENDATIONS",
                        "events": [{"name": e, "params": {"engagement_time_msec": 1}} for e in send[i:i + MP_BATCH]]}
                r = call("POST", "mp", "/debug/mp/collect", body,
                         query={"measurement_id": mid, "api_secret": sec["api_secret"]})
                msgs += r.get("validationMessages") or []
    except GAError as e:
        return ga_failed(e)
    doc["debug_hit"] = {"at": M.now(), "ok": not msgs, "measurement_id": mid, "events_sha256": events_sha(evs),
                        "events": len(send), "messages": msgs[:20]}
    store(path, doc)
    M.emit(cwd, "telemetry.setup", role="mission", refs={"telemetry": M.rel(cwd, path)}, action="debug_hit",
           telemetry_id=doc["telemetry_id"], ok=not msgs, events=len(send))
    for m in msgs:
        say("BLOCK", "GA validation: %s (%s) %s" % (m.get("validationCode"), m.get("fieldPath"), m.get("description")))
    if msgs:
        return 2
    print("[telemetry] debug hit ok: %d event(s) valid for %s" % (len(send), mid), file=sys.stderr)
    return 0


def number(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return int(f) if f.is_integer() else f


def simplify(r):
    """A Data API response as plain rows: [{dimension: value, metric: number}], plus its flags."""
    dims = [h["name"] for h in r.get("dimensionHeaders") or []]
    mets = [h["name"] for h in r.get("metricHeaders") or []]
    rows = []
    for row in r.get("rows") or []:
        d = {k: v.get("value") for k, v in zip(dims, row.get("dimensionValues") or [])}
        d.update({k: number(v.get("value")) for k, v in zip(mets, row.get("metricValues") or [])})
        rows.append(d)
    meta = r.get("metadata") or {}
    return {"rows": rows, "row_count": r.get("rowCount", len(rows)),
            "thresholded": bool(meta.get("subjectToThresholding")), "data_loss": bool(meta.get("dataLossFromOtherRow"))}


def probes(doc, tok):
    """The probe events the stream's realtime report counts (the last 30 minutes)."""
    r = simplify(call("POST", "data", "/v1beta/%s:runRealtimeReport" % doc["property"],
                      request("realtime", [PROBE_EVENT], stream_id(doc)), tok))
    return sum(int(x.get("eventCount") or 0) for x in r["rows"] if x.get("eventName") == PROBE_EVENT)


def verify_wait():
    try:
        wait = float(_paths.env("GA_VERIFY_WAIT") or VERIFY_WAIT)
    except ValueError:
        wait = -1
    if not 0 <= wait <= VERIFY_MAX:
        raise Usage("ALADDIN_GA_VERIFY_WAIT must be 0 to %d seconds" % VERIFY_MAX)
    return wait


def still(cwd, doc):
    """The consent and setup verify started from still hold (a revoke or a new setup may land meanwhile)."""
    path, now = current(cwd)
    return now is not None and now.get("telemetry_id") == doc["telemetry_id"] and now.get("stream") == doc["stream"] \
        and consent_state(cwd, path, now)[0] == "ok"


def cmd_verify(cwd, o):
    """The live proof (M9.14): one probe through the real Measurement Protocol endpoint must reach
    the stream's realtime report. MP answers 2xx even when it drops a hit (a wrong secret or
    measurement id), so nothing short of this round-trip proves the setup."""
    path, doc, rc = consented(cwd)
    if rc:
        return rc
    endpoint("mp")
    endpoint("data")
    wait = verify_wait()
    sec, rc = ready(cwd, doc)
    if rc:
        return rc
    tok = need_token()
    if not tok:
        return 3
    mid = doc["stream"]["measurement_id"]
    changed = ("the consent or setup changed while verify ran (a revoke, new terms or a new stream): nothing "
               "recorded; rerun `aloop mission telemetry verify` once it is consented and set up")
    try:
        if not secret_live(doc, sec, tok):
            say("BLOCK", not_live(doc))
            return 2
        before = probes(doc, tok)
        # Realtime shows a Measurement Protocol event only with a session id and engagement time
        call("POST", "mp", "/mp/collect", {"client_id": DEBUG_CLIENT_ID, "events": [
            {"name": PROBE_EVENT, "params": {"session_id": str(int(time.time())), "engagement_time_msec": 1}}]},
             query={"measurement_id": mid, "api_secret": sec["api_secret"]})
        deadline = time.time() + wait
        while True:
            if not still(cwd, doc):
                say("ASK", changed)
                return 3
            after = probes(doc, tok)
            left = deadline - time.time()
            if after > before or left <= 0:
                break
            before = min(before, after)      # older probes left the 30-minute window: count from here
            time.sleep(min(VERIFY_EVERY, left))
    except GAError as e:
        return ga_failed(e)
    if not still(cwd, doc):
        say("ASK", changed)
        return 3
    path, doc = current(cwd)
    ok = after > before
    doc["verified"] = {"at": M.now(), "ok": ok, "measurement_id": mid, "before": before, "after": after}
    store(path, doc)
    M.emit(cwd, "telemetry.setup", role="mission", refs={"telemetry": M.rel(cwd, path)}, action="verify",
           telemetry_id=doc["telemetry_id"], ok=ok)
    if not ok:
        say("BLOCK", "the %s probe sent to %s did not reach the stream's realtime report within %ds: Google "
            "dropped it (a wrong secret or measurement id answers 2xx too) or is slow; rerun "
            "`aloop mission telemetry verify`, then `setup`" % (PROBE_EVENT, mid, wait))
        return 2
    print("[telemetry] verified live: the probe reached %s (realtime %s: %d -> %d)" % (mid, PROBE_EVENT, before, after),
          file=sys.stderr)
    return 0


def stream_id(doc):
    return doc["stream"]["name"].rsplit("/", 1)[-1]


def request(name, events, stream):
    """The exact body sent for a fixed report: its definition, scoped to the consented stream,
    without the probe's client for user counts and, for event reports, to the journey events."""
    body = json.loads(json.dumps(REPORTS[name]["body"]))
    exprs = [{"filter": {"fieldName": "streamId", "stringFilter": {"matchType": "EXACT", "value": stream}}}]
    if REPORTS[name].get("no_probe"):        # verify's probe client is no user
        exprs.append({"notExpression": {"filter": {"fieldName": "eventName", "stringFilter": {
            "matchType": "EXACT", "value": PROBE_EVENT}}}})
    if REPORTS[name].get("events"):
        exprs.append({"filter": {"fieldName": "eventName", "inListFilter": {"values": sorted(events)}}})
    body["dimensionFilter"] = exprs[0] if len(exprs) == 1 else {"andGroup": {"expressions": exprs}}
    return body


def share(users, thresholded, settled):
    """The minimum-sample rule: how much of the next goal usage may drive. Code, not judgment."""
    if not settled:
        return {"usage": 0, "research": 1, "reason": "unsettled (realtime) data never drives a goal"}
    if thresholded:
        return {"usage": 0, "research": 1, "reason": "GA4 thresholded the data: treated as below the minimum sample"}
    if users < MIN_USERS:
        return {"usage": 0, "research": 1, "reason": "%d users over %d days is below the minimum sample of %d"
                % (users, WINDOW_DAYS, MIN_USERS)}
    return {"usage": USAGE_SHARE, "research": RESEARCH_FLOOR,
            "reason": "%d users over %d days: usage leads, research keeps %d%%" % (users, WINDOW_DAYS, RESEARCH_FLOOR * 100)}


def cmd_report(cwd, o):
    path, doc, rc = consented(cwd)
    if rc:
        return rc
    endpoint("data")
    if not bound(doc):
        say("BLOCK", "no stream for %s yet: `aloop mission telemetry setup` first" % doc["terms"]["uri"])
        return 2
    tok = need_token()
    if not tok:
        return 3
    realtime, evs, results = bool(o.get("realtime")), journey_events(cwd), {}
    try:
        for n in (("realtime",) if realtime else SETTLED_REPORTS):
            if REPORTS[n].get("events") and not evs:
                continue            # only journey events are read; none yet, nothing to read
            r = call("POST", "data", "/v1beta/%s:%s" % (doc["property"], REPORTS[n]["api"]),
                     request(n, evs, stream_id(doc)), tok)
            results[n] = simplify(r)
    except GAError as e:
        return ga_failed(e)
    sample = sh = None
    if not realtime:
        rows = results["users"]["rows"]
        users = int(rows[0].get("totalUsers") or 0) if rows else 0
        thr = any(x["thresholded"] for x in results.values())
        sample = {"users": users, "min_users": MIN_USERS, "days": WINDOW_DAYS, "thresholded": thr,
                  "met": users >= MIN_USERS and not thr}
        sh = share(users, thr, True)
    rid = M.new_id("telemetry")
    rep = {"report_id": rid, "telemetry_id": doc["telemetry_id"], "kind": "realtime" if realtime else "settled",
           "fetched_at": M.now(), "property": doc["property"], "stream": doc["stream"]["name"],
           "definitions_sha256": M.digest(REPORTS, drop=()), "date_range": "last 30 minutes" if realtime else SETTLED,
           "events": evs, "results": results, "sample": sample, "share": sh}
    M.seal(rep)
    p = M.save(M.mdir(cwd, "telemetry", "reports", rid + ".json"), rep)
    M.emit(cwd, "telemetry.report", role="mission", refs={"report": M.rel(cwd, p)}, report_id=rid, kind=rep["kind"],
           users=(sample or {}).get("users"), sample_met=(sample or {}).get("met"))
    if o.get("json"):
        print(json.dumps(dict(rep, path=M.rel(cwd, p)), indent=1))
    else:
        print(M.rel(cwd, p))
        if sh:
            print("[telemetry] usage share %s, research %s: %s" % (sh["usage"], sh["research"], sh["reason"]),
                  file=sys.stderr)
    return 0


def latest_share(cwd):
    """The usage/research split for the next goal (M9.11 reads this): from the newest settled report
    of the current record and stream whose seal holds, and only while the consent covers the terms."""
    path, doc = current(cwd)
    none = {"usage": 0, "research": 1, "report": None}
    if doc is None or consent_state(cwd, path, doc)[0] != "ok":
        return dict(none, reason="telemetry is off, revoked, not consented, or its ledger fails its check: "
                                 "research only")
    stream = (doc.get("stream") or {}).get("name") if bound(doc) else None
    for p in sorted(glob.glob(M.mdir(cwd, "telemetry", "reports", "*.json")), reverse=True):
        rep, _ = M.load(p)
        if not isinstance(rep, dict) or rep.get("kind") != "settled" or rep.get("sha256") != M.digest(rep) \
                or rep.get("telemetry_id") != doc["telemetry_id"] or not stream or rep.get("stream") != stream:
            continue
        s = rep.get("sample") or {}
        return dict(share(int(s.get("users") or 0), bool(s.get("thresholded")), True), report=rep.get("report_id"),
                    users=s.get("users"))
    return dict(none, reason="no settled usage report for this stream yet: research only")


def cmd_share(cwd, o):
    sh = latest_share(cwd)
    if o.get("json"):
        print(json.dumps(sh, indent=1))
    else:
        print("usage %s · research %s · %s" % (sh["usage"], sh["research"], sh["reason"]))
    return 0


def evaluate(cwd):
    """(exit, problems, facts) of the structural check. It never reads usage numbers."""
    path, doc = current(cwd)
    if doc is None:
        return 0, [], {"status": "off", "note": "telemetry off (opt-in): nothing to check"}
    bad = M.problems(doc, "telemetry", path) + ledger_problems(cwd)
    facts = {"telemetry_id": doc.get("telemetry_id"), "path": M.rel(cwd, path), "status": doc.get("status")}
    if bad:
        return 2, bad, facts
    state, ev = consent_state(cwd, path, doc)
    facts["consent"] = state
    if state == "none":
        return 0, [], dict(facts, note="telemetry off (opt-in): drafted, not consented")
    if state == "revoked":
        mid = (doc.get("stream") or {}).get("measurement_id")
        at = tagged(cwd, mid) if mid else None
        return 0, [], dict(facts, still_tagged=at, note="telemetry revoked (opt-out) by %s%s" % (
            (ev.get("data") or {}).get("by"), "; the app still carries %s at %s (remove it to stop collection)"
            % (mid, at) if at else ""))
    if state == "stale":
        return 3, ["the consent does not cover the current terms: a human runs "
                   "`aloop mission telemetry consent --by NAME`"], facts
    js, problems = journeys(cwd), []
    for j, _, drifted in js:
        if drifted:
            problems.append("journey %r changed after the records pinned it: `aloop mission journeys check` names "
                            "the fix" % j)
    evs = sorted({e for _, x, _ in js for e in x})
    for e in evs:
        if name_problem(e):
            problems.append("journey event %r %s" % (e, name_problem(e)))
    found = emitted(cwd, evs)
    missing = [{"journey": j, "event": e} for j, x, _ in js for e in x if e not in found]
    for m in missing:
        problems.append("journey %r step event %r is not emitted by any non-test source file (instrumentation is "
                        "part of done)" % (m["journey"], m["event"]))
    facts.update(events=evs, emitted=found, missing=missing)
    if doc.get("stream") and not bound(doc):
        problems.append("the stream measures %s, not the consented %s: `aloop mission telemetry setup`"
                        % (doc["stream"]["default_uri"], doc["terms"]["uri"]))
    elif bound(doc):
        mid = doc["stream"]["measurement_id"]
        facts["tagged"] = tagged(cwd, mid)
        if not facts["tagged"]:
            problems.append("the app does not carry the stream's measurement id %s (e.g. gtag('config', '%s')): "
                            "its events would reach no stream" % (mid, mid))
        if doc.get("key_events") != key_events(cwd):
            problems.append("the key events %s do not match the active journeys' last steps %s: "
                            "`aloop mission telemetry setup`" % (doc.get("key_events"), key_events(cwd)))
        facts["verified"] = bool((doc.get("verified") or {}).get("ok")
                                 and doc["verified"].get("measurement_id") == mid)
        dh = doc.get("debug_hit")
        if not dh:
            problems.append("no debug hit yet: `aloop mission telemetry debug-hit`")
        elif not dh.get("ok"):
            problems.append("the last debug hit failed: fix it and rerun `aloop mission telemetry debug-hit`")
        elif dh.get("measurement_id") != mid or dh.get("events_sha256") != events_sha(evs):
            problems.append("the debug hit is stale (the journey events or stream changed): "
                            "`aloop mission telemetry debug-hit`")
    return (2 if problems else 0), problems, facts


def cmd_check(cwd, o):
    rc, bad, facts = evaluate(cwd)
    if o.get("json"):
        print(json.dumps(dict(facts, exit=rc, problems=bad), indent=1))
    for b in bad:
        say("ASK" if rc == 3 else "BLOCK", b)
    if rc == 0:
        print("[telemetry] %s" % (facts.get("note") or "ok: consented, instrumented%s" % (
            ", tagged, debug hit fresh" if facts.get("tagged") else "")), file=sys.stderr)
    return rc


def cmd_show(cwd, o):
    path, doc = current(cwd)
    if doc is None:
        say("ASK", OPT_IN)
        return 3
    state = consent_state(cwd, path, doc)[0]
    if o.get("json"):
        print(json.dumps({"record": doc, "consent": state, "terms_sha256": terms_sha(doc), "share": latest_share(cwd)},
                         indent=1))
        return 0
    t = doc["terms"]
    print("Telemetry %s (%s, consent: %s)\nMeasures: %s\nPurpose: %s\nCollects: %s\nAgents see: %s\nOthers: %s\n"
          "Google's acknowledgement: %s\nCredential: %s\nScopes: %s" % (
              doc["telemetry_id"], doc.get("status"), state, t["uri"], t["purpose"], "; ".join(t["collects"]),
              t["agents_see"], t["others"], t["acknowledgement"], t["credential"], " ".join(t["scopes"])))
    if doc.get("property"):
        v = doc.get("verified") or {}
        print("Property: %s · stream %s · key events: %s · verified live: %s" % (
            doc["property"], (doc.get("stream") or {}).get("measurement_id"),
            ", ".join(doc.get("key_events") or []) or "none", v.get("at") if v.get("ok") else "no"))
    return 0


def cmd_auth(cwd, o):
    print(AUTH_STEPS)
    return 0


CMDS = {"init": (cmd_init, {"uri", "name", "time_zone", "force"}), "consent": (cmd_consent, {"by", "reason"}),
        "revoke": (cmd_revoke, {"by", "reason"}), "auth": (cmd_auth, set()),
        "setup": (cmd_setup, {"account", "property"}), "debug-hit": (cmd_debug_hit, set()),
        "verify": (cmd_verify, set()),
        "report": (cmd_report, {"realtime", "json"}), "share": (cmd_share, {"json"}), "show": (cmd_show, {"json"}),
        "check": (cmd_check, {"json"})}
FLAGS = {"json", "force", "realtime"}


def parse(argv, allowed):
    """Options of one command: only its own, so a typo or a --help never runs a network command."""
    opts, k = {}, None
    for a in argv:
        if k:
            opts[k], k = a, None
        elif a in ("-h", "--help"):
            raise Help()
        elif not a.startswith("--") or a[2:].replace("-", "_") not in allowed:
            raise Usage("unexpected %r (allowed: %s)" % (a, " ".join(sorted("--" + x.replace("_", "-")
                                                                             for x in allowed)) or "no options"))
        elif a[2:].replace("-", "_") in FLAGS:
            opts[a[2:].replace("-", "_")] = True
        else:
            k = a[2:].replace("-", "_")
    if k:
        raise Usage("--%s needs a value" % k.replace("_", "-"))
    return opts


def main(argv, cwd):
    if argv[0] not in CMDS:
        say("BLOCK", "unknown command %r (mission telemetry --help)" % argv[0])
        return 64
    fn, allowed = CMDS[argv[0]]
    try:
        return fn(cwd, parse(argv[1:], allowed))
    except Help:
        print(__doc__.strip())
        return 0
    except Usage as e:
        say("BLOCK", str(e))
        return 64
