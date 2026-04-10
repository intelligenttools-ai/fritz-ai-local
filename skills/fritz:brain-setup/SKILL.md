---
name: fritz:brain-setup
description: >
  Set up the Fritz Local brain overlay for a new vault. Explores directory
  structure, generates manifest and schema, creates instruction files, registers
  the vault. Use when the user asks to set up a new brain vault, or run /fritz:brain-setup.
---

# Brain Setup

Set up the Fritz Local brain overlay for a vault. Explores the vault's directory structure, generates the manifest and schema, creates instruction files, and registers the vault.

## Trigger

Activate when the user asks to:
- Set up a vault for the brain system
- Initialize the brain for a directory/vault
- Run `/fritz:brain-setup`
- "Add this vault to the brain"

Also activate when the brain system is installed but a vault in the registry has no `.brain/manifest.yaml`.

## Workflow

### 1. Identify the target vault

If the user specifies a path, use that. Otherwise use the current working directory.

Check if this path is already in `~/.brain/registry.yaml`. If not, ask the user for:
- **name**: short identifier (lowercase, no spaces)
- **domain**: work | personal | engineering | research | <custom>

### 2. Explore the vault structure

List the top-level directories and key files. Look for:

**Daily/journal patterns** (→ `capture_daily`):
- Folders named: `Daily/`, `Journal/`, `Journals/`, `*Daily*/`, `*daily*/`
- Folders containing files matching `YYYY-MM-DD*.md`

**Knowledge/wiki patterns** (→ `knowledge`):
- Folders named: `Knowledge/`, `Wiki/`, `Articles/`, `Resources/`, `*Para*/`, `*PARA*/`
- Folders with topical subfolders (not date-based)

**Index patterns** (→ `index`):
- Files named: `index.md`, `INDEX.md`, `_index.md`, `MOC.md`, `README.md` (at vault root or in knowledge dir)

**Identity file patterns** (→ `soul`, `user`, `memory`):
- Files named: `SOUL.md`, `USER.md`, `MEMORY.md` (anywhere in vault)

**Archive patterns** (→ `archive`):
- Folders named: `Archive/`, `*Archive*/`, `*archive*/`

**Inbox/notes patterns** (→ `capture_inbox`):
- Folders named: `Inbox/`, `Notes/`, `Ideas/`, `Unsorted/`

**Naming conventions**:
- Numeric prefixes (`100_`, `200_`)? → Johnny Decimal style
- Date prefixes (`YYYY-MM-DD`)? → Date-based naming
- Plain names? → Flat naming

**Exclusions**:
- `.obsidian/`, `.trash/`, `_Attachments/`, `node_modules/`, `.git/`
- Anything that looks like credentials, keys, or secrets

### 3. Generate the manifest

Create `.brain/manifest.yaml` with the discovered path mappings:

```yaml
version: 1
name: <vault-name>
domain: <domain>
owner: <from registry or ask>

paths:
  capture_daily: <discovered path or .brain/capture/daily/>
  capture_sessions: .brain/capture/sessions/
  capture_inbox: <discovered path or .brain/capture/inbox/>
  knowledge: <discovered path>
  index: <discovered path or .brain/index.md>
  archive: <discovered path or .brain/archive/>
  # Optional identity files — only if found
  soul: <path if found>
  user: <path if found>
  memory: <path if found>

exclude:
  - ".obsidian/"
  - ".trash/"
  - <any secrets/keys directories found>
```

If a concept has no matching directory, use `.brain/` defaults (e.g., `.brain/capture/daily/`).

### 4. Generate the schema

Read `templates/schema.template.md` from the fritz-ai-local repo (at `~/.brain/` or the repo path). Replace `{{vault_name}}`, `{{domain}}`, `{{owner}}` placeholders. Write to `.brain/schema.md`.

If the template is not available, generate the schema from scratch following the same structure.

### 5. Generate instruction files

For each agent type, generate a brief instruction file in `.brain/instructions/`:
- `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `HERMES.md`

Each should contain:
- Vault name and domain
- Quick reference to key paths (from the manifest)
- "Save key decisions and lessons before session end" instruction
- "Read `.brain/schema.md` for full details" pointer

Symlink these to the vault root so each agent finds its instruction file where it expects it.

### 6. Create the index

If no index file was found, create `.brain/index.md` with:
- List of discovered knowledge directories and their apparent purpose
- Any existing markdown files in the knowledge path
- A note that this index should be updated as knowledge articles are added

### 7. Create supporting structure

```bash
mkdir -p .brain/{instructions,capture/sessions,capture/inbox}
```

Create `.brain/log.md` if it doesn't exist.

### 8. Register the vault

Add or update the vault entry in `~/.brain/registry.yaml`.

### 9. Report

Show the user:
- What was discovered
- What paths were mapped
- What was created
- What the agent couldn't figure out (ask for clarification)

## Important

- NEVER restructure or move existing files. The manifest maps to what exists.
- If you can't determine a mapping, use `.brain/` defaults and note it in the report.
- Respect existing conventions — if the vault uses numeric prefixes, note that in the schema.
- The vault owner may have patterns you don't recognize. When in doubt, ask.
- Exclude sensitive directories (keys, credentials, secrets) from the manifest.
