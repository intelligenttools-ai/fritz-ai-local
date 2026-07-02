"""Route + feature-preservation tests for the multi-page UI shell (#220).

The old single-page /dashboard was split into an /ui/ app shell (#220). These
tests assert:

- /dashboard now 307-redirects to /ui/.
- Each /ui/* page returns 200 HTML (clean paths AND the .html form).
- The shared assets (CSS + JS modules) are served under /ui/shared/.
- Every feature from the old dashboard still lives on some page (no feature
  loss): auth/token flow, auto-refresh (#195), actions (#196), theme (#197),
  SSE (#198), agent drill-down (#199), system panel (#205), chart toggle (#207),
  config panel (#208), and the XSS esc() guard.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from fritz_local_brain.app import create_app


def _client() -> TestClient:
    return TestClient(create_app())


# ---------------------------------------------------------------------------
# /dashboard now redirects to the /ui/ shell
# ---------------------------------------------------------------------------

def test_dashboard_redirects_to_ui() -> None:
    """GET /dashboard must 307-redirect to /ui/ (no HTML served directly)."""
    resp = _client().get("/dashboard", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/ui/"


def test_dashboard_redirect_needs_no_auth() -> None:
    """The redirect itself must not require a token."""
    resp = _client().get("/dashboard", follow_redirects=False)  # no headers
    assert resp.status_code == 307


# ---------------------------------------------------------------------------
# Each /ui/* page returns 200 HTML (clean path form)
# ---------------------------------------------------------------------------

def test_ui_root_serves_index() -> None:
    resp = _client().get("/ui/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "<title>" in resp.text


def test_ui_pages_return_200_html_clean_paths() -> None:
    """/ui/activity, /ui/agents, /ui/operations, /ui/settings, /ui/knowledge
    must each return 200 HTML on the clean (no-.html) path."""
    client = _client()
    for path in ("/ui/activity", "/ui/agents", "/ui/operations",
                 "/ui/settings", "/ui/knowledge"):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"
        assert "text/html" in resp.headers["content-type"], path
        assert "<title>" in resp.text, path


def test_ui_pages_also_served_as_html_suffix() -> None:
    """The StaticFiles mount also serves the .html form (deep-link robustness)."""
    client = _client()
    for path in ("/ui/index.html", "/ui/activity.html", "/ui/settings.html"):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"


def test_ui_pages_need_no_auth() -> None:
    """The page shells are unauthenticated (the token is supplied client-side)."""
    resp = _client().get("/ui/activity")  # no headers
    assert resp.status_code == 200


def test_ui_unknown_page_404() -> None:
    resp = _client().get("/ui/does-not-exist")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Shared assets under /ui/shared/
# ---------------------------------------------------------------------------

def test_ui_shared_css_served() -> None:
    resp = _client().get("/ui/shared/app.css")
    assert resp.status_code == 200
    assert "text/css" in resp.headers["content-type"]
    assert ":root" in resp.text  # the CSS-var theme block


def test_ui_shared_js_modules_served() -> None:
    client = _client()
    for path in ("/ui/shared/api.js", "/ui/shared/nav.js", "/ui/shared/sse.js"):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"
        assert "javascript" in resp.headers["content-type"].lower(), path


# ---------------------------------------------------------------------------
# Feature preservation — auth / token flow (shared api.js + every page shell)
# ---------------------------------------------------------------------------

def _api_js() -> str:
    return _client().get("/ui/shared/api.js").text


def test_shared_has_html_escape_helper() -> None:
    """Stored-XSS guard: the esc() helper must be present in the shared toolbox."""
    assert "function esc(" in _api_js()


def test_shared_token_flow_uses_sessionstorage_bearer() -> None:
    """Token auth unchanged: sessionStorage bearer + Bearer header."""
    js = _api_js()
    assert "sessionStorage" in js
    assert "Bearer ${token}" in js
    assert "function saveToken(" in js
    assert "function apiFetch(" in js


def test_shared_savetoken_dismisses_auth_overlay() -> None:
    """saveToken() must hide the auth overlay after storing the token (#193)."""
    js = _api_js()
    start = js.index("function saveToken(")
    end = js.index("}", start)
    assert "hideAuthOverlay()" in js[start:end]


def test_shared_post_helper_supports_method_param() -> None:
    """The shared fetch helper accepts a method arg (defaulting to POST)."""
    assert 'function postAction(path, body, method = "POST")' in _api_js()


def test_every_page_has_auth_overlay_and_token_input() -> None:
    client = _client()
    for path in ("/ui/", "/ui/activity", "/ui/agents", "/ui/operations",
                 "/ui/settings", "/ui/knowledge"):
        body = client.get(path).text
        assert 'id="auth-overlay"' in body, path
        assert 'id="token-input"' in body, path
        assert "/ui/shared/api.js" in body, path


# ---------------------------------------------------------------------------
# Feature preservation — auto-refresh (#195) on data pages
# ---------------------------------------------------------------------------

def test_auto_refresh_wiring_in_shared_and_pages() -> None:
    js = _api_js()
    assert "visibilitychange" in js
    assert "localStorage" in js
    assert "function startRefreshTimer(" in js
    # The data pages expose the auto-refresh select.
    body = _client().get("/ui/").text
    assert "auto-refresh-select" in body


# ---------------------------------------------------------------------------
# Feature preservation — actions (#196) live on the Operations page
# ---------------------------------------------------------------------------

def _ops() -> str:
    return _client().get("/ui/operations").text


def test_operations_actions_panel_and_endpoints() -> None:
    body = _ops()
    assert 'id="actions-panel"' in body
    assert "/v1/compile/run" in body
    assert "/v1/sync/run" in body
    assert "/v1/embeddings/index/run" in body
    assert "/v1/lint/run" in body


def test_operations_approval_gate_and_toast() -> None:
    body = _ops()
    assert 'id="approval-gate"' in body
    assert 'id="approval-token-input"' in body
    assert 'id="action-toast"' in body


def test_operations_recent_runs_table() -> None:
    body = _ops()
    assert "/v1/runs/recent" in body
    assert 'id="runs-table"' in body


def test_operations_uses_shared_post_helper() -> None:
    """The action handlers must reuse the shared postAction() helper (no inline
    duplication of the Bearer-token POST)."""
    body = _ops()
    assert "postAction(" in body
    assert "/ui/shared/api.js" in body


# ---------------------------------------------------------------------------
# Feature preservation — theme toggle (#197)
# ---------------------------------------------------------------------------

def test_theme_toggle_present() -> None:
    js = _api_js()
    assert "function toggleTheme(" in js
    css = _client().get("/ui/shared/app.css").text
    assert 'data-theme="light"' in css
    assert 'id="theme-toggle"' in _client().get("/ui/").text


# ---------------------------------------------------------------------------
# Feature preservation — SSE live updates (#198) on the Activity page
# ---------------------------------------------------------------------------

def _sse_js() -> str:
    return _client().get("/ui/shared/sse.js").text


def test_sse_uses_stream_ticket_and_eventsource() -> None:
    js = _sse_js()
    assert "EventSource" in js
    assert "/v1/usage/stream-ticket" in js
    assert "/v1/usage/stream?ticket=" in js


def test_sse_cleanup_and_fallback() -> None:
    js = _sse_js()
    assert "beforeunload" in js
    assert "closeSSE(" in js
    assert 'addEventListener("error"' in js
    assert "_sseRetried" in js


def test_activity_page_loads_sse_module() -> None:
    body = _client().get("/ui/activity").text
    assert "/ui/shared/sse.js" in body


# ---------------------------------------------------------------------------
# Feature preservation — agent drill-down (#199) on the Agents page
# ---------------------------------------------------------------------------

def _agents() -> str:
    return _client().get("/ui/agents").text


def test_agents_drilldown_selector_and_endpoint() -> None:
    body = _agents()
    assert "/v1/usage/agents" in body
    assert 'id="agent-filter-select"' in body
    assert "agentParams(" in body
    assert "agent:" in body


def test_agents_agent_id_escaped_in_selector() -> None:
    """XSS guard: agent ids (untrusted telemetry) esc()'d in the selector."""
    body = _agents()
    start = body.index("function populateAgentFilter(")
    end = body.index("function onAgentFilterChange(", start)
    assert "esc(a.agent)" in body[start:end]


# ---------------------------------------------------------------------------
# Feature preservation — Knowledge Base Health (regression for B1)
#
# The old dashboard's KB-health section (#182) — articles-by-status chart,
# embedding/compile summary cards, and the growth chart — was fed by
# /v1/usage/knowledge. It must survive the split. It is GLOBAL KB state, NOT
# agent-scoped, so it lives on the Overview page and must not thread an agent
# param into the /v1/usage/knowledge fetch.
# ---------------------------------------------------------------------------

def _overview() -> str:
    return _client().get("/ui/").text


def test_overview_has_kb_health_markup_and_endpoint() -> None:
    body = _overview()
    assert "/v1/usage/knowledge" in body, "KB-health fetch dropped"
    assert 'id="kb-status-chart"' in body
    assert 'id="kb-cards"' in body
    assert 'id="kb-growth-chart"' in body
    assert "function renderKnowledge(" in body
    assert "Knowledge Base Health" in body


def test_overview_kb_health_is_not_agent_scoped() -> None:
    """KB-health is global state — the knowledge fetch must NOT pass an agent
    param, and the page must not carry an agent-filter control."""
    body = _overview()
    assert 'apiFetch("/v1/usage/knowledge")' in body, (
        "knowledge must be fetched without params (global, never agent-scoped)"
    )
    assert 'id="agent-filter-select"' not in body, (
        "Overview must not add an agent filter — KB-health is global"
    )


def test_overview_kb_health_escapes_status_labels() -> None:
    """XSS guard: article-status labels (telemetry-stored) esc()'d in the chart."""
    body = _overview()
    start = body.index("function renderKnowledge(")
    end = body.index("async function loadAll(", start)
    fn = body[start:end]
    assert "esc(item.label)" in fn, "KB status labels not escaped"


# ---------------------------------------------------------------------------
# Feature preservation — per-agent Activity timeline drill-down (regression B2)
#
# The old header agent-filter scoped the /v1/usage/activity chart. The Activity
# page must carry the agent-filter control AND thread the selected agent into
# the activity fetch params.
# ---------------------------------------------------------------------------

def test_activity_has_agent_filter_control() -> None:
    body = _client().get("/ui/activity").text
    assert 'id="agent-filter-select"' in body, "activity agent-filter control missing"
    assert "/v1/usage/agents" in body, "activity must discover agents for the filter"
    assert "function populateAgentFilter(" in body
    assert "function onAgentFilterChange(" in body


def test_activity_threads_agent_param_into_activity_fetch() -> None:
    body = _client().get("/ui/activity").text
    assert "agentParams(" in body, "agentParams helper missing on Activity"
    # The activity fetch must spread the agent param alongside the `by` grouping.
    assert "by: _activityBy, ...agentParams()" in body, (
        "activity fetch does not thread the selected agent"
    )


def test_activity_agent_id_escaped_in_selector() -> None:
    """XSS guard: agent ids esc()'d in the Activity selector too."""
    body = _client().get("/ui/activity").text
    start = body.index("function populateAgentFilter(")
    end = body.index("function onAgentFilterChange(", start)
    assert "esc(a.agent)" in body[start:end]


# ---------------------------------------------------------------------------
# Feature preservation — system activity panel (#205) on the Activity page
# ---------------------------------------------------------------------------

def test_activity_system_panel() -> None:
    body = _client().get("/ui/activity").text
    assert "/v1/usage/system" in body
    assert 'id="system-panel"' in body
    assert 'id="system-activity"' in body
    assert "function renderSystem(" in body
    assert "System activity" in body
    # XSS guard: system event type esc()'d.
    start = body.index("function renderSystem(")
    assert "esc(type)" in body[start:]


# ---------------------------------------------------------------------------
# Feature preservation — chart toggle + stacked-area chart (#207)
# ---------------------------------------------------------------------------

def test_activity_chart_toggle_present() -> None:
    body = _client().get("/ui/activity").text
    assert 'id="activity-by-toggle"' in body
    assert 'data-by="agent"' in body
    assert 'data-by="vault"' in body
    assert "function setActivityBy(" in body


def test_shared_time_chart_stacked_series_and_escaping() -> None:
    js = _api_js()
    start = js.index("function renderTimeChart(")
    fn = js[start:]
    assert "tc-legend" in fn
    assert "seriesData" in fn
    assert "seriesKeys" in fn
    assert "MAX_SERIES" in fn
    assert '"other"' in fn
    assert "esc(sk)" in fn   # legend key escaping
    assert "esc(k)" in fn    # tooltip key escaping
    assert "esc(day)" in fn  # day label escaping


# ---------------------------------------------------------------------------
# Feature preservation — configuration panel (#208) on the Settings page
# ---------------------------------------------------------------------------

def _settings() -> str:
    return _client().get("/ui/settings").text


def test_settings_config_panel() -> None:
    body = _settings()
    assert 'id="config-panel"' in body
    assert 'id="config-runtime"' in body
    assert 'id="config-rebuild"' in body
    assert ">Configuration<" in body


def test_settings_config_endpoint_and_handlers() -> None:
    body = _settings()
    assert "/v1/config" in body
    assert "function loadConfig(" in body
    assert "function renderConfig(" in body
    assert "function onConfigChange(" in body


def test_settings_config_uses_patch_verb() -> None:
    """Verb guard (#208): the config write must use PATCH, not a bare POST."""
    body = _settings()
    start = body.index("async function onConfigChange(")
    end = body.index("async function loadAll(", start)
    fn = body[start:end]
    assert "confirm(" in fn
    assert "postAction(" in fn
    assert '"PATCH"' in fn
    assert "showToast(" in fn


def test_settings_config_escapes_strings() -> None:
    body = _settings()
    start = body.index("function renderConfig(")
    end = body.index("async function loadConfig(", start)
    assert "esc(" in body[start:end]


# ---------------------------------------------------------------------------
# Hard constraint — dependency-free (no external script/link/font/CDN)
# ---------------------------------------------------------------------------

def test_ui_pages_are_dependency_free() -> None:
    """No remote document fetches: only same-origin /ui/shared/ assets are
    referenced. No CDN host, no external URL, no @import."""
    client = _client()
    for path in ("/ui/", "/ui/activity", "/ui/agents", "/ui/operations",
                 "/ui/settings", "/ui/knowledge"):
        body = client.get(path).text
        lowered = body.lower()
        assert "//cdn" not in lowered, path
        assert "https://" not in body and "http://" not in body, path
        assert "@import" not in lowered, path


def test_ui_shell_svg_charts_present() -> None:
    """Charts remain hand-drawn inline SVG (no charting library)."""
    js = _api_js()
    assert "function sparkline(" in js
    assert "function renderTimeChart(" in js
    assert 'class="data-line"' in js


# ---------------------------------------------------------------------------
# Old single-file dashboard.html is gone
# ---------------------------------------------------------------------------

def test_old_dashboard_html_removed() -> None:
    """The monolithic dashboard.html must be deleted — its content now lives on
    the /ui/ pages."""
    from pathlib import Path

    from fritz_local_brain import app as app_module

    old = Path(app_module.__file__).parent / "static" / "dashboard.html"
    assert not old.exists(), "dashboard.html should have been deleted (#220)"
