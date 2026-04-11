---
name: fritz:brain-setup
description: >
  Set up the Fritz Local brain overlay for a new vault. Explores directory
  structure, presents findings, asks questions interactively, then creates
  structure based on human answers. Handles per-project directories, common/
  shared area, index files, .fritz-local.json creation, and context injection
  configuration. Use when the user asks to set up a brain vault, add a project,
  or run /fritz:brain-setup.
---

# Brain Setup

The human decides structure. The agent discovers, proposes, and executes.

## Trigger

Activate when the user asks to:
- Set up a vault for the brain system
- Initialize the brain for a directory/vault
- Run `/fritz:brain-setup`
- "Add this vault to the brain"
- Add a new project to an existing vault

Also activate when the brain system is installed but a vault in the registry has no `.brain/manifest.yaml`.

## Workflow

### Phase 1: Analyze

Explore the vault's directory structure. Detect the following patterns:

**Daily/journal patterns** (→ `capture_daily`):
- Folders named: `Daily/`, `Journal/`, `Journals/`, `*Daily*/`, `*daily*/`
- Folders containing files matching `YYYY-MM-DD*.md`

**Knowledge/wiki patterns** (→ `knowledge`):
- Folders named: `Knowledge/`, `Wiki/`, `Articles/`, `Resources/`, `*Para*/`, `*PARA*/`
- Folders with topical subfolders (not date-based)

**Per-project directories**:
- Folders that appear to correspond to named projects, products, or codebases
- Folders with a `README.md` or `index.md` at their root
- Folders referenced in a registry, config, or manifest file in the vault

**Index patterns** (→ `index`):
- Files named: `index.md`, `INDEX.md`, `_index.md`, `MOC.md`, `README.md` at vault root or in knowledge dir

**Identity file patterns** (→ `soul`, `user`, `memory`):
- Files named: `SOUL.md`, `USER.md`, `MEMORY.md` anywhere in vault

**Archive patterns** (→ `archive`):
- Folders named: `Archive/`, `*Archive*/`, `*archive*/`

**Naming conventions**:
- Numeric prefixes (`100_`, `200_`)? → Johnny Decimal style
- Date prefixes (`YYYY-MM-DD`)? → Date-based naming
- Plain names? → Flat naming

**Exclusions**:
- `.obsidian/`, `.trash/`, `_Attachments/`, `node_modules/`, `.git/`
- Anything that looks like credentials, keys, or secrets

---

### Phase 2: Present Findings

After analysis, show a structured summary of what was discovered and what's missing. Do not create anything yet.

Example output:

```
Brain Setup — Discovery Report
================================

Vault path: ~/Notes/Engineering

Found:
  ✓ Daily notes     → Daily/
  ✓ Knowledge dir   → Wiki/
  ✓ Archive         → Archive/
  ✓ Index file      → Wiki/index.md
  ✓ Projects        → Projects/fritz-ai/, Projects/api-gateway/, Projects/infra/

Missing / unclear:
  ? No common/ shared area detected
  ? No .fritz-local.json bindings for projects
  ? No context injection setting found

Naming convention: date-prefixed files (YYYY-MM-DD)
```

---

### Phase 3: Ask Questions

Ask questions **one at a time**. Wait for the human's answer before asking the next question. Never ask multiple questions in a single message.

**Question 1 — Vault identity** (only if this is a new vault not yet in the registry):
> "What short name should I use for this vault? (lowercase, no spaces — e.g. `engineering`)"

Also ask for domain if not clear from context: `work | personal | engineering | research | <custom>`

**Question 2 — Per-project structure**:
> "I found these project folders: [list]. Should I create a `.brain/` overlay structure inside each one? (yes / no / only for specific ones)"

If yes, confirm which subdirs to create under each project overlay:
> "For each project, I'll create: `index.md`, `feedback/`, `decisions/`, `runbooks/`, `context/` — each with their own `index.md`. Does that work, or do you want to adjust?"

**Question 3 — Common/shared area**:
> "Should I create a `common/` area under `.brain/` for shared patterns, research, and conventions that apply across projects? (yes / no)"

**Question 4 — Index files**:
> "Should I create or update `index.md` files to catalogue the vault structure? (yes / no)"

**Question 5 — .fritz-local.json binding**:
> "Should I create `.fritz-local.json` files in project directories to bind them to this vault? This lets the brain automatically associate captures from those directories with this vault. (yes / no)"

**Question 6 — Context injection** (only ask if global `settings.context_injection` is `off` or unset):
> "What level of context injection do you want for this vault?
>   - `off` — advisory reminder only (default)
>   - `light` — hook injects matching file paths into context
>   - `full` — light + agent spawns a subagent to read and synthesize relevant knowledge
> (off / light / full)"

---

### Phase 4: Execute

Based on the human's answers, create the agreed structure. Never create anything that wasn't confirmed.

**Per-project structure** (if confirmed):

For each confirmed project, create directories **at the vault root** (not inside `.brain/`):

```
<vault-root>/<project-name>/
  index.md              # Project overview and links
  feedback/
    index.md            # User corrections and preferences
  decisions/
    index.md            # Architecture and design decisions
  runbooks/
    index.md            # Operational fixes and debugging guides
  context/
    index.md            # Requirements, background, state
```

`index.md` files should contain a brief header and a note that content will be populated as the project evolves.

**Common/shared area** (if confirmed):

Create at the vault root (not inside `.brain/`):

```
<vault-root>/common/
  index.md              # Shared knowledge overview
  patterns/
    index.md            # Reusable patterns across projects
  research/
    index.md            # Research results and findings
  conventions/
    index.md            # Team conventions and standards
```

**.fritz-local.json files** (if confirmed):

Create `.fritz-local.json` in each confirmed project directory:

```json
{
  "vault": "<vault-name>",
  "project": "<project-folder-name>"
}
```

**Manifest**:

Create `.brain/manifest.yaml` at the vault root with discovered path mappings and a `project_structure` field listing confirmed projects:

```yaml
version: 1
name: <vault-name>
domain: <domain>

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

project_structure:
  - index.md
  - feedback/
  - decisions/
  - runbooks/
  - context/

projects:
  <project-name-1>: <project-name-1>/
  <project-name-2>: <project-name-2>/

exclude:
  - ".obsidian/"
  - ".trash/"
  - <any secrets/keys directories found>
```

**Registry entry**:

Add or update the vault entry in `~/.brain/registry.yaml`:

```yaml
vaults:
  <vault-name>:
    path: <vault-path>
    domain: <domain>
    sync: local
```

If the human specified a context injection level in Question 6, record it in
each `.fritz-local.json` file (per-project), NOT in the registry vault entry.
For global injection, add it to the `settings:` block in the registry instead:

```yaml
settings:
  context_injection: <level>
```

**Instruction files** (always create):

Create `.brain/instructions/` with agent instruction files: `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`.

Each should contain:
- Vault name and domain
- Quick reference to key paths from the manifest
- "Save key decisions and lessons before session end" instruction
- "Read `.brain/schema.md` for full details" pointer

If context injection level is `full`, add to each instruction file:

```
## Brain Knowledge

When starting work in this vault, spawn a subagent to search `.brain/` and
relevant knowledge directories for prior decisions, patterns, and context
related to the current task. Synthesize findings before proceeding.
```

**Schema**:

Read `templates/schema.template.md` from the fritz-ai-local repo. Replace `{{vault_name}}`, `{{domain}}`, `{{owner}}` placeholders. Write to `.brain/schema.md`.

If the template is not available, generate the schema from scratch following the same structure.

**Supporting structure**:

```bash
mkdir -p .brain/{instructions,capture/sessions,capture/inbox}
```

Create `.brain/log.md` if it doesn't exist.

---

### Phase 5: Report

After execution, show the human:

- What was created (with paths)
- What paths were mapped in the manifest
- What was skipped (and why)
- Any items the agent couldn't resolve (ask for clarification)

---

## Important

- NEVER create without asking. Phase 3 questions gate all creation in Phase 4.
- NEVER restructure or move existing files. The manifest maps to what exists.
- Ask one question per message. Never bundle questions.
- Respect existing conventions — if the vault uses numeric prefixes, note that in the schema.
- If a concept has no matching directory, use `.brain/` defaults and note it in the report.
- Exclude sensitive directories (keys, credentials, secrets) from the manifest.
- If the user says "add a project", skip vault identity questions and go straight to per-project questions for the new project only.
