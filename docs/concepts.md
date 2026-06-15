# Concepts

Fritz Local is made of a small number of objects. This page names each one
and states what it does, where it lives, and who writes it.

## Brain home — `~/.brain/`

The machine-local root of the brain — the agent-agnostic store that every
binding reads from and writes to. Every vault on this machine shares one brain
home. Resolved from `BRAIN_HOME` (default `~/.brain`); created by
[`SETUP.md`](../SETUP.md) / `scripts/install.py install` during setup.

Layout:

```
~/.brain/
├── registry.yaml          # Vault registry + central settings (see below)
├── capture/
│   ├── inbox/             # Explicit saves (C3) + auto-captured facts (C4)
│   ├── daily/             # Automatic per-session rollups (C5), one file per day
│   └── auto/              # .seen content-hash dedup markers for auto-capture
├── handovers/             # Structured handover documents (global scope)
├── hooks/                 # Symlinks to the canonical repo hooks
├── templates/             # Symlinks to shared templates
├── tools/                 # Agent-installed tools (e.g. youtube-transcript)
├── log.md                 # Human-readable audit/operations log
└── .migrations-run        # List of migrations already executed
```

The brain home is **location-independent** from the repo: durable data lives
here regardless of where the Fritz repository is cloned (resolved via
`FRITZ_REPO_PATH` or each file's own location). No fixed clone path is required.

## Binding

A **binding** wires one agent runtime into the shared brain. It lives under
`bindings/<runtime>/` and contains no duplicated logic — only the mapping from
the runtime's native lifecycle onto the canonical events of the
[integration contract](integration-contract.md), plus committed symlinks back to
the canonical repo hooks. Fritz ships four first-class bindings — **Claude Code**
(plugin), **pi** (native extension, the role model), **Codex** (plugin), and
**Hermes** (gateway YAML hook merge). A new runtime adds its own binding from
[`../bindings/_template/`](../bindings/_template/README.md).

## Capability bar

The nine capabilities a binding must satisfy to be **Fritz-complete**: context
injection (C1), the BRAIN CHECK guardrail (C2), explicit save (C3), auto-capture
(C4), session capture (C5), mode detection (C6), bootstrap/health (C7), skills
with runtime-correct names (C8), and centralized config + per-project override
(C9). The full contract is [`capability-spec.md`](capability-spec.md). Hermes is
the one exception: as a non-coding gateway it has no skills mechanism, so C8 is
**N/A** for it.

## Vault

A vault is a directory of knowledge on disk. It is **existing user content**
— Fritz Local overlays it, never replaces it. A vault can be an Obsidian
vault, a `Notes/` folder, a company wiki checkout, or any directory tree
that holds notes.

Each vault is registered in `~/.brain/registry.yaml` with:

- `path` — absolute or `~`-prefixed directory path
- `domain` — short label (`work`, `personal`, `engineering`, …) used to
  route captures
- `sync` — external sync target (`local`, `affine`, or any value a sync
  adapter understands)

## Registry — `~/.brain/registry.yaml`

The single source of truth for which vaults exist on this machine. It also
holds global settings (`context_injection`, `max_injection_chars`,
`update_check`, `local_brain_service`).

Template: [`../registry/registry.template.yaml`](../registry/registry.template.yaml).

A vault exists only if it is listed in the registry. Adding a new vault
means: add an entry here, then run `/fritz:brain-setup` in that vault.

## Vault overlay — `<vault-path>/.brain/`

Per-vault state. Created by `/fritz:brain-setup`. Layout:

```
<vault-path>/.brain/
├── manifest.yaml              # Authoritative path mappings inside the vault
├── schema.md                  # Full contract for agents working in this vault
├── instructions/
│   └── brain.md               # Shared brain contract (see below)
├── capture/
│   ├── sessions/              # Vault-scoped session captures
│   └── inbox/                 # Raw ingested sources awaiting compilation
└── log.md                     # Vault operations log
```

## Manifest — `<vault-path>/.brain/manifest.yaml`

Tells every skill where things are inside the vault. The manifest maps
*logical* paths (knowledge, index, archive, daily captures) to *actual*
directories in the vault, respecting existing conventions.

A vault with Obsidian's PARA structure and one with Johnny Decimal
numbering both have manifests — their file layouts differ, the manifest
hides that difference from skills.

Authored by `/fritz:brain-setup` after interactive discovery. Never edited
silently.

## Schema — `<vault-path>/.brain/schema.md`

The long-form contract for a vault: identity, session-start behaviour,
writing rules, frontmatter schema, cross-vault reference syntax, security
tier summary. Generated from
[`../templates/schema.template.md`](../templates/schema.template.md).

Agents read this when they need full detail. Most day-to-day work goes
through the brain contract instead (see next).

## Brain contract — `<vault-path>/.brain/instructions/brain.md`

A single shared file that each agent references from its own conventional
project-root instruction file (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`,
`HERMES.md`, …). It carries:

- Vault name and domain
- Quick reference to key manifest paths
- Capture discipline
- The **Knowledge Management (Mandatory)** section that governs when each
  `/fritz:brain-*` skill must run
- Optional `Brain Knowledge` block for `context_injection: full` vaults

Frontmatter carries `brain_contract_version: N`. When the version in the
skill bumps, `/fritz:update` detects the drift and reports it; the human
re-runs `/fritz:brain-setup` in affected vaults to refresh.

Each agent runtime references `brain.md` from its own root file using its
native import/reference syntax. Agents do **not** duplicate the contract,
and do **not** write into other agents' root files.

## Captures

Captures are the raw input to the brain. The capture step is deliberately dumb —
every session writes a capture regardless of where the session ran. There are
three capture targets under `~/.brain/capture/`:

- **inbox** (`~/.brain/capture/inbox/`) — the **explicit/durable** store. Both
  `brain_save_fact` (C3, explicit save) and auto-capture (C4) write one
  YAML-frontmatter fact file here.
- **daily** (`~/.brain/capture/daily/YYYY-MM-DD.md`) — the **automatic**
  per-session rollup, written by the capture hook (C5) on session end or
  pre-compact.
- **auto** (`~/.brain/capture/auto/`) — `.seen` content-hash **dedup markers**
  (not facts) that make auto-capture idempotent: the same transcript is never
  auto-captured twice.

Every write is also appended to `~/.brain/log.md`, the human-readable audit log.

Captures are promoted into knowledge articles by `/fritz:brain-compile`,
which reads content and routes each item to the correct vault — not by
where the session ran. When `settings.local_brain_service.enabled: true` and
`auto_compile_on_ingest` is not false, capture hooks trigger a compile attempt
after saving. Default compile runs cap capture discovery to a safe batch size
(to avoid repeatedly sending the full historical corpus); configure
`COMPILE_MAX_CAPTURES=all` only for an intentional full-backlog pass. If no
processor can run, Fritz Local writes `.compile-needed` and `.compile-failed`
markers instead of silently piling up captures.

## Knowledge articles

The durable output. Markdown files under each vault's `knowledge` path
(defined in its manifest). Written by `/fritz:brain-compile` and
`/fritz:brain-ingest`, linted by `/fritz:brain-lint`, synced by
`/fritz:brain-sync`.

Articles carry YAML frontmatter with at minimum a `type` field; see the
schema for the full frontmatter spec.

### Knowledge lifecycle

Articles carry an optional `status` field (freeform frontmatter — no migration
required for existing articles, which default to `active`):

| Status | Default retrieval | Notes |
|---|---|---|
| `active` | included (primary) | Default when field is absent |
| `corroborated` | included (primary) | Confirmed by multiple sources |
| `deprecated` | included (demoted) | Still visible but ranked after active/corroborated |
| `superseded` | excluded | Hidden from default scope; reachable via `scope=all` |
| `historical` | excluded | Hidden from default scope; reachable via `scope=all` |

The `scope` query parameter (`active` by default, or `all`) controls which
articles are returned. In the `active` scope, `deprecated` matches appear after
all `active`/`corroborated` matches; `superseded` and `historical` articles are
never returned.

Articles may also carry optional bidirectional link lists:

- `supersedes: [<path>, ...]` — this article replaces the listed articles
- `superseded_by: [<path>, ...]` — this article has been replaced by the listed articles

Both fields are optional lists of strings. They are freeform references — no
referential integrity is enforced at write time.

## Project binding — `.fritz-local.json`

An optional file at the root of a source-code project that links the
project to a vault. Fields:

```json
{
  "vault": "<vault-name>",
  "project": "<project-folder-name>",
  "brain_home": "~/.brain",
  "context_injection": "off"
}
```

When the agent works in a directory that has a `.fritz-local.json` (walked up
from `cwd`), captures and context injection are scoped to that vault/project.
Safe to commit — no secrets.

`context_injection` controls how much brain content the hooks push into
the agent's context at session start:

- `off` (default) — advisory reminder only; no token cost
- `light` — hook injects matching file paths from knowledge directories
- `full` — `light` plus agent spawns a subagent to read and synthesize

Settings resolve through one path with precedence **project
(`.fritz-local.json`) > central (`registry.yaml` `settings:`) > defaults**. See
[configuration.md](configuration.md) for the full model.
