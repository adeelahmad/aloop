#!/usr/bin/env python3
"""A tiny, deterministic fake of the Google Analytics APIs the mission layer's telemetry area
calls (ROADMAP §4.12, M9.6), for tests: no network, no Google account, no tokens.

  /admin/v1beta/…   Admin API: accountSummaries, properties, :acknowledgeUserDataCollection,
                    dataStreams, measurementProtocolSecrets, keyEvents
  /data/v1beta/…    Data API: :runReport, :runRealtimeReport
  /mp/debug/mp/collect   the Measurement Protocol validation server (like Google's, it checks the
                         payload and never the measurement_id or api_secret)

Point the client at it with ALADDIN_GA_BASE=http://127.0.0.1:<port>. The port is the first
line on stdout. Admin and Data calls need `Authorization: Bearer $FAKE_GA_TOKEN` (default
`fake-token`); the Measurement Protocol takes none. Test controls:
  POST /_control {json}   merge into the scenario: accounts (0, 1, 2…), users, new_users,
                          thresholded, events {name: [count, users]}, realtime {name: [count, users]}
                          (all on the property's first stream), mp_messages [extra validation
                          messages], redirect (a path every admin/data call is 302-redirected to)
  GET  /_log              every request so far: [{method, path, query, auth, body}]
"""
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

TOKEN = os.environ.get("FAKE_GA_TOKEN", "fake-token")
ACK = ("I acknowledge that I have the necessary privacy disclosures and rights from my end users for the "
       "collection and processing of their data, including the association of such data with the visitation "
       "information Google Analytics collects from my site and/or app property.")
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
RESERVED = {"ad_activeview", "ad_click", "ad_exposure", "ad_impression", "ad_query", "ad_reward", "adunit_exposure",
            "app_clear_data", "app_exception", "app_install", "app_remove", "app_store_refund", "app_update",
            "app_upgrade", "dynamic_link_app_open", "dynamic_link_app_update", "dynamic_link_first_open", "error",
            "first_open", "first_visit", "in_app_purchase", "notification_dismiss", "notification_foreground",
            "notification_open", "notification_receive", "os_update", "session_start", "session_start_with_rollout",
            "user_engagement"}

LOCK = threading.Lock()
SCENARIO = {"accounts": 1, "users": 0, "new_users": 0, "thresholded": False, "events": {}, "realtime": {},
            "mp_messages": [], "redirect": ""}
DB = {"n": 1000, "properties": {}, "acked": set(), "streams": {}, "secrets": {}, "key_events": {}}
LOG = []


def nid():
    DB["n"] += 1
    return str(DB["n"])


def accounts():
    out = []
    for i in range(int(SCENARIO["accounts"])):
        acct = "accounts/%d" % (100 + i)
        props = [{"property": p, "displayName": d["displayName"]} for p, d in sorted(DB["properties"].items())
                 if d["parent"] == acct]
        out.append({"name": "accountSummaries/%d" % (100 + i), "account": acct, "displayName": "Account %d" % i,
                    "propertySummaries": props})
    return out


def err(code, status, message):
    return code, {"error": {"code": code, "message": message, "status": status}}


def admin(method, parts, body):
    if parts == ["accountSummaries"] and method == "GET":
        return 200, {"accountSummaries": accounts()}
    if parts == ["properties"] and method == "POST":
        if not any(a["account"] == body.get("parent") for a in accounts()):
            return err(403, "PERMISSION_DENIED", "The caller does not have permission")
        for k in ("displayName", "timeZone"):
            if not body.get(k):
                return err(400, "INVALID_ARGUMENT", "%s is required" % k)
        name = "properties/" + nid()
        DB["properties"][name] = dict(body, name=name, propertyType="PROPERTY_TYPE_ORDINARY")
        return 200, DB["properties"][name]
    if len(parts) < 2 or parts[0] != "properties":
        return err(404, "NOT_FOUND", "unknown admin path")
    prop = "properties/" + parts[1].split(":")[0]
    if prop not in DB["properties"]:
        return err(403, "PERMISSION_DENIED", "The caller does not have permission")
    if len(parts) == 2 and parts[1].endswith(":acknowledgeUserDataCollection") and method == "POST":
        if body.get("acknowledgement") != ACK:
            return err(400, "INVALID_ARGUMENT", "acknowledgement does not match the required text")
        DB["acked"].add(prop)
        return 200, {}
    if len(parts) == 2 and method == "GET":
        return 200, DB["properties"][prop]
    rest = parts[2:]
    if rest == ["dataStreams"]:
        if method == "GET":
            return 200, {"dataStreams": [s for s in DB["streams"].values() if s["name"].startswith(prop + "/")]}
        if body.get("type") != "WEB_DATA_STREAM" or not (body.get("webStreamData") or {}).get("defaultUri"):
            return err(400, "INVALID_ARGUMENT", "a web data stream needs webStreamData.defaultUri")
        sid = nid()
        s = {"name": "%s/dataStreams/%s" % (prop, sid), "type": "WEB_DATA_STREAM", "displayName": body.get("displayName"),
             "webStreamData": {"defaultUri": body["webStreamData"]["defaultUri"], "measurementId": "G-FAKE" + sid}}
        DB["streams"][s["name"]] = s
        return 200, s
    if len(rest) == 3 and rest[0] == "dataStreams" and rest[2] == "measurementProtocolSecrets":
        stream = "%s/dataStreams/%s" % (prop, rest[1])
        if stream not in DB["streams"]:
            return err(404, "NOT_FOUND", "no such data stream")
        if method == "GET":
            return 200, {"measurementProtocolSecrets": [s for s in DB["secrets"].values() if s["name"].startswith(stream + "/")]}
        if prop not in DB["acked"]:
            return err(403, "PERMISSION_DENIED", "User data collection acknowledgement is required")
        sec = {"name": "%s/measurementProtocolSecrets/%s" % (stream, nid()), "displayName": body.get("displayName"),
               "secretValue": "fake-secret-" + str(DB["n"])}
        DB["secrets"][sec["name"]] = sec
        return 200, sec
    if rest == ["keyEvents"]:
        if method == "GET":
            return 200, {"keyEvents": [k for k in DB["key_events"].values() if k["name"].startswith(prop + "/")]}
        if body.get("countingMethod") not in ("ONCE_PER_EVENT", "ONCE_PER_SESSION"):
            return err(400, "INVALID_ARGUMENT", "countingMethod is required")
        if any(k["eventName"] == body.get("eventName") for k in DB["key_events"].values() if k["name"].startswith(prop + "/")):
            return err(409, "ALREADY_EXISTS", "key event exists")
        k = {"name": "%s/keyEvents/%s" % (prop, nid()), "eventName": body.get("eventName"),
             "countingMethod": body["countingMethod"]}
        DB["key_events"][k["name"]] = k
        return 200, k
    return err(404, "NOT_FOUND", "unknown admin path")


def filters(expr):
    """{fieldName: filter} of a dimensionFilter: one filter, or an andGroup of them."""
    if not expr:
        return {}
    if "filter" in expr:
        return {expr["filter"]["fieldName"]: expr["filter"]}
    out = {}
    for e in (expr.get("andGroup") or {}).get("expressions") or []:
        out.update(filters(e))
    return out


def report(prop, body, source):
    dims = [d["name"] for d in body.get("dimensions") or []]
    mets = [m["name"] for m in body.get("metrics") or []]
    f = filters(body.get("dimensionFilter"))
    rows = []
    first = sorted((s for s in DB["streams"].values() if s["name"].startswith(prop + "/")), key=lambda s: s["name"])
    sid = (f.get("streamId") or {}).get("stringFilter", {}).get("value")
    if sid is not None and (not first or first[0]["name"].rsplit("/", 1)[-1] != sid):
        pass                                    # another stream: none of the scenario's traffic
    elif dims == ["eventName"]:
        keep = set((f.get("eventName") or {}).get("inListFilter", {}).get("values") or []) if "eventName" in f else None
        for name, (count, users) in sorted(SCENARIO[source].items()):
            if keep is None or name in keep:
                vals = {"eventCount": count, "totalUsers": users, "activeUsers": users}
                rows.append({"dimensionValues": [{"value": name}],
                             "metricValues": [{"value": str(vals.get(m, 0))} for m in mets]})
    elif not dims:
        vals = {"totalUsers": SCENARIO["users"], "activeUsers": SCENARIO["users"], "newUsers": SCENARIO["new_users"]}
        rows.append({"metricValues": [{"value": str(vals.get(m, 0))} for m in mets]})
    else:
        return err(400, "INVALID_ARGUMENT", "fake-ga supports no dimensions or eventName only")
    out = {"dimensionHeaders": [{"name": d} for d in dims],
           "metricHeaders": [{"name": m, "type": "TYPE_INTEGER"} for m in mets],
           "rows": rows, "rowCount": len(rows), "kind": "analyticsData#" + ("runReport" if source == "events" else "runRealtimeReport")}
    if source == "events":
        out["metadata"] = {"currencyCode": "USD", "timeZone": "Etc/UTC"}
        if SCENARIO["thresholded"]:
            out["metadata"]["subjectToThresholding"] = True
    return 200, out


def data(method, parts, body):
    if method != "POST" or len(parts) != 2 or parts[0] != "properties":
        return err(404, "NOT_FOUND", "unknown data path")
    pid, _, verb = parts[1].partition(":")
    if "properties/" + pid not in DB["properties"]:
        return err(403, "PERMISSION_DENIED", "User does not have sufficient permissions for this property")
    if verb == "runReport":
        if not body.get("dateRanges"):
            return err(400, "INVALID_ARGUMENT", "dateRanges is required")
        return report("properties/" + pid, body, "events")
    if verb == "runRealtimeReport":
        return report("properties/" + pid, body, "realtime")
    return err(404, "NOT_FOUND", "unknown data method")


def mp_debug(body):
    msgs = list(SCENARIO["mp_messages"])
    if not body.get("client_id"):
        msgs.append({"fieldPath": "client_id", "description": "client_id is required", "validationCode": "VALUE_REQUIRED"})
    events = body.get("events") or []
    if not 1 <= len(events) <= 25:
        msgs.append({"fieldPath": "events", "description": "1 to 25 events per request", "validationCode": "VALUE_INVALID"})
    for i, e in enumerate(events):
        n = e.get("name", "")
        if not NAME.match(n):
            msgs.append({"fieldPath": "events", "description": "Event at index: [%d] has invalid name [%s]." % (i, n),
                         "validationCode": "NAME_INVALID"})
        elif n in RESERVED or n.startswith(("_", "firebase_", "ga_", "google_", "gtag.")):
            msgs.append({"fieldPath": "events", "description": "Event at index: [%d] has name [%s] which is reserved." % (i, n),
                         "validationCode": "NAME_RESERVED"})
    return 200, {"validationMessages": msgs}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def handle_any(self, method):
        u = urlsplit(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            return self.reply(400, {"error": {"code": 400, "message": "body is not JSON", "status": "INVALID_ARGUMENT"}})
        with LOCK:
            if u.path == "/_log":
                return self.reply(200, LOG)
            if u.path == "/_control":
                SCENARIO.update(body)
                return self.reply(200, SCENARIO)
            auth = self.headers.get("Authorization") or ""
            LOG.append({"method": method, "path": u.path, "query": u.query, "auth": auth, "body": body})
            parts = [p for p in u.path.split("/") if p]
            if parts[:2] == ["mp", "debug"] and parts[2:] == ["mp", "collect"] and method == "POST":
                return self.reply(*mp_debug(body))
            if parts[:1] in (["admin"], ["data"]) and SCENARIO["redirect"]:
                self.send_response(302)
                self.send_header("Location", SCENARIO["redirect"])
                self.send_header("Content-Length", "0")
                return self.end_headers()
            if parts[:1] in (["admin"], ["data"]):
                if auth != "Bearer " + TOKEN:
                    return self.reply(*err(401, "UNAUTHENTICATED", "Request had invalid authentication credentials."))
                if parts[1:2] != ["v1beta"]:
                    return self.reply(*err(404, "NOT_FOUND", "unknown version"))
                fn = admin if parts[0] == "admin" else data
                return self.reply(*fn(method, parts[2:], body))
            return self.reply(*err(404, "NOT_FOUND", "unknown path %s" % u.path))

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    print(srv.server_address[1], flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
