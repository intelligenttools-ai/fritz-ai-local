# Fritz Local — Documentation

Fritz Local is an agent-agnostic brain overlay: a thin layer that lets any AI
coding agent (Claude Code, Codex CLI, Gemini CLI, Hermes, or your own) share
a single personal knowledge base across sessions, machines, and projects.

This directory contains reference documentation. Install and first-use
instructions live in the repository root.

## Where to start

- **Installing Fritz Local** — see [`../SETUP.md`](../SETUP.md). It's an
  agent-facing install guide; paste it into any supported agent and it sets
  itself up.
- **What Fritz Local is for** — see [`../README.md`](../README.md) at the
  repository root.

## Reference

- [Concepts](concepts.md) — the nouns: brain home, vaults, registry,
  manifest, captures, articles, project bindings.
- [Architecture](architecture.md) — how the pieces interact: the hook
  lifecycle, the capture → compile flow, the agent-agnostic boundary, and
  the adapter layer.
- [Skills](skills.md) — purpose and lifecycle position for each `fritz:*`
  skill.
- [Operations](operations.md) — updating, version drift, troubleshooting,
  common day-to-day flows.
- [Security model](security-model.md) — the four-tier access model that
  governs which operations an agent may perform.

## Conventions used in this documentation

- `~/.brain/` refers to the brain home directory. On Windows,
  `%USERPROFILE%\.brain\`.
- `~/.fritz-ai-local/` refers to the repository clone. On Windows,
  `%USERPROFILE%\.fritz-ai-local\`.
- `<vault-path>` refers to the root of a specific vault on disk, as
  registered in `~/.brain/registry.yaml`.
- Code and paths are in backticks. Slash-commands (`/fritz:brain-query`) are
  agent-invoked skill names.
