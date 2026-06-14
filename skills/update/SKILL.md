---
name: update
description: >
  Update Fritz Local to the latest version. Pulls from git, symlinks new skills
  and managed hooks, runs pending migrations, and reports changes. Use when the
  session-start hook reports an update is available, or run /update manually.
---

# Update

Update the local Fritz Local installation to the latest version.

## Trigger

Activate when:
- The session-start hook reports a Fritz Local update is available
- The user asks to update or upgrade Fritz Local
- Run `/update`

## Workflow

### 0. Resolve the repo location

Fritz can be cloned anywhere; do not assume `~/.fritz-ai-local`. Resolve the
real repo root first and use it for every command below (shown as `<REPO>`):

```
python3 -c "import pathlib,os; print(os.environ.get('FRITZ_REPO_PATH') or pathlib.Path('$HOME/.brain/hooks/brain_common.py').resolve().parents[1])"
```

This follows the `~/.brain/hooks/` symlinks back to the actual clone. If
`~/.brain/hooks/brain_common.py` is missing, fall back to wherever the managed
hooks were symlinked from, or ask the human for the clone path. On Windows,
substitute `%USERPROFILE%` for `$HOME`.

### 1. Pull latest

Run:
```
git -C <REPO> pull
```

If the pull fails (dirty tree, merge conflict), report the error and stop.

### 2. Read version change

Read `<REPO>/VERSION` for the new version. Compare with the version shown in the update notification (if any). Report the version bump.

### 3. Symlink new skills and managed hooks

List all directories in `<REPO>/skills/`. For each skill directory (any subdir containing a `SKILL.md`), check if a symlink exists in the agent's skill directory:
- Claude Code: `~/.claude/skills/`
- Codex CLI: `~/.codex/skills/`
- Gemini CLI: `~/.gemini/skills/`

If a skill directory exists in the repo but has no symlink, create the symlink.

If a skill directory was removed from the repo but a symlink still exists, **warn the user** but do NOT delete the symlink. The human decides.

Then refresh managed hook symlinks from `<REPO>/hooks/` into
`~/.brain/hooks/` for all Python hook files required by the current agent.
At minimum keep these common hooks current:
- `brain_capture.py`
- `brain_session_start.py`
- `brain_prompt_check.py`
- `brain_common.py`
- `brain_security.py`

For Hermes Agent, also symlink:
- `hermes_brain_context.py`
- `hermes_brain_capture.py`

### 4. Run pending migrations

Check `<REPO>/migrations/` for numbered Python scripts (e.g., `001-add-settings.py`). Check `~/.brain/.migrations-run` for which migrations have already been executed. Run any new migrations in order.

Each migration script:
- Receives no arguments
- Reads/modifies files in `~/.brain/` or vault directories
- Is idempotent (safe to run twice)
- Prints a summary of what it did

After running each pending migration, the runner must **ensure** that
migration's number is recorded in `~/.brain/.migrations-run`. Do a line-exact
membership check (so `002` does not match `0020`) and append the number on its
own line **only if it is not already present**. Some migrations (e.g. 002) also
self-record their own number; because the runner appends only when the number is
absent, this is safe and never double-records. Migrations that do not self-record
(e.g. 001) rely on the runner to record them.

### 5. Scan vaults for brain contract drift

The brain contract declared in `brain-setup` carries a
`brain_contract_version`. When the skill ships a new version, existing vaults
keep their older `brain.md` until the human re-runs setup there.

This step is a passive scan — read-only. No writes, no prompts, no per-vault
updates.

1. Read the current version declared in
   `<REPO>/skills/brain-setup/SKILL.md` by locating the line
   `The current brain contract version is \`N\``. If it cannot be parsed,
   skip this step silently.
2. Read `~/.brain/registry.yaml` and iterate over its `vaults:` entries.
3. For each vault, open `<vault-path>/.brain/instructions/brain.md` and parse
   its frontmatter. If the file is absent, the vault has not been set up with
   the new architecture yet — flag it for the report as "no brain.md".
4. If the file's `brain_contract_version` is less than the current version,
   flag the vault for the report as "outdated (vN → vM)".
5. Do not touch any file. This is a detection step only.

### 6. Resolve Local Brain service behavior if unconfigured

Read `~/.brain/registry.yaml`. If `settings.local_brain_service` is absent,
pause and ask the human which behavior they want for the optional Dockerized
Local Brain service:

1. Configure and start the optional Docker Local Brain service now.
   - After setup succeeds, write:
     ```yaml
     settings:
       local_brain_service:
         enabled: true
         base_url: http://127.0.0.1:8765
         api_token: <same random value as API_TOKEN, when trusted local agents should authenticate automatically>
         api_token_env: LOCAL_BRAIN_API_TOKEN
         allow_remote: false
         suggest_setup: true
     ```
2. Keep using the existing local slash-skill workflow, but allow future setup
   suggestions.
   - Write `enabled: false`, `base_url: http://127.0.0.1:8765`,
      `api_token_env: LOCAL_BRAIN_API_TOKEN`, optionally `api_token` for trusted local agent use, `allow_remote: false`, and
     `suggest_setup: true`.
3. Keep using the existing local slash-skill workflow, and stop future setup
   suggestions.
   - Write `enabled: false`, `base_url: http://127.0.0.1:8765`,
      `api_token_env: LOCAL_BRAIN_API_TOKEN`, optionally `api_token` for trusted local agent use, `allow_remote: false`, and
     `suggest_setup: false`.

Do not start Docker or set `enabled: true` without explicit human approval. If
the registry setting already exists, do not ask and do not overwrite it.

### 7. Report

Show the user:
- Version change (e.g., `1.0.0 → 1.1.0`)
- New skills added
- Hook symlinks refreshed or missing
- Removed skills (warnings only)
- Migrations run and their summaries
- Local Brain service setting state: report whether `settings.local_brain_service`
  exists, whether it is enabled, and whether a human decision was recorded during
  the update.
- Brain contract drift: list of vaults with outdated or missing `brain.md`,
  along with "Run `/brain-setup` in those vaults to refresh."
- Any errors encountered

## Important

- Execute immediately when invoked. No second confirmation.
- If `<REPO>` is not a git repo, report the error and suggest re-cloning.
- On Windows, use `%USERPROFILE%` for `~` and `mklink` for symlinks.
- Log the update operation to `~/.brain/log.md`.
