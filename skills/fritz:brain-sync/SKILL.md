---
name: fritz:brain-sync
description: >
  Push knowledge articles from brain vaults to external systems (AFFiNE, Obsidian,
  etc.). Brain markdown is source of truth — external systems are views.
  Use when the user asks to sync, push, or export brain knowledge, or run /fritz:brain-sync.
---

# Brain Sync

Push knowledge articles from brain vaults to external systems. Brain markdown is the source of truth — external systems are collaborative views.

## Trigger

Activate when the user asks to:
- Sync the brain/knowledge to an external system
- Push knowledge articles externally
- Run `/fritz:brain-sync`

## Workflow

### Service-first gate

If this skill is invoked by an interactive agent, first read `~/.brain/registry.yaml`. If `settings.local_brain_service` is absent, ask the human which behavior they want, then write the selected setting before choosing the sync path. The choices are: enable and configure the Docker service, keep local workflows with future setup suggestions, or keep local workflows without suggestions.

After the setting exists, check `settings.local_brain_service.enabled`. It must be boolean `true`; strings such as `"true"` or `"false"` do not enable service routing. If enabled, check whether the configured service is reachable with `GET <base_url>/v1/status`, using the token from `settings.local_brain_service.api_token_env` when that environment variable is set. Only use loopback URLs unless `settings.local_brain_service.allow_remote: true` is explicitly configured. Reject service URLs with credentials, query strings, fragments, or non-root paths. If enabled and reachable, use the service-backed sync path first with `POST <base_url>/v1/sync/run`; from the host, prefer `curl`/HTTP and do not assume a Local Brain CLI is installed on PATH. Do not also perform the manual sync workflow below for the same articles unless the service is unavailable or the human explicitly requests the non-service path.

If the service is disabled or not reachable, continue with the manual workflow below. The Docker service is optional and absence of the service is not an error.

If service mode is disabled and `settings.local_brain_service.suggest_setup` is not `false`, you may briefly offer to configure the optional Docker stack when sync is repeated, external, or part of a larger automated preservation workflow. Do not block sync if the human declines.

If these instructions are being used inside the Local Brain service sync agent itself, do not call the service again. Continue with the workflow below as sync policy guidance.

### 1. Identify what to sync

Read `~/.brain/registry.yaml`. For each vault where `sync` is not `local` or `none`:
1. Read the vault's `.brain/manifest.yaml` to find the `knowledge` path
2. Read the vault's `.brain/log.md` to find the last SYNC operation timestamp
3. Find all knowledge articles newer than the last sync (by file modification time or `updated` frontmatter)

If no previous SYNC exists, sync all knowledge articles.

If the user specifies a vault or article, sync only that.

### 2. Determine the sync target

The `sync` value in the registry tells you WHAT to sync to, not HOW. The agent determines HOW based on available tools:

- Check which MCP tools are available (AFFiNE, Notion, Confluence, etc.)
- Check if git remotes are configured
- Check if filesystem sync paths exist (Nextcloud, Dropbox, etc.)

If the configured sync target's tools aren't available, report the error and skip.

### 3. For each article to sync

1. **Read** the article's markdown content and frontmatter
2. **Search** the target system for an existing version (by title or ID)
3. **Compare** — skip if unchanged
4. **Create or update** in the target system
5. **Organize** — place in the correct folder/space/section, mirroring the vault's knowledge structure

### 4. Handle cross-vault references

`vault://` URIs in article content should be converted to target-system links where the referenced article has also been synced. If it hasn't, leave the URI as-is.

### 5. Log the operation

Append to `~/.brain/log.md` and the vault's `.brain/log.md`:
```
YYYY-MM-DD HH:MM | SYNC | <agent> | Synced N articles from <vault> to <target> (X created, Y updated, Z unchanged)
```

### 6. Report

Show the user what was synced, created, updated, skipped, or errored.

## Configuration

In `~/.brain/registry.yaml`:

```yaml
vaults:
  engineering:
    path: ~/Notes/Engineering
    sync: affine                    # Sync target name
    sync_config:                    # Target-specific config (optional)
      workspace_id: "<id>"

  research:
    path: ~/Notes/Research
    sync: git                       # Push to git remote

  work:
    path: ~/Notes/Work
    sync: local                     # No external sync (default)

  personal:
    path: ~/Notes/Personal
    sync: none                      # Never sync — private content
```

The `sync` value is a hint. The agent uses its available tools to implement the sync. Common targets:

| Target | How the agent syncs | Tools needed |
|--------|-------------------|-------------|
| `affine` | MCP tools | `mcp__affine__*` |
| `notion` | MCP tools | `mcp__notion__*` |
| `confluence` | MCP tools or REST API | Confluence MCP or API access |
| `git` | Git push to remote | `git` CLI |
| `filesystem` | Copy to synced folder | Filesystem access (Nextcloud, Dropbox path) |

## Important

- Brain markdown is ALWAYS the source of truth. Never pull from external systems back to brain.
- Never sync vaults with `sync: none`.
- Respect the `exclude` list in the vault manifest.
- If the target's tools aren't available, report and skip — don't fail.
- Sync should be idempotent.
