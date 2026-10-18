"""_routes.py — abstract model routes (ROADMAP §3.6); see bin/models and hosts/routes.json.

A role group (planning | worker) is configured as a ROUTE (strongest | strong | economical)
or a pinned concrete id. Each host resolves a route to its own id; a pinned id passes
through. The host-specific settings (MODEL_*_CODEX, MODEL_*_OPENCODE) still win on that host.
"""
import json
import os

import _paths

HERE = os.path.dirname(os.path.realpath(__file__))
GROUPS = {"planning": ("intake", "standards", "planner", "archivist"),
          "worker": ("red-worker", "scaffolder", "green-worker", "structural-reviewer", "final-gate")}
# Claude Code's short aliases, for the hosts that need a full id
ALIAS = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5", "haiku": "claude-haiku-4-5", "fable": "claude-fable-5-1"}


def table():
    with open(os.path.join(HERE, "..", "hosts", "routes.json")) as f:
        return json.load(f)


def group_of(name):
    """planning | worker for a group name or a role name; None if unknown."""
    if name in GROUPS:
        return name
    return next((g for g, roles in GROUPS.items() if name in roles), None)


def resolve(host, group, st=None, cwd=None):
    """{group, host, route, model, source}. route is the route name, or 'pinned'.
    Raises ValueError for an unknown host or group."""
    t = table()
    if host not in t["hosts"]:
        raise ValueError("unknown host %r (known: %s)" % (host, ", ".join(sorted(t["hosts"]))))
    g = group_of(group)
    if not g:
        raise ValueError("unknown role or group %r" % group)
    st = st if st is not None else _paths.settings(cwd)
    key = "MODEL_" + g.upper()
    specific = (st.get("%s_%s" % (key, host.upper())) or "") if host != "claude" else ""
    if specific:
        return {"group": g, "host": host, "route": "pinned", "model": specific, "source": "AGENTIC_%s_%s" % (key, host.upper())}
    want = st.get(key) or ""
    if want in t["routes"]:
        return {"group": g, "host": host, "route": want, "model": t["hosts"][host][want], "source": "AGENTIC_" + key}
    model = want
    if host == "opencode":
        base = ALIAS.get(want, want)
        model = base if "/" in base else ("anthropic/" + base if base.startswith("claude") else "")
    elif host == "codex":
        model = "" if (want in ALIAS or want.startswith("claude")) else want
    return {"group": g, "host": host, "route": "pinned", "model": model, "source": "AGENTIC_" + key}
