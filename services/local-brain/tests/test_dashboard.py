"""Smoke tests for the /dashboard route (#182).

Acceptance:
- GET /dashboard returns 200 with content-type text/html.
- The body contains the expected title marker and references to the usage endpoints.
- No auth token required (the route is unauthenticated).
- The served file exists on disk and is non-empty.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from fritz_local_brain.app import create_app, _DASHBOARD


def _client() -> TestClient:
    return TestClient(create_app())


def test_dashboard_returns_200() -> None:
    resp = _client().get("/dashboard")
    assert resp.status_code == 200


def test_dashboard_content_type_is_html() -> None:
    resp = _client().get("/dashboard")
    assert "text/html" in resp.headers["content-type"]


def test_dashboard_contains_title() -> None:
    resp = _client().get("/dashboard")
    assert "<title>" in resp.text


def test_dashboard_references_usage_summary() -> None:
    resp = _client().get("/dashboard")
    assert "v1/usage/summary" in resp.text


def test_dashboard_references_usage_activity() -> None:
    resp = _client().get("/dashboard")
    assert "v1/usage/activity" in resp.text


def test_dashboard_no_auth_required() -> None:
    """GET /dashboard without any Authorization header must still return 200."""
    client = TestClient(create_app())
    resp = client.get("/dashboard")  # no headers
    assert resp.status_code == 200


def test_dashboard_file_exists_and_is_nonempty() -> None:
    assert _DASHBOARD.exists(), f"dashboard.html not found at {_DASHBOARD}"
    assert _DASHBOARD.stat().st_size > 0, "dashboard.html is empty"


def test_dashboard_has_html_escape_helper() -> None:
    """Regression guard for stored XSS: agent-supplied telemetry strings (query
    text, agent ids, vault names) are interpolated into innerHTML and MUST be
    HTML-escaped. A real DOM XSS test isn't feasible at the Python layer, so we
    assert the escape helper is present in the served body — if escaping is
    dropped, this fails."""
    resp = _client().get("/dashboard")
    assert "function esc(" in resp.text


def test_savetoken_dismisses_auth_overlay() -> None:
    """Regression guard (#193): saveToken() must hide the auth overlay after
    storing the token, otherwise the overlay stays up after a valid token is
    entered. A DOM test isn't feasible at the Python layer, so assert the
    saveToken function body calls hideAuthOverlay()."""
    body = _client().get("/dashboard").text
    start = body.index("function saveToken(")
    end = body.index("}", start)
    save_token_body = body[start:end]
    assert "hideAuthOverlay()" in save_token_body


def test_dashboard_auto_refresh_feature() -> None:
    """Regression guard (#195): auto-refresh control, visibility-pause, and
    localStorage persistence must all be present in the served dashboard body.
    A regression that removes any of these wiring points will fail this test."""
    body = _client().get("/dashboard").text
    assert "auto-refresh-select" in body, "auto-refresh select element id missing"
    assert "visibilitychange" in body, "visibilitychange listener missing"
    assert "localStorage" in body, "localStorage persistence missing"


# ---------------------------------------------------------------------------
# Actions panel (#196)
# ---------------------------------------------------------------------------

def test_dashboard_actions_panel_container() -> None:
    """The Actions section must be present with the expected container id."""
    body = _client().get("/dashboard").text
    assert 'id="actions-panel"' in body, "actions-panel container id missing"


def test_dashboard_actions_post_helper() -> None:
    """postAction helper must be declared in the served body."""
    body = _client().get("/dashboard").text
    assert "function postAction(" in body, "postAction function missing"


def test_dashboard_actions_compile_endpoint() -> None:
    body = _client().get("/dashboard").text
    assert "/v1/compile/run" in body, "/v1/compile/run endpoint reference missing"


def test_dashboard_actions_sync_endpoint() -> None:
    body = _client().get("/dashboard").text
    assert "/v1/sync/run" in body, "/v1/sync/run endpoint reference missing"


def test_dashboard_actions_embeddings_endpoint() -> None:
    body = _client().get("/dashboard").text
    assert "/v1/embeddings/index/run" in body, "/v1/embeddings/index/run endpoint reference missing"


def test_dashboard_actions_lint_endpoint() -> None:
    body = _client().get("/dashboard").text
    assert "/v1/lint/run" in body, "/v1/lint/run endpoint reference missing"


def test_dashboard_actions_approval_token_input() -> None:
    """Approval-token input must be present for the large-batch approval retry flow."""
    body = _client().get("/dashboard").text
    assert 'id="approval-token-input"' in body, "approval-token-input id missing"


def test_dashboard_actions_approval_gate() -> None:
    """Approval gate container must be present."""
    body = _client().get("/dashboard").text
    assert 'id="approval-gate"' in body, "approval-gate container id missing"


def test_dashboard_actions_toast() -> None:
    """Action result toast must be present."""
    body = _client().get("/dashboard").text
    assert 'id="action-toast"' in body, "action-toast element id missing"


# ---------------------------------------------------------------------------
# Per-agent drill-down (#199)
# ---------------------------------------------------------------------------

def test_dashboard_references_usage_agents() -> None:
    """The served body must fetch the per-agent discovery endpoint."""
    body = _client().get("/dashboard").text
    assert "/v1/usage/agents" in body, "/v1/usage/agents endpoint reference missing"


def test_dashboard_has_agent_selector() -> None:
    """An agent-selector element id must be present in the served body."""
    body = _client().get("/dashboard").text
    assert 'id="agent-filter-select"' in body, "agent-filter-select element id missing"


def test_dashboard_passes_agent_param() -> None:
    """The client must pass an `agent` param when an agent is selected (the
    agentParams() helper feeding the scoped fetches)."""
    body = _client().get("/dashboard").text
    assert "agentParams(" in body, "agentParams helper missing"
    assert "agent:" in body, "agent param not passed to fetches"


def test_dashboard_agent_id_escaped_in_selector() -> None:
    """XSS guard: agent ids (telemetry-stored, untrusted) must be esc()'d when
    built into the selector options innerHTML."""
    body = _client().get("/dashboard").text
    start = body.index("function populateAgentFilter(")
    end = body.index("function onAgentFilterChange(", start)
    fn = body[start:end]
    assert "esc(a.agent)" in fn, "agent id not escaped in selector"


def test_dashboard_actions_esc_used_in_post_results() -> None:
    """XSS guard: response-derived strings in action handlers must go through esc().
    Check that esc() calls appear in the postAction / action-handler block."""
    body = _client().get("/dashboard").text
    # The action handlers use esc() for error messages and mode strings.
    # Count occurrences — there must be more than just the original renderBarChart uses.
    assert body.count("esc(") >= 10, "too few esc() calls — XSS guard may have regressed"


# ---------------------------------------------------------------------------
# Live updates via SSE (#198)
# ---------------------------------------------------------------------------

def test_dashboard_uses_eventsource() -> None:
    """The served body must open an EventSource for live updates."""
    body = _client().get("/dashboard").text
    assert "EventSource" in body, "EventSource reference missing"


def test_dashboard_references_stream_ticket_endpoint() -> None:
    """The client must exchange the Bearer token for a short-lived stream ticket."""
    body = _client().get("/dashboard").text
    assert "/v1/usage/stream-ticket" in body, "stream-ticket endpoint reference missing"


def test_dashboard_references_stream_endpoint() -> None:
    body = _client().get("/dashboard").text
    assert "/v1/usage/stream?ticket=" in body, "stream endpoint (with ticket) reference missing"


def test_dashboard_sse_cleanup_on_unload() -> None:
    """SSE connections must be closed on page unload so they don't leak."""
    body = _client().get("/dashboard").text
    assert "beforeunload" in body, "beforeunload cleanup listener missing"
    assert "closeSSE(" in body, "closeSSE cleanup helper missing"


def test_dashboard_sse_falls_back_to_polling() -> None:
    """On SSE error the client must keep polling as the backstop (#195 timer).
    Assert the error handler closes the source rather than spamming reconnects."""
    body = _client().get("/dashboard").text
    start = body.index("function setupSSE(")
    end = body.index("window.addEventListener(\"beforeunload\"", start)
    fn = body[start:end]
    assert 'addEventListener("error"' in fn, "SSE error handler missing"
    assert "_sseRetried" in fn, "single-reconnect guard missing"


# ---------------------------------------------------------------------------
# Visual overhaul (#197): inline-SVG charts, states, theme toggle.
# Dependency-free by construction — no CDN/library/asset is permitted.
# ---------------------------------------------------------------------------

def test_dashboard_has_inline_svg_charts() -> None:
    """The overhaul replaces div-bar time-series with hand-drawn inline SVG.
    The served body must contain inline <svg> markup (no charting lib)."""
    body = _client().get("/dashboard").text
    assert "<svg" in body, "inline SVG charts missing"


def test_dashboard_has_svg_chart_render_helpers() -> None:
    """The new SVG renderers (sparkline + time-series line/area) must be present."""
    body = _client().get("/dashboard").text
    assert "function sparkline(" in body, "sparkline SVG helper missing"
    assert "function renderTimeChart(" in body, "renderTimeChart helper missing"
    # The line/area chart emits <path> elements for the line and fill.
    assert "class=\"data-line\"" in body, "SVG data line class missing"


def test_dashboard_has_error_and_empty_states() -> None:
    """Endpoints returning null/500 must surface an error card, and empty data
    must show a 'No data' message rather than a broken panel."""
    body = _client().get("/dashboard").text
    assert "function renderError(" in body, "renderError state helper missing"
    assert "error-state" in body, "error-state styling missing"
    assert "No data yet" in body, "empty-state messaging missing"
    assert "skeleton" in body, "loading skeleton missing"


def test_dashboard_has_theme_toggle() -> None:
    """The optional light/dark toggle must be wired and persist its choice."""
    body = _client().get("/dashboard").text
    assert 'id="theme-toggle"' in body, "theme-toggle control missing"
    assert "function toggleTheme(" in body, "toggleTheme handler missing"
    assert 'data-theme="light"' in body, "light theme variant missing"


def test_dashboard_chart_tooltips_are_escaped() -> None:
    """XSS guard for new chart code: per-day breakdown tooltips built into SVG
    innerHTML must esc() the untrusted bucket keys."""
    body = _client().get("/dashboard").text
    start = body.index("function renderTimeChart(")
    end = body.index("function ", start + 1)
    fn = body[start:end]
    assert "esc(k)" in fn, "bucket keys not escaped in chart tooltips"
    assert "esc(day)" in fn, "day label not escaped in chart tooltips"


def test_dashboard_is_dependency_free() -> None:
    """Hard constraint: no external script/link/font references. The dashboard
    must remain a single self-contained offline file."""
    body = _client().get("/dashboard").text
    lowered = body.lower()
    # No remote document fetches of any kind (cdn hosts, fonts, etc.).
    assert "//cdn" not in lowered, "unexpected CDN reference"
    assert "https://" not in body and "http://" not in body, "external URL reference present"
    assert "<script src=" not in lowered, "external script tag present"
    assert "<link " not in lowered, "external link tag present"
    assert "@import" not in lowered, "external CSS import present"


# ---------------------------------------------------------------------------
# Stacked area chart / multi-series renderTimeChart (#207)
# ---------------------------------------------------------------------------

def _render_time_chart_fn(body: str) -> str:
    """Extract the renderTimeChart function body from the dashboard source."""
    start = body.index("function renderTimeChart(")
    end = body.index("\nfunction ", start + 1)
    return body[start:end]


def test_dashboard_time_chart_has_legend_container() -> None:
    """renderTimeChart must emit a legend container so the toggle is visually
    distinguishable when the grouping dimension changes."""
    body = _client().get("/dashboard").text
    fn = _render_time_chart_fn(body)
    assert "tc-legend" in fn, "legend container class 'tc-legend' missing from renderTimeChart"


def test_dashboard_time_chart_iterates_per_key() -> None:
    """renderTimeChart must iterate over per-key counts in each day's bucket —
    not just collapse each day to a single total — so multi-series stacking
    actually reflects the chosen grouping dimension."""
    body = _client().get("/dashboard").text
    fn = _render_time_chart_fn(body)
    # The multi-series path builds seriesData by iterating keys in each bucket.
    assert "seriesData" in fn, "seriesData (per-key series matrix) missing"
    assert "seriesKeys" in fn, "seriesKeys (distinct grouping keys) missing"


def test_dashboard_time_chart_top_n_fold() -> None:
    """renderTimeChart must cap the number of series and fold the remainder into
    an 'other' series so the chart stays legible with many distinct keys."""
    body = _client().get("/dashboard").text
    fn = _render_time_chart_fn(body)
    assert "MAX_SERIES" in fn, "MAX_SERIES cap missing — no top-N fold"
    assert '"other"' in fn, "'other' fold bucket label missing"


def test_dashboard_time_chart_esc_keys_in_legend() -> None:
    """XSS guard: series keys (type/agent/vault values from telemetry) must be
    esc()'d wherever they appear in the legend innerHTML."""
    body = _client().get("/dashboard").text
    fn = _render_time_chart_fn(body)
    # The legend label must escape the key before inserting it into innerHTML.
    assert "esc(sk)" in fn, "series key not esc()'d in legend"


def test_dashboard_time_chart_esc_keys_in_tooltip() -> None:
    """XSS guard: series keys must be esc()'d in hover tooltip strings too."""
    body = _client().get("/dashboard").text
    fn = _render_time_chart_fn(body)
    # tooltip breakdown loop uses esc(k) for each series key
    assert "esc(k)" in fn, "series key not esc()'d in tooltip"
    assert "esc(day)" in fn, "day label not esc()'d in tooltip"


def test_dashboard_time_chart_legend_css_present() -> None:
    """The legend CSS classes must be defined in the dashboard stylesheet."""
    body = _client().get("/dashboard").text
    assert ".tc-legend" in body, ".tc-legend CSS rule missing"
    assert ".tc-legend-swatch" in body, ".tc-legend-swatch CSS rule missing"
