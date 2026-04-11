---
name: fritz:update
description: >
  Update Fritz Local to the latest version. Pulls from git, symlinks new skills,
  runs pending migrations, and reports changes. Use when the session-start hook
  reports an update is available, or run /fritz:update manually.
---

# Update

Update the local Fritz Local installation to the latest version.

## Trigger

Activate when:
- The session-start hook reports a Fritz Local update is available
- The user asks to update or upgrade Fritz Local
- Run `/fritz:update`

## Workflow

### 1. Pull latest

Run:
```
git -C ~/.fritz-ai-local pull
```

On Windows use `%USERPROFILE%\.fritz-ai-local`. If the pull fails (dirty tree, merge conflict), report the error and stop.

### 2. Read version change

Read `~/.fritz-ai-local/VERSION` for the new version. Compare with the version shown in the update notification (if any). Report the version bump.

### 3. Symlink new skills

List all directories in `~/.fritz-ai-local/skills/`. For each `fritz:*` skill directory, check if a symlink exists in the agent's skill directory:
- Claude Code: `~/.claude/skills/`
- Codex CLI: `~/.codex/skills/`
- Gemini CLI: `~/.gemini/skills/`

If a skill directory exists in the repo but has no symlink, create the symlink.

If a skill directory was removed from the repo but a symlink still exists, **warn the user** but do NOT delete the symlink. The human decides.

### 4. Run pending migrations

Check `~/.fritz-ai-local/migrations/` for numbered Python scripts (e.g., `001-add-settings.py`). Check `~/.brain/.migrations-run` for which migrations have already been executed. Run any new migrations in order.

Each migration script:
- Receives no arguments
- Reads/modifies files in `~/.brain/` or vault directories
- Is idempotent (safe to run twice)
- Prints a summary of what it did

After running, append the migration number to `~/.brain/.migrations-run`.

### 5. Report

Show the user:
- Version change (e.g., `1.0.0 → 1.1.0`)
- New skills added
- Removed skills (warnings only)
- Migrations run and their summaries
- Any errors encountered

## Important

- Execute immediately when invoked. No second confirmation.
- If `~/.fritz-ai-local` is not a git repo, report the error and suggest re-cloning.
- On Windows, use `%USERPROFILE%` for `~` and `mklink` for symlinks.
- Log the update operation to `~/.brain/log.md`.
