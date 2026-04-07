# Brain Lint

Health checks on the brain overlay — validates integrity, finds gaps, reports issues.

## Trigger

Activate when the user asks to:
- Lint, validate, or check the brain/knowledge base
- Find stale, broken, or orphaned content
- Run `/brain-lint`

## Checks

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
