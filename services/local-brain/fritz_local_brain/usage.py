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
_UNKNOWN_AGENT = "unknown"


def _agent_key(event: dict[str, Any]) -> str:
    """Normalized agent for an event: empty/None -> ``"unknown"``.

    The SINGLE normalization used by the agent dimension, the per-agent
    discovery list, and the agent FILTER (telemetry.query_events) so the three
    stay consistent (#199).
    """
    return event.get("agent") or _UNKNOWN_AGENT


def _dimension_value(event: dict[str, Any], by: str) -> str:
    """Resolve the grouping dimension value.

    Agent normalizes empty/None to ``"unknown"`` (consistent with the agent
    filter and discovery list, #199); vault/type map None to ``"(none)"``.
    """
    if by == "agent":
        return _agent_key(event)
    if by == "vault":
        return event.get("vault") or _NONE_KEY
    return event.get("event_type") or _NONE_KEY


def agents(
    settings: Settings,
    *,
    since: str | None = None,
    until: str | None = None,
) -> list[dict[str, Any]]:
    """Discover the distinct agents present in the telemetry store (#199).

    Data-driven, NO enum: every normalized agent value seen in the events
    (empty/None -> ``"unknown"``) becomes one entry
    ``{agent, count, first_seen, last_seen}`` where first/last are the min/max
    ts strings for that agent. Sorted by count desc, then agent asc.
    """
    events = query_events(settings, since=since, until=until)
    counts: dict[str, int] = {}
    first_seen: dict[str, str] = {}
    last_seen: dict[str, str] = {}
    for e in events:
        agent = _agent_key(e)
        ts = e.get("ts") or ""
        counts[agent] = counts.get(agent, 0) + 1
        if ts:
            if agent not in first_seen or ts < first_seen[agent]:
                first_seen[agent] = ts
            if agent not in last_seen or ts > last_seen[agent]:
                last_seen[agent] = ts
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [
        {
            "agent": agent,
            "count": count,
            "first_seen": first_seen.get(agent),
            "last_seen": last_seen.get(agent),
        }
        for agent, count in ordered
    ]


def activity(
    settings: Settings,
    *,
    since: str | None,
    until: str | None,
    bucket: str = "day",
    by: str = "type",
    agent: str | None = None,
) -> dict[str, dict[str, int]]:
    """Bucketed event counts: ``{day: {dimension_value: count}}``.

    Only ``bucket="day"`` is supported (groups by ``ts[:10]``); any other value
    falls back to day. Includes BOTH write and read events. ``agent`` (when set)
    scopes the events to that single normalized agent (#199).
    """
    by = by if by in {"type", "agent", "vault"} else "type"
    events = query_events(settings, since=since, until=until, agent=agent)
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
    agent: str | None = None,
) -> dict[str, Any]:
    """Aggregate query/search events: total, hit_rate, latency, by_agent, top.

    ``agent`` (when set) scopes to that single normalized agent (#199).
    """
    events = query_events(
        settings, since=since, until=until, event_types=_QUERY_TYPES, agent=agent
    )
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
        key = _agent_key(e)
        by_agent[key] = by_agent.get(key, 0) + 1

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
    agent: str | None = None,
) -> list[dict[str, Any]]:
    """Per-vault rollup merging event activity with KB article counts.

    Known limitation (follow-up beyond #181): write-side events largely carry
    vault=None (the log sync doesn't attribute a vault), so global write
    activity buckets under "(none)" while store-mode articles key under
    "brain". The counts are correct, but those two rows won't merge into one
    global row until write events are vault-attributed.

    ``agent`` (when set) scopes the event side to that single normalized agent
    (#199); the KB article counts are agent-independent.
    """
    events = query_events(settings, since=since, until=until, agent=agent)

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
    agent: str | None = None,
) -> dict[str, Any]:
    """Headline numbers for the landing page.

    ``agent`` (when set) scopes the events to that single normalized agent
    (#199).
    """
    events = query_events(settings, since=since, until=until, agent=agent)

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
