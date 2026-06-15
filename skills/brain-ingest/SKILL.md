---
name: brain-ingest
description: >
  Import external sources into the brain — articles, videos, papers, web pages,
  handover documents. Use when the user asks to ingest, import, or add external
  content to the knowledge base, or run /brain-ingest.
---

# Brain Ingest

Import external sources into the brain — articles, videos, papers, web pages. This is Karpathy's "raw → wiki" ingest operation.

## Trigger

Activate when the user asks to:
- Ingest, import, or add an external source to the brain/knowledge base
- Save an article, video transcript, paper, or web page for future reference
- Run `/brain-ingest`

## Workflow

### 1. Capture the source

Determine the source type and obtain the content:

- **URL**: Fetch the page content using WebFetch or browser tools
- **YouTube video**: Use the youtube-transcript skill to get the transcript
- **File path**: Read the file directly
- **Pasted text**: Use the content as-is

### 2. Save raw source to inbox

Write the raw content to `~/.brain/capture/inbox/` as a provenance-tagged
capture. Filename: `YYYY-MM-DD-<source-slug>.md`. Include frontmatter:

```yaml
---
type: capture
title: "<source title>"
sources:
  - <original URL or path>
created: <today>
agent_last_edit: <agent>
---
```

For content mirrored from an external system (e.g. via the service's mirror
agent), the frontmatter also carries provenance fields written by the mirror
pipeline:

```yaml
---
type: capture
title: "<entry title>"
source: "<target-name> (<target-kind>)"
mirrored_at: "<ISO-8601 timestamp>"
mode: "full" | "index-only"
pointer: "<target-name>:<relpath-or-id>"
---
```

Do not write these provenance fields manually when ingesting from a URL or
file — they are reserved for the service mirror path.

### 3. Trigger live processing when enabled

After writing the inbox capture, call the configured Fritz Local processing path:

- If `settings.local_brain_service.enabled: true` and `auto_compile_on_ingest` is not `false`, trigger the Local Brain compile path (service first, local fallback) so the capture does not silently pile up.
- If processing is not active, leave the raw capture in `~/.brain/capture/inbox/` and report that compile is pending/manual.

If live processing is unavailable and manual compile is needed, read the raw source and create a compiled knowledge article. In registry-free mode, write to `~/.brain/knowledge/<scope>/<section>/<slug>.md` (scope = `common` or project slug; section = `decisions`, `lessons`, `runbooks`, or `context`). In vault mode, write to the appropriate `knowledge/` subfolder. Either way:

- Extract key concepts, facts, and insights
- Structure as a clear, scannable article
- Cross-reference with existing articles where relevant
- Include full frontmatter with `sources` pointing to the inbox file

### 4. Update index and log

- Add the new article to the index file
- Append to `.brain/log.md`:
```
YYYY-MM-DD HH:MM | INGEST | <agent> | Ingested "<title>" from <source type>, created <article path>
```

## Example

User: "Ingest this video: https://youtu.be/1FiER-40zng"

1. Fetch transcript via youtube-transcript skill
2. Save raw transcript to `capture/inbox/2026-04-07-cole-medin-second-brain.md`
3. Extract key concepts: memory layer, hooks, skills, heartbeat, zero-trust security
4. Create `knowledge/300_Resources/ai-second-brain-architecture.md` with structured summary
5. Update index, log the operation

## Important

- Always preserve the raw source in `capture/inbox/` — it's the provenance chain
- The knowledge article is a distillation, not a copy — extract insight, don't dump text
- Cross-reference existing articles using `related` frontmatter
