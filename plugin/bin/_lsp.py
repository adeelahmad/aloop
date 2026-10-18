"""_lsp.py — a minimal LSP client for the structural adapter seam (ROADMAP §4.6).

Only what the narrow-context CLI needs: references and the call hierarchy (incoming /
outgoing). The server is whatever AGENTIC_LSP_COMMAND names (pyright-langserver --stdio,
rust-analyzer, typescript-language-server --stdio, ...). A capability the server does not
advertise raises Missing — the caller reports it; nothing is guessed.
"""
import json
import os
import shlex
import subprocess
import threading


class Missing(Exception):
    """The backend cannot answer this question (not configured, or capability absent)."""


class Client:
    def __init__(self, cmd, root, timeout=20):
        self.root, self.timeout, self.seq = os.path.realpath(root), timeout, 0
        try:
            self.p = subprocess.Popen(shlex.split(cmd), cwd=self.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL)
        except (OSError, ValueError) as e:
            raise Missing("cannot start the LSP server %r (%s)" % (cmd, e))
        res = self.request("initialize", {
            "processId": os.getpid(), "rootUri": self.uri(self.root), "workspaceFolders": None,
            "capabilities": {"textDocument": {"references": {}, "callHierarchy": {}}}})
        self.caps = (res or {}).get("capabilities") or {}
        self.notify("initialized", {})
        self.opened = set()

    def uri(self, path):
        return "file://" + os.path.realpath(path)

    def rel(self, uri):
        p = uri[len("file://"):] if uri.startswith("file://") else uri
        return os.path.relpath(p, self.root)

    def _send(self, msg):
        body = json.dumps(msg).encode()
        self.p.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
        self.p.stdin.flush()

    def _read(self):
        length = None
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise Missing("the LSP server exited")
            line = line.strip()
            if not line:
                break
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1])
        return json.loads(self.p.stdout.read(length))

    def notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method, params):
        self.seq += 1
        rid, box = self.seq, {}
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})

        def pump():
            try:
                while True:
                    m = self._read()
                    if m.get("id") == rid and ("result" in m or "error" in m):
                        box["m"] = m
                        return
            except Exception as e:  # noqa: BLE001
                box["e"] = e
        t = threading.Thread(target=pump, daemon=True)
        t.start()
        t.join(self.timeout)
        if "e" in box:
            raise Missing(str(box["e"]))
        if "m" not in box:
            raise Missing("the LSP server did not answer %s within %ds" % (method, self.timeout))
        if "error" in box["m"]:
            raise Missing("%s failed: %s" % (method, box["m"]["error"].get("message")))
        return box["m"].get("result")

    def open(self, path):
        full = os.path.join(self.root, path)
        if full in self.opened:
            return
        self.notify("textDocument/didOpen", {"textDocument": {
            "uri": self.uri(full), "languageId": os.path.splitext(path)[1].lstrip("."), "version": 1,
            "text": open(full).read()}})
        self.opened.add(full)

    def need(self, cap):
        if not self.caps.get(cap):
            raise Missing("the LSP server does not advertise %s" % cap)

    def pos(self, path, line, col):
        self.open(path)
        return {"textDocument": {"uri": self.uri(os.path.join(self.root, path))},
                "position": {"line": line - 1, "character": col}}

    def references(self, path, line, col):
        self.need("referencesProvider")
        p = self.pos(path, line, col)
        p["context"] = {"includeDeclaration": False}
        return [(self.rel(r["uri"]), r["range"]["start"]["line"] + 1, r["range"]["start"]["character"])
                for r in self.request("textDocument/references", p) or []]

    def calls(self, path, line, col, direction):
        self.need("callHierarchyProvider")
        items = self.request("textDocument/prepareCallHierarchy", self.pos(path, line, col)) or []
        out = []
        for it in items:
            for c in self.request("callHierarchy/%sCalls" % direction, {"item": it}) or []:
                peer = c["from"] if direction == "incoming" else c["to"]
                out.append((self.rel(peer["uri"]), peer["selectionRange"]["start"]["line"] + 1, peer["name"]))
        return out

    def close(self):
        try:
            self.request("shutdown", None)
            self.notify("exit", None)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.p.kill()
        except OSError:
            pass
