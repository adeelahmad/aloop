"""telemetry — usage from Google Analytics 4: opt-in, informs goals, never a gate (ROADMAP §4.12, M9.6).

Real usage drives most of a later goal once there is enough of it, so it is captured from day
one through GA4, which the agents set up through Google's APIs: no server to host, nothing to
buy. Nothing reaches the network until a human consents to the exact terms below, and a
revoke stops it. Agents see aggregate counts from fixed report definitions only.

Usage:
  mission telemetry init --uri URL [--name NAME] [--time-zone TZ] [--force]
        draft the terms (what is measured, why, what agents see, GA's user-data acknowledgement,
        the OAuth scopes) as a sealed record, status off. Nothing leaves the machine.
        --force re-drafts the terms of the existing record (consent is asked again).
  mission telemetry consent --by NAME [--reason TEXT]
        the human opts in to exactly these terms: telemetry.consent names their sha256
  mission telemetry revoke --by NAME [--reason TEXT]
        opt out: setup, debug-hit and report refuse until a new consent
  mission telemetry auth
        the one-time Google sign-in, as pastable steps (the only human step)
  mission telemetry setup [--account accounts/N] [--property properties/N]
        Admin API, idempotent: find or create the property, acknowledge user-data collection,
        a web data stream for the uri, a Measurement Protocol secret (kept in .aladdin/state/,
        never in docs/) and a key event for each active journey's last step
  mission telemetry debug-hit
        send every active journey step's event to GA's validation server; the result is recorded
  mission telemetry report [--realtime] [--json]
        the fixed Data API reports: settled (28 days ending 3 days ago, so ≥48h old) or realtime
        (the last 30 minutes, a quick signal only); written to docs/agents/mission/telemetry/reports/
  mission telemetry share [--json]
        how much of the next goal usage may drive, from the latest settled report: 0 below
        50 users over 28 days or when GA thresholded the data, else 0.7 (research keeps 0.3)
  mission telemetry show [--json]
  mission telemetry check [--json]
        the structural check: consent covers the current terms; once consented, every active
        journey step's event is emitted by non-test source code; once configured, a passing
        debug hit covers the current events. Usage numbers never change its exit.
        Exit 0 ok (or off) · 2 block · 3 needs the human · 64 usage

Environment: ALADDIN_GA_ACCESS_TOKEN (an OAuth access token), ALADDIN_GA_TOKEN_COMMAND (default
`gcloud auth application-default print-access-token`), ALADDIN_GA_BASE (a loopback fake, tests only).
"""
import glob
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit

import _events
import _mission as M
import _paths

SCOPES = ("https://www.googleapis.com/auth/analytics.edit", "https://www.googleapis.com/auth/analytics.readonly")
# Google's verbatim text for properties.acknowledgeUserDataCollection, which a Measurement Protocol
# secret needs; the human consents to it as part of the terms
ACKNOWLEDGEMENT = ("I acknowledge that I have the necessary privacy disclosures and rights from my end users for the "
                   "collection and processing of their data, including the association of such data with the "
                   "visitation information Google Analytics collects from my site and/or app property.")
HOSTS = {"admin": "https://analyticsadmin.googleapis.com", "data": "https://analyticsdata.googleapis.com",
         "mp": "https://www.google-analytics.com"}
LOOPBACK = ("127.0.0.1", "localhost", "::1")
TOKEN_COMMAND = "gcloud auth application-default print-access-token"

# the minimum-sample rule (§4.12 call): small numbers are noise, and GA hides them anyway
MIN_USERS, WINDOW_DAYS, USAGE_SHARE, RESEARCH_FLOOR = 50, 28, 0.7, 0.3
SETTLED = {"startDate": "30daysAgo", "endDate": "3daysAgo"}   # 28 days; the newest is ≥48h old, so settled
# the fixed report definitions (hashed into every report). Aggregate dimensions only: agents
# never see a user or client id
AGGREGATE_DIMENSIONS = {"eventName", "date", "newVsReturning", "deviceCategory"}
REPORTS = {
    "users": {"api": "runReport", "body": {"dateRanges": [SETTLED], "metrics": [
        {"name": "totalUsers"}, {"name": "newUsers"}, {"name": "activeUsers"}]}},
    "journeys": {"api": "runReport", "events": True, "body": {"dateRanges": [SETTLED], "dimensions": [
        {"name": "eventName"}], "metrics": [{"name": "eventCount"}, {"name": "totalUsers"}]}},
    "realtime": {"api": "runRealtimeReport", "events": True, "body": {"dimensions": [{"name": "eventName"}],
                                                                       "metrics": [{"name": "eventCount"}, {"name": "activeUsers"}]}},
}
SETTLED_REPORTS = ("users", "journeys")

# GA4 event names: a letter, then letters, digits or underscores, at most 40; reserved names and prefixes
EVENT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
RESERVED = {"ad_activeview", "ad_click", "ad_exposure", "ad_impression", "ad_query", "ad_reward", "adunit_exposure",
            "app_clear_data", "app_exception", "app_install", "app_remove", "app_store_refund", "app_update",
            "app_upgrade", "dynamic_link_app_open", "dynamic_link_app_update", "dynamic_link_first_open", "error",
            "first_open", "first_visit", "in_app_purchase", "notification_dismiss", "notification_foreground",
            "notification_open", "notification_receive", "os_update", "session_start", "session_start_with_rollout",
            "user_engagement"}
RESERVED_PREFIXES = ("_", "firebase_", "ga_", "google_", "gtag.")
MP_BATCH = 25                     # events per Measurement Protocol request
DEBUG_CLIENT_ID = "aloop.debug"   # the debug hit's client id: no user's
PROBE_EVENT = "aloop_probe"       # sent when no journey has events yet, to prove the wiring
SECRET_NAME = "aloop"

# the emit check reads code, not docs, data or tests
CODE_EXT = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".svelte", ".astro", ".html", ".htm", ".py",
            ".rb", ".erb", ".go", ".rs", ".java", ".kt", ".swift", ".dart", ".php", ".cs", ".ex", ".exs", ".m"}
SKIP_DIRS = {"docs", "test", "tests", "__tests__", "spec", "specs", "fixtures", "node_modules", "vendor", "dist", "build"}
TEST_FILE = re.compile(r"(^test_|_test\.[^.]+$|\.(test|spec)\.[^.]+$)")

AUTH_STEPS = """One-time Google sign-in (the only human step; paste in a terminal on your Mac):
  1. A Google Analytics account (skip if you have one): https://analytics.google.com -> Start measuring.
  2. A Google Cloud project with the two Analytics APIs on:
       gcloud projects create aloop-ga-$RANDOM --set-as-default   # or: gcloud config set project YOUR_PROJECT
       gcloud services enable analyticsadmin.googleapis.com analyticsdata.googleapis.com
  3. An OAuth client for that project: https://console.cloud.google.com/auth/clients -> Create client ->
     Desktop app -> Download JSON, saved as ~/aloop-ga-client.json (the first time, the consent screen asks
     for an app name and your email, and you add yourself as a test user).
  4. Sign in once:
       gcloud auth application-default login --client-id-file="$HOME/aloop-ga-client.json" \\
         --scopes=https://www.googleapis.com/auth/analytics.edit,https://www.googleapis.com/auth/analytics.readonly,https://www.googleapis.com/auth/cloud-platform
Then: aloop mission telemetry setup"""


class Usage(Exception):
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
    """none | revoked | stale (the last consent covers other terms) | ok, from the events only."""
    ref, last = M.rel(cwd, path), None
    for e in _events.read(cwd):
        if e.get("event") in ("telemetry.consent", "telemetry.revoked") and (e.get("data") or {}).get("subject") == ref:
            last = e
    if last is None:
        return "none", None
    if last["event"] == "telemetry.revoked":
        return "revoked", last
    return ("ok" if (last.get("data") or {}).get("sha256") == terms_sha(doc) else "stale"), last


OPT_IN = ("telemetry is opt-in and off: `aloop mission telemetry init --uri URL`, then a human runs "
          "`aloop mission telemetry consent --by NAME`")


def consented(cwd):
    """(path, doc, 0) when the human's consent covers the current terms; else asks and returns rc 3
    (or 2 for a record that fails its own check). Every network command starts here."""
    path, doc = current(cwd)
    if doc is None:
        say("ASK", OPT_IN)
        return None, None, 3
    bad = M.problems(doc, "telemetry", path)
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


# ── journeys (M9.3's files, read by contract fields only) and the emit check ─────────
def journeys(cwd):
    """Active journeys as [(name, [step event, …])], oldest first."""
    out = []
    for _, d in M.artifacts(cwd, "journey"):
        if d.get("status") == "active":
            evs = [s.get("event") for s in d.get("steps") or [] if isinstance(s, dict) and isinstance(s.get("event"), str)]
            out.append((d.get("name") or d.get("journey_id"), evs))
    return out


def journey_events(cwd):
    return sorted({e for _, evs in journeys(cwd) for e in evs})


def events_sha(evs):
    return M.digest({"events": sorted(evs)}, drop=())


def name_problem(name):
    if not EVENT_NAME.match(name):
        return "must start with a letter and use only letters, digits and _ (at most 40)"
    if name in RESERVED or name.startswith(RESERVED_PREFIXES):
        return "is reserved by Google Analytics"
    return None


def source_files(root):
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
        if os.path.splitext(f)[1].lower() in CODE_EXT:
            yield f


def emitted(cwd, names):
    """{event: "file:line"} for each name found as a quoted literal ('x', "x" or `x`) in code."""
    root, found = _paths.project(cwd), {}
    want = {n: re.compile(r"(['\"`])%s\1" % re.escape(n)) for n in names}
    for f in source_files(root):
        if len(found) == len(want):
            break
        try:
            with open(os.path.join(root, f), errors="ignore") as fh:
                for i, line in enumerate(fh, 1):
                    for n, rx in want.items():
                        if n not in found and rx.search(line):
                            found[n] = "%s:%d" % (f, i)
        except OSError:
            continue
    return found


# ── the network: three Google hosts, or one loopback fake ────────────────────────────
def endpoint(api):
    base = _paths.env("GA_BASE")
    if not base:
        return HOSTS[api]
    u = urlsplit(base)
    if u.scheme not in ("http", "https") or u.hostname not in LOOPBACK:
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


def call(method, api, path, body=None, tok=None, query=None):
    url = endpoint(api) + path + ("?" + urlencode(query) if query else "")
    headers = {"Content-Type": "application/json"}
    if tok:
        headers["Authorization"] = "Bearer " + tok
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), headers=headers,
                                 method=method)
    # a loopback fake is reached directly, never through a proxy
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if _paths.env("GA_BASE") else \
        urllib.request.build_opener()
    try:
        with opener.open(req, timeout=60) as r:
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
    """Every item of a paged Admin API list."""
    out, page = [], None
    while True:
        q = {"pageSize": 200}
        if page:
            q["pageToken"] = page
        r = call("GET", "admin", path, tok=tok, query=q)
        out += r.get(key) or []
        page = r.get("nextPageToken")
        if not page:
            return out


def ga_failed(e):
    """Map a GA error to an exit: credentials and API access are the human's (3), the rest block (2)."""
    if e.code == 401:
        say("ASK", "Google rejected the credentials (%s).\n%s" % (e.message, AUTH_STEPS))
        return 3
    if e.code == 403 and re.search(r"has not been used|is disabled|SERVICE_DISABLED|accessNotConfigured", e.message):
        say("ASK", "an Analytics API is off in your Google Cloud project (%s). Paste:\n"
            "  gcloud services enable analyticsadmin.googleapis.com analyticsdata.googleapis.com" % e.message)
        return 3
    if e.code == 403:
        say("ASK", "the signed-in Google account lacks access (%s): give it the Editor role on the Analytics "
            "account (Admin -> Account access management), or pass --account / --property you can edit" % e.message)
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
        say("BLOCK", "--uri must be the app's http(s) URL (what is measured is part of the terms)")
        return 64
    path, doc = current(cwd)
    if doc is not None and not o.get("force"):
        say("BLOCK", "%s exists; `aloop mission telemetry init --uri URL --force` re-drafts its terms "
            "(consent is asked again)" % M.rel(cwd, path))
        return 2
    mission, _ = M.load(M.path_of(cwd, "mission"))
    mission = mission if isinstance(mission, dict) else {}
    name = o.get("name") or ("aloop: " + (mission.get("statement") or urlsplit(uri).hostname))[:100]
    terms = {"uri": uri,
             "purpose": "usage of %s informs research and goal-setting for the mission; it is never a gate" % uri,
             "collects": ["the app's Google Analytics 4 web measurement on this uri",
                          "one event per user-journey step (its name and engagement time)"],
             "agents_see": "aggregate counts only (event name, users, new users) from fixed report definitions; "
                           "never a user or client id",
             "acknowledgement": ACKNOWLEDGEMENT, "scopes": list(SCOPES),
             "others": "once other people use the app, a consent banner (Consent Mode v2) is a journey requirement"}
    settings = {"display_name": name, "time_zone": o.get("time_zone") or "Etc/GMT", "currency": "USD"}
    if doc is None:
        tid = M.new_id("telemetry")
        path = M.path_of(cwd, "telemetry", tid)
        doc = {"telemetry_id": tid, "status": "off", "created_at": M.now(), "provider": "ga4",
               "mission_id": mission.get("mission_id"), "terms": terms, "settings": settings, "account": None,
               "property": None, "acknowledged": False, "stream": None, "secret": None, "key_events": [],
               "debug_hit": None}
    else:
        doc.update(terms=terms, settings=settings, status="off")
    store(path, doc)
    print(path)
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
    doc["status"] = "configured" if doc.get("stream") else "consented"
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
    ref = M.rel(cwd, path)
    M.emit(cwd, "telemetry.revoked", role="human", refs={"subject": ref}, subject=ref, by=by, reason=reason,
           telemetry_id=doc.get("telemetry_id"))
    doc["status"] = "revoked"
    M.save(path, doc)       # status is outside the digest: the seal still holds
    print("[telemetry] %s revoked by %s: nothing is sent until a new consent" % (doc.get("telemetry_id"), by),
          file=sys.stderr)
    return 0


def pick_property(doc, o, tok):
    """(account, property, rc): the property given, recorded, found by display name, or created in
    the one reachable account. Which account is the human's call when there are none or several (rc 3)."""
    prop = o.get("property") or doc.get("property")
    if prop:
        call("GET", "admin", "/v1beta/" + prop, tok=tok)
        return doc.get("account"), prop, 0
    summaries = listall("/v1beta/accountSummaries", "accountSummaries", tok)
    if o.get("account"):
        found = [a for a in summaries if a.get("account") == o["account"]]
        if not found:
            say("BLOCK", "%s is not an account the signed-in Google user can reach (%s)"
                % (o["account"], ", ".join(a.get("account", "") for a in summaries) or "none"))
            return None, None, 2
    elif not summaries:
        say("ASK", "the signed-in Google user has no Analytics account: create one at https://analytics.google.com "
            "(Admin -> Create account; Google needs you to accept its terms there), then rerun "
            "`aloop mission telemetry setup`")
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
                       debug_hit=None)
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
            doc.update(stream=stream, secret=None, debug_hit=None)
        store(path, doc)
        local = read_secret(cwd, tid)
        secrets = listall("/v1beta/%s/measurementProtocolSecrets" % s["name"], "measurementProtocolSecrets", tok)
        sec = next((x for x in secrets if local and x.get("name") == local.get("name")), None) or \
            next((x for x in secrets if x.get("displayName") == SECRET_NAME and x.get("secretValue")), None)
        if sec is None:
            sec = call("POST", "admin", "/v1beta/%s/measurementProtocolSecrets" % s["name"],
                       {"displayName": SECRET_NAME}, tok)
        value = sec.get("secretValue") or (local or {}).get("api_secret")
        if not value:
            raise GAError(0, "NO_SECRET", "Google returned no Measurement Protocol secret value")
        write_secret(cwd, tid, {"telemetry_id": tid, "stream": s["name"], "name": sec["name"], "api_secret": value})
        doc["secret"] = {"name": sec["name"], "display_name": sec.get("displayName") or SECRET_NAME}
        want = [evs[-1] for _, evs in journeys(cwd) if evs and not name_problem(evs[-1])]
        have = {k.get("eventName") for k in listall("/v1beta/%s/keyEvents" % prop, "keyEvents", tok)}
        for ev in sorted(set(want) - have):
            call("POST", "admin", "/v1beta/%s/keyEvents" % prop, {"eventName": ev, "countingMethod": "ONCE_PER_EVENT"},
                 tok)
        doc["key_events"] = sorted(set(want))
        doc["status"] = "configured"
        store(path, doc)
    except GAError as e:
        return ga_failed(e)
    M.emit(cwd, "telemetry.setup", role="mission", refs={"telemetry": M.rel(cwd, path)}, action="setup",
           telemetry_id=tid, property=prop, measurement_id=stream["measurement_id"], key_events=doc["key_events"])
    print("[telemetry] %s configured: %s, stream %s, %d key event(s)"
          % (tid, prop, stream["measurement_id"], len(doc["key_events"])), file=sys.stderr)
    return 0


def cmd_debug_hit(cwd, o):
    path, doc, rc = consented(cwd)
    if rc:
        return rc
    endpoint("mp")
    sec = read_secret(cwd, doc["telemetry_id"])
    if not doc.get("stream") or not sec:
        say("BLOCK", "not set up on this machine yet: `aloop mission telemetry setup` first")
        return 2
    evs = journey_events(cwd)
    bad = ["event %r %s" % (e, name_problem(e)) for e in evs if name_problem(e)]
    for b in bad:
        say("BLOCK", b)
    if bad:
        return 2
    mid, msgs = doc["stream"]["measurement_id"], []
    send = evs or [PROBE_EVENT]
    try:
        for i in range(0, len(send), MP_BATCH):
            body = {"client_id": DEBUG_CLIENT_ID,
                    "events": [{"name": e, "params": {"engagement_time_msec": 1}} for e in send[i:i + MP_BATCH]]}
            r = call("POST", "mp", "/debug/mp/collect", body, query={"measurement_id": mid, "api_secret": sec["api_secret"]})
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


def request(name, events):
    """The exact body sent for a fixed report: its definition, filtered to the journey events."""
    body = json.loads(json.dumps(REPORTS[name]["body"]))
    if REPORTS[name].get("events"):
        body["dimensionFilter"] = {"filter": {"fieldName": "eventName", "inListFilter": {"values": sorted(events)}}}
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
    if not doc.get("property"):
        say("BLOCK", "no property yet: `aloop mission telemetry setup` first")
        return 2
    tok = need_token()
    if not tok:
        return 3
    realtime, evs, results = bool(o.get("realtime")), journey_events(cwd), {}
    try:
        for n in (("realtime",) if realtime else SETTLED_REPORTS):
            if REPORTS[n].get("events") and not evs:
                continue            # only journey events are read; none yet, nothing to read
            r = call("POST", "data", "/v1beta/%s:%s" % (doc["property"], REPORTS[n]["api"]), request(n, evs), tok)
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
           "fetched_at": M.now(), "property": doc["property"], "definitions_sha256": M.digest(REPORTS, drop=()),
           "date_range": "last 30 minutes" if realtime else SETTLED, "events": evs, "results": results,
           "sample": sample, "share": sh}
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
    whose seal holds, and only while the consent covers the terms."""
    path, doc = current(cwd)
    none = {"usage": 0, "research": 1, "report": None}
    if doc is None or consent_state(cwd, path, doc)[0] != "ok":
        return dict(none, reason="telemetry is off, revoked or not consented: research only")
    for p in sorted(glob.glob(M.mdir(cwd, "telemetry", "reports", "*.json")), reverse=True):
        rep, _ = M.load(p)
        if not isinstance(rep, dict) or rep.get("kind") != "settled" or rep.get("sha256") != M.digest(rep):
            continue
        s = rep.get("sample") or {}
        return dict(share(int(s.get("users") or 0), bool(s.get("thresholded")), True), report=rep.get("report_id"),
                    users=s.get("users"))
    return dict(none, reason="no settled usage report yet: research only")


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
    bad = M.problems(doc, "telemetry", path)
    facts = {"telemetry_id": doc.get("telemetry_id"), "path": M.rel(cwd, path), "status": doc.get("status")}
    if bad:
        return 2, bad, facts
    state, ev = consent_state(cwd, path, doc)
    facts["consent"] = state
    if state == "none":
        return 0, [], dict(facts, note="telemetry off (opt-in): drafted, not consented")
    if state == "revoked":
        return 0, [], dict(facts, note="telemetry revoked (opt-out) by %s" % (ev.get("data") or {}).get("by"))
    if state == "stale":
        return 3, ["the consent does not cover the current terms: a human runs "
                   "`aloop mission telemetry consent --by NAME`"], facts
    js, problems = journeys(cwd), []
    evs = sorted({e for _, x in js for e in x})
    for e in evs:
        if name_problem(e):
            problems.append("journey event %r %s" % (e, name_problem(e)))
    found = emitted(cwd, evs)
    missing = [{"journey": j, "event": e} for j, x in js for e in x if e not in found]
    for m in missing:
        problems.append("journey %r step event %r is not emitted by any non-test source file (instrumentation is "
                        "part of done)" % (m["journey"], m["event"]))
    facts.update(events=evs, emitted=found, missing=missing)
    if doc.get("stream"):
        dh = doc.get("debug_hit")
        if not dh:
            problems.append("no debug hit yet: `aloop mission telemetry debug-hit`")
        elif not dh.get("ok"):
            problems.append("the last debug hit failed GA validation: fix it and rerun `aloop mission telemetry debug-hit`")
        elif dh.get("measurement_id") != doc["stream"]["measurement_id"] or dh.get("events_sha256") != events_sha(evs):
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
            ", debug hit fresh" if facts.get("status") == "configured" else "")), file=sys.stderr)
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
          "Google's acknowledgement: %s\nScopes: %s" % (doc["telemetry_id"], doc.get("status"), state, t["uri"],
                                                       t["purpose"], "; ".join(t["collects"]), t["agents_see"],
                                                       t["others"], t["acknowledgement"], " ".join(t["scopes"])))
    if doc.get("property"):
        print("Property: %s · stream %s · key events: %s" % (doc["property"], (doc.get("stream") or {}).get(
            "measurement_id"), ", ".join(doc.get("key_events") or []) or "none"))
    return 0


def cmd_auth(cwd, o):
    print(AUTH_STEPS)
    return 0


CMDS = {"init": cmd_init, "consent": cmd_consent, "revoke": cmd_revoke, "auth": cmd_auth, "setup": cmd_setup,
        "debug-hit": cmd_debug_hit, "report": cmd_report, "share": cmd_share, "show": cmd_show, "check": cmd_check}
FLAGS = ("json", "force", "realtime")


def parse(argv):
    opts, pos, k = {}, [], None
    for a in argv:
        if k:
            opts[k], k = a, None
        elif a.startswith("--"):
            k = a[2:].replace("-", "_")
            if k in FLAGS:
                opts[k], k = True, None
        else:
            pos.append(a)
    if k:
        opts[k] = ""
    return opts, pos


def main(argv, cwd):
    fn = CMDS.get(argv[0])
    if fn is None:
        say("BLOCK", "unknown command %r (mission telemetry --help)" % argv[0])
        return 64
    o, _ = parse(argv[1:])
    try:
        return fn(cwd, o)
    except Usage as e:
        say("BLOCK", str(e))
        return 64
