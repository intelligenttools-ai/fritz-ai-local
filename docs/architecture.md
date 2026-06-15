# Architecture

Fritz Local is deliberately small. It does three things:

1. **Captures** every agent session and explicit fact into a single brain.
2. **Compiles** those captures into knowledge articles (in the brain store or
   a registered vault).
3. **Enforces** that agents consult the brain before planning and preserve new
   learnings before exit.

Everything else — queries, ingest, sync, handovers, lint, update — sits around
those three.

## Two modes, one core

Fritz Local runs in one of two modes depending on configuration:

| Mode | When | Knowledge target |
|------|------|-----------------|
| **Local-only** | No Docker service, or service disabled | `~/.brain/knowledge` (brain store, registry-free) |
| **Service (Docker)** | `local_brain_service.enabled: true`, service reachable | Same brain store + optional vault manifests + optional external targets + mirror |

The **brain core** (capture → compile → store → query) is identical in both
modes. The service adds a specialist-agent fleet, scheduled processing, optional
vector embeddings, and federation via external targets. Switching modes never
restructures or migrates the store.

## Contract-first, location-independent, four-platform model

Fritz is **contract-first**: the durable behavior (save, auto-capture, capture,
config, skills) is shared Python under `hooks/` and `adapters/`, and each agent
runtime is wired in through a thin **binding** under `bindings/` that maps the
runtime's native lifecycle onto the canonical events of the
[integration contract](integration-contract.md). The contract — not any single
agent — is the source of truth.

Fritz is **location-independent**: the repository can live anywhere on disk.
Durable data lives under `~/.brain` (`BRAIN_HOME`), and the repo is resolved from
`FRITZ_REPO_PATH` or from each hook/binding file's own location. No clone path is
required; `~/.fritz-ai-local` is only an optional example.

Fritz ships four **first-class bindings**:

| Platform | Binding mechanism |
|----------|-------------------|
| Claude Code | self-registering plugin from a local `directory` marketplace |
| pi | native extension (`@earendil-works` SDK) — the **role-model** binding |
| Codex | plugin via `codex plugin marketplace add` / `codex plugin add` |
| Hermes | non-coding gateway: YAML hook block merged into the profile config |

Per-platform install lives in [`../SETUP.md`](../SETUP.md) and in each binding's
own README under `bindings/`.

## Bindings vs. the shared core

A binding's only job is to map the runtime's native events onto the canonical
events and inject the hooks' output. It contains **no duplicated logic**:

- Plugin/extension bindings (Claude, Codex, pi) ship committed **symlinks** back
  to the canonical repo hooks (`<repo>/hooks/*.py`) plus the runtime's
  event-registration manifest. `scripts/install.py` symlinks the same canonical
  hooks into `~/.brain/hooks/`.
- The Hermes binding ships three wrapper symlinks plus a YAML hook block, because
  Hermes expects hook stdout in its own `{"context": ...}` shape and resolves
  transcripts from `$HERMES_HOME/sessions`.
- The pi binding (`bindings/pi/index.ts`) is the **role model**: it delegates
  save and auto-capture to the Python core by subprocess, so there is a single
  authoritative capture path.

Adding a new runtime is: write a binding that satisfies the nine-capability bar
against the contract (see [Adopting a new runtime](#adopting-a-new-runtime)).

## Canonical events

Bindings map their runtime's native lifecycle onto the canonical events defined
in the [integration contract](integration-contract.md#1-canonical-events). The
shared hooks that implement them:

| Hook | Canonical event | What it does |
|------|-----------------|--------------|
| `brain_session_start.py` | session start (C1) | Injects brain context: recent captures, update notice, optional knowledge refs by `context_injection`. |
| `brain_prompt_check.py` | before-turn (C2) | Emits the `BRAIN CHECK` guardrail so the agent saves durable knowledge, not merely answers. |
| `brain_save_fact.py` | explicit save (C3) | Writes one YAML-frontmatter fact to `~/.brain/capture/inbox/`, appends `log.md`. |
| `brain_autocapture.py` | auto-capture (C4) | Signal + intent detection over recent transcript; writes an inbox fact with `.seen` dedup under `capture/auto/`. |
| `brain_capture.py` | session capture (C5) | Summarizes the session into `~/.brain/capture/daily/`. |
| `brain_security.py` | library, not a hook | Enforces the four security tiers for any write. |

Not every runtime exposes every native event. Hermes, for example, has only two
shell-hook events, so C1 context injection is **folded into `pre_llm_call`**.
Each binding README documents its exact canonical→native mapping.

## Adapter layer

Agents produce transcripts in different formats. The capture hook reads the
current session's transcript to summarize it; that translation happens in
`adapters/`:

- `adapters/base.py` — `TranscriptAdapter` interface, `CaptureEntry`, and
  `detect()`.
- `adapters/claude_code.py` — Claude Code JSONL.
- `adapters/pi_agent.py` — pi-coding-agent JSONL (tree structure).
- `adapters/hermes.py` — Hermes Agent JSONL.
- `adapters/codex.py`, `adapters/gemini.py` — stubs; a new binding implements its
  own adapter (it knows its own transcript format best).
- `adapters/registry.py` — agent detection + adapter selection.

A new-agent integration implements the adapter, registers it, and optionally PRs
it back.

## Capture layout

All durable artifacts live under `~/.brain/capture/`:

```
~/.brain/
├── capture/
│   ├── inbox/        # explicit saves (C3) + auto-captured facts (C4)
│   ├── daily/        # automatic per-session rollups (C5), one file per day
│   └── auto/         # .seen content-hash dedup markers for auto-capture
├── hooks/            # symlinks to the canonical repo hooks
├── registry.yaml     # vault registry + central settings
└── log.md            # human-readable audit log
```

- **inbox** is the explicit/durable store: `brain_save_fact` (C3) and
  auto-capture (C4) both write one frontmatter file here.
- **daily** is the automatic session record (C5).
- **auto** holds only `.seen` dedup markers (SHA-256 of the captured transcript),
  not facts — it makes auto-capture idempotent.
- **log.md** is the append-only audit log.

## Capture → compile → store flow

```
session     → brain_capture.py    → ~/.brain/capture/daily/YYYY-MM-DD.md
explicit    → brain_save_fact     → ~/.brain/capture/inbox/
auto        → brain_autocapture   → ~/.brain/capture/inbox/
mirror      → run_mirror (Docker) → ~/.brain/capture/inbox/  (provenance-tagged)
                                                │
                                          (compile)
                                                │
                  ┌─────────────────────────────┴──────────────────────────┐
                  │   Registry-free / local-only       Registry / service   │
                  │                                                         │
                  ▼                                                         ▼
     ~/.brain/knowledge/<scope>/<section>/      <vault>/<knowledge-path>/
          (brain store — default)                 (vault manifests)
                  │                                        │
           correlation                              correlation
            (top-K TF-IDF)                          (not in vault mode)
                  │
          reconciliation agent
          (compare new vs. related)
                  │
            verdict applied
          (superseded → archive tier)
                  │
           index updated
         (index.md + archive.index.md)
```

The capture step is deliberately dumb — it does not care which directory the
session ran in. Routing is the compile step's job: `/fritz:brain-compile` reads
each capture and in registry-free mode writes articles directly to the brain
store under `<scope>/<section>/`. In service mode with vault manifests, it
routes to the appropriate registered vault instead.

## Specialist-agent fleet (service mode)

The service runs three specialist agents with deterministic validation:

| Agent | Role |
|-------|------|
| **Compile agent** | Reads captures, proposes article creates/updates; output validated by Python security layer before write |
| **Reconciliation agent** | Compares each new article against related existing content; returns a verdict that is applied (or proposed) automatically |
| **Mirror agent** | Summarizes full-summary external targets into inbox captures; index-only targets generate minimal stub captures for live-fetch enrichment at query time |

The query path is **deterministic** (no LLM): `BrainQueryAgent.search_store`
does case-insensitive text scan with scope-aware status filtering, then
`merge_matches` combines brain results with any live-fetched external content
under the configured `merge_policy` (default `brain-first`).

## Skills: plain source → per-platform variants

The canonical skills live under `skills/` with **plain `fritz:*` names** — that is
the single source of truth. Per-platform variants are produced by the generator
(`hooks/setup_hyphenated_skills.generate_variants`) and installed by
`scripts/install.py install --agent <agent>`:

- Claude and Codex keep the `fritz:` prefix.
- pi rewrites `fritz:` → `fritz-` (its runtime rejects colons), and in-body
  `/fritz:brain-*` references are rewritten to match.

The variants for the directory-source plugins (Claude, Codex) are committed so
the plugin is self-contained, and tests assert they match fresh generator output
so they cannot drift. Variants are **never hand-edited** — change the source under
`skills/` and regenerate. Hermes has **no skills mechanism**, so it installs no
skills (C8 is N/A for the gateway).

## Mode detection and graceful degradation

Every binding implements two-state mode detection (C6): **full** when
`~/.brain/registry.yaml` exists and every required hook is present and resolvable,
else **minimal-capture**. Hook invocations are fail-soft: a missing, broken, or
timed-out hook never breaks a turn — it is treated as empty with a one-time
warning. Capture capabilities that do not depend on the hooks (explicit save,
auto-capture) keep working in minimal mode.

## Configuration

There is one resolution path for project-overridable settings,
`get_setting()` in `hooks/brain_common.py`, with precedence:

> **project (`.fritz-local.json`) > central (`registry.yaml` `settings:`) > defaults**

Bindings never invent their own config store: they thread the session `cwd` into
the hooks and let the shared Python resolver apply the per-project override. Full
reference: [configuration.md](configuration.md).

## Adopting a new runtime

A runtime outside the four first-class platforms builds a conformant binding from
the kit alone:

1. [`integration-contract.md`](integration-contract.md) — the canonical events,
   hook JSON protocol, adapter interface, config model, skill-naming rule, and
   the [capability checklist](integration-contract.md#7-capability-checklist).
2. [`../bindings/_template/`](../bindings/_template/README.md) — the skeleton to
   copy to `bindings/<runtime>/`, with
   [`../bindings/_template/INITIAL_PROMPT.md`](../bindings/_template/INITIAL_PROMPT.md)
   as a self-contained brief for an agent loop.
3. Verify with `scripts/install.py install/smoke-test --agent <runtime>` against
   a temp `BRAIN_HOME`.

## Versioning

Two independent version numbers:

- **Fritz Local version** — in `<repo>/VERSION`. Bumped on releases. Migrations
  keyed off it run via `/fritz:update`.
- **Brain contract version** — declared in the `/fritz:brain-setup` skill, stored
  in each vault's `.brain/instructions/brain.md` frontmatter as
  `brain_contract_version`. `/fritz:update` detects drift and reports affected
  vaults; refresh happens when the human re-runs `/fritz:brain-setup`.
