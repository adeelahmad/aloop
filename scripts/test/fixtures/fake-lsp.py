#!/usr/bin/env python3
"""A tiny, deterministic LSP server for tests: references + call hierarchy over Python
files by name matching. FAKE_LSP_CAPS=refs,calls picks the advertised capabilities."""
import json
import os
import re
import sys

CAPS = set((os.environ.get("FAKE_LSP_CAPS") or "refs,calls").split(","))
ROOT = os.getcwd()
DEF = re.compile(r"^(\s*)def\s+(\w+)")


def send(m):
    b = json.dumps(m).encode()
    sys.stdout.buffer.write(b"Content-Length: %d\r\n\r\n" % len(b) + b)
    sys.stdout.buffer.flush()


def recv():
    n = None
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            sys.exit(0)
        line = line.strip()
        if not line:
            break
        if line.lower().startswith(b"content-length:"):
            n = int(line.split(b":")[1])
    return json.loads(sys.stdin.buffer.read(n))


def files():
    for d, _, fs in os.walk(ROOT):
        if "/." in d:
            continue
        for f in fs:
            if f.endswith(".py"):
                yield os.path.join(d, f)


def word_at(uri, pos):
    line = open(uri[7:]).read().splitlines()[pos["line"]]
    m = [x for x in re.finditer(r"\w+", line) if x.start() <= pos["character"] < x.end()]
    return m[0].group(0)


def loc(path, i, c, n):
    return {"uri": "file://" + path, "range": {"start": {"line": i, "character": c}, "end": {"line": i, "character": c + n}}}


def item(name, path, i, c):
    r = loc(path, i, c, len(name))["range"]
    return {"name": name, "kind": 12, "uri": "file://" + path, "range": r, "selectionRange": r}


def defs():
    out = {}
    for p in files():
        for i, ln in enumerate(open(p).read().splitlines()):
            m = DEF.match(ln)
            if m:
                out[m.group(2)] = (p, i, ln.index(m.group(2)))
    return out


def handle(m):
    meth, prm = m.get("method"), m.get("params") or {}
    if meth == "initialize":
        caps = {}
        if "refs" in CAPS:
            caps["referencesProvider"] = True
        if "calls" in CAPS:
            caps["callHierarchyProvider"] = True
        return {"capabilities": caps}
    if meth == "textDocument/references":
        w = word_at(prm["textDocument"]["uri"], prm["position"])
        out = []
        for p in sorted(files()):
            for i, ln in enumerate(open(p).read().splitlines()):
                for x in re.finditer(r"\b%s\b" % w, ln):
                    if not DEF.match(ln) or DEF.match(ln).group(2) != w:
                        out.append(loc(p, i, x.start(), len(w)))
        return out
    if meth == "textDocument/prepareCallHierarchy":
        w = word_at(prm["textDocument"]["uri"], prm["position"])
        d = defs().get(w)
        return [item(w, *d)] if d else []
    if meth == "callHierarchy/incomingCalls":
        w, out = prm["item"]["name"], []
        for p in sorted(files()):
            cur = None
            for i, ln in enumerate(open(p).read().splitlines()):
                dm = DEF.match(ln)
                if dm:
                    cur = (dm.group(2), p, i, ln.index(dm.group(2)))
                elif cur and re.search(r"\b%s\(" % w, ln):
                    out.append({"from": item(*cur), "fromRanges": []})
        return out
    if meth == "callHierarchy/outgoingCalls":
        it, d, out = prm["item"], defs(), []
        lines = open(it["uri"][7:]).read().splitlines()
        start = it["range"]["start"]["line"]
        ind = len(DEF.match(lines[start]).group(1))
        for ln in lines[start + 1:]:
            if ln.strip() and len(ln) - len(ln.lstrip()) <= ind:
                break
            for name in re.findall(r"\b(\w+)\(", ln):
                if name in d and name not in [o["to"]["name"] for o in out]:
                    out.append({"to": item(name, *d[name]), "fromRanges": []})
        return out
    if meth == "shutdown":
        return None
    raise KeyError(meth)


while True:
    msg = recv()
    if "id" not in msg:
        if msg.get("method") == "exit":
            sys.exit(0)
        continue
    try:
        send({"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg)})
    except KeyError as e:
        send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "unsupported %s" % e}})
