"""Usage aggregation over the telemetry store and KB-health scanner (#181).

Pure, defensive aggregation helpers consumed by the ``/v1/usage/*`` HTTP
endpoints. Every helper is empty-safe: an empty store yields zeros / empty
maps / null rates, never a 500 or ZeroDivisionError.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .config import Settings
from .kb_health import compute_kb_health
from .telemetry import _percentile, query_events

_QUERY_TYPES = {"query", "search"}
_NONE_KEY = "(none)"


def _dimension_value(event: dict[str, Any], by: str) -> str:
    """Resolve the grouping dimension value, mapping None to ``"(none)"``."""
    if by == "agent":
        return event.get("agent") or _NONE_KEY
    if by == "vault":
        return event.get("vault") or _NONE_KEY
    return event.get("event_type") or _NONE_KEY


def activity(
    settings: Settings,
    *,
    since: str | None,
    until: str | None,
    bucket: str = "day",
    by: str = "type",
) -> dict[str, dict[str, int]]:
    """Bucketed event counts: ``{day: {dimension_value: count}}``.

    Only ``bucket="day"`` is supported (groups by ``ts[:10]``); any other value
    falls back to day. Includes BOTH write and read events.
    """
    by = by if by in {"type", "agent", "vault"} else "type"
    events = query_events(settings, since=since, until=until)
    buckets: dict[str, dict[str, int]] = {}
    for event in events:
        day = (event.get("ts") or "")[:10]
        if not day:
            continue
        key = _dimension_value(event, by)
        buckets.setdefault(day, {})
        buckets[day][key] = buckets[day].get(key, 0) + 1
    return buckets


def queries(
    settings: Settings,
    *,
    since: str | None,
    until: str | None,
    limit: int = 10,
) -> dict[str, Any]:
    """Aggregate query/search events: total, hit_rate, latency, by_agent, top."""
    events = query_events(settings, since=since, until=until, event_types=_QUERY_TYPES)
    total = len(events)

    hits = sum(1 for e in events if e["payload"].get("hit") is True)
    hit_rate = (hits / total) if total > 0 else None

    durations = sorted(
        e["duration_ms"] for e in events if isinstance(e.get("duration_ms"), int)
    )
    latency = {
        "p50": _percentile(durations, 50),
        "p95": _percentile(durations, 95),
        "p99": _percentile(durations, 99),
    }

    by_agent: dict[str, int] = {}
    for e in events:
        agent = e.get("agent") or _NONE_KEY
        by_agent[agent] = by_agent.get(agent, 0) + 1

    query_counter: Counter[str] = Counter()
    for e in events:
        text = e["payload"].get("query")
        if isinstance(text, str) and text:
            query_counter[text] += 1
    top_queries = [
        {"query": text, "count": count}
        for text, count in query_counter.most_common(limit)
    ]

    return {
        "total": total,
        "hit_rate": hit_rate,
        "latency_ms": latency,
        "by_agent": by_agent,
        "top_queries": top_queries,
    }


def knowledge(settings: Settings) -> dict[str, Any]:
    """Point-in-time KB-health snapshot (no date filter)."""
    return compute_kb_health(settings)


def projects(
    settings: Settings,
    *,
    since: str | None,
    until: str | None,
) -> list[dict[str, Any]]:
    """Per-vault rollup merging event activity with KB article counts.

    Known limitation (follow-up beyond #181): write-side events largely carry
    vault=None (the log sync doesn't attribute a vault), so global write
    activity buckets under "(none)" while store-mode articles key under
    "brain". The counts are correct, but those two rows won't merge into one
    global row until write events are vault-attributed.
    """
    events = query_events(settings, since=since, until=until)

    event_count: dict[str, int] = {}
    events_by_type: dict[str, dict[str, int]] = {}
    for e in events:
        vault = e.get("vault") or _NONE_KEY
        event_count[vault] = event_count.get(vault, 0) + 1
        etype = e.get("event_type") or _NONE_KEY
        events_by_type.setdefault(vault, {})
        events_by_type[vault][etype] = events_by_type[vault].get(etype, 0) + 1

    health = compute_kb_health(settings)
    articles_by_vault = health.get("articles_by_vault") or {}

    vaults = sorted(set(event_count) | set(articles_by_vault))
    return [
        {
            "vault": vault,
            "event_count": event_count.get(vault, 0),
            "events_by_type": events_by_type.get(vault, {}),
            "article_count": int(articles_by_vault.get(vault, 0)),
        }
        for vault in vaults
    ]


def summary(
    settings: Settings,
    *,
    since: str | None,
    until: str | None,
) -> dict[str, Any]:
    """Headline numbers for the landing page."""
    events = query_events(settings, since=since, until=until)

    events_by_type: dict[str, int] = {}
    agents: set[str] = set()
    query_events_list: list[dict[str, Any]] = []
    for e in events:
        etype = e.get("event_type") or _NONE_KEY
        events_by_type[etype] = events_by_type.get(etype, 0) + 1
        if e.get("agent"):
            agents.add(e["agent"])
        if etype in _QUERY_TYPES:
            query_events_list.append(e)

    total_queries = len(query_events_list)
    hits = sum(1 for e in query_events_list if e["payload"].get("hit") is True)
    hit_rate = (hits / total_queries) if total_queries > 0 else None

    health = compute_kb_health(settings)
    backlog = health.get("backlog") or {}
    pending_by_source = backlog.get("pending_captures_by_source") or {}
    backlog_pending = sum(int(v) for v in pending_by_source.values())

    return {
        "total_events": len(events),
        "events_by_type": events_by_type,
        "total_queries": total_queries,
        "hit_rate": hit_rate,
        "total_articles": int(health.get("articles_total") or 0),
        "backlog_pending": backlog_pending,
        "distinct_agents": len(agents),
    }
