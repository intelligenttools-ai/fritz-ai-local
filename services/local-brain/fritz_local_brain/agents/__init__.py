"""Local Brain Pydantic agents.

Every fleet agent is constructed via :func:`llm.build_model` (the shared model
factory) called inside its ``build_*`` function.  The registry below formalises
the available LLM-backed agents so callers can build them uniformly without
importing each module directly.

Note: retrieval-synthesis (``query_workflow.merge_matches``) is intentionally
NOT included here — it is a deterministic merge step, not an LLM agent.
"""

from __future__ import annotations

from .compile_agent import build_compile_agent
from .mirror_agent import build_mirror_agent
from .reconciliation_agent import build_reconciliation_agent

# ---------------------------------------------------------------------------
# Fleet registry
# ---------------------------------------------------------------------------

#: Maps each agent name to its builder callable.  The compile agent requires
#: an additional ``skill_text`` keyword argument; mirror and reconciliation
#: agents take only ``settings``.
AGENT_BUILDERS: dict[str, object] = {
    "compile": build_compile_agent,           # (settings, skill_text)
    "reconciliation": build_reconciliation_agent,  # (settings)
    "mirror": build_mirror_agent,             # (settings)
}

#: Stable tuple of all registered agent names.
AGENT_KINDS: tuple[str, ...] = tuple(AGENT_BUILDERS)


def get_agent_builder(name: str):
    """Return the builder callable for *name*.

    Raises :exc:`ValueError` for unknown names so callers get a clear message
    rather than a bare :exc:`KeyError`.
    """
    try:
        return AGENT_BUILDERS[name]
    except KeyError:
        raise ValueError(
            f"Unknown agent {name!r}. Available agents: {list(AGENT_KINDS)}"
        ) from None


def build_agent(name: str, settings, **kwargs):
    """Build and return a fleet agent by name.

    Dispatches to the registered builder: ``build_compile_agent`` needs
    ``skill_text`` passed as a keyword argument; ``build_reconciliation_agent``
    and ``build_mirror_agent`` need only ``settings``.

    Example::

        agent = build_agent("reconciliation", settings)
        agent = build_agent("mirror", settings)
        agent = build_agent("compile", settings, skill_text="...")
    """
    return get_agent_builder(name)(settings, **kwargs)
