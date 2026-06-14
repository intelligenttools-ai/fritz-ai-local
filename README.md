# Fritz Local

An **agent-agnostic brain** — a shared personal knowledge base that any AI
coding agent can read from and write to, across sessions, machines, and
projects. The durable knowledge lives under `~/.brain` and is owned by no single
agent. Each agent runtime is wired in through a thin **binding** that maps the
runtime's native lifecycle onto a small, written **integration contract**.

## The four first-class platforms

Fritz ships verified bindings for four runtimes. Each lives under `bindings/`
and is documented by its own README (the authoritative per-platform install):

| Platform | Mechanism | Binding |
|----------|-----------|---------|
| **Claude Code** | Self-registering plugin from a local `directory` marketplace — no manual `~/.claude/settings.json` hook edits | [`bindings/claude/`](bindings/claude/README.md) |
| **pi** (`pi-coding-agent`) | Native extension against the `@earendil-works` SDK, bootstrapped with `/fritz` | [`bindings/pi/`](bindings/pi/README.md) |
| **Codex** | Plugin registered via `codex plugin marketplace add` + `codex plugin add` | [`bindings/codex/`](bindings/codex/README.md) |
| **Hermes** | Non-coding gateway: a YAML hook block merged into the Hermes profile `config.yaml` | [`bindings/hermes/`](bindings/hermes/README.md) |

The brain itself is identical across all four — only the binding differs. See
[`SETUP.md`](SETUP.md) for a per-platform install walkthrough.

## The nine-capability bar

A binding is **Fritz-complete** only when it satisfies all nine capabilities of
the [capability spec](docs/capability-spec.md). The bar is derived from the
role-model binding (`bindings/pi/index.ts`) and is the contract every binding
targets:

| # | Capability |
|---|-----------|
| C1 | **Context injection** at session start |
| C2 | **Brain-first guardrail** before each turn (the "BRAIN CHECK") |
| C3 | **Explicit save** — `brain_save_fact` → `~/.brain/capture/inbox/` |
| C4 | **Auto-capture** of durable knowledge (signal + intent, `.seen` dedup) |
| C5 | **Session capture** on end/compact → `~/.brain/capture/daily/` |
| C6 | **Mode detection** (full vs minimal) with graceful degradation |
| C7 | **Bootstrap / health** — install / repair / status / smoke-test |
| C8 | **Skills** installed with runtime-correct names |
| C9 | **Centralized config** + per-project override |

(The capability numbers above follow the capability spec's checklist ordering;
individual binding READMEs may map a runtime's native events to these in a
slightly different order — e.g. Hermes folds C1 context injection into its
`pre_llm_call` event because it has no dedicated session-start event.)

### Which platform meets which capability

All four platforms meet the durable-data capabilities (C1–C5, C9). The
differences are at the edges, and each is documented honestly in the binding
READMEs:

| Capability | Claude | pi | Codex | Hermes |
|-----------|--------|----|----|--------|
| C1–C5 capture/guardrail | yes | yes | hooks **require in-Codex verification** | yes |
| C6 mode detection | yes | yes | yes | yes (shared resolver) |
| C7 bootstrap/health | `install.py` + `/plugin` | `install.py` + `/fritz` | `install.py` + `codex plugin` | YAML merge |
| C8 skills | yes (`fritz:*`) | yes (`fritz-*`) | yes (`fritz:*`) | **N/A** — gateway has no skills mechanism |
| C9 central + project config | yes | yes | yes | yes |

Codex's plugin/skills half is **verified** against `codex-cli 0.139.0`; its hook
half is the documented open capability (Codex has a real hook subsystem but its
config schema is not introspectable from the local CLI), so the Codex README
marks those rows `REQUIRES-IN-CODEX-VERIFICATION` rather than overstating them.
Hermes is a non-coding gateway with no skills/plugin mechanism, so C8 is N/A and
the explicit-save capability is delivered through the `brain_save_fact` CLI
instead.

## Location independence

Fritz is **location-independent**. The repository can live anywhere on disk —
there is no required clone path. Everything resolves dynamically:

- **Durable data** lives under `~/.brain` (override with `BRAIN_HOME`).
- **The repo** is resolved from `FRITZ_REPO_PATH` if set, otherwise from each
  hook/binding file's own location.

`~/.fritz-ai-local` appears in this documentation only as an *optional* example
clone path. It is never required — clone wherever you like.

## Install

Per-platform walkthroughs for all four runtimes are in [`SETUP.md`](SETUP.md).
The shared bootstrap is one command:

```bash
python3 scripts/install.py install --agent <claude|codex|pi>
python3 scripts/install.py status
python3 scripts/install.py smoke-test
python3 scripts/install.py install --agent claude --dry-run   # preview, writes nothing
```

`install.py` creates the `~/.brain` layout, symlinks the canonical Python hooks
into `~/.brain/hooks/`, and installs the per-platform skill variants. **Hermes
is not an `--agent`** (it has no skills) — it is bootstrapped by merging
[`bindings/hermes/hermes-hooks.yaml`](bindings/hermes/hermes-hooks.yaml) into the
Hermes profile config; see SETUP.

## Capture layout

All durable artifacts land under `~/.brain/capture/`:

| Path | Role | Written by |
|------|------|-----------|
| `capture/inbox/` | **Explicit** + auto-captured durable facts (one YAML-frontmatter file per fact) | `brain_save_fact` (C3), auto-capture (C4) |
| `capture/daily/` | **Automatic** per-session rollups, one file per day | session capture (C5) |
| `capture/auto/` | `.seen` content-hash **dedup markers** for auto-capture (not facts) | auto-capture (C4) |
| `log.md` | Human-readable **audit** log (`INGEST` lines, etc.) | every write |

Captures are promoted into per-vault knowledge articles by
`/fritz:brain-compile`, which routes each item to the correct vault by content.

## Config model

Configuration is centralized and overridable per project, with a single
resolution path (`get_setting()` in `hooks/brain_common.py`):

> **project (`.fritz-local.json`) > central (`registry.yaml` `settings:`) > defaults**

Central settings (`context_injection`, `max_injection_chars`, `update_check`,
the optional `local_brain_service` block) live under `settings:` in
`~/.brain/registry.yaml`; a per-project `.fritz-local.json` walked up from `cwd`
overrides them for that project. Full reference: [docs/configuration.md](docs/configuration.md).

## Adopting a new runtime

A runtime that is not one of the four first-class platforms can build its own
conformant binding from the kit alone:

1. Read [`docs/integration-contract.md`](docs/integration-contract.md) — the
   canonical events, the hook stdin→stdout JSON protocol, the adapter interface,
   the config model, the skill-naming rule, and the capability checklist.
2. Copy [`bindings/_template/`](bindings/_template/README.md) to
   `bindings/<runtime>/` and hand
   [`bindings/_template/INITIAL_PROMPT.md`](bindings/_template/INITIAL_PROMPT.md)
   — a self-contained brief — to an agent loop.
3. Satisfy all nine capabilities, then verify with
   `scripts/install.py install/smoke-test --agent <runtime>` against a temp
   `BRAIN_HOME`.

## What the brain does

- **Captures** every session and explicit fact into `~/.brain/capture/`.
- **Compiles** captures into per-vault knowledge articles (`/fritz:brain-compile`).
- **Queries** across vaults (`/fritz:brain-query`).
- **Ingests** external sources — URLs, videos, papers (`/fritz:brain-ingest`).
- **Syncs** to external views (`/fritz:brain-sync`).
- **Lints** for stale/broken/orphaned content (`/fritz:brain-lint`).
- **Hands over** sessions with a structured document (`/fritz:handover`).
- **Updates** itself and runs migrations (`/fritz:update`).
- **Enforces brain-first** — every turn re-checks whether durable knowledge in
  the prompt must be saved, not merely answered.

## Local Brain service (optional)

An optional Dockerized Local Brain service lives in
[`services/local-brain/`](services/local-brain/) for compile, semantic search,
sync, lint, and embeddings. It is disabled by default and enabled explicitly in
`~/.brain/registry.yaml` via `settings.local_brain_service.enabled: true`. When
enabled and reachable, agents prefer the service for supported workflows;
otherwise the local hooks and slash skills remain the fallback. See
[`services/local-brain/README.md`](services/local-brain/README.md) and the
optional steps in [`SETUP.md`](SETUP.md).

## Documentation

See [`docs/`](docs/) for reference documentation, starting with
[`docs/README.md`](docs/README.md):

- [Concepts](docs/concepts.md) — brain home, vaults, registry, captures, bindings.
- [Architecture](docs/architecture.md) — canonical events, capture→compile flow, bindings, adapters.
- [Capability spec](docs/capability-spec.md) — the nine-capability bar.
- [Integration contract](docs/integration-contract.md) — the contract a new binding targets.
- [Configuration](docs/configuration.md) — the central + per-project config model.
- [Skills](docs/skills.md) — purpose and lifecycle of each `fritz:*` skill.
- [Operations](docs/operations.md) — updating, drift, troubleshooting.
- [Security model](docs/security-model.md) — the four-tier access model.
