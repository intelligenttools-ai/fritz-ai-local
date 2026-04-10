---
name: fritz:brain-compile
description: >
  Promote raw brain captures into compiled knowledge articles across vaults.
  Use when the user asks to compile, flush, or promote brain captures, process
  daily logs into knowledge, update the knowledge base, or run /fritz:brain-compile.
---

# Brain Compile

Promote raw captures into compiled knowledge articles. Reads from the global capture directory (`~/.brain/capture/`) and routes knowledge to the correct vault based on **content**, not working directory.

## Trigger

Activate when the user asks to:
- Compile, flush, or promote brain captures
- Process daily logs into knowledge
- Update the knowledge base from recent sessions
- Run `/fritz:brain-compile`

## Architecture

Captures are dumb — every conversation is saved to `~/.brain/capture/daily/` regardless of where the session happened. The compile step is where intelligence lives: it reads captures, analyzes content, and routes each piece of knowledge to the appropriate vault.

## Workflow

### 1. Read the vault registry

Read `~/.brain/registry.yaml` to get all available vaults and their domains. Each vault has a `.brain/manifest.yaml` mapping brain concepts to actual paths.

### 2. Find unprocessed captures

Read `~/.brain/log.md` to find the last COMPILE operation timestamp. Find all capture files in `~/.brain/capture/daily/` newer than that date.

If no previous COMPILE exists, process all captures.

### 3. Analyze and route

For each capture file, read the content and for each promotable item determine:

**Which vault does this belong in?** Route based on content:
- Business operations → `my-vault` vault
- Engineering runbooks, infrastructure → `engineering` vault
- Personal notes, ideas → `personal` vault
- AI agent development, research → `ai-agents` vault
- General work topics → `work` vault

Use the `cwd` recorded in the capture as a hint, but the **content** is the primary signal. A session at `~/Work/Development/fritz-ai-local` discussing AI agent architecture belongs in `ai-agents`, not `work`.

**Is this worth promoting?** Extract:
- **Decisions** that affect future work
- **Patterns** that solved real problems
- **Facts** about the domain not previously known
- **Corrections** to existing knowledge
- **Lessons from failures**

Skip ephemeral content: routine Q&A, tool outputs without insight, status checks.

### 4. Create or update knowledge articles

For each promotable item, read the target vault's manifest to find its `knowledge` path, then:

**Check if an article already covers this topic:**
- Search the vault's `knowledge/` by filename and content
- Check the vault's index for related entries

**If article exists — UPDATE it:**
- Add new information to the appropriate section
- Update `updated` date in frontmatter
- Add the capture file to `sources`

**If no article exists — CREATE one:**
- Place in the appropriate subfolder (PARA or whatever the vault uses)
- Use descriptive filename: `<topic-slug>.md` (lowercase, hyphenated)
- Include full frontmatter:

```yaml
---
type: article
title: "Descriptive title"
domain: <vault domain>
sources:
  - ~/.brain/capture/daily/YYYY-MM-DD.md
related:
  - <paths to related articles>
  - vault://<other-vault>/knowledge/<topic>
tags: [<relevant tags>]
confidence: medium
status: active
created: <today>
updated: <today>
promoted_from: ~/.brain/capture/daily/YYYY-MM-DD.md
agent_last_edit: <agent>
---
```

### 5. Update indexes and log

- Update each affected vault's index file
- Append to `~/.brain/log.md`:
```
YYYY-MM-DD HH:MM | COMPILE | <agent> | Processed N captures → X articles across Y vaults
```
- Append to each affected vault's `.brain/log.md` as well

## Important

- Do NOT compile if there are no new captures since the last COMPILE
- Do NOT create articles for trivial or ephemeral content
- DO cross-reference related articles across vaults using `vault://` URIs
- DO preserve existing article content when updating — integrate, never overwrite
- A single capture file may produce knowledge for multiple vaults
- Each compile run should be idempotent
