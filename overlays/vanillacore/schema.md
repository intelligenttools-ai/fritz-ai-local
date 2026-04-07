# Brain Schema v1 — VanillaCore

This vault is managed by the brain overlay system. Any agent working in this vault should follow these conventions.

## Vault Identity

- **Name**: vanillacore
- **Domain**: work
- **Owner**: karsten
- **Path mappings**: see `.brain/manifest.yaml`

## On Session Start

1. Read `.brain/manifest.yaml` to resolve paths
2. Read the index at `200_Brain_COO/index.md` for topic orientation
3. If the manifest maps soul/user/memory, load those for context:
   - `200_Brain_COO/SOUL.md` — agent persona
   - `200_Brain_COO/USER.md` — user context
   - `200_Brain_COO/MEMORY.md` — durable facts
4. If task-relevant, browse knowledge articles in `200_Brain_COO/500_Para/`

## On Capture (before session end or context compaction)

1. Summarize key decisions, lessons, and facts from the session
2. Write summary to `200_Brain_COO/400_Daily/YYYY-MM-DD.md` (append if file exists)
3. If session produced reusable artifacts, also write to `.brain/capture/sessions/YYYY-MM-DD-<agent>.md`
4. Append a one-line entry to `.brain/log.md`

## On Ingest (external sources)

1. Place raw source material in `.brain/capture/inbox/`
2. Create or update a knowledge article in `200_Brain_COO/500_Para/` based on the source
3. Set `sources` in YAML frontmatter for provenance
4. Update `200_Brain_COO/index.md`

## Writing Rules

- All new files get YAML frontmatter (see below)
- Knowledge articles go in `200_Brain_COO/500_Para/` (PARA structure: Projects, Areas, Resources, Archives)
- Raw captures go in `200_Brain_COO/400_Daily/` or `.brain/capture/`
- Never modify files in `.brain/archive/`
- Update `200_Brain_COO/index.md` after creating knowledge articles
- Use standard markdown links `[text](path)`, not platform-specific syntax
- Follow vault naming conventions: no numeric prefixes on files, `YYYY-MM-DD <Title>.md` for working notes

## Frontmatter Schema

```yaml
---
type: article | capture | index
title: "Human-readable title"
domain: work
sources:
  - path/to/source.md
related:
  - vault://work/knowledge/topic
  - ./other-article.md
tags: []
confidence: high | medium | low
status: draft | active | archived
created: YYYY-MM-DD
updated: YYYY-MM-DD
promoted_from: capture/daily/YYYY-MM-DD.md
agent_last_edit: <agent-name>
---
```

Only `type` is required. All other fields are optional.

## Cross-Vault References

Use `vault://` URIs to reference other vaults: `vault://engineering/knowledge/k8s-networking`

These resolve through `~/.brain/registry.yaml`.

## Security

- **Tier 0 (Read)**: Any agent can read any file. Default.
- **Tier 1 (Capture)**: Any agent can write to `capture/` paths and append to `.brain/log.md`.
- **Tier 2 (Knowledge)**: Trusted agents can create/update articles in `500_Para/` and update `index.md`.
- **Tier 3 (Structure)**: Only human or admin agent modifies manifest, schema, soul, user, memory.
