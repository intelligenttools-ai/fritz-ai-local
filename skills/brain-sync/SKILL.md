# Brain Sync

Synchronize knowledge articles from brain vaults to external systems. Brain markdown is the source of truth — external systems are collaborative views.

Currently supports AFFiNE via MCP. The adapter pattern allows adding other targets (Notion, Confluence, etc.).

## Trigger

Activate when the user asks to:
- Sync the brain/knowledge to AFFiNE
- Push knowledge articles to docs
- Run `/brain-sync`
- "Update AFFiNE with latest knowledge"

## Supported Targets

### AFFiNE (via MCP)

Uses the `mcp__affine__` tool prefix. Requires the AFFiNE MCP server to be configured.

## Workflow

### 1. Identify what to sync

Read `~/.brain/registry.yaml`. For each vault with `sync: affine` (or the specified target):
1. Read the vault's `.brain/manifest.yaml` to find the `knowledge` path
2. Read the vault's `.brain/log.md` to find the last SYNC operation timestamp
3. Find all knowledge articles newer than the last sync (by file modification time or `updated` frontmatter)

If no previous SYNC exists, sync all knowledge articles.

If the user specifies a vault or article, sync only that.

### 2. For each article to sync

Read the article's markdown content and frontmatter.

#### Check if it exists in AFFiNE

Use `mcp__affine__search_docs` with the article title. If a matching doc is found:
- Read the existing doc with `mcp__affine__read_doc`
- Compare content — skip if unchanged
- If changed: update with `mcp__affine__replace_doc_with_markdown`
- Update the doc title if it changed: `mcp__affine__update_doc_title`

#### If it doesn't exist in AFFiNE

1. Create the doc: `mcp__affine__create_doc_from_markdown` with the article content
2. Move it to the correct folder: `mcp__affine__move_doc`
   - Use the vault's workspace ID from the registry
   - Create the folder structure if needed: `mcp__affine__create_folder`
   - Mirror the vault's knowledge directory structure in AFFiNE

### 3. Folder mapping

Map vault knowledge paths to AFFiNE folder structure:

```
vault knowledge/100_Projects/ → AFFiNE: Brain/<vault-name>/Projects/
vault knowledge/200_Areas/    → AFFiNE: Brain/<vault-name>/Areas/
vault knowledge/300_Resources/→ AFFiNE: Brain/<vault-name>/Resources/
vault knowledge/             → AFFiNE: Brain/<vault-name>/
```

Create the `Brain/` top-level folder and vault subfolder if they don't exist.

### 4. Handle cross-vault references

`vault://` URIs in article content should be converted to AFFiNE doc links where the target article has also been synced. If the target hasn't been synced, leave the URI as-is (it's still meaningful as a reference).

### 5. Log the operation

Append to `~/.brain/log.md`:
```
YYYY-MM-DD HH:MM | SYNC | <agent> | Synced N articles from <vault> to affine (X created, Y updated, Z unchanged)
```

Append to the vault's `.brain/log.md` as well.

### 6. Report

Show the user:
- How many articles were synced
- Which were created vs updated vs unchanged
- Any errors (e.g., AFFiNE MCP not available)

## Configuration

In `~/.brain/registry.yaml`, vaults can specify sync targets:

```yaml
vaults:
  engineering:
    path: ~/Notes/Engineering
    sync: affine
    affine_workspace_id: "<workspace-id>"

  work:
    path: ~/Notes/Work
    sync: local    # No external sync

  privat:
    path: ~/Notes/Privat
    sync: none     # Never sync (private content)
```

Sync values:
- `affine` — sync to AFFiNE via MCP
- `local` — no external sync (default)
- `none` — explicitly never sync (private/sensitive)

## Adding New Sync Targets

To add a new target (e.g., Notion, Confluence):
1. Create a new skill or extend this one
2. Implement the same workflow: search → compare → create/update
3. Add the target name as a valid `sync` value in the registry
4. Use the target's MCP tools or API

## Important

- Brain markdown is ALWAYS the source of truth. Never pull from AFFiNE back to brain.
- Never sync vaults with `sync: none` — these contain private/sensitive content.
- Respect the `exclude` list in the vault manifest.
- If AFFiNE MCP is not available, report the error and skip — don't fail the entire operation.
- Sync should be idempotent — running twice produces the same result.
