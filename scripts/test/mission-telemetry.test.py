#!/usr/bin/env python3
"""ROADMAP §4.12 (M9.6): usage telemetry through Google Analytics 4. It is opt-in: nothing
reaches the network until a human consents to exact terms, and a revoke stops it. Setup goes
through the Admin API (property, web stream, user-data acknowledgement, Measurement Protocol
secret, a key event per journey), a debug hit proves the wiring, fixed Data API reports read
settled data, and the minimum-sample rule decides how much of a goal usage may drive. Usage
informs goals and is never a gate; the check is structural (consent, every journey step's
event is emitted, a fresh debug hit). All network is a local fake (fixtures/fake-ga.py).
    python3 scripts/test/mission-telemetry.test.py
"""
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import urllib.request

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "plugin"))
BIN = os.path.join(ROOT, "bin")
MI = os.path.join(BIN, "mission")
T = tempfile.mkdtemp(prefix="aloop-telemetry-")
PASS = [0]
sys.path.insert(0, BIN)
import _mission as M  # noqa: E402

spec = importlib.util.spec_from_file_location("telemetry", os.path.join(ROOT, "mission", "telemetry.py"))
TM = importlib.util.module_from_spec(spec)
spec.loader.exec_module(TM)

FAKE = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(__file__), "fixtures", "fake-ga.py")],
                        stdout=subprocess.PIPE, text=True)
BASE = "http://127.0.0.1:%s" % FAKE.stdout.readline().strip()
ENV = dict(os.environ, HOME=os.path.join(T, "home"), ALADDIN_GA_BASE=BASE, ALADDIN_GA_ACCESS_TOKEN="fake-token")
for k in ("AGENTIC_HOST", "AGENTIC_GA_ACCESS_TOKEN", "AGENTIC_GA_TOKEN_COMMAND", "AGENTIC_GA_BASE"):
    ENV.pop(k, None)


def ok(name):
    PASS[0] += 1
    print("ok -", name)


def run(*args, env=None):
    return subprocess.run([MI, "telemetry"] + list(args), cwd=T, env=env or ENV, capture_output=True, text=True)


def fake(path, body=None):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10) as r:
        return json.load(r)


def calls():
    return fake("/_log")


def events(kind=None):
    p = os.path.join(T, ".aladdin", "logs", "events.jsonl")
    evs = [json.loads(l) for l in open(p)] if os.path.exists(p) else []
    return [e for e in evs if kind is None or e["event"] == kind]


def record():
    recs = M.artifacts(T, "telemetry")
    assert len(recs) == 1, recs
    return recs[0]


def journey(name, steps, status="active"):
    jid = M.new_id("journey")
    M.save(M.path_of(T, "journey", jid), {"journey_id": jid, "status": status, "created_at": M.now(), "name": name,
                                          "steps": [{"name": s, "event": s} for s in steps]})
    return jid


def write(rel, text):
    p = os.path.join(T, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)
    subprocess.run(["git", "add", rel], cwd=T, check=True)


try:
    subprocess.run(["git", "init", "-q"], cwd=T, check=True)

    # 1) off by default: the check passes with nothing to check, and no command reaches the network
    r = run("check")
    assert r.returncode == 0 and "off (opt-in)" in r.stderr, (r.returncode, r.stderr)
    for cmd in (["setup"], ["debug-hit"], ["report"], ["consent", "--by", "Adeel"]):
        r = run(*cmd)
        assert r.returncode == 3 and "ASK[telemetry]" in r.stderr and "telemetry init" in r.stderr, (cmd, r.stderr)
    assert calls() == [], calls()
    ok("telemetry is off until a human opts in: check passes, every network command asks, zero requests")

    # 2) init drafts exact terms (sealed, harness id, GA's verbatim acknowledgement); consent covers them
    assert run("init").returncode == 64                      # what is measured is part of the terms
    assert run("init", "--uri", "ftp://todo").returncode == 64
    r = run("init", "--uri", "https://todo.example", "--time-zone", "Europe/Stockholm")
    assert r.returncode == 0, r.stderr
    path, doc = record()
    assert M.problems(doc, "telemetry", path) == [], M.problems(doc, "telemetry", path)
    assert doc["status"] == "off" and doc["telemetry_id"].startswith("tlm_") and doc["sha256"] == M.digest(doc)
    assert doc["terms"]["acknowledgement"] == TM.ACKNOWLEDGEMENT and "never a gate" in doc["terms"]["purpose"]
    assert doc["terms"]["uri"] == "https://todo.example" and set(TM.SCOPES) == set(doc["terms"]["scopes"])
    assert run("init", "--uri", "https://other.example").returncode == 2      # one record; terms change by re-init --force
    r = run("setup")
    assert r.returncode == 3 and "consent" in r.stderr, r.stderr
    assert run("consent").returncode == 64                    # who consents is required
    r = run("consent", "--by", "Adeel", "--reason", "my own app, day one")
    assert r.returncode == 0, r.stderr
    path, doc = record()
    ev = events("telemetry.consent")
    assert len(ev) == 1 and ev[0]["role"] == "human" and ev[0]["data"]["by"] == "Adeel", ev
    assert ev[0]["data"]["sha256"] == TM.terms_sha(doc) and ev[0]["data"]["subject"] == M.rel(T, path)
    assert doc["status"] == "consented" and run("check").returncode == 0 and calls() == []
    ok("init seals the terms (uri, scopes, GA's acknowledgement); consent is a human event on exactly those terms")

    # 3) consent covers exact terms: a changed term asks again and setup refuses; reverting restores it
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
    r = run("check")
    assert r.returncode == 2 and "sha256 does not match" in r.stderr, r.stderr
    M.seal(doc)
    M.save(path, doc)
    ok("consent covers exact terms (changed terms ask again, hand edits block); no request was made")

    # 4) no Google credentials: setup asks the human once, with the pastable sign-in, and sends nothing
    env = dict(ENV, ALADDIN_GA_TOKEN_COMMAND="false")
    env.pop("ALADDIN_GA_ACCESS_TOKEN")
    r = run("setup", env=env)
    assert r.returncode == 3 and "gcloud auth application-default login" in r.stderr, r.stderr
    assert "analytics.edit" in r.stderr and calls() == []
    r = run("auth")
    assert r.returncode == 0 and "--client-id-file" in r.stdout and "analyticsadmin.googleapis.com" in r.stdout
    r = run("setup", env=dict(ENV, ALADDIN_GA_BASE="https://evil.example"))
    assert r.returncode == 64 and "loopback" in r.stderr and calls() == [], r.stderr
    ok("no token: setup asks with the one-time sign-in; a non-loopback endpoint override is refused")

    # 5) setup through the Admin API: account choice is the human's when it is ambiguous or missing
    fake("/_control", {"accounts": 0})
    r = run("setup")
    assert r.returncode == 3 and "analytics.google.com" in r.stderr, r.stderr
    fake("/_control", {"accounts": 2})
    r = run("setup")
    assert r.returncode == 3 and "--account accounts/100" in r.stderr and "accounts/101" in r.stderr, r.stderr
    assert all(c["method"] == "GET" for c in calls())           # asking never creates anything
    fake("/_control", {"accounts": 1})
    signup = journey("sign up", ["signup_started", "signup_done"])
    journey("add a todo", ["todo_add_opened", "todo_added"])
    journey("old idea", ["old_step"], status="retired")
    r = run("setup")
    assert r.returncode == 0, r.stderr
    path, doc = record()
    assert doc["status"] == "configured" and doc["property"].startswith("properties/") and M.problems(doc, "telemetry", path) == []
    assert doc["stream"]["measurement_id"].startswith("G-") and doc["stream"]["default_uri"] == "https://todo.example"
    assert sorted(doc["key_events"]) == ["signup_done", "todo_added"], doc["key_events"]
    log = calls()
    posts = [c["path"].split("/v1beta/")[1] for c in log if c["method"] == "POST"]
    assert posts[0] == "properties" and posts[1].endswith(":acknowledgeUserDataCollection"), posts
    assert [p.rsplit("/", 1)[-1] for p in posts[2:]] == ["dataStreams", "measurementProtocolSecrets", "keyEvents", "keyEvents"], posts
    prop_body = [c["body"] for c in log if c["path"].endswith("/v1beta/properties") and c["method"] == "POST"][0]
    assert prop_body["parent"] == "accounts/100" and prop_body["timeZone"] == "Europe/Stockholm", prop_body
    ack = [c["body"] for c in log if c["path"].endswith(":acknowledgeUserDataCollection")][0]
    assert ack == {"acknowledgement": TM.ACKNOWLEDGEMENT}
    assert all(c["auth"] == "Bearer fake-token" for c in log)
    secret = [c for c in log if c["path"].endswith("measurementProtocolSecrets") and c["method"] == "POST"]
    assert len(secret) == 1
    sfile = TM.secret_file(T, doc["telemetry_id"])
    assert os.path.isfile(sfile) and stat.S_IMODE(os.stat(sfile).st_mode) == 0o600, sfile
    assert M.rel(T, sfile).startswith(".aladdin" + os.sep), sfile
    value = json.load(open(sfile))["api_secret"]
    for d, _, fs in os.walk(os.path.join(T, "docs")):
        for f in fs:
            assert value not in open(os.path.join(d, f)).read(), "the MP secret leaked into %s" % f
    assert [e["data"].get("action") for e in events("telemetry.setup")] == ["setup"]
    ok("setup: property, acknowledgement, web stream, MP secret, a key event per active journey; secret 0600 outside docs/")

    # 6) setup is idempotent: a rerun finds everything and creates nothing
    n = len(calls())
    r = run("setup")
    assert r.returncode == 0, r.stderr
    again = calls()[n:]
    assert again and not [c for c in again if c["method"] == "POST" and not c["path"].endswith(":acknowledgeUserDataCollection")], again
    assert record()[1]["property"] == doc["property"]
    ok("a second setup reuses the property, stream, secret and key events (no duplicate creates)")

    # 7) the static emit check: every active journey step's event must be emitted by source code
    r = run("check")
    assert r.returncode == 2 and "signup_started" in r.stderr and "not emitted" in r.stderr, r.stderr
    write("src/app.js", "gtag('event', 'signup_started');\ngtag('event', \"signup_done\");\ntrack(`todo_add_opened`);\n")
    write("src/app.test.js", "expect(sent).toContain('todo_added')\n")     # a test mentioning it does not count
    write("docs/notes.md", "todo_added is emitted when … \n")              # nor do docs
    r = run("check", "--json")
    assert r.returncode == 2, r.stderr
    rep = json.loads(r.stdout)
    assert [m["event"] for m in rep["missing"]] == ["todo_added"] and rep["missing"][0]["journey"] == "add a todo", rep
    assert "old_step" not in r.stderr                                      # retired journeys are not checked
    write("src/todo.js", "export const add = () => gtag('event', 'todo_added');\n")
    r = run("check", "--json")
    assert r.returncode == 2 and "debug hit" in r.stderr, r.stderr          # instrumented, not yet proven
    assert json.loads(r.stdout)["emitted"]["todo_added"] == "src/todo.js:1", r.stdout
    jid = journey("bad names", ["session_start", "2fast", "x" * 41])
    r = run("check")
    assert r.returncode == 2 and "session_start" in r.stderr and "reserved" in r.stderr and "2fast" in r.stderr, r.stderr
    os.remove(M.path_of(T, "journey", jid))
    ok("emit check: each active journey step's event appears in non-test source; GA name rules apply")

    # 8) the debug hit: journey events go to GA's validation server (no OAuth token sent), and stay fresh
    n = len(calls())
    r = run("debug-hit")
    assert r.returncode == 0, r.stderr
    hits = calls()[n:]
    assert len(hits) == 1 and hits[0]["path"] == "/mp/debug/mp/collect" and hits[0]["auth"] == "", hits
    assert "measurement_id=G-" in hits[0]["query"] and "api_secret=" in hits[0]["query"]
    assert sorted(e["name"] for e in hits[0]["body"]["events"]) == sorted(TM.journey_events(T)), hits[0]["body"]
    assert hits[0]["body"]["client_id"] == TM.DEBUG_CLIENT_ID
    r = run("check")
    assert r.returncode == 0, r.stderr
    journey("share a list", ["list_shared"])
    write("src/share.js", "gtag('event', 'list_shared')\n")
    r = run("check")
    assert r.returncode == 2 and "stale" in r.stderr, r.stderr               # a new event needs a new hit
    assert run("debug-hit").returncode == 0 and run("check").returncode == 0
    sf = json.load(open(sfile))
    json.dump(dict(sf, api_secret="wrong"), open(sfile, "w"))
    r = run("debug-hit")
    assert r.returncode == 2 and "api_secret" in r.stderr, r.stderr          # GA's validation messages surface
    assert run("check").returncode == 2                                     # a failed hit is not a pass
    json.dump(sf, open(sfile, "w"))
    assert run("debug-hit").returncode == 0 and run("check").returncode == 0
    ok("debug hit: every journey event validated by GA's debug endpoint; a new event or a failed hit blocks")

    # 9) fixed reports on settled data; the minimum-sample rule decides usage's share; never a gate
    fake("/_control", {"users": 12, "new_users": 9, "events": {"signup_done": [14, 9], "todo_added": [40, 7], "page_view": [99, 12]}})
    n = len(calls())
    r = run("report", "--json")
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    sent = [c["body"] for c in calls()[n:]]
    assert all(b["dateRanges"] == [{"startDate": "30daysAgo", "endDate": "3daysAgo"}] for b in sent), sent
    assert {json.dumps(TM.request("users", []), sort_keys=True), json.dumps(TM.request("journeys", TM.journey_events(T)), sort_keys=True)} \
        == {json.dumps(b, sort_keys=True) for b in sent}
    assert rep["kind"] == "settled" and rep["sample"] == {"users": 12, "min_users": 50, "days": 28, "thresholded": False, "met": False}
    assert rep["share"]["usage"] == 0 and rep["share"]["research"] == 1, rep["share"]
    assert "page_view" not in json.dumps(rep["results"]["journeys"]), rep                     # only journey events are read
    assert rep["definitions_sha256"] == M.digest(TM.REPORTS, drop=())
    assert os.path.dirname(rep["path"]) == os.path.join("docs", "agents", "mission", "telemetry", "reports")
    assert run("check").returncode == 0                                                       # below the sample: still passes
    fake("/_control", {"users": 80, "new_users": 50})
    rep = json.loads(run("report", "--json").stdout)
    assert rep["sample"]["met"] and rep["share"] == {"usage": 0.7, "research": 0.3, "reason": rep["share"]["reason"]}, rep
    fake("/_control", {"thresholded": True})
    rep = json.loads(run("report", "--json").stdout)
    assert rep["sample"]["thresholded"] and not rep["sample"]["met"] and rep["share"]["usage"] == 0, rep
    fake("/_control", {"thresholded": False, "realtime": {"todo_added": [3, 2]}})
    r = run("report", "--realtime", "--json")
    rt = json.loads(r.stdout)
    assert r.returncode == 0 and rt["kind"] == "realtime" and rt["share"] is None, rt          # realtime never drives a goal
    assert [c["path"] for c in calls()][-1].endswith(":runRealtimeReport")
    r = run("share", "--json")
    sh = json.loads(r.stdout)
    assert r.returncode == 0 and sh["usage"] == 0 and sh["report"] != rt["report_id"], sh     # latest SETTLED report
    assert len(events("telemetry.report")) == 4 and run("check").returncode == 0
    ok("reports: fixed definitions on settled data (≥48h), usage leads only past 50 users/28d, thresholding = below")

    # 10) the minimum-sample rule is code with fixed boundaries; report definitions stay aggregate-only
    assert TM.share(49, False, True)["usage"] == 0 and TM.share(50, False, True)["usage"] == 0.7
    assert TM.share(500, True, True)["usage"] == 0 and TM.share(500, False, False)["usage"] == 0
    assert TM.share(10 ** 6, False, True)["research"] >= 0.3
    for name, d in TM.REPORTS.items():
        dims = [x["name"] for x in d["body"].get("dimensions", [])]
        assert set(dims) <= TM.AGGREGATE_DIMENSIONS, (name, dims)
    assert not TM.AGGREGATE_DIMENSIONS & {"customUser:id", "userId", "clientId", "city"}
    ok("share(): 49 users → 0, 50 → 0.7, thresholded or unsettled → 0, research ≥ 30%; reports read aggregates only")

    # 11) a tampered report is ignored by share(); revoke stops all network and turns the check off
    reports = sorted(os.listdir(M.mdir(T, "telemetry", "reports")))
    assert len(reports) == 4
    sh_before = json.loads(run("share", "--json").stdout)["report"]
    p = M.mdir(T, "telemetry", "reports", sh_before + ".json")
    raw = json.load(open(p))
    raw["sample"]["users"] = 10 ** 6
    json.dump(raw, open(p, "w"))
    assert json.loads(run("share", "--json").stdout)["report"] != sh_before
    assert run("revoke").returncode == 64
    r = run("revoke", "--by", "Adeel", "--reason", "pausing")
    assert r.returncode == 0, r.stderr
    assert record()[1]["status"] == "revoked" and len(events("telemetry.revoked")) == 1
    n = len(calls())
    for cmd in (["setup"], ["debug-hit"], ["report"]):
        r = run(*cmd)
        assert r.returncode == 3 and "revoked" in r.stderr, (cmd, r.stderr)
    assert calls()[n:] == []
    r = run("check")
    assert r.returncode == 0 and "revoked" in r.stderr, r.stderr
    assert run("consent", "--by", "Adeel").returncode == 0 and record()[1]["status"] == "configured"
    assert run("check").returncode == 0
    ok("revoke: setup, debug-hit and report refuse with no request; a fresh consent resumes; tampered reports skipped")

    # 12) discovery and help: the area is found by name, its docstring is its help
    r = subprocess.run([MI, "--list"], cwd=T, env=ENV, capture_output=True, text=True)
    assert "telemetry" in r.stdout.split(), r.stdout
    r = run("--help")
    assert r.returncode == 0 and "never a gate" in r.stdout and "mission telemetry setup" in r.stdout, r.stdout
    assert run("nope").returncode == 64
    ok("`mission telemetry` is discovered; --help is the module docstring; unknown subcommands are usage errors")
finally:
    FAKE.terminate()
    FAKE.wait(timeout=10)

print("mission-telemetry: %d checks passed" % PASS[0])
