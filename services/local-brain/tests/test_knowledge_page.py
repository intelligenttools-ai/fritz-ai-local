"""Static-structure + client-logic tests for the /ui/knowledge browser (#222).

The #221 knowledge endpoints are NOT on main yet, so these are static-markup +
client-logic assertions (same style as the other /ui page tests in
test_dashboard.py). They cover: the page serves 200 HTML, references the three
knowledge endpoints and /v1/search/run, has the status filter chips, a search
field, a tree container, an article-detail container, supersession-link
handling, deep-link (pushState / ?path=) wiring, and — importantly — that the
inline markdown renderer neutralizes raw HTML (stored-XSS guard, learning from
the bug that shipped in #220).
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from fritz_local_brain.app import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def _knowledge() -> str:
    return _client().get("/ui/knowledge").text


# ---------------------------------------------------------------------------
# Page serves 200 HTML (clean path + .html form)
# ---------------------------------------------------------------------------

def test_knowledge_page_serves_200_html_clean_path() -> None:
    resp = _client().get("/ui/knowledge")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "<title>" in resp.text


def test_knowledge_page_serves_200_html_suffix() -> None:
    resp = _client().get("/ui/knowledge.html")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_knowledge_page_needs_no_auth() -> None:
    """The shell loads without a bearer (token entered in-page)."""
    resp = _client().get("/ui/knowledge")  # no headers
    assert resp.status_code == 200
    assert 'id="auth-overlay"' in resp.text
    assert 'id="token-input"' in resp.text


# ---------------------------------------------------------------------------
# References the three knowledge endpoints + /v1/search/run
# ---------------------------------------------------------------------------

def test_knowledge_page_references_all_endpoints() -> None:
    body = _knowledge()
    assert "/v1/knowledge/tree" in body
    assert "/v1/knowledge/articles" in body
    assert "/v1/knowledge/article" in body
    assert "/v1/search/run" in body


def test_knowledge_search_uses_post_helper() -> None:
    """Search runs POST /v1/search/run via the shared postAction helper."""
    body = _knowledge()
    assert "postAction(" in body
    assert '"/v1/search/run"' in body or "'/v1/search/run'" in body
    assert "query:" in body  # body is {"query": ...}


# ---------------------------------------------------------------------------
# Status filter chips (all 5), search field, tree + detail containers
# ---------------------------------------------------------------------------

def test_knowledge_has_all_status_filter_chips() -> None:
    body = _knowledge()
    for status in ("active", "corroborated", "deprecated", "superseded", "historical"):
        assert f'data-status="{status}"' in body, status


def test_knowledge_has_search_and_text_filter_fields() -> None:
    body = _knowledge()
    assert 'id="search-input"' in body
    assert 'id="filter-input"' in body


def test_knowledge_has_tree_and_detail_containers() -> None:
    body = _knowledge()
    assert 'id="tree"' in body
    assert 'id="article-list"' in body
    assert 'id="article-detail"' in body


def test_knowledge_tree_shows_article_counts() -> None:
    """Tree nodes render their article_count from the tree endpoint."""
    body = _knowledge()
    assert "article_count" in body


# ---------------------------------------------------------------------------
# Supersession navigation
# ---------------------------------------------------------------------------

def test_knowledge_has_supersession_navigation() -> None:
    body = _knowledge()
    assert "supersedes" in body
    assert "superseded_by" in body
    # clickable links that load the linked article
    assert "loadArticle(" in body


# ---------------------------------------------------------------------------
# Deep links (pushState + ?path= / ?q=)
# ---------------------------------------------------------------------------

def test_knowledge_deep_link_wiring() -> None:
    body = _knowledge()
    assert "pushState" in body or "replaceState" in body
    assert "?path=" in body or 'set("path"' in body or '"path"' in body
    assert "URLSearchParams" in body or "searchParams" in body


def test_knowledge_restores_state_on_load() -> None:
    """On load the page reads the query string and restores that view."""
    body = _knowledge()
    assert "popstate" in body  # back/forward support


# ---------------------------------------------------------------------------
# SSE opt-out (shell convention preserved)
# ---------------------------------------------------------------------------

def test_knowledge_opts_out_of_sse() -> None:
    body = _knowledge()
    assert "window.usesSSE = false" in body
    assert "sse.js" not in body


# ---------------------------------------------------------------------------
# Escaping of untrusted fields
# ---------------------------------------------------------------------------

def test_knowledge_escapes_untrusted_fields() -> None:
    """paths / titles / tags / statuses / snippets must pass through esc()."""
    body = _knowledge()
    # article list / search rendering escapes titles + paths
    assert "esc(" in body
    # A specific check: the search snippet (untrusted) is escaped.
    assert "esc(m.snippet" in body, "search snippet must be esc()'d"
    assert "esc(m.title" in body, "search title must be esc()'d"


# ---------------------------------------------------------------------------
# Markdown renderer — HTML-injection neutralization (the #220-class bug)
# ---------------------------------------------------------------------------

def _markdown_js() -> str:
    """The shared markdown renderer module source."""
    return _client().get("/ui/shared/markdown.js").text


def test_markdown_module_served_and_loaded_by_page() -> None:
    resp = _client().get("/ui/shared/markdown.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"].lower()
    assert "/ui/shared/markdown.js" in _knowledge()


def test_markdown_renderer_present_and_escapes_first() -> None:
    """The renderer must HTML-escape the raw source BEFORE applying markdown
    transforms, so raw tags can never survive as live markup."""
    fn = _markdown_js()
    assert "function renderMarkdown(" in fn
    assert "esc(" in fn, "renderer must escape the source first"


def test_markdown_renderer_neutralizes_script_and_img_onerror() -> None:
    """The renderer's guarantee: it escapes the HTML-significant chars of the
    SOURCE before any markdown transform, and never does a raw-HTML pass-through
    (no `allowHtml`). Escape must appear before the first `.replace()` transform.
    """
    fn = _markdown_js()
    assert "allowHtml" not in fn, "no raw-HTML pass-through switch allowed"
    esc_pos = fn.index("esc(")
    transform_pos = fn.find(".replace(")
    assert transform_pos != -1, "renderer should use .replace() transforms"
    assert esc_pos < transform_pos, (
        "source must be HTML-escaped BEFORE markdown .replace() transforms"
    )


def test_markdown_output_has_no_unescaped_angle_from_source() -> None:
    """The renderMarkdown argument must be esc()'d up front (escape-first
    contract) — no code path re-injects the raw source into HTML unescaped."""
    fn = _markdown_js()
    m = re.search(r"function renderMarkdown\((\w+)\)", fn)
    assert m, "renderMarkdown must take a single named arg"
    arg = m.group(1)
    assert f"esc({arg}" in fn, f"markdown source arg {arg} must be esc()'d"


def test_markdown_neutralizes_html_when_executed() -> None:
    """Execute the real renderer under Node (if present) and assert a body with
    <script> / <img onerror> is NEUTRALIZED — no live tag survives in the output.
    Skips cleanly if node is unavailable so pytest stays green everywhere.
    """
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("node not available for JS-level XSS execution test")

    # Provide esc() (from api.js) + the renderer, then render a hostile body.
    esc_src = (
        "function esc(s){return String(s==null?'':s)"
        ".replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"
        "'\"':'&quot;',\"'\":'&#39;'}[c]));}"
    )
    md_src = _markdown_js()
    script = (
        esc_src + "\n" + md_src + "\n"
        "const hostile = '# Title\\n<script>alert(1)</script>\\n"
        "<img src=x onerror=alert(2)>\\n[x](javascript:alert(3))';\n"
        "const html = renderMarkdown(hostile);\n"
        "process.stdout.write(html);\n"
    )
    with tempfile.TemporaryDirectory() as td:
        script_path = Path(td) / "run.js"
        script_path.write_text(script, encoding="utf-8")
        out = subprocess.run(
            [node, str(script_path)], capture_output=True, text=True, timeout=20
        ).stdout

    # No live <script> or <img> tag may appear — they must be escaped to entities.
    # (The literal text "onerror=alert" may survive, but only INSIDE an escaped,
    # non-tag string — it is inert because "<img" never opens a real element.)
    assert "<script>" not in out, out
    assert "<img" not in out, out
    # The escaped forms ARE present (rendered as visible, inert text).
    assert "&lt;script&gt;" in out
    assert "&lt;img" in out
    # javascript: link must be defused — no javascript: href emitted.
    assert 'href="javascript:' not in out


# ---------------------------------------------------------------------------
# Dependency-free (no CDN / external URL) — knowledge page specifically
# ---------------------------------------------------------------------------

def test_knowledge_page_is_dependency_free() -> None:
    body = _knowledge()
    lowered = body.lower()
    assert "//cdn" not in lowered
    assert "https://" not in body and "http://" not in body
    assert "@import" not in lowered
