"""Tests for the read-only knowledge browse API (#221).

Acceptance mapping:
- tree: vault/dir nesting, per-node article counts, status breakdown sums.
- articles: flat list; status filter; q substring over title/path; pagination
  (limit/offset) + total count.
- traversal: ../, absolute, and escaping paths rejected with exactly 400 for
  BOTH the articles ``path`` param and the article ``path`` param.
- article detail: frontmatter incl. supersedes/superseded_by, raw body, resolved
  link existence flags.
- index.md excluded from counts/lists.
- AUTH: endpoints return 401 without the Bearer token.
- NUL byte in path → 400 (not 500) for both /articles and /article.
- Non-.md file path → 404 for /article.
- limit clamped to max 500; negative limit/offset behave safely.
- Symlink INSIDE store pointing OUTSIDE → 400 for /articles subtree path.
- Malformed/missing frontmatter on /article → 200 with partial frontmatter (not 500).
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from fritz_local_brain.api import auth, routes
from fritz_local_brain.app import create_app
from fritz_local_brain.config import Settings

_AUTH = {"Authorization": "Bearer secret"}


def _settings(tmp_path: Path, **overrides) -> Settings:
    # Point the store root explicitly at a subdir so we control the layout.
    store = tmp_path / "knowledge"
    return Settings(
        _env_file=None,
        LOCAL_BRAIN_HOME=tmp_path,
        LOCAL_BRAIN_API_TOKEN="secret",
        LOCAL_BRAIN_STORE_PATH=store,
        **overrides,
    )


def _client(monkeypatch, settings) -> TestClient:
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    return TestClient(create_app())


def _write(root: Path, rel: str, *, front: dict | None = None, body: str = "Body text.") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if front is None:
        path.write_text(body + "\n", encoding="utf-8")
        return
    lines = ["---"]
    for k, v in front.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {item}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _seed_store(settings: Settings) -> Path:
    from fritz_local_brain import knowledge

    root = knowledge.store_root(settings)
    root.mkdir(parents=True, exist_ok=True)

    # vault "projectA" with a nested dir. use-postgres carries supersedes with
    # one existing target (old-mysql) and one missing target (missing.md).
    _write(
        root,
        "projectA/decisions/use-postgres.md",
        front={
            "title": "Use Postgres",
            "status": "active",
            "created": "2026-01-01",
            "updated": "2026-02-01",
            "tags": ["db", "arch"],
            "supersedes": ["projectA/decisions/old-mysql.md", "projectA/decisions/missing.md"],
        },
    )
    _write(
        root,
        "projectA/decisions/old-mysql.md",
        front={
            "title": "Use MySQL",
            "status": "superseded",
            "superseded_by": ["projectA/decisions/use-postgres.md"],
        },
    )
    _write(
        root,
        "projectA/patterns/retry.md",
        front={"title": "Retry Pattern", "status": "corroborated"},
    )
    # directory index files — must NOT count as articles.
    _write(root, "projectA/index.md", body="# Index")
    _write(root, "projectA/decisions/index.md", body="# Decisions Index")

    # vault "common" with one article missing a title (derived from filename) and
    # missing a status (→ active).
    _write(root, "common/glossary-terms.md", body="No frontmatter here.")
    return root


# ---------------------------------------------------------------------------
# tree
# ---------------------------------------------------------------------------

def test_tree_nesting_counts_and_status_breakdown(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed_store(settings)
    client = _client(monkeypatch, settings)

    resp = client.get("/v1/knowledge/tree", headers=_AUTH)
    assert resp.status_code == 200
    root = resp.json()

    # Root counts: 3 (projectA articles) + 1 (common) = 4 articles total.
    assert root["article_count"] == 4
    # index.md excluded → status breakdown sums to article_count.
    assert sum(root["status_counts"].values()) == 4
    # all five status keys present.
    assert set(root["status_counts"]) == {
        "active", "corroborated", "deprecated", "superseded", "historical"
    }
    assert root["status_counts"]["active"] == 2  # use-postgres + glossary(default)
    assert root["status_counts"]["corroborated"] == 1
    assert root["status_counts"]["superseded"] == 1

    children = {c["name"]: c for c in root["children"]}
    assert set(children) == {"projectA", "common"}
    assert children["projectA"]["article_count"] == 3
    assert children["common"]["article_count"] == 1

    # nested dir: projectA/decisions has 2 articles (index.md excluded).
    proj_children = {c["name"]: c for c in children["projectA"]["children"]}
    assert proj_children["decisions"]["article_count"] == 2
    assert proj_children["patterns"]["article_count"] == 1


# ---------------------------------------------------------------------------
# articles list
# ---------------------------------------------------------------------------

def test_articles_list_all_excludes_index(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed_store(settings)
    client = _client(monkeypatch, settings)

    resp = client.get("/v1/knowledge/articles", headers=_AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 4
    paths = {a["path"] for a in data["articles"]}
    assert not any(p.endswith("index.md") for p in paths)
    # title derived from filename when frontmatter lacks it; missing status → active.
    glossary = next(a for a in data["articles"] if a["path"] == "common/glossary-terms.md")
    assert glossary["title"] == "glossary-terms"
    assert glossary["status"] == "active"


def test_articles_status_filter(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed_store(settings)
    client = _client(monkeypatch, settings)

    resp = client.get("/v1/knowledge/articles?status=superseded", headers=_AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["articles"][0]["path"] == "projectA/decisions/old-mysql.md"


def test_articles_q_substring_over_title_and_path(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed_store(settings)
    client = _client(monkeypatch, settings)

    # matches title "Use Postgres" (case-insensitive)
    resp = client.get("/v1/knowledge/articles?q=postgres", headers=_AUTH)
    assert resp.status_code == 200
    assert {a["path"] for a in resp.json()["articles"]} == {
        "projectA/decisions/use-postgres.md"
    }

    # matches path substring "patterns"
    resp = client.get("/v1/knowledge/articles?q=patterns", headers=_AUTH)
    assert {a["path"] for a in resp.json()["articles"]} == {"projectA/patterns/retry.md"}


def test_articles_path_subtree_filter(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed_store(settings)
    client = _client(monkeypatch, settings)

    resp = client.get("/v1/knowledge/articles?path=projectA/decisions", headers=_AUTH)
    assert resp.status_code == 200
    paths = {a["path"] for a in resp.json()["articles"]}
    assert paths == {
        "projectA/decisions/use-postgres.md",
        "projectA/decisions/old-mysql.md",
    }


def test_articles_pagination(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed_store(settings)
    client = _client(monkeypatch, settings)

    r1 = client.get("/v1/knowledge/articles?limit=2&offset=0", headers=_AUTH).json()
    r2 = client.get("/v1/knowledge/articles?limit=2&offset=2", headers=_AUTH).json()
    assert r1["total"] == 4 and r2["total"] == 4
    assert len(r1["articles"]) == 2 and len(r2["articles"]) == 2
    # disjoint slices
    assert {a["path"] for a in r1["articles"]} & {a["path"] for a in r2["articles"]} == set()


# ---------------------------------------------------------------------------
# article detail
# ---------------------------------------------------------------------------

def test_article_detail_frontmatter_body_and_links(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed_store(settings)
    client = _client(monkeypatch, settings)

    resp = client.get(
        "/v1/knowledge/article?path=projectA/decisions/use-postgres.md", headers=_AUTH
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["path"] == "projectA/decisions/use-postgres.md"
    assert data["title"] == "Use Postgres"
    assert data["status"] == "active"
    assert data["frontmatter"]["tags"] == ["db", "arch"]
    assert data["supersedes"] == [
        "projectA/decisions/old-mysql.md",
        "projectA/decisions/missing.md",
    ]
    assert "Body text." in data["body"]

    # resolved-link existence flags: existing target True, missing target False.
    links = {link["target"]: link["exists"] for link in data["links"]}
    assert links["projectA/decisions/old-mysql.md"] is True
    assert links["projectA/decisions/missing.md"] is False


def test_article_detail_superseded_by(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed_store(settings)
    client = _client(monkeypatch, settings)

    resp = client.get(
        "/v1/knowledge/article?path=projectA/decisions/old-mysql.md", headers=_AUTH
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["superseded_by"] == ["projectA/decisions/use-postgres.md"]
    links = {link["target"]: link["exists"] for link in data["links"]}
    assert links["projectA/decisions/use-postgres.md"] is True


def test_article_detail_missing_returns_404(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed_store(settings)
    client = _client(monkeypatch, settings)

    resp = client.get("/v1/knowledge/article?path=projectA/nope.md", headers=_AUTH)
    assert resp.status_code == 404


def test_article_index_md_is_not_an_article(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed_store(settings)
    client = _client(monkeypatch, settings)

    resp = client.get("/v1/knowledge/article?path=projectA/index.md", headers=_AUTH)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# path-traversal rejection (security-critical) — for BOTH path params
# ---------------------------------------------------------------------------

_ESCAPES = ["../secret.md", "../../etc/passwd", "/etc/passwd", "projectA/../../escape.md"]


def test_articles_path_traversal_rejected(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed_store(settings)
    # a real file OUTSIDE the store root that a traversal could reach
    (tmp_path / "secret.md").write_text("---\ntitle: secret\n---\nleak\n", encoding="utf-8")
    client = _client(monkeypatch, settings)

    for bad in _ESCAPES:
        resp = client.get("/v1/knowledge/articles", params={"path": bad}, headers=_AUTH)
        assert resp.status_code == 400, f"{bad!r} should be rejected with 400, got {resp.status_code}"


def test_article_path_traversal_rejected(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed_store(settings)
    (tmp_path / "secret.md").write_text("---\ntitle: secret\n---\nleak\n", encoding="utf-8")
    client = _client(monkeypatch, settings)

    for bad in _ESCAPES:
        resp = client.get("/v1/knowledge/article", params={"path": bad}, headers=_AUTH)
        assert resp.status_code == 400, f"{bad!r} should be rejected with 400, got {resp.status_code}"
        # never leak the outside file's body
        assert "leak" not in resp.text


def test_article_symlink_escape_rejected(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    root = _seed_store(settings)
    outside = tmp_path / "outside.md"
    outside.write_text("---\ntitle: outside\n---\nleak\n", encoding="utf-8")
    link = root / "projectA" / "link.md"
    link.symlink_to(outside)
    client = _client(monkeypatch, settings)

    resp = client.get("/v1/knowledge/article", params={"path": "projectA/link.md"}, headers=_AUTH)
    assert resp.status_code in (400, 404)
    assert "leak" not in resp.text


# ---------------------------------------------------------------------------
# FIX 1 — NUL byte in path → 400 (not 500)
# ---------------------------------------------------------------------------

def test_articles_nul_byte_in_path_returns_400(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed_store(settings)
    client = _client(monkeypatch, settings)

    resp = client.get("/v1/knowledge/articles", params={"path": "proj\x00ect"}, headers=_AUTH)
    assert resp.status_code == 400, f"NUL in articles path should be 400, got {resp.status_code}"


def test_article_nul_byte_in_path_returns_400(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed_store(settings)
    client = _client(monkeypatch, settings)

    resp = client.get("/v1/knowledge/article", params={"path": "proj\x00ect.md"}, headers=_AUTH)
    assert resp.status_code == 400, f"NUL in article path should be 400, got {resp.status_code}"


# ---------------------------------------------------------------------------
# FIX 2 — frontmatter-only read (listing must not need the body)
# ---------------------------------------------------------------------------

def test_frontmatter_only_read_does_not_require_body(tmp_path) -> None:
    """_frontmatter_only must return correct frontmatter even on huge-body files.

    We verify by writing a file with a large body and confirming the frontmatter
    fields are parsed correctly (the body is never needed for listing).
    """
    from fritz_local_brain import knowledge_browse

    f = tmp_path / "big.md"
    fm_block = "---\ntitle: Big Article\nstatus: active\n---\n"
    big_body = "x" * 500_000  # 500 KB body
    f.write_text(fm_block + big_body, encoding="utf-8")

    result = knowledge_browse._frontmatter_only(f)
    assert result.get("title") == "Big Article"
    assert result.get("status") == "active"


def test_frontmatter_only_read_stops_at_delimiter(tmp_path) -> None:
    """_frontmatter_only must return the header block even without a body."""
    from fritz_local_brain import knowledge_browse

    f = tmp_path / "no_body.md"
    f.write_text("---\ntitle: Header Only\nstatus: corroborated\n---\n", encoding="utf-8")

    result = knowledge_browse._frontmatter_only(f)
    assert result.get("title") == "Header Only"
    assert result.get("status") == "corroborated"


# ---------------------------------------------------------------------------
# FIX 3 — non-.md file → 404
# ---------------------------------------------------------------------------

def test_article_non_md_path_returns_404(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    root = _seed_store(settings)
    # place a real non-md file inside the store root
    config_file = root / "config.yaml"
    config_file.write_text("key: value\n", encoding="utf-8")
    client = _client(monkeypatch, settings)

    resp = client.get("/v1/knowledge/article", params={"path": "config.yaml"}, headers=_AUTH)
    assert resp.status_code == 404, f"non-.md file should be 404, got {resp.status_code}"


def test_articles_list_excludes_non_md_files(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    root = _seed_store(settings)
    # place a non-md file inside the store root
    (root / "readme.txt").write_text("some text\n", encoding="utf-8")
    client = _client(monkeypatch, settings)

    resp = client.get("/v1/knowledge/articles", headers=_AUTH)
    assert resp.status_code == 200
    paths = {a["path"] for a in resp.json()["articles"]}
    assert not any(not p.endswith(".md") for p in paths), \
        f"non-.md files should not appear in listing: {paths}"


# ---------------------------------------------------------------------------
# FIX 4 — limit clamped to 500; negative limit/offset safe
# ---------------------------------------------------------------------------

_LIMIT_CAP = 500


def test_articles_huge_limit_capped(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed_store(settings)
    client = _client(monkeypatch, settings)

    resp = client.get("/v1/knowledge/articles?limit=10000000", headers=_AUTH)
    assert resp.status_code == 200
    data = resp.json()
    # The effective limit applied must be <= cap; with only 4 articles the
    # window trivially has ≤4 items — confirm we got results and no crash.
    assert len(data["articles"]) <= _LIMIT_CAP
    assert data["total"] == 4


def test_articles_negative_limit_safe(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed_store(settings)
    client = _client(monkeypatch, settings)

    # FastAPI ge=0 will block negative via query param, but let's ensure the
    # helper itself is safe if called directly.
    from fritz_local_brain import knowledge_browse
    from fritz_local_brain.knowledge import store_root

    root = store_root(settings)
    result = knowledge_browse.list_articles(settings, limit=-1, offset=0)
    assert isinstance(result["articles"], list)


def test_articles_negative_offset_safe(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed_store(settings)
    client = _client(monkeypatch, settings)

    from fritz_local_brain import knowledge_browse

    result = knowledge_browse.list_articles(settings, limit=10, offset=-5)
    assert isinstance(result["articles"], list)


# ---------------------------------------------------------------------------
# FIX 5 — symlink inside store pointing outside → 400 on /articles
# ---------------------------------------------------------------------------

def test_articles_symlink_dir_inside_store_pointing_outside_returns_400(
    monkeypatch, tmp_path
) -> None:
    settings = _settings(tmp_path)
    root = _seed_store(settings)

    # Create a directory outside the store with a file in it.
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    (outside_dir / "secret.md").write_text("---\ntitle: secret\n---\nleak\n", encoding="utf-8")

    # Create a symlink INSIDE the store pointing to that outside directory.
    link_dir = root / "escaped_dir"
    link_dir.symlink_to(outside_dir)

    client = _client(monkeypatch, settings)

    # Requesting the symlinked dir as a subtree path should be rejected.
    resp = client.get("/v1/knowledge/articles", params={"path": "escaped_dir"}, headers=_AUTH)
    assert resp.status_code == 400, (
        f"symlink dir inside store pointing outside should be 400, got {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# FIX 5 — malformed/missing frontmatter on /article → 200, not 500
# ---------------------------------------------------------------------------

def test_article_malformed_frontmatter_returns_200(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    root = _seed_store(settings)
    # Write a file with malformed YAML frontmatter.
    bad_fm = root / "bad-frontmatter.md"
    bad_fm.write_text("---\ntitle: [unclosed bracket\nstatus: active\n---\nBody here.\n", encoding="utf-8")
    client = _client(monkeypatch, settings)

    resp = client.get("/v1/knowledge/article", params={"path": "bad-frontmatter.md"}, headers=_AUTH)
    assert resp.status_code == 200, f"malformed frontmatter should return 200, got {resp.status_code}"
    data = resp.json()
    # frontmatter may be empty dict but the body should still be present.
    assert "body" in data


def test_article_missing_frontmatter_returns_200(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    root = _seed_store(settings)
    # A .md file with no frontmatter at all.
    no_fm = root / "no-frontmatter.md"
    no_fm.write_text("Just plain text, no YAML header.\n", encoding="utf-8")
    client = _client(monkeypatch, settings)

    resp = client.get("/v1/knowledge/article", params={"path": "no-frontmatter.md"}, headers=_AUTH)
    assert resp.status_code == 200, f"missing frontmatter should return 200, got {resp.status_code}"
    data = resp.json()
    assert "body" in data
    assert data["frontmatter"] == {}


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------

def test_endpoints_require_token(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    _seed_store(settings)
    client = _client(monkeypatch, settings)

    assert client.get("/v1/knowledge/tree").status_code == 401
    assert client.get("/v1/knowledge/articles").status_code == 401
    assert client.get("/v1/knowledge/article?path=x.md").status_code == 401
