---
name: fritz:brain-lint
description: >
  Health checks on the brain overlay — validates vault integrity, finds gaps,
  reports issues. Use when the user asks to validate, check, or lint brain
  vaults, or run /fritz:brain-lint.
---

# Brain Lint

Health checks on the brain overlay — validates integrity, finds gaps, reports issues.

## Trigger

Activate when the user asks to:
- Lint, validate, or check the brain/knowledge base
- Find stale, broken, or orphaned content
- Run `/fritz:brain-lint`

## Checks

### Service-first gate

First read `~/.brain/registry.yaml`. If `settings.local_brain_service` is absent, ask the human which behavior they want, then write the selected setting before choosing the lint path. The choices are: enable and configure the Docker service, keep local workflows with future setup suggestions, or keep local workflows without suggestions.

After the setting exists, check `settings.local_brain_service.enabled`. It must be boolean `true`; strings such as `"true"` or `"false"` do not enable service routing. If enabled, check whether the configured service is reachable with `GET <base_url>/v1/status`, using the token from `settings.local_brain_service.api_token` or from the configured `api_token_env` environment variable. Only use loopback URLs unless `settings.local_brain_service.allow_remote: true` is explicitly configured. Reject service URLs with credentials, query strings, fragments, or non-root paths. If enabled and reachable, use the service-backed lint path first: prefer the registered MCP tool `brain_lint` when available and authorized, otherwise use `POST <base_url>/v1/lint/run` from the host. Do not assume a Local Brain CLI is installed on PATH. Do not also perform the manual lint workflow below for the same vaults unless the service is unavailable, returns insufficient details, or the human explicitly requests the non-service path.

If the service is disabled or not reachable, continue with the manual lint workflow below. The Docker service is optional and absence of the service is not an error.

If service mode is disabled and `settings.local_brain_service.suggest_setup` is not `false`, you may briefly offer to configure the optional Docker stack when linting is repeated, scheduled, or part of a larger automated brain hygiene workflow. Do not block lint if the human declines.

### 1. Frontmatter validation

Scan all files in `knowledge/` path (from manifest). For each `.md` file:
- Verify YAML frontmatter exists and parses
- Verify `type` field is present
- Warn if `updated` is older than 90 days (stale)
- Warn if `confidence: low` articles have no `sources`

### 2. Broken references

For all `related` entries in frontmatter and markdown links in article bodies:
- Verify linked files exist
- Verify `vault://` URIs point to vaults in `~/.brain/registry.yaml`
- Report broken links with file and line number

### 3. Orphaned captures

Find capture files (in `capture_daily/` and `capture_sessions/`) that:
- Are older than 14 days
- Were never referenced in a `promoted_from` field of any knowledge article
- These are candidates for compilation or archival

### 4. Index freshness

Compare knowledge articles on disk against entries in the index file:
- Articles on disk not in the index → missing from index
- Index entries pointing to non-existent files → stale index entries

### 5. Duplicate detection

Check for knowledge articles covering the same topic:
- Similar filenames
- Same tags with overlapping content
- Candidates for merging

## Output

Print a structured lint report:

```
## Brain Lint Report — <vault name>

### Errors (must fix)
- [ ] Broken link: `invoicing-workflow.md` references `./billing-setup.md` (not found)
- [ ] Missing frontmatter: `quick-note.md` has no YAML frontmatter

### Warnings (should fix)
- [ ] Stale article: `k8s-networking.md` last updated 2025-12-01 (120 days ago)
- [ ] Orphaned capture: `2026-03-15.md` never promoted (23 days old)
- [ ] Missing from index: `new-article.md` not listed in index.md

### Info
- Total knowledge articles: 12
- Total captures: 45
- Last compile: 2026-04-06
- Vault health: 85% (2 errors, 3 warnings)
```

Also append to `.brain/log.md`:
```
YYYY-MM-DD HH:MM | LINT | <agent> | N errors, M warnings, vault health X%
```

## Automation

This skill can be run on a schedule. To automate:
- **Claude Code**: Use `/schedule` to set up a cron trigger
- **Hermes Agent**: Add a cron job in `~/.hermes/cron/jobs.json`
- **Generic**: Run via any cron/scheduler that invokes the agent with "run /fritz:brain-lint"

Recommended frequency: daily or weekly.
