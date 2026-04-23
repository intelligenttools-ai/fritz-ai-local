# Concepts

Fritz Local is made of a small number of objects. This page names each one
and states what it does, where it lives, and who writes it.

## Brain home — `~/.brain/`

The machine-local root of the brain. Every vault on this machine shares
one brain home. Created by [`SETUP.md`](../SETUP.md) during install.

Layout:

```
~/.brain/
├── registry.yaml          # Vault registry (see below)
├── capture/
│   ├── daily/             # One file per day, appended by the capture hook
│   └── sessions/          # One file per session (full transcript excerpt)
├── handovers/             # Structured handover documents (global scope)
├── hooks/                 # Symlinks to hook scripts from the repo
├── templates/             # Symlinks to shared templates
├── tools/                 # Agent-installed tools (e.g. youtube-transcript)
├── log.md                 # Human-readable operations log
└── .migrations-run        # List of migrations already executed
```

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
`update_check`).

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

Captures are the raw inbox. They are dumb — every session writes a
capture regardless of where the session happened.

- **Daily** (`~/.brain/capture/daily/YYYY-MM-DD.md`) — short, appended by
  the capture hook on session end or pre-compact.
- **Sessions** (`~/.brain/capture/sessions/`) — fuller session excerpts
  when a longer record is warranted.

Captures are promoted into knowledge articles by `/fritz:brain-compile`,
which reads content and routes each item to the correct vault — not by
where the session ran.

## Knowledge articles

The durable output. Markdown files under each vault's `knowledge` path
(defined in its manifest). Written by `/fritz:brain-compile` and
`/fritz:brain-ingest`, linted by `/fritz:brain-lint`, synced by
`/fritz:brain-sync`.

Articles carry YAML frontmatter with at minimum a `type` field; see the
schema for the full frontmatter spec.

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

When the agent works in a directory that has a `.fritz-local.json`,
captures and context injection are scoped to that vault/project. Safe to
commit — no secrets.

`context_injection` controls how much brain content the hooks push into
the agent's context at session start:

- `off` (default) — advisory reminder only; no token cost
- `light` — hook injects matching file paths from knowledge directories
- `full` — `light` plus agent spawns a subagent to read and synthesize

Per-project settings override global settings in the registry.
