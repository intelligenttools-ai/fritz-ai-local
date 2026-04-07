# Brain Compile

Promote raw captures into compiled knowledge articles. This is the "compiler" step from Karpathy's LLM Wiki pattern — raw daily logs and session captures get processed into structured, cross-referenced knowledge articles.

## Trigger

Activate when the user asks to:
- Compile, flush, or promote brain captures
- Process daily logs into knowledge
- Update the knowledge base from recent sessions
- Run `/brain-compile`

## Workflow

### 1. Discover vault and read manifest

Read `.brain/manifest.yaml` in the current working directory (or walk up to find it). Resolve paths for:
- `capture_daily` — where daily logs live
- `capture_sessions` — where session captures live
- `knowledge` — where compiled articles go
- `index` — the master index file

### 2. Find unprocessed captures

Read `.brain/log.md` to find the last COMPILE operation timestamp. Then find all capture files newer than that date.

If no previous COMPILE exists, process all captures.

Read each capture file. These contain session summaries with:
- Topics discussed
- Tools used
- Key responses
- Decisions made
- Lessons learned

### 3. Extract promotable knowledge

For each capture file, identify content worth promoting:

- **Decisions** that affect future work → article in knowledge area
- **Patterns** that solved real problems → article in knowledge resources
- **Facts** about the domain not previously known → update existing or new article
- **Corrections** to existing knowledge → update existing article
- **Lessons from failures** → article in knowledge resources

Skip ephemeral content:
- Routine Q&A that doesn't produce lasting knowledge
- Tool outputs without insight
- Repetitive status checks

### 4. Create or update knowledge articles

For each promotable item:

**Check if an article already covers this topic:**
- Search `knowledge/` by filename and content
- Check the index for related entries

**If article exists — UPDATE it:**
- Add new information to the appropriate section
- Update the `updated` date in frontmatter
- Add the capture file to `sources`
- Ensure backlinks to related articles

**If no article exists — CREATE one:**
- Place in the appropriate PARA subfolder:
  - `100_Projects/` — active project-specific knowledge
  - `200_Areas/` — ongoing responsibility areas
  - `300_Resources/` — reusable patterns, references, how-tos
  - `400_Archives/` — completed project knowledge
- Use descriptive filename: `<topic-slug>.md` (lowercase, hyphenated)
- Include full frontmatter:

```yaml
---
type: article
title: "Descriptive title"
domain: <from manifest>
sources:
  - <relative path to capture file>
related:
  - <paths to related articles>
tags: [<relevant tags>]
confidence: medium
status: active
created: <today>
updated: <today>
promoted_from: <capture file path>
agent_last_edit: claude-code
---
```

- Write clear, concise content
- Use standard markdown links `[text](path)` for cross-references
- No platform-specific syntax (no Obsidian callouts, no wiki-links as sole reference)

### 5. Update the index

Read and update the index file (path from manifest `index` key) to include new articles. The index should list:
- All knowledge articles grouped by PARA category
- Brief description of each
- Path to the file

### 6. Log the operation

Append to `.brain/log.md`:
```
YYYY-MM-DD HH:MM | COMPILE | <agent> | Processed N captures, created X articles, updated Y articles
```

## Example

Given a daily capture with:
```
### Topics discussed
- How to structure invoicing workflow for VanillaCore
- Decided on three-stage pipeline: draft → review → send

### Key responses
- Quarterly summary template needed in Areas
```

The compile step would:
1. Create `knowledge/200_Areas/invoicing-workflow.md` with the three-stage pipeline decision
2. Create `knowledge/200_Areas/quarterly-summary.md` or note it as a TODO
3. Update index with both new entries
4. Log: `COMPILE | claude-code | Processed 1 capture, created 2 articles`

## Important

- Do NOT compile if there are no new captures since the last COMPILE
- Do NOT create articles for trivial or ephemeral content
- DO cross-reference related articles using `related` frontmatter
- DO preserve existing article content when updating — append or integrate, never overwrite
- Each compile run should be idempotent — running twice on the same captures produces the same result
