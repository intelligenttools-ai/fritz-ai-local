---
name: fritz:brain-query
description: >
  Search and synthesize knowledge across all brain vaults. Use when the user
  asks to search the brain/knowledge base, find what they know about a topic,
  recall a decision/pattern/lesson, or run /fritz:brain-query.
---

# Brain Query

Search and synthesize knowledge across all brain vaults.

## Trigger

Activate when the user asks to:
- Search the brain/knowledge base
- Find what they know about a topic
- Recall a decision, pattern, or lesson
- Run `/fritz:brain-query`

## Workflow

### 1. Load the registry

Read `~/.brain/registry.yaml` to get all vaults. For each vault with a `.brain/manifest.yaml`, resolve its `index` and `knowledge` paths.

### 2. Search strategy (index-first, no vector DB)

Following Karpathy's insight: "I thought I had to reach for fancy RAG, but the LLM has been pretty good about auto-maintaining index files."

**Step 1 — Index scan**: Read each vault's index file. Use the index to identify which knowledge articles are likely relevant to the query.

**Step 2 — Article read**: Read the identified articles. Follow `related` links and `vault://` URIs to find connected knowledge across vaults.

**Step 3 — Capture search** (if knowledge articles insufficient): Search `~/.brain/capture/daily/` for raw captures that may contain relevant information not yet compiled into knowledge.

### 3. Synthesize and cite

Combine findings into a clear answer. Always cite sources:

```
Based on your knowledge base:

- [Topic X](vault://engineering/knowledge/k8s-networking.md): ...
- [Decision Y](vault://my-vault/knowledge/invoicing-workflow.md): ...
- [Raw capture](~/.brain/capture/daily/2026-04-05.md): ...
```

### 4. Optionally file the answer

If the synthesized answer is itself valuable knowledge (connects dots that weren't connected before), offer to file it as a new knowledge article via the brain-compile workflow.

## Search tools

Use standard file tools — no special infrastructure needed:

- **Grep**: search file contents across vault knowledge directories
- **Glob**: find articles by filename patterns
- **Read**: read index files and articles

For vaults with many articles, read the index first to narrow the search before reading individual files.

## Cross-vault queries

A query like "what do I know about Keycloak?" should search:
- `engineering` vault (infrastructure knowledge)
- `work` vault (customer implementations)
- `my-vault` vault (business operations)
- `~/.brain/capture/daily/` (recent uncategorized captures)

Use `vault://` URIs in citations so the user can navigate to the source.

## Important

- Always search across ALL vaults, not just the one matching cwd
- Cite sources — never present knowledge without attribution
- If nothing is found, say so clearly rather than hallucinating
- Prefer compiled knowledge articles over raw captures (higher signal)
- Suggest `/fritz:brain-compile` if relevant captures exist but haven't been compiled yet
