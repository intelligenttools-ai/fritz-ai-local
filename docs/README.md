# Fritz Local — Documentation

Fritz Local is an agent-agnostic brain: a shared personal knowledge base under
`~/.brain` that any AI coding agent can read from and write to, across sessions,
machines, and projects. Four runtimes are first-class — **Claude Code** (plugin),
**pi** (native extension), **Codex** (plugin), and **Hermes** (gateway YAML
merge) — and any other runtime can build a conformant binding from the
[integration contract](integration-contract.md) plus
[`../bindings/_template/`](../bindings/_template/README.md).

This directory contains reference documentation. Install and first-use
instructions live in the repository root.

## Where to start

- **Installing Fritz Local** — see [`../SETUP.md`](../SETUP.md). It's an
  agent-facing install guide; paste it into any supported agent and it sets
  itself up.
- **What Fritz Local is for** — see [`../README.md`](../README.md) at the
  repository root.

## Reference

- [Concepts](concepts.md) — the nouns: brain home, bindings, the capability
  bar, vaults, registry, manifest, captures, articles, project bindings.
- [Architecture](architecture.md) — how the pieces interact: the contract-first
  four-platform model, canonical events, the capture → compile flow, bindings,
  and the adapter layer.
- [Capability spec](capability-spec.md) — the nine-capability bar every binding
  targets.
- [Integration contract](integration-contract.md) — the contract a new binding
  is built against (canonical events, hook protocol, config model, checklist).
- [Configuration](configuration.md) — the central + per-project config model and
  its precedence.
- [Skills](skills.md) — purpose and lifecycle position for each `fritz:*`
  skill.
- [Operations](operations.md) — updating, version drift, troubleshooting,
  common day-to-day flows.
- [Security model](security-model.md) — the four-tier access model that
  governs which operations an agent may perform.

## Conventions used in this documentation

- `~/.brain/` refers to the brain home directory (override with `BRAIN_HOME`).
  On Windows, `%USERPROFILE%\.brain\`.
- `<repo>` refers to the repository clone, which can live **anywhere** —
  resolved via `FRITZ_REPO_PATH` or each file's own location. `~/.fritz-ai-local`
  is only an optional example clone path, never required.
- `<vault-path>` refers to the root of a specific vault on disk, as
  registered in `~/.brain/registry.yaml`.
- Code and paths are in backticks. Slash-commands (`/fritz:brain-query`) are
  agent-invoked skill names.
