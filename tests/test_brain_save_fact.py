"""Tests for hooks/brain_save_fact.py.

All writes are confined to ``tmp_path`` via the ``root=`` parameter or the
``BRAIN_HOME`` env override, so the live ``~/.brain`` is never touched.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
sys.path.insert(0, str(HOOKS))

import brain_save_fact  # noqa: E402


# --- slugify ---------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Hello World", "hello-world"),
        ("  Trim  Me  ", "trim-me"),
        ("Forgejo Token @ Server!", "forgejo-token-server"),
        ("under_score_path", "under-score-path"),
        ("---leading-and-trailing---", "leading-and-trailing"),
        ("", "brain-fact"),
        ("!!!", "brain-fact"),
    ],
)
def test_slugify(value, expected):
    assert brain_save_fact.slugify(value) == expected


def test_slugify_caps_at_80_chars():
    long = "a" * 200
    assert brain_save_fact.slugify(long) == "a" * 80


# --- date helpers ----------------------------------------------------------


def test_today_str_is_utc():
    """``today_str`` mirrors the role model's UTC ``toISOString().slice(0, 10)``
    (``bindings/pi/index.ts``), so the inbox filename + ``created:`` value do
    not drift from the role model near the midnight boundary."""
    from datetime import datetime, timezone

    assert brain_save_fact.today_str() == datetime.now(timezone.utc).strftime(
        "%Y-%m-%d"
    )


def test_timestamp_stays_local():
    """The ``log.md`` audit timestamp must remain local time, mirroring the
    role model's ``timestamp()`` (getFullYear/getHours/...)."""
    from datetime import datetime

    assert brain_save_fact.timestamp() == datetime.now().strftime("%Y-%m-%d %H:%M")


# --- frontmatter / file content --------------------------------------------


def test_save_fact_writes_inbox_file_with_frontmatter(tmp_path):
    file = brain_save_fact.save_fact(
        title="Forgejo Server URL",
        body="The server is at https://git.example.ai",
        source="session-note",
        sensitive=False,
        tags=["FritzBrain", "Infra"],
        agent="pi",
        root=tmp_path,
    )

    expected = tmp_path / "capture" / "inbox" / f"{brain_save_fact.today_str()}-forgejo-server-url.md"
    assert file == expected
    assert file.exists()

    content = file.read_text(encoding="utf-8")
    assert content == (
        "---\n"
        "type: capture\n"
        'title: "Forgejo Server URL"\n'
        "domain: work\n"
        "sources:\n"
        '  - "session-note"\n'
        f"created: {brain_save_fact.today_str()}\n"
        "agent_last_edit: pi\n"
        "sensitive: false\n"
        "---\n"
        "# Forgejo Server URL\n"
        "\n"
        "The server is at https://git.example.ai\n"
        "\n"
        "Tags: #FritzBrain #Infra\n"
    )


def test_save_fact_default_source_is_pi_session(tmp_path):
    file = brain_save_fact.save_fact(
        title="No Source Fact", body="body", root=tmp_path
    )
    content = file.read_text(encoding="utf-8")
    assert "sources:\n  - pi-session\n" in content


def test_save_fact_no_tags_uses_single_newline(tmp_path):
    file = brain_save_fact.save_fact(title="No Tags", body="body here", root=tmp_path)
    content = file.read_text(encoding="utf-8")
    assert content.endswith("# No Tags\n\nbody here\n")
    assert "Tags:" not in content


def test_save_fact_strips_leading_hash_from_tags(tmp_path):
    file = brain_save_fact.save_fact(
        title="Hashed Tags", body="b", tags=["#one", "two"], root=tmp_path
    )
    content = file.read_text(encoding="utf-8")
    assert content.endswith("Tags: #one #two\n")


def test_save_fact_records_sensitive_true(tmp_path):
    file = brain_save_fact.save_fact(
        title="Secret Token", body="token=abc", sensitive=True, root=tmp_path
    )
    content = file.read_text(encoding="utf-8")
    assert "sensitive: true\n" in content


def test_save_fact_body_is_trimmed(tmp_path):
    file = brain_save_fact.save_fact(
        title="Trim Body", body="\n\n  hello  \n\n", root=tmp_path
    )
    content = file.read_text(encoding="utf-8")
    assert content.endswith("# Trim Body\n\nhello\n")


def test_save_fact_title_with_special_chars_is_json_quoted(tmp_path):
    file = brain_save_fact.save_fact(
        title='Quote "this" fact', body="b", root=tmp_path
    )
    content = file.read_text(encoding="utf-8")
    assert 'title: "Quote \\"this\\" fact"' in content


# --- log.md audit line -----------------------------------------------------


def test_save_fact_appends_log_line(tmp_path):
    file = brain_save_fact.save_fact(title="Logged Fact", body="b", root=tmp_path)
    log = (tmp_path / "log.md").read_text(encoding="utf-8")
    assert ' | INGEST | pi-extension | Auto-saved "Logged Fact" to ' in log
    assert log.rstrip("\n").endswith(str(file))
    assert log.endswith("\n")


def test_save_fact_appends_multiple_log_lines(tmp_path):
    brain_save_fact.save_fact(title="First", body="b", root=tmp_path)
    brain_save_fact.save_fact(title="Second", body="b", root=tmp_path)
    log = (tmp_path / "log.md").read_text(encoding="utf-8")
    assert len(log.strip().splitlines()) == 2


# --- permissions -----------------------------------------------------------


def test_save_fact_dir_and_file_permissions(tmp_path):
    file = brain_save_fact.save_fact(title="Perms Fact", body="b", root=tmp_path)

    assert (file.stat().st_mode & 0o777) == 0o600
    assert ((tmp_path / "log.md").stat().st_mode & 0o777) == 0o600

    for d in (tmp_path, tmp_path / "capture", tmp_path / "capture" / "inbox"):
        assert (d.stat().st_mode & 0o777) == 0o700


# --- brain_home override ---------------------------------------------------


def test_brain_home_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("BRAIN_HOME", str(tmp_path / "envbrain"))
    assert brain_save_fact.brain_home() == tmp_path / "envbrain"


def test_brain_home_defaults_to_dot_brain(monkeypatch):
    monkeypatch.delenv("BRAIN_HOME", raising=False)
    assert brain_save_fact.brain_home() == Path.home() / ".brain"


def test_save_fact_uses_env_brain_home(monkeypatch, tmp_path):
    monkeypatch.setenv("BRAIN_HOME", str(tmp_path))
    file = brain_save_fact.save_fact(title="Env Fact", body="b")
    assert str(file).startswith(str(tmp_path))


# --- CLI -------------------------------------------------------------------


def test_cli_json_stdin(tmp_path):
    payload = json.dumps(
        {
            "title": "CLI Fact",
            "body": "from json",
            "source": "src",
            "sensitive": True,
            "tags": ["a", "b"],
        }
    )
    result = subprocess.run(
        [sys.executable, str(HOOKS / "brain_save_fact.py"), "--json"],
        input=payload,
        capture_output=True,
        text=True,
        env={"BRAIN_HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    file = tmp_path / "capture" / "inbox" / f"{brain_save_fact.today_str()}-cli-fact.md"
    assert file.exists()
    content = file.read_text(encoding="utf-8")
    assert "sensitive: true\n" in content
    assert content.endswith("Tags: #a #b\n")
    assert "Saved to Fritz-Brain:" in result.stdout


def test_cli_args(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(HOOKS / "brain_save_fact.py"),
            "--title",
            "Arg Fact",
            "--body",
            "arg body",
            "--sensitive",
        ],
        capture_output=True,
        text=True,
        env={"BRAIN_HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    file = tmp_path / "capture" / "inbox" / f"{brain_save_fact.today_str()}-arg-fact.md"
    assert file.exists()
    assert "sensitive: true\n" in file.read_text(encoding="utf-8")
