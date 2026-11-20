"""_prices.py — published per-token prices, for a host whose CLI prints no cost (ROADMAP §4.9).

Claude Code reports its own cost (`total_cost_usd` on its `result` line). DeepSeek Harness (dsh)
reports none, but the aloop dsh plugin logs each completed call's exact usage per role (_usage
rows; dsh's compaction and session-title calls included, a retried attempt not: ROADMAP §4.9),
so run-summary prices each call here. A model with no row is UNPRICED: its tokens are reported
and its cost is None, never a guess.

DeepSeek, USD per 1M tokens, from https://api-docs.deepseek.com/quick_start/pricing (read 2026-10-20):

  model id            input, cache hit    input, cache miss    output         (off-peak / peak)
  deepseek-flash      0.003 / 0.006       0.15 / 0.30          0.60 / 1.20    DeepSeek-V4.1-Flash-1017
  deepseek-v4-pro     0.022 / 0.044       0.66 / 1.32          1.98 / 3.96    DeepSeek-V4-Pro-0813

  The legacy ids deepseek-v4-flash and deepseek-v4-flash-vision-exp are "billed at the Flash price".
  Peak is 01:00-04:00 and 06:00-10:00 UTC, Monday to Friday; every other hour is off-peak, and so
  are Chinese public holidays, which this table does not list: a weekday call in a peak band is
  priced at peak (an upper bound on a holiday). A call with no readable time is priced at peak too.

The ids are the ones dsh sends to api.deepseek.com/anthropic (dsh-llm-deepseek's model catalog;
the plugin logs message.source.model). DeepSeek bills input as cache hit or cache miss only, and
dsh keeps the Messages classes disjoint (its totalTokens adds all four), so a row's `in` and
`cache_write` are misses and `cache_read` the hits.
"""
import datetime

SOURCE = "https://api-docs.deepseek.com/quick_start/pricing (read 2026-10-20)"
# (cache hit, cache miss, output) per 1M tokens
DEEPSEEK = {
    "deepseek-flash": {"off": (0.003, 0.15, 0.60), "peak": (0.006, 0.30, 1.20)},
    "deepseek-v4-pro": {"off": (0.022, 0.66, 1.98), "peak": (0.044, 1.32, 3.96)},
}
ALIASES = {"deepseek-v4-flash": "deepseek-flash", "deepseek-v4-flash-vision-exp": "deepseek-flash"}


def peak(ts):
    """True when a call at UTC time `ts` (ISO 8601) falls in DeepSeek's peak band."""
    try:
        t = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if t.tzinfo:
            t = t.astimezone(datetime.timezone.utc)
    except ValueError:
        return True
    return t.weekday() < 5 and (1 <= t.hour < 4 or 6 <= t.hour < 10)


def usd(row):
    """The dollars of one _usage row, or None when its model has no published price."""
    p = DEEPSEEK.get(ALIASES.get(row.get("model") or "", row.get("model") or ""))
    if p is None:
        return None
    hit, miss, out = p["peak" if peak(row.get("ts")) else "off"]
    return ((row["in"] + row["cache_write"]) * miss + row["cache_read"] * hit + row["out"] * out) / 1e6
