# Brain Schema v1 — {{vault_name}}

This vault is managed by the brain overlay system. Any agent working in this vault should follow these conventions.

## Vault Identity

- **Name**: {{vault_name}}
- **Domain**: {{domain}}
- **Owner**: {{owner}}
- **Path mappings**: see `.brain/manifest.yaml`

## On Session Start

1. Read `.brain/manifest.yaml` to resolve paths
2. Read the index at the mapped `index` path for topic orientation
3. If the manifest maps identity files (soul/user/memory), load those for context
4. If task-relevant, browse knowledge articles in the mapped `knowledge` path

## On Capture (before session end or context compaction)

Handled globally by `~/.brain/capture/daily/` — no vault-specific action needed.

## On Ingest (external sources)

1. Place raw source material in the mapped `capture_inbox` path
2. Create or update a knowledge article in the mapped `knowledge` path
3. Set `sources` in YAML frontmatter for provenance
4. Update the index

## Writing Rules

- All new files get YAML frontmatter (at minimum `type` field)
- Knowledge articles go in the mapped `knowledge` path
- Never modify files in the mapped `archive` path
- Update the index after creating knowledge articles
- Use standard markdown links `[text](path)`, not platform-specific syntax
- Follow existing vault naming conventions (detected during setup)

## Frontmatter Schema

```yaml
---
type: article | capture | index
title: "Human-readable title"
domain: {{domain}}
sources: []
related: []
tags: []
confidence: high | medium | low
status: draft | active | archived
created: YYYY-MM-DD
updated: YYYY-MM-DD
promoted_from: ~/.brain/capture/daily/YYYY-MM-DD.md
agent_last_edit: <agent-name>
---
```

Only `type` is required. All other fields are optional.

## Cross-Vault References

Use `vault://` URIs to reference other vaults: `vault://<vault-name>/knowledge/<topic>`

These resolve through `~/.brain/registry.yaml`.

## Security

- **Tier 0 (Read)**: Any agent can read any file. Default.
- **Tier 1 (Capture)**: Any agent can write to capture paths and append to `.brain/log.md`.
- **Tier 2 (Knowledge)**: Trusted agents can create/update articles and update the index.
- **Tier 3 (Structure)**: Only human or admin agent modifies manifest, schema, identity files.
