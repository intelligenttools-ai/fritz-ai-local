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
    for path in ("/ui/index.html", "/ui/activity.html", "/ui/settings.html",
                 "/ui/agents.html", "/ui/operations.html", "/ui/knowledge.html"):
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
    assert "/v1/runs" in body
    assert 'id="runs-table"' in body


def test_operations_uses_shared_post_helper() -> None:
    """The action handlers must reuse the shared postAction() helper (no inline
    duplication of the Bearer-token POST)."""
    body = _ops()
    assert "postAction(" in body
    assert "/ui/shared/api.js" in body


# ---------------------------------------------------------------------------
# Run detail drill-down (#224): GET /v1/runs + ?run= deep link
# ---------------------------------------------------------------------------

def test_operations_run_detail_view_and_endpoint() -> None:
    """The run-detail view fetches GET /v1/runs/{id} and is deep-linkable."""
    body = _ops()
    assert 'id="run-detail"' in body
    assert "/v1/runs/" in body
    assert "function loadRun(" in body
    assert "popstate" in body
    assert "data-run-id" in body


def test_operations_run_detail_escapes_untrusted_fields() -> None:
    """XSS guard: the run-detail renderer must esc() EACH untrusted field it
    renders (not merely contain esc() somewhere). If any of these fields is
    later interpolated unescaped, the matching assertion here must fail."""
    body = _ops()
    start = body.index("function renderRunDetail(")
    end = body.index("\n}", start)
    fn = body[start:end]
    for token in (
        "esc(r.kind",
        "esc(r.id",
        "esc(r.status",
        "esc(r.source",
        "esc(r.started_at",
        "esc(r.finished_at",
        "esc(r.summary",
        "esc(e)",  # each error message in the errors.map(...)
    ):
        assert token in fn, f"run detail must escape via {token}"


def test_operations_run_detail_shows_llm_model() -> None:
    """#256: the run-detail view must display the effective LLM model (+
    base_url/protocol if present) recorded on a compile run's ``detail``,
    esc()'d like every other untrusted field."""
    body = _ops()
    start = body.index("function renderRunDetail(")
    end = body.index("\n}", start)
    fn = body[start:end]
    # All three LLM fields are rendered, each esc()'d.
    assert "esc(detail.llm_model" in fn
    assert "esc(detail.llm_base_url" in fn
    assert "esc(detail.llm_protocol" in fn
    # And they're excluded from the generic detail loop (via the llmFields Set)
    # so they aren't double-rendered — which would risk an unescaped path.
    assert "llmFields" in fn
    assert "!llmFields.has(k)" in fn


def test_operations_runs_table_kind_filter() -> None:
    """The runs table is backed by GET /v1/runs with an optional kind filter."""
    body = _ops()
    assert "/v1/runs" in body
    assert "kind" in body


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
# Per-agent drill-down (#224): GET /v1/usage/agents/{agent} + ?agent= deep link
# ---------------------------------------------------------------------------

def test_agents_drilldown_detail_view_and_endpoint() -> None:
    """Agent cards drill into a detail view backed by GET /v1/usage/agents/{agent}."""
    body = _agents()
    assert "/v1/usage/agents/" in body
    assert "function loadAgent(" in body
    assert "writeQuery" in body
    assert "data-agent" in body


def test_agents_detail_escapes_untrusted_fields() -> None:
    """XSS guard: the agent-detail + recent-events renderers must esc() EACH
    untrusted field they render (not merely contain esc() somewhere). The run
    cross-link id must be BOTH encodeURIComponent()'d (URL) and esc()'d
    (attribute). If any field is later interpolated unescaped, the matching
    assertion here must fail."""
    body = _agents()

    detail_start = body.index("function renderAgentDetail(")
    detail_end = body.index("\n}", detail_start)
    detail_fn = body[detail_start:detail_end]
    for token in ("esc(d.agent", "esc(d.first_seen", "esc(d.last_seen"):
        assert token in detail_fn, f"agent detail must escape via {token}"

    ev_start = body.index("function renderAgentEvents(")
    ev_end = body.index("\n}", ev_start)
    ev_fn = body[ev_start:ev_end]
    for token in ("esc(e.ts", "esc(e.event_type", "esc(e.vault", "esc(e.status"):
        assert token in ev_fn, f"agent recent-events must escape via {token}"
    # The run cross-link id is untrusted → encodeURIComponent for URL correctness
    # AND esc() for the attribute-context safety of the href.
    assert "encodeURIComponent(e.run_id" in ev_fn
    assert "esc(encodeURIComponent(e.run_id" in ev_fn


def test_agents_recent_events_link_to_run_detail() -> None:
    """recent_events rows with a run_id must cross-link to Operations run detail."""
    body = _agents()
    assert "/ui/operations?run=" in body


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
# Feature preservation + expansion — settings page config editor (#208 -> #227)
# ---------------------------------------------------------------------------

def _settings() -> str:
    return _client().get("/ui/settings").text


def test_settings_config_panel() -> None:
    body = _settings()
    assert 'id="config-panel"' in body
    assert 'id="config-groups"' in body
    assert ">Configuration<" in body


def test_settings_config_endpoint_and_handlers() -> None:
    body = _settings()
    assert "/v1/config" in body
    assert "function loadConfig(" in body
    assert "function renderConfig(" in body
    assert "function saveGroup(" in body


def test_settings_config_uses_patch_verb() -> None:
    """Verb guard (#208): the config write must use PATCH, not a bare POST."""
    body = _settings()
    start = body.index("async function saveGroup(")
    end = body.index("// ---- connection tests", start)
    fn = body[start:end]
    assert "postAction(" in fn
    assert '"PATCH"' in fn
    assert "showToast(" in fn


def _config_render_src(body: str) -> str:
    """The config-render function bodies (controls -> renderConfig), where all
    server data is turned into innerHTML. Handlers live below this slice."""
    start = body.index("// ---- controls")
    end = body.index("// ---- delegated listeners")
    return body[start:end]


def test_settings_config_escapes_strings() -> None:
    """XSS guard (strengthened, #227 review): every untrusted sink in the config
    render path is wrapped in esc() — asserted per-sink, not just 'esc appears'."""
    body = _settings()
    render = _config_render_src(body)
    # field value (text control), field-level rejected reason, service status value:
    assert "esc(v == null" in render           # field value -> input value=
    assert "esc(fieldErrors[name])" in render   # server rejection reason
    assert "esc(shown)" in render               # service read-only value
    # whole-request error (PATCH .detail is stored in groupErrors then rendered):
    assert "esc(groupErrors[g.id])" in render
    # requires indicator sourced from metadata is esc()'d too:
    assert "esc(field.requires)" in render


def test_settings_no_interpolated_inline_handlers() -> None:
    """#227 review FIX 1: the config render path must use delegated listeners, not
    inline on*= handlers with interpolated data (esc() does NOT sanitize inside a
    JS-handler-source attribute)."""
    body = _settings()
    render = _config_render_src(body)
    for attr in ("onclick=", "oninput=", "onchange=", "onkeyup=", "onkeydown="):
        assert attr not in render, f"inline handler {attr} in config render path"
    # Positive: behaviour is wired through data-* + delegated listeners.
    assert "data-action=" in render
    assert "data-field=" in render
    assert 'el.addEventListener("click", onConfigClick)' in body
    assert 'el.addEventListener("input", onConfigInput)' in body


def test_settings_secret_never_serialized_into_value_attr() -> None:
    """#227 review FIX 2: the Replace <input> must carry NO value= attribute, and
    the key must never be stored in `pending` — it is read from the live element
    at save/test time only."""
    body = _settings()
    start = body.index("function secretControl(")
    end = body.index("function selectControl(", start)
    # Ignore comment lines (which explain WHY there is no value=).
    code_lines = [ln for ln in body[start:end].splitlines() if not ln.lstrip().startswith("//")]
    secret_fn = "\n".join(code_lines)
    # The password input has no value= binding at all (no key serialized to HTML).
    assert "value=" not in secret_fn
    assert 'type="password"' in secret_fn
    # Save/test read the key from the live element, not from a stored copy.
    assert 'document.getElementById("secret-input-" + f)' in body
    assert "secret-input-${prefix}_api_key" in body
    # onSecretInput stores only the Symbol marker, never the typed value.
    s2 = body.index("function onSecretInput(")
    e2 = body.index("function revealSecretInput(", s2)
    on_secret = body[s2:e2]
    assert "SECRET_PENDING" in on_secret
    assert "pending[name] = value" not in on_secret
    assert "pending[name] = inp.value" not in on_secret


def test_settings_autorefresh_skips_active_edit() -> None:
    """#227 review FIX 3: a background poll must not clobber an in-progress edit."""
    body = _settings()
    assert "function isConfigBusy(" in body
    assert "document.activeElement" in body
    assert "revealedSecrets.size" in body
    start = body.index("async function loadConfig(")
    end = body.index("async function loadAll(", start)
    fn = body[start:end]
    assert "isConfigBusy()" in fn
    # Text/number edits are captured on `input` (not just blur) so pending stays
    # current as the user types — the delegated input listener covers them.
    assert 'el.addEventListener("input", onConfigInput)' in body


def test_settings_reindex_notice_cleared_each_save() -> None:
    """#227 review FIX 4: the reindex notice is cleared before each save and only
    re-set when THIS response reports reindex_scheduled === true."""
    body = _settings()
    start = body.index("async function saveGroup(")
    end = body.index("// ---- connection tests", start)
    fn = body[start:end]
    assert "delete groupErrors.__reindex" in fn
    assert "json.reindex_scheduled === true" in fn


def test_settings_five_grouped_sections() -> None:
    """#227: the flat list becomes 5 grouped sections."""
    body = _settings()
    for title in ("LLM", "Embeddings", "Scheduler", "Telemetry", "Service"):
        assert f'"{title}"' in body or f">{title}<" in body or f"{title} (read-only)" in body


def test_settings_group_field_membership() -> None:
    """Each section groups the exact fields specified for #227."""
    body = _settings()
    start = body.index("const GROUPS = [")
    end = body.index("];", start)
    groups_src = body[start:end]
    assert '"llm_protocol", "llm_base_url", "llm_model", "llm_api_key", "llm_timeout_seconds"' in groups_src
    assert '"embedding_enabled", "embedding_protocol", "embedding_base_url", "embedding_model",' in groups_src
    assert '"embedding_api_key", "embedding_timeout_seconds"' in groups_src
    assert '"scheduler_enabled", "interval_minutes", "scheduler_dry_run",' in groups_src
    assert '"scheduler_compile_failure_alarm_threshold", "compile_context_budget_chars",' in groups_src
    assert '"reconciliation_autonomy"' in groups_src
    assert '"telemetry_enabled", "telemetry_store_query_text", "telemetry_retention_days"' in groups_src


def test_settings_test_connection_buttons_hit_test_endpoints() -> None:
    """Per-section Test buttons POST the section's UNSAVED form values."""
    body = _settings()
    assert "/v1/config/test-${prefix}" in body  # built from GROUPS' g.test: "llm" | "embedding"
    assert 'test: "llm"' in body
    assert 'test: "embedding"' in body
    assert "function testConnection(" in body
    start = body.index("async function testConnection(")
    end = body.index("// ---- load ----", start)
    fn = body[start:end]
    assert "pending" in fn  # reads unsaved (dirty) values, not the saved config


def test_settings_test_button_handlers_read_result_fields() -> None:
    body = _settings()
    start = body.index("function setTestResult(")
    end = body.index("async function testConnection(", start)
    fn = body[start:end]
    assert ".ok" in fn
    assert ".latency_ms" in fn
    assert ".error" in fn


def test_settings_secret_ux_for_both_api_keys() -> None:
    """Both llm_api_key AND embedding_api_key get configured/not-set + Replace + Clear."""
    body = _settings()
    assert "function secretControl(" in body
    start = body.index("function secretControl(")
    end = body.index("function selectControl(", start)
    fn = body[start:end]
    assert "configured" in fn
    assert "not set" in fn
    # Replace + Clear are delegated actions (no inline handlers), and their
    # handler functions exist elsewhere in the module.
    assert 'data-action="replace"' in fn
    assert 'data-action="clear"' in fn
    assert "function revealSecretInput(" in body
    assert "function clearSecret(" in body
    # Both secret fields are driven through the SAME generic control (field.secret),
    # not field-name-specific branches, so both get identical treatment.
    assert 'name === "llm_api_key"' not in fn
    assert 'name === "embedding_api_key"' not in fn


def test_settings_dirty_state_per_section() -> None:
    """Save is disabled until something in that section changed, and it re-enables per-group."""
    body = _settings()
    assert "function hasPendingInGroup(" in body
    assert 'id="save-${' in body or 'id="save-' in body
    start = body.index("function groupHtml(")
    end = body.index("function serviceGroupHtml(", start)
    fn = body[start:end]
    assert "hasPendingInGroup(g.id)" in fn
    assert "disabled" in fn


def test_settings_save_reapplies_from_server_response() -> None:
    """Save re-renders from response.config (server-confirmed), not optimistically,
    and handles applied/rejected/reindex_scheduled — not just a bare .detail."""
    body = _settings()
    start = body.index("async function saveGroup(")
    end = body.index("// ---- connection tests", start)
    fn = body[start:end]
    assert "json.config" in fn
    assert "json.applied" in fn
    assert "json.rejected" in fn
    assert "json.reindex_scheduled" in fn
    assert "renderConfig()" in fn


def test_settings_rejected_fields_shown_as_field_errors() -> None:
    body = _settings()
    assert "fieldErrors" in body
    assert "field-error" in body
    start = body.index("async function saveGroup(")
    end = body.index("// ---- connection tests", start)
    fn = body[start:end]
    assert "fieldErrors[fname] = reason" in fn


def test_settings_reindex_notice_surfaced() -> None:
    body = _settings()
    assert "reindex_scheduled" in body
    assert "reindex-notice" in body
    assert "rebuild scheduled" in body


def test_settings_protocol_and_autonomy_selects_exact_values() -> None:
    body = _settings()
    start = body.index("const PROTOCOL_OPTIONS")
    end = body.index("const FLOAT_FIELDS", start)
    src = body[start:end]
    assert '"openai-compatible"' in src
    assert '"anthropic-compatible"' in src
    assert '"apply"' in src
    assert '"propose"' in src


def test_settings_requires_indicator_from_metadata() -> None:
    """Each field row shows requires: runtime|rebuild sourced from field.requires."""
    body = _settings()
    assert "field.requires" in body
    assert "requires: " in body


def test_settings_service_section_read_only_with_guidance() -> None:
    body = _settings()
    assert "install-autostart" in body
    assert "fritz:brain-service-setup" in body
    assert "/v1/status" in body


# ---------------------------------------------------------------------------
# Model discovery — pick the LLM/embedding model from what the gateway exposes
# instead of typing a free-text string (POST /v1/config/{llm|embedding}-models).
# ---------------------------------------------------------------------------

def test_settings_model_discovery_endpoints_wired() -> None:
    """The settings page must POST the candidate gateway values to both model
    discovery endpoints."""
    body = _settings()
    assert "/v1/config/llm-models" in body
    assert "/v1/config/embedding-models" in body
    assert "function listModels(" in body
    # Reuses the shared POST helper (Bearer auth) like testConnection.
    start = body.index("async function listModels(")
    end = body.index("// ---- load ----", start)
    fn = body[start:end]
    assert 'postAction(' in fn
    assert '"POST"' in fn


def test_settings_model_picker_controls_for_both_groups() -> None:
    """Both llm_model and embedding_model render a 'List models' button + a
    populated <select>, driven through the modelControl renderer."""
    body = _settings()
    assert "function modelControl(" in body
    # fieldControl routes BOTH model fields into the picker control.
    assert 'name === "llm_model" || name === "embedding_model"' in body
    start = body.index("function modelControl(")
    end = body.index("function fieldControl(", start)
    fn = body[start:end]
    assert 'data-action="list-models"' in fn
    assert "data-model-prefix" in fn
    assert "data-model-target" in fn
    assert "<select" in fn
    assert "data-model-input" in fn  # free-text input stays the source of truth


def test_settings_model_picker_wired_through_delegated_listeners() -> None:
    """No inline handlers: the List-models button and the model <select> are wired
    via the existing delegated click/input listeners (data-action / data-model-*)."""
    body = _settings()
    assert 'action === "list-models"' in body
    assert "listModels(btn.dataset.modelPrefix)" in body
    assert "function onModelPick(" in body
    assert "dataset.modelTarget" in body


def test_settings_model_picker_handles_empty_list_distinctly() -> None:
    """A reachable gateway that advertises no models must not read as an error
    (no 'HTTP 200' error text) — it shows a distinct 'No models advertised'."""
    body = _settings()
    assert "No models advertised" in body
    # Picking a model registers pending exactly like typing (Save unchanged).
    start = body.index("function onModelPick(")
    end = body.index("async function listModels(", start)
    fn = body[start:end]
    assert "onFieldChange(" in fn


def test_settings_model_ids_escaped() -> None:
    """XSS guard: gateway-supplied model ids are untrusted and must be esc()'d
    before entering the <select> innerHTML."""
    body = _settings()
    start = body.index("async function listModels(")
    end = body.index("// ---- load ----", start)
    fn = body[start:end]
    assert "esc(String(m))" in fn
    # Graceful fallback: an unreachable/endpoint-less gateway keeps free text.
    assert "json.ok" in fn
    assert "json.error" in fn


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


# ---------------------------------------------------------------------------
# BLOCKER 1 regression — XSS sink: tooltip must use textContent not innerHTML
# ---------------------------------------------------------------------------

def test_chart_tip_sink_uses_textcontent_not_innerhtml() -> None:
    """bindChartTip() must read data-tip via textContent (safe), not innerHTML.

    innerHTML on an attribute-round-trip HTML-decodes the value, then re-parses
    it as live HTML, undoing esc()'s protection. textContent displays the same
    decoded text but never parses markup. (#220 BLOCKER 1)
    """
    js = _api_js()
    assert 'tip.textContent = dot.getAttribute("data-tip")' in js, (
        "tooltip sink must use textContent"
    )
    assert 'tip.innerHTML = dot.getAttribute("data-tip")' not in js, (
        "tooltip sink must NOT use innerHTML (XSS)"
    )


# ---------------------------------------------------------------------------
# BLOCKER 2 regression — setupSSE guard in saveToken (#220 / #193)
# ---------------------------------------------------------------------------

def test_savetoken_setupsse_is_guarded_by_usessse() -> None:
    """saveToken() must not call setupSSE() unconditionally.

    knowledge.html sets window.usesSSE = false and does not load sse.js, so
    an unguarded setupSSE() call throws ReferenceError and the auth overlay
    never dismisses. (#220 BLOCKER 2)
    """
    js = _api_js()
    start = js.index("function saveToken(")
    end = js.index("}", start)
    fn = js[start:end]
    # The guard must be present inside saveToken.
    assert "window.usesSSE !== false" in fn, (
        "setupSSE() in saveToken must be guarded by window.usesSSE !== false"
    )
    # The bare unconditional call must not appear.
    assert "setupSSE();" not in fn.replace("if (window.usesSSE !== false) setupSSE();", ""), (
        "saveToken must not contain an unguarded setupSSE() call"
    )


def test_knowledge_page_sets_usessse_false_and_omits_sse_script() -> None:
    """knowledge.html must set window.usesSSE = false (before api.js runs) and
    must NOT load sse.js (which would make the guard unnecessary but its absence
    is what makes the guard *required*). (#220 BLOCKER 2)
    """
    body = _client().get("/ui/knowledge").text
    assert "window.usesSSE = false" in body, (
        "knowledge.html must opt out of SSE via window.usesSSE = false"
    )
    assert "sse.js" not in body, (
        "knowledge.html must not load sse.js (stub page — no SSE endpoint)"
    )
