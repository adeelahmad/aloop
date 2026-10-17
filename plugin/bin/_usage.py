"""_usage.py — read token usage from ANY host's session log, as one row per API call.

  Claude Code   <session>.jsonl / subagents/agent-<id>.jsonl — assistant records carry
                message.usage (input / cache read / cache creation / output); streamed
                duplicates share a message id (last write wins).
  Codex         $CODEX_HOME/sessions/YYYY/MM/DD/rollout-…-<thread>.jsonl[.zst] —
                event_msg/token_count records carry CUMULATIVE total_token_usage
                (input_tokens INCLUDES cached_input_tokens; output_tokens includes
                reasoning). One row per increase of the running total.
  OpenCode      the agentic-agile OpenCode plugin writes Claude-shaped usage records
                (.agentic/host/opencode/…), so they read as Claude.

Row: {ts, id, model, in, cache_read, cache_write, out}  (in = uncached input).
"""
import glob
import io
import json
import os
import subprocess


def _open_text(path):
    if path.endswith(".zst"):
        try:
            import zstandard  # type: ignore
            with open(path, "rb") as f:
                return io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(f), encoding="utf-8")
        except ImportError:
            try:
                out = subprocess.run(["zstd", "-dc", path], capture_output=True, timeout=30).stdout
                return io.StringIO(out.decode("utf-8", "replace"))
            except Exception:
                return io.StringIO("")
    return open(path, encoding="utf-8", errors="replace")


def _records(path):
    try:
        with _open_text(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
    except OSError:
        return


def rows(path):
    """Per-API-call usage rows for one session/agent log, oldest first."""
    if not path or not os.path.isfile(path):
        return []
    claude, codex = {}, []
    prev, model = None, ""
    for rec in _records(path):
        msg = rec.get("message")
        if isinstance(msg, dict) and msg.get("usage"):
            u = msg["usage"]
            mid = msg.get("id") or rec.get("uuid") or str(len(claude))
            claude[mid] = {"ts": rec.get("timestamp") or "", "id": mid, "model": msg.get("model") or "",
                           "in": u.get("input_tokens") or 0, "cache_read": u.get("cache_read_input_tokens") or 0,
                           "cache_write": u.get("cache_creation_input_tokens") or 0, "out": u.get("output_tokens") or 0}
            continue
        p = rec.get("payload") if isinstance(rec.get("payload"), dict) else None
        if not p:
            continue
        if rec.get("type") == "turn_context" and p.get("model"):
            model = p["model"]
        if p.get("type") != "token_count" or not isinstance(p.get("info"), dict):
            continue
        tot = p["info"].get("total_token_usage") or {}
        if prev is not None and tot == prev:
            continue
        base = prev or {}
        d = {k: (tot.get(k) or 0) - (base.get(k) or 0) for k in
             ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens")}
        if prev is not None and all(v <= 0 for v in d.values()):
            prev = tot
            continue
        prev = tot
        last = p["info"].get("last_token_usage") or {}
        codex.append({"ts": rec.get("timestamp") or "", "id": "%s#%d" % (os.path.basename(path), len(codex)),
                      "model": model, "in": max(d["input_tokens"] - d["cached_input_tokens"], 0),
                      "cache_read": max(d["cached_input_tokens"], 0), "cache_write": max(d["cache_write_input_tokens"], 0),
                      "out": max(d["output_tokens"], 0),
                      "_ctx": last.get("input_tokens") or 0})
    return sorted(claude.values(), key=lambda r: r["ts"]) + codex


def classes(path):
    """Token CLASSES for one agent log, kept apart (ROADMAP §3.2): cumulative fresh (uncached)
    input, cache read, cache write and output, plus the context in use on the latest call.
    Cache traffic is cumulative churn/cost, never context size. None when the log is empty."""
    rs = rows(path)
    if not rs:
        return None
    last = rs[-1]
    return {"in": sum(r["in"] for r in rs), "cache_read": sum(r["cache_read"] for r in rs),
            "cache_write": sum(r["cache_write"] for r in rs), "out": sum(r["out"] for r in rs),
            "context": last.get("_ctx") or (last["in"] + last["cache_read"] + last["cache_write"])}


def charge(c, meter="fresh"):
    """The budget charge from classes(): fresh input + output (default), or the legacy
    `context` meter (context in use + all output)."""
    if c is None:
        return None
    return c["context"] + c["out"] if meter == "context" else c["in"] + c["out"]


def context_plus_output(path):
    """Budget metric: context in use on the latest call + every output token so far."""
    rs = rows(path)
    if not rs:
        return None
    last = rs[-1]
    ctx = last.get("_ctx") or (last["in"] + last["cache_read"] + last["cache_write"])
    return ctx + sum(r["out"] for r in rs)


def codex_home():
    return os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")


def find_codex_rollout(thread_id):
    """A Codex thread's rollout file by id (sub-agents are threads with their own rollout)."""
    if not thread_id:
        return ""
    hits = glob.glob(os.path.join(codex_home(), "sessions", "*", "*", "*", "rollout-*%s.jsonl*" % thread_id))
    return max(hits, key=os.path.getmtime) if hits else ""


def host_of(payload):
    """claude | codex | opencode — from the hook payload (or AGENTIC_HOST)."""
    h = os.environ.get("AGENTIC_HOST")
    if h:
        return h
    tp = (payload or {}).get("transcript_path") or ""
    if "/.agentic/host/opencode/" in tp:
        return "opencode"
    if os.path.basename(tp).startswith("rollout-"):
        return "codex"                     # Codex session logs are rollout-*.jsonl[.zst]
    if not tp and (payload or {}).get("turn_id"):
        return "codex"
    return "claude"


def agent_transcript(payload, agent_id):
    """The transcript that holds `agent_id`'s own API calls, on any host."""
    tp = (payload or {}).get("agent_transcript_path") or ""
    if tp and os.path.isfile(tp):
        return tp
    tp = (payload or {}).get("transcript_path") or ""
    if host_of(payload) == "codex":
        return find_codex_rollout(agent_id)
    if tp:
        base = tp[:-6] if tp.endswith(".jsonl") else tp
        for c in (os.path.join(base, "subagents", "agent-%s.jsonl" % agent_id),
                  os.path.join(os.path.dirname(tp), "subagents", "agent-%s.jsonl" % agent_id)):
            if os.path.isfile(c):
                return c
        hits = glob.glob(os.path.join(os.path.dirname(tp), "*", "subagents", "agent-%s.jsonl" % agent_id))
        if hits:
            return hits[0]
    return ""
