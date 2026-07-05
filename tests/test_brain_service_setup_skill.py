from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "skills" / "brain-service-setup" / "SKILL.md"
CLAUDE = ROOT / "bindings" / "claude" / "skills" / "brain-service-setup" / "SKILL.md"
CODEX = ROOT / "bindings" / "codex" / "skills" / "fritz:brain-service-setup" / "SKILL.md"


def _canonicalized_binding_text(path: Path) -> str:
    return (
        path.read_text(encoding="utf-8")
        .replace("name: fritz:brain-service-setup", "name: brain-service-setup")
        .replace("/fritz-brain:brain-service-setup", "/brain-service-setup")
        .replace("/fritz:brain-service-setup", "/brain-service-setup")
    )


def test_setup_skill_copies_stay_in_sync_except_binding_name():
    canonical = CANONICAL.read_text(encoding="utf-8")

    assert _canonicalized_binding_text(CLAUDE) == canonical
    assert _canonicalized_binding_text(CODEX) == canonical


def test_setup_skill_asks_context_injection_before_confirmation_and_maps_flag():
    text = CANONICAL.read_text(encoding="utf-8")

    question = "**Q10 — Context injection level**"
    summary = "## Phase 2: Summary and confirmation"
    assert question in text
    assert text.index(question) < text.index(summary)
    assert "off / light / full" in text
    assert "recommend `light`" in text
    assert "--context-injection <off|light|full>" in text
    assert "| context injection level | `--context-injection <off|light|full>` |" in text
