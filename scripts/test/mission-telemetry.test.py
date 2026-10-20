#!/usr/bin/env python3
"""ROADMAP §4.12 (M9.6): usage telemetry through Google Analytics 4. It is opt-in: nothing
reaches the network until a human consents to exact terms, and a revoke stops the agents'
use of it. Setup goes through the Admin API (property, web stream, user-data acknowledgement,
Measurement Protocol secret, a key event per journey), a debug hit proves the wiring, fixed
Data API reports read settled data of the consented stream only, and the minimum-sample rule
decides how much of a goal usage may drive. Usage informs goals and is never a gate; the check
is structural (consent, every journey step's event is emitted, the app carries the stream, a
fresh debug hit). All network is a local fake (fixtures/fake-ga.py).
    python3 scripts/test/mission-telemetry.test.py
"""
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
MI = os.path.join(BIN, "mission")
T = tempfile.mkdtemp(prefix="aloop-telemetry-")
WT = T + "-wt"
T2 = tempfile.mkdtemp(prefix="aloop-telemetry-probe-")
PASS = [0]
sys.path.insert(0, BIN)
import _mission as M  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "fixtures"))
from mission_fixture import as_human  # noqa: E402

spec = importlib.util.spec_from_file_location("telemetry", os.path.join(ROOT, "mission", "telemetry.py"))
TM = importlib.util.module_from_spec(spec)
spec.loader.exec_module(TM)

FAKE = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(__file__), "fixtures", "fake-ga.py")],
                        stdout=subprocess.PIPE, text=True)
BASE = "http://127.0.0.1:%s" % FAKE.stdout.readline().strip()
ENV = dict(os.environ, HOME=os.path.join(T, "home"), ALADDIN_GA_BASE=BASE, ALADDIN_GA_ACCESS_TOKEN="fake-token",
           TZ="Europe/Stockholm")
for k in ("AGENTIC_HOST", "AGENTIC_GA_ACCESS_TOKEN", "AGENTIC_GA_TOKEN_COMMAND", "AGENTIC_GA_BASE"):
    ENV.pop(k, None)
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def run(*args, env=None, cwd=T):
    return as_human(lambda: subprocess.run([MI, "telemetry"] + list(args), cwd=cwd, env=env or ENV,
                                           capture_output=True, text=True), cwd, env or ENV)


def fake(path, body=None):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10) as r:
        return json.load(r)


def calls():
    return fake("/_log")


def mp_calls(log):
    return [c for c in log if c["path"] == "/mp/debug/mp/collect"]


def events(kind=None, root=T):
    p = os.path.join(root, ".aladdin", "logs", "events.jsonl")
    evs = [json.loads(l) for l in open(p)] if os.path.exists(p) else []
    return [e for e in evs if kind is None or e["event"] == kind]


def record(root=T):
    recs = M.artifacts(root, "telemetry")
    assert len(recs) == 1, recs
    return recs[0]


def journey(name, steps, status="active"):
    jid = M.new_id("journey")
    M.save(M.path_of(T, "journey", jid), {"journey_id": jid, "status": status, "created_at": M.now(), "name": name,
                                          "steps": [{"name": s, "event": s} for s in steps]})
    return jid


def write(rel, text, root=T):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)
    subprocess.run(["git", "add", rel], cwd=root, check=True)


def unwrite(rel, root=T):
    subprocess.run(["git", "rm", "-q", "-f", rel], cwd=root, check=True)


try:
    subprocess.run(["git", "init", "-q"], cwd=T, check=True)

    # 1) off by default: the check passes with nothing to check, and no command reaches the network
    r = run("check")
    assert r.returncode == 0 and "off (opt-in)" in r.stderr, (r.returncode, r.stderr)
    for cmd in (["setup"], ["debug-hit"], ["report"], ["consent", "--by", "Adeel"]):
        r = run(*cmd)
        assert r.returncode == 3 and "ASK[telemetry]" in r.stderr and "telemetry init" in r.stderr, (cmd, r.stderr)
    r = run("setup", "--account", "accounts/100", "--help")
    assert r.returncode == 0 and "never a gate" in r.stdout, r.stderr                 # --help anywhere is help
    assert run("setup", "--propery", "properties/1").returncode == 64                   # a typo is not ignored
    assert run("report", "extra").returncode == 64 and run("consent", "--by").returncode == 64
    assert calls() == [], calls()
    ok("telemetry is off until a human opts in: check passes, network commands ask, --help/typos run nothing")

    # 2) init drafts exact terms; only a human's consent counts, and it covers exactly those terms
    assert run("init").returncode == 64                      # what is measured is part of the terms
    assert run("init", "--uri", "ftp://todo").returncode == 64
    r = run("init", "--uri", "https://todo.example")
    assert r.returncode == 0, r.stderr
    path, doc = record()
    assert M.problems(doc, "telemetry", path) == [], M.problems(doc, "telemetry", path)
    assert doc["status"] == "off" and doc["telemetry_id"].startswith("tlm_") and doc["sha256"] == M.digest(doc)
    assert doc["terms"]["acknowledgement"] == TM.ACKNOWLEDGEMENT and "never a gate" in doc["terms"]["purpose"]
    assert doc["terms"]["uri"] == "https://todo.example" and set(TM.SCOPES) == set(doc["terms"]["scopes"])
    assert "service account" in doc["terms"]["credential"] and "cloud-platform" in doc["terms"]["credential"]
    assert doc["settings"]["time_zone"] == "Europe/Stockholm", doc["settings"]        # the machine's zone
    assert run("init", "--uri", "https://other.example").returncode == 2      # one record; --force re-drafts
    r = run("setup")
    assert r.returncode == 3 and "consent" in r.stderr, r.stderr
    forged = subprocess.run([os.path.join(BIN, "events"), "emit", "telemetry.consent", "--role", "supervisor",
                             "--data", "subject=" + M.rel(T, path), "--data", "sha256=" + TM.terms_sha(doc)],
                            cwd=T, env=ENV, capture_output=True, text=True)
    assert forged.returncode == 64 and "only its own command" in forged.stderr, forged.stderr  # events emit refuses it
    M.emit(T, "telemetry.consent", role="supervisor", subject=M.rel(T, path), sha256=TM.terms_sha(doc))  # appended anyway
    assert run("setup").returncode == 3 and run("check").returncode == 0 and calls() == []   # not a consent
    assert run("consent").returncode == 64                    # who consents is required
    r = run("consent", "--by", "Adeel", "--reason", "my own app, day one")
    assert r.returncode == 0, r.stderr
    path, doc = record()
    ev = events("telemetry.consent")
    assert [e["role"] for e in ev] == ["supervisor", "human"] and ev[1]["data"]["by"] == "Adeel", ev
    assert ev[1]["data"]["sha256"] == TM.terms_sha(doc) and ev[1]["data"]["subject"] == M.rel(T, path)
    assert doc["status"] == "consented" and run("check").returncode == 0 and calls() == []
    ok("init seals the terms (uri, credential, GA's acknowledgement); a forged non-human consent is ignored")

    # 3) consent covers exact terms; a hand-edited record blocks every command, consent cannot launder it
    doc["terms"]["uri"] = "https://elsewhere.example"
    M.seal(doc)
    M.save(path, doc)
    r = run("check")
    assert r.returncode == 3 and "does not cover" in r.stderr, r.stderr
    assert run("setup").returncode == 3 and calls() == []
    doc["terms"]["uri"] = "https://todo.example"
    M.seal(doc)
    M.save(path, doc)
    assert run("check").returncode == 0
    raw = json.load(open(path))
    raw["property"] = "properties/999"                      # a hand edit after sealing
    json.dump(raw, open(path, "w"))
    for cmd in (["check"], ["setup"], ["report"], ["debug-hit"], ["consent", "--by", "Adeel"]):
        r = run(*cmd)
        assert r.returncode == 2 and "sha256 does not match" in r.stderr, (cmd, r.stderr)
    assert calls() == [] and json.load(open(path)) == raw     # not re-sealed by consent
    M.seal(doc)
    M.save(path, doc)
    ok("consent covers exact terms; a hand-edited record blocks check, setup, report, debug-hit and consent")

    # 4) credentials: none asks once with the pastable sign-in; a token command works; endpoints are loopback only
    env = dict(ENV, ALADDIN_GA_TOKEN_COMMAND="false")
    env.pop("ALADDIN_GA_ACCESS_TOKEN")
    r = run("setup", env=env)
    assert r.returncode == 3 and "gcloud auth application-default login" in r.stderr, r.stderr
    assert "analytics.edit" in r.stderr and "--impersonate-service-account" in r.stderr and calls() == []
    r = run("auth")
    assert r.returncode == 0 and "iamcredentials.googleapis.com" in r.stdout and "Account access management" in r.stdout
    for bad in ("https://evil.example", "https://127.0.0.1.evil.example", "https://evil.example/localhost",
                "http://localhost.evil.example", "http://user@127.0.0.1:9", "file:///127.0.0.1"):
        r = run("setup", env=dict(ENV, ALADDIN_GA_BASE=bad))
        assert r.returncode == 64 and "loopback" in r.stderr, (bad, r.stderr)
    assert calls() == []
    ok("no token: setup asks with the one-time sign-in; every non-loopback endpoint override is refused")

    # 5) setup through the Admin API: the account is the human's call when it is missing or ambiguous
    fake("/_control", {"accounts": 0})
    r = run("setup")
    assert r.returncode == 3 and "analytics.google.com" in r.stderr, r.stderr
    fake("/_control", {"accounts": 2})
    r = run("setup")
    assert r.returncode == 3 and "--account accounts/100" in r.stderr and "accounts/101" in r.stderr, r.stderr
    assert run("setup", "--account", "accounts/7").returncode == 2
    assert all(c["method"] == "GET" for c in calls())           # asking never creates anything
    journey("sign up", ["signup_started", "signup_done"])
    journey("add a todo", ["todo_add_opened", "todo_added"])
    journey("old idea", ["old_step"], status="retired")
    n = len(calls())
    r = run("setup", "--account", "accounts/101")
    assert r.returncode == 0, r.stderr
    path, doc = record()
    assert doc["status"] == "configured" and doc["property"].startswith("properties/") and M.problems(doc, "telemetry", path) == []
    assert doc["account"] == "accounts/101" and doc["stream"]["default_uri"] == "https://todo.example"
    assert doc["stream"]["measurement_id"].startswith("G-") and doc["key_events"] == ["signup_done", "todo_added"]
    log = calls()[n:]
    posts = [c["path"].split("/v1beta/")[1] for c in log if c["method"] == "POST"]
    assert posts[0] == "properties" and posts[1].endswith(":acknowledgeUserDataCollection"), posts
    assert [p.rsplit("/", 1)[-1] for p in posts[2:]] == ["dataStreams", "measurementProtocolSecrets", "keyEvents", "keyEvents"], posts
    prop_body = [c["body"] for c in log if c["path"].endswith("/v1beta/properties") and c["method"] == "POST"][0]
    assert prop_body["parent"] == "accounts/101" and prop_body["timeZone"] == "Europe/Stockholm", prop_body
    assert [c["body"] for c in log if c["path"].endswith(":acknowledgeUserDataCollection")] == [{"acknowledgement": TM.ACKNOWLEDGEMENT}]
    assert all(c["auth"] == "Bearer fake-token" for c in log)
    sfile = TM.secret_file(T, doc["telemetry_id"])
    assert os.path.isfile(sfile) and stat.S_IMODE(os.stat(sfile).st_mode) == 0o600, sfile
    assert M.rel(T, sfile).startswith(".aladdin" + os.sep), sfile
    secret = json.load(open(sfile))
    for d, _, fs in os.walk(os.path.join(T, "docs")):
        for f in fs:
            assert secret["api_secret"] not in open(os.path.join(d, f)).read(), "the MP secret leaked into %s" % f
    assert [e["data"].get("action") for e in events("telemetry.setup")] == ["setup"]
    ok("setup: --account picks; property, acknowledgement, web stream, MP secret (0600, outside docs/), key events")

    # 6) setup is idempotent and recovers: reruns create nothing; expired tokens, redirects and lost properties
    n = len(calls())
    r = run("setup")
    assert r.returncode == 0, r.stderr
    assert not [c for c in calls()[n:] if c["method"] != "GET"] and record()[1]["property"] == doc["property"]
    env = dict(ENV, ALADDIN_GA_TOKEN_COMMAND="echo fake-token")
    env.pop("ALADDIN_GA_ACCESS_TOKEN")
    assert run("setup", env=env).returncode == 0                                  # the token command path
    r = run("setup", env=dict(ENV, ALADDIN_GA_ACCESS_TOKEN="expired"))
    assert r.returncode == 3 and "gcloud auth application-default login" in r.stderr, r.stderr   # 401 → sign in
    fake("/_control", {"redirect": "/stolen"})
    r = run("setup")
    assert r.returncode == 2 and "302" in r.stderr, r.stderr
    assert not [c for c in calls() if c["path"] == "/stolen"]                     # the token never follows
    fake("/_control", {"redirect": ""})
    path, doc = record()
    lost = dict(doc, property="properties/999")
    store = lambda d: (M.seal(d), M.save(path, d))  # noqa: E731
    store(lost)
    r = run("setup")
    assert r.returncode == 3 and "not reachable" in r.stderr and "--account" in r.stderr, r.stderr
    store(doc)
    ok("a rerun creates nothing; a token command works; 401 asks; redirects are refused; a lost property asks")

    # 7) the static emit check: every active journey step's event in non-test code; the app carries the stream
    r = run("check")
    assert r.returncode == 2 and "signup_started" in r.stderr and "not emitted" in r.stderr, r.stderr
    write("src/app.js", "gtag('event', 'signup_started');\ngtag('event', \"signup_done\");\ntrack(`todo_add_opened`);\n")
    for rel in ("src/app.test.js", "docs/site/demo.js", "tests/helpers.js", "test_flow.py", ".storybook/preview.js",
                "cypress/e2e/signup.cy.ts", "todo/tests.py", "src/conftest.py", "e2e/flow.ts", "src/TodoTest.java"):
        write(rel, "expect(sent).toContain('todo_added')\n")                  # tests, docs, dot-dirs never count
    write("src/later.js", "// TODO: send todo_added\n")                       # nor does a bare word
    write("README.md", "todo_added is emitted when … 'todo_added'\n")          # nor docs at the root
    r = run("check", "--json")
    assert r.returncode == 2, r.stderr
    rep = json.loads(r.stdout)
    assert [m["event"] for m in rep["missing"]] == ["todo_added"] and rep["missing"][0]["journey"] == "add a todo", rep
    assert "old_step" not in r.stderr                                      # retired journeys are not checked
    write("src/todo.js", "export const add = () => gtag('event', 'todo_added');\n")
    r = run("check", "--json")
    assert r.returncode == 2 and "measurement id" in r.stderr, r.stderr    # instrumented, but to no stream
    assert json.loads(r.stdout)["emitted"]["todo_added"] == "src/todo.js:1", r.stdout
    mid = doc["stream"]["measurement_id"]
    write("src/ga.js", "gtag('config', '%s');\n" % mid)
    r = run("check")
    assert r.returncode == 2 and "measurement id" not in r.stderr and "no debug hit" in r.stderr, r.stderr
    jid = journey("bad names", ["session_start", "2fast", "x" * 41, "google_x"])
    r = run("check")
    assert r.returncode == 2 and "'session_start' is reserved" in r.stderr and "2fast" in r.stderr, r.stderr
    assert "'google_x' is reserved" in r.stderr
    os.remove(M.path_of(T, "journey", jid))
    ok("emit check: quoted literals in non-test code (tests, docs, dot-dirs, comments ignored); the stream's id")

    # 8) the debug hit: the secret is proven live on the Admin API, then GA's validation server checks the events
    n = len(calls())
    r = run("debug-hit")
    assert r.returncode == 0, r.stderr
    log = calls()[n:]
    hits = mp_calls(log)
    assert len(hits) == 1 and hits[0]["auth"] == "" and "measurement_id=%s" % mid in hits[0]["query"], hits
    assert "api_secret=" + secret["api_secret"] in hits[0]["query"]
    assert [c["path"] for c in log if c["path"].endswith("measurementProtocolSecrets")] and \
        all(c["auth"] == "Bearer fake-token" for c in log if c["path"].startswith("/admin"))
    body = hits[0]["body"]
    assert sorted(e["name"] for e in body["events"]) == TM.journey_events(T), body
    assert body["client_id"] == TM.DEBUG_CLIENT_ID and body["validation_behavior"] == "ENFORCE_RECOMMENDATIONS"
    assert run("check").returncode == 0
    journey("share a list", ["list_opened", "list_shared"])
    r = run("check")
    assert r.returncode == 2 and "stale" in r.stderr and "key events" in r.stderr, r.stderr
    subprocess.run(GIT + ["commit", "-qm", "app"], cwd=T, check=True)
    subprocess.run(["git", "worktree", "add", "-q", WT, "HEAD"], cwd=T, check=True)
    write("src/share.js", "gtag('event', 'list_opened'); gtag('event', 'list_shared')\n", root=WT)
    rw = json.loads(run("check", "--json", cwd=WT).stdout)                 # a worker's worktree: its own code
    assert rw["emitted"]["list_shared"] == "src/share.js:1" and not rw["missing"], rw
    rm = json.loads(run("check", "--json").stdout)
    assert [m["event"] for m in rm["missing"]] == ["list_opened", "list_shared"], rm
    write("src/share.js", "gtag('event', 'list_opened'); gtag('event', 'list_shared')\n")
    n = len(calls())
    assert run("setup").returncode == 0 and record()[1]["key_events"] == ["list_shared", "signup_done", "todo_added"]
    assert [c["body"]["eventName"] for c in calls()[n:] if c["method"] == "POST"] == ["list_shared"]
    assert run("debug-hit").returncode == 0 and run("check").returncode == 0
    json.dump(dict(secret, api_secret="wrong"), open(sfile, "w"))
    n = len(calls())
    r = run("debug-hit")
    assert r.returncode == 2 and "not live" in r.stderr and not mp_calls(calls()[n:]), r.stderr
    assert run("check").returncode == 2                                     # a failed hit is not a pass
    json.dump(secret, open(sfile, "w"))
    fake("/_control", {"mp_messages": [{"fieldPath": "events.params", "validationCode": "VALUE_INVALID",
                                        "description": "Param [p] has invalid type."}]})
    r = run("debug-hit")
    assert r.returncode == 2 and "VALUE_INVALID" in r.stderr, r.stderr      # GA's own messages surface
    fake("/_control", {"mp_messages": []})
    bulk = journey("bulk", ["bulk_%02d" % i for i in range(30)])
    write("src/bulk.js", "\n".join("track('bulk_%02d')" % i for i in range(30)) + "\n")
    n = len(calls())
    assert run("debug-hit").returncode == 0
    sizes = [len(c["body"]["events"]) for c in mp_calls(calls()[n:])]
    assert sizes == [25, 11], sizes                                          # 36 events in batches of ≤25
    os.remove(M.path_of(T, "journey", bulk))
    unwrite("src/bulk.js")
    assert run("debug-hit").returncode == 0 and run("check").returncode == 0
    ok("debug hit: secret live on the stream, events validated in ≤25 batches; stale, failed or not-live blocks")

    # 9) fixed reports scoped to the stream, on settled data; the minimum-sample rule decides usage's share
    fake("/_control", {"users": 12, "new_users": 9, "events": {"signup_done": [14, 9], "todo_added": [40, 7], "page_view": [99, 12]}})
    sid = record()[1]["stream"]["name"].rsplit("/", 1)[-1]
    n = len(calls())
    r = run("report", "--json")
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    sent = [c["body"] for c in calls()[n:]]
    want = [TM.request("users", [], sid), TM.request("journeys", TM.journey_events(T), sid)]
    assert sorted(json.dumps(b, sort_keys=True) for b in sent) == sorted(json.dumps(b, sort_keys=True) for b in want), sent
    assert all(b["dateRanges"] == [{"startDate": "30daysAgo", "endDate": "3daysAgo"}] for b in sent), sent
    assert want[0]["dimensionFilter"]["filter"] == {"fieldName": "streamId", "stringFilter": {"matchType": "EXACT", "value": sid}}
    assert rep["kind"] == "settled" and rep["sample"] == {"users": 12, "min_users": 50, "days": 28, "thresholded": False, "met": False}
    assert rep["share"]["usage"] == 0 and rep["share"]["research"] == 1, rep["share"]
    assert "page_view" not in json.dumps(rep["results"]["journeys"]), rep                     # only journey events
    assert rep["definitions_sha256"] == M.digest(TM.REPORTS, drop=()) and rep["stream"] == record()[1]["stream"]["name"]
    assert os.path.dirname(rep["path"]) == os.path.join("docs", "agents", "mission", "telemetry", "reports")
    assert run("check").returncode == 0                                                       # below the sample: passes
    fake("/_control", {"users": 80, "new_users": 50})
    rep = json.loads(run("report", "--json").stdout)
    assert rep["sample"]["met"] and rep["share"] == {"usage": 0.7, "research": 0.3, "reason": rep["share"]["reason"]}, rep
    fake("/_control", {"thresholded": True})
    rep = json.loads(run("report", "--json").stdout)
    assert rep["sample"]["thresholded"] and not rep["sample"]["met"] and rep["share"]["usage"] == 0, rep
    fake("/_control", {"thresholded": False, "realtime": {"todo_added": [3, 2], "page_view": [9, 4]}})
    n = len(calls())
    r = run("report", "--realtime", "--json")
    rt = json.loads(r.stdout)
    assert r.returncode == 0 and rt["kind"] == "realtime" and rt["share"] is None, rt          # never drives a goal
    assert rt["results"]["realtime"]["rows"] == [{"eventName": "todo_added", "eventCount": 3, "activeUsers": 2}], rt
    assert [c["path"] for c in calls()[n:]][-1].endswith(":runRealtimeReport")
    sh = json.loads(run("share", "--json").stdout)
    assert sh["usage"] == 0 and sh["report"] != rt["report_id"], sh                         # latest SETTLED report
    assert len(events("telemetry.report")) == 4 and run("check").returncode == 0
    ok("reports: fixed definitions scoped to the stream, settled data (≥48h); usage leads only past 50 users/28d")

    # 10) the minimum-sample rule is code with fixed boundaries; report definitions stay aggregate-only
    assert TM.share(49, False, True)["usage"] == 0 and TM.share(50, False, True)["usage"] == 0.7
    assert TM.share(500, True, True)["usage"] == 0 and TM.share(500, False, False)["usage"] == 0
    assert TM.share(10 ** 6, False, True)["research"] >= 0.3
    for name, d in TM.REPORTS.items():
        dims = [x["name"] for x in d["body"].get("dimensions", [])]
        assert set(dims) <= TM.AGGREGATE_DIMENSIONS, (name, dims)
        assert "streamId" in json.dumps(TM.request(name, ["x"], "1")), name
    assert not TM.AGGREGATE_DIMENSIONS & {"customUser:id", "userId", "clientId", "city"}
    ok("share(): 49 users → 0, 50 → 0.7, thresholded or unsettled → 0, research ≥ 30%; aggregate, stream-scoped")

    # 11) share skips tampered reports; revoke stops the agents (share included) and deletes the local secret
    reports = sorted(os.listdir(M.mdir(T, "telemetry", "reports")))
    assert len(reports) == 4
    latest = json.loads(run("share", "--json").stdout)["report"]
    p = M.mdir(T, "telemetry", "reports", latest + ".json")
    raw = json.load(open(p))
    raw["sample"]["thresholded"] = False
    json.dump(raw, open(p, "w"))
    sh = json.loads(run("share", "--json").stdout)
    assert sh["report"] != latest and sh["usage"] == 0.7 and sh["users"] == 80, sh             # the one before it
    assert run("revoke").returncode == 64
    r = run("revoke", "--by", "Adeel", "--reason", "pausing")
    assert r.returncode == 0 and "keeps sending" in r.stderr, r.stderr
    assert record()[1]["status"] == "revoked" and len(events("telemetry.revoked")) == 1 and not os.path.exists(sfile)
    sh = json.loads(run("share", "--json").stdout)
    assert sh["usage"] == 0 and sh["report"] is None, sh
    n = len(calls())
    for cmd in (["setup"], ["debug-hit"], ["report"]):
        r = run(*cmd)
        assert r.returncode == 3 and "revoked" in r.stderr, (cmd, r.stderr)
    assert calls()[n:] == []
    r = run("check")
    assert r.returncode == 0 and "revoked" in r.stderr and "src/ga.js" in r.stderr, r.stderr  # names the live tag
    assert run("consent", "--by", "Adeel").returncode == 0 and record()[1]["status"] == "configured"
    assert run("debug-hit").returncode == 2                                     # the secret is gone: setup again
    n = len(calls())
    assert run("setup").returncode == 0 and not [c for c in calls()[n:] if c["method"] == "POST"]
    assert run("debug-hit").returncode == 0 and run("check").returncode == 0
    r = run("init", "--uri", "https://next.example", "--force")
    path, doc = record()
    assert r.returncode == 0 and doc["stream"] is None and doc["status"] == "off" and doc["property"], doc
    assert run("check").returncode == 3 and run("setup").returncode == 3       # new terms, new consent
    assert run("consent", "--by", "Adeel").returncode == 0 and record()[1]["status"] == "consented"
    assert run("setup").returncode == 0 and record()[1]["stream"]["default_uri"] == "https://next.example"
    r = run("check")
    assert r.returncode == 2 and "measurement id" in r.stderr, r.stderr        # the new stream is not in the app
    ok("revoke: agents stop (share too), secret deleted, live tag named; a new uri drops the old stream")

    # 12) discovery and help: the area is found by name, its docstring is its help
    r = subprocess.run([MI, "--list"], cwd=T, env=ENV, capture_output=True, text=True)
    assert "telemetry" in r.stdout.split(), r.stdout
    r = run("--help")
    assert r.returncode == 0 and "never a gate" in r.stdout and "mission telemetry setup" in r.stdout, r.stdout
    n = len(calls())
    assert run("nope").returncode == 64 and run("debug-hit", "--help").returncode == 0 and calls()[n:] == []
    ok("`mission telemetry` is discovered; --help is the module docstring; unknown subcommands are usage errors")

    # 13) day one, no journeys yet: the debug hit sends a probe event so the wiring is still proven
    subprocess.run(["git", "init", "-q"], cwd=T2, check=True)
    env = dict(ENV, HOME=os.path.join(T2, "home"))
    assert run("init", "--uri", "https://probe.example", env=env, cwd=T2).returncode == 0
    assert run("consent", "--by", "Adeel", env=env, cwd=T2).returncode == 0
    assert run("setup", "--account", "accounts/100", env=env, cwd=T2).returncode == 0
    n = len(calls())
    assert run("debug-hit", env=env, cwd=T2).returncode == 0
    assert [e["name"] for e in mp_calls(calls()[n:])[0]["body"]["events"]] == [TM.PROBE_EVENT]
    assert run("check", env=env, cwd=T2).returncode == 2
    write("index.html", "<script>gtag('config', '%s')</script>\n" % record(T2)[1]["stream"]["measurement_id"], root=T2)
    r = run("check", env=env, cwd=T2)
    assert r.returncode == 0, r.stderr
    ok("no journeys yet: the probe event proves the wiring; check passes once the page carries the stream")
finally:
    FAKE.terminate()
    FAKE.wait(timeout=10)
    for d in (T, WT, T2):
        shutil.rmtree(d, ignore_errors=True)

print("mission-telemetry: %d checks passed" % PASS[0])
