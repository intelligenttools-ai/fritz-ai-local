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

# SYSTEM vs AGENT classification (#205).
#
# The service records its OWN write-side workflow work with agent="local-brain",
# which made "local-brain" rank as the #1 "agent" and drown the real runtimes.
# We classify by EVENT_TYPE (data-driven, never by hardcoding agent names):
#
#   SYSTEM_EVENT_TYPES — the scheduler's own compile/index/reconcile pipeline.
#   AGENT_EVENT_TYPES  — runtime-driven capture/ingest and read-side query/search.
#
# Any event_type NOT in SYSTEM_EVENT_TYPES is treated as AGENT-side, so an
# unknown/new event_type stays visible in the agent views and totals rather than
# silently vanishing. The two sets are documented here as the single source of
# truth for the split; ``_is_system_event`` is the only classifier callers use.
SYSTEM_EVENT_TYPES: frozenset[str] = frozenset(
    {"compile", "sync", "lint", "reconcile", "rereconcile", "embeddings", "mirror"}
)
AGENT_EVENT_TYPES: frozenset[str] = frozenset({"capture", "ingest", "query", "search"})


def _is_system_event(event: dict[str, Any]) -> bool:
    """Return True when *event* is the service's own (SYSTEM) work.

    Classified purely by ``event_type`` membership in :data:`SYSTEM_EVENT_TYPES`.
    Unknown/other types are NOT system (default to the agent side) so they never
    vanish from agent totals.
    """
    return (event.get("event_type") or "") in SYSTEM_EVENT_TYPES

# Known e2e/test agents — hidden from operator telemetry views.
# Exact matches: "diag", "pwsse". Prefix match: "pwtest" (e.g. pwtest199).
TEST_AGENT_PATTERNS: tuple[str, ...] = ("diag", "pwsse", "pwtest")


def _is_test_agent(name: str) -> bool:
    """Return True when *name* is a known e2e/test agent (hidden from operator views)."""
    return name in ("diag", "pwsse") or name.startswith("pwtest")


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

    SYSTEM events (the service's own compile/index/reconcile work, #205) are
    EXCLUDED so ``local-brain`` — which only emits system-type events — no
    longer appears as an agent. Real runtimes (pi/claude/unknown) still appear.
    """
    events = query_events(settings, since=since, until=until)
    counts: dict[str, int] = {}
    first_seen: dict[str, str] = {}
    last_seen: dict[str, str] = {}
    for e in events:
        if _is_system_event(e):
            continue
        agent = _agent_key(e)
        if _is_test_agent(agent):
            continue
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


def agent_detail(
    settings: Settings,
    agent: str,
    *,
    since: str | None = None,
    until: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """Per-agent drill-down for ``GET /v1/usage/agents/{agent}`` (#223).

    Returns first/last seen, an event-type breakdown, a daily activity series,
    and a paginated slice of recent events (newest first) for the single
    normalized ``agent``. Test-agent isolation (#206) is preserved by the
    ``agent`` filter itself: :func:`telemetry.query_events` matches ONLY events
    whose normalized agent equals the requested value, so a real agent's detail
    never includes test-agent rows (and querying a test agent returns only its
    own rows) — neither bleeds into the other. Empty-safe: an unknown agent
    yields ``event_count=0`` with empty breakdown/series/events.
    """
    events = query_events(settings, since=since, until=until, agent=agent)

    first_seen: str | None = None
    last_seen: str | None = None
    by_type: dict[str, int] = {}
    daily: dict[str, int] = {}
    for e in events:
        ts = e.get("ts") or ""
        if ts:
            if first_seen is None or ts < first_seen:
                first_seen = ts
            if last_seen is None or ts > last_seen:
                last_seen = ts
            day = ts[:10]
            daily[day] = daily.get(day, 0) + 1
        etype = e.get("event_type") or _NONE_KEY
        by_type[etype] = by_type.get(etype, 0) + 1

    # Recent events: newest first, then paginate.
    ordered = sorted(events, key=lambda e: e.get("ts") or "", reverse=True)
    bounded_limit = max(0, limit)
    bounded_offset = max(0, offset)
    page = ordered[bounded_offset : bounded_offset + bounded_limit]
    recent = [
        {
            "ts": e.get("ts"),
            "event_type": e.get("event_type"),
            "vault": e.get("vault"),
            "status": e.get("status"),
            "duration_ms": e.get("duration_ms"),
            "run_id": e.get("run_id"),
        }
        for e in page
    ]

    return {
        "agent": agent,
        "event_count": len(events),
        "first_seen": first_seen,
        "last_seen": last_seen,
        "events_by_type": by_type,
        "daily_activity": daily,
        "recent_events": recent,
        "limit": bounded_limit,
        "offset": bounded_offset,
    }


def system(
    settings: Settings,
    *,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    """Aggregate SYSTEM activity (the service's own work) for the System panel (#205).

    Counts only events whose ``event_type`` is in :data:`SYSTEM_EVENT_TYPES`
    (data-driven). Returns per-type ``{type: {total, ok, error}}`` plus overall
    ``total``, ``ok``, ``error`` and a ``success_rate`` (ok / total, or None when
    there are no system events). ``status`` is treated as an error whenever it is
    the literal ``"error"``; everything else counts as ok. Empty-safe.
    """
    events = query_events(settings, since=since, until=until)

    by_type: dict[str, dict[str, int]] = {}
    total = ok = error = 0
    for e in events:
        if not _is_system_event(e):
            continue
        etype = e.get("event_type") or _NONE_KEY
        bucket = by_type.setdefault(etype, {"total": 0, "ok": 0, "error": 0})
        is_error = e.get("status") == "error"
        bucket["total"] += 1
        total += 1
        if is_error:
            bucket["error"] += 1
            error += 1
        else:
            bucket["ok"] += 1
            ok += 1

    success_rate = (ok / total) if total > 0 else None
    return {
        "by_type": by_type,
        "total": total,
        "ok": ok,
        "error": error,
        "success_rate": success_rate,
    }


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
    falls back to day. ``by="type"`` and ``by="vault"`` count ALL events (system
    + agent) so no data is lost. ``by="agent"`` EXCLUDES system events (#205) so
    the by-agent chart shows real runtimes only. ``agent`` (when set) scopes the
    events to that single normalized agent (#199).
    """
    by = by if by in {"type", "agent", "vault"} else "type"
    events = query_events(settings, since=since, until=until, agent=agent)
    buckets: dict[str, dict[str, int]] = {}
    for event in events:
        day = (event.get("ts") or "")[:10]
        if not day:
            continue
        if by == "agent" and _is_system_event(event):
            continue
        key = _dimension_value(event, by)
        if by == "agent" and _is_test_agent(key):
            continue
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
