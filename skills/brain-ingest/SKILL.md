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

Write the raw content to `capture/inbox/` (path from manifest):
- Filename: `YYYY-MM-DD-<source-slug>.md`
- Include frontmatter:

```yaml
---
type: capture
title: "<source title>"
domain: <from manifest>
sources:
  - <original URL or path>
created: <today>
agent_last_edit: <agent>
---
```

### 3. Compile into knowledge article

Read the raw source and create a compiled knowledge article:

- Extract key concepts, facts, and insights
- Structure as a clear, scannable article
- Place in the appropriate `knowledge/` subfolder based on topic
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
