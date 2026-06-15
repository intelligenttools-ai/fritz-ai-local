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

The registry (`registry.yaml`) holds global settings (`context_injection`,
`max_injection_chars`, `update_check`, `local_brain_service`) and optionally
describes **external targets**.

Template: [`../registry/registry.template.yaml`](../registry/registry.template.yaml).

A vault exists only if it is listed in the registry. Adding a new vault
means: add an entry here, then run `/fritz:brain-setup` in that vault.

### Brain core works without the registry

The brain store, index, and knowledge lifecycle (compile, query, captures,
lint) all operate fully when `registry.yaml` is absent. The registry is
**optional and additive** — its absence is never an error for core
workflows. Switching between local mode and the optional Docker service mode
never restructures or migrates the brain store.

### External targets

The registry also carries an optional `external_targets:` block that lists
off-brain systems the optional Docker mirror agent can pull data FROM:

| Kind | What it points to |
|---|---|
| `local-vault` | Another vault directory on this machine |
| `mcp` | An MCP server (e.g. Obsidian bridge) |
| `drive` | A shared or mounted filesystem path |
| `offsite` | A remote URL (Affine, Notion, etc.) |

Each target has a `mirror_mode`:

- `index-only` (default) — only a title/path index is mirrored in; full
  content is fetched live at query time via `live_fetch` when a query hits
  an index-only capture
- `full-summary` — a full-text summary produced by the mirror agent is
  written as an inbox capture alongside the index entry

External targets are **service mode only** and entirely additive. The brain
store operates identically with or without them. Mirror execution (fetching
and summarisation) is performed by the Docker mirror agent and is triggered
by the optional mirror scheduler or manually.

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

## Brain knowledge store — `~/.brain/knowledge`

The brain-owned durable knowledge store. Used in **registry-free local mode**
(when no vault manifest is found) as the single target for compile output.
Registry-free, relocatable: the store root comes from `brain_store_path`
(config) or the `<brain_home>/knowledge` default — never from `registry.yaml`.
The store is created automatically on first compile; no migration is required.

Store layout:

```
~/.brain/knowledge/
├── index.md                  ← global MOC (active articles only)
├── archive.index.md          ← archive tier (superseded / historical)
├── common/
│   ├── index.md              ← scope MOC
│   ├── decisions/            ← architecture decisions, ADRs
│   ├── lessons/              ← retrospective learnings, feedback
│   ├── runbooks/             ← how-to, operational procedures
│   └── context/              ← background knowledge, glossaries
└── <project-slug>/
    ├── index.md
    ├── decisions/
    ├── lessons/
    ├── runbooks/
    └── context/
```

`common` holds cross-project knowledge. Each project slug is derived from the
content routing decision made during compile. Index files (`index.md`) at each
level are Maps of Content (MOCs) maintained automatically — do not write them
manually. The global `archive.index.md` lists articles in archive-tier statuses
(`superseded`, `historical`) separately from the active MOC.

## Knowledge articles

The durable output. Markdown files under each vault's `knowledge` path
(defined in its manifest), or under `~/.brain/knowledge` in registry-free mode.
Written by `/fritz:brain-compile` and `/fritz:brain-ingest`, linted by
`/fritz:brain-lint`, synced by `/fritz:brain-sync`.

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
| `superseded` | excluded — archive tier | Hidden from default scope; reachable via `scope=include_archive` |
| `historical` | excluded — archive tier | Hidden from default scope; reachable via `scope=include_archive` |

The `scope` query parameter controls which articles are returned:

- `active` (default) — primary matches (`active`, `corroborated`, no status)
  followed by demoted (`deprecated`); archived articles are excluded
- `include_archive` — same active results first, then archived articles appended
- `all` — everything in natural order; no status filtering

Articles may also carry optional bidirectional link lists:

- `supersedes: [<path>, ...]` — this article replaces the listed articles
- `superseded_by: [<path>, ...]` — this article has been replaced by the listed articles

Both fields are optional lists of strings. They are freeform references — no
referential integrity is enforced at write time.

### Reconciliation

When a compile run produces a new store article in non-dry-run mode, the
**reconciliation agent** compares it against related existing articles (found
via the correlation feed — top-K related content by TF-IDF similarity). For
each (new, old) pair it returns one of five verdicts:

| Verdict | Effect |
|---|---|
| `corroborates` | old article promoted to `corroborated`; link added |
| `refines` | no status change; bidirectional `refines`/`refined_by` links added |
| `contradicts_supersedes` | old article demoted to `superseded`; old removed from active indexes, added to `archive.index.md` |
| `context_split` | both articles gain a `scope` qualifier; no status change |
| `orthogonal` | no changes |

Only `contradicts_supersedes` moves an article to the archive tier.
`context_split` retains both articles as active.

**Autonomy** (`reconciliation_autonomy` setting):

- `apply` (default) — verdicts are applied automatically; a bulk-supersession
  safeguard escalates when the number of `contradicts_supersedes` verdicts in a
  single run exceeds `bulk_supersession_threshold` (default 5) and no approval
  token is supplied.
- `propose` — verdicts are computed but not applied until an approval token is
  provided.

All status-mutating verdicts write a reversible record to
`~/.brain/reconciliation-undo.jsonl`.

### Archive and resurrection

Articles with `superseded` or `historical` status live in the **archive tier**:
they are excluded from the active `index.md` MOC files and from default
(`active`) query results, but remain on disk and appear in `archive.index.md`.
They are reachable via `scope=include_archive` or `scope=all`.

An article that is superseded by an article that is itself later invalidated
can be flagged for re-examination (`needs_rereconciliation: true` in
frontmatter). The optional re-reconciliation sweep (`rereconciliation_enabled`,
default off) processes these flagged articles, re-running the reconciliation
agent to decide whether to restore active status.

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
