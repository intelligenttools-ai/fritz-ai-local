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

**Shared instruction file** (single source of truth):

The brain contract lives as a single file at `.brain/instructions/brain.md`.
It is the canonical, agent-neutral source. Its frontmatter carries a
`brain_contract_version` integer so that future setups can detect drift.

**The current brain contract version is `1`.** Bump this line when the
contract body below changes substantively.

Contents of `.brain/instructions/brain.md`:
- Frontmatter: `type: instructions`, `brain_contract_version: <current>`
- Vault name and domain
- Quick reference to key paths from the manifest
- "Save key decisions and lessons before session end" instruction
- "Read `.brain/schema.md` for full details" pointer
- The **Knowledge Management (Mandatory)** section defined below
- The **Brain Knowledge (context injection)** section if context injection
  level is `full`

**Handling when `brain.md` already exists:**

1. **Exists, current version, suitable for your ecosystem** → do NOT recreate
   or rewrite it. Just add the reference from your own root file (below).
2. **Exists, older `brain_contract_version`** → ask the human whether to
   update it in place. Never overwrite silently. If yes, replace the body
   with the current contract and bump the version; if no, leave it and still
   add your reference.
3. **Exists but unsuitable for your ecosystem** (e.g., references tools or
   conventions that don't fit your runtime) → do NOT modify the shared
   `brain.md` to accommodate your agent alone. Ask the human whether to
   create a sibling variant (e.g., `.brain/instructions/brain-<agent>.md`)
   and reference that variant from your own root file instead.

**Reference from the current agent's root** (idempotent):

The running agent knows its own project-root instruction file and the
reference/import syntax its ecosystem supports. Using that knowledge, add a
single reference pointing to `.brain/instructions/brain.md` (or the variant
chosen above) from its own root file. Append-only; if a reference to that
path already exists, skip.

Do NOT touch root files belonging to other agents. Each agent adds its own
reference when it runs `/fritz:brain-setup` in the vault.

**Knowledge Management (Mandatory) section — body for `.brain/instructions/brain.md`:**

```markdown
## Knowledge Management (Mandatory)

This project is part of a Fritz Local brain overlay. The brain toolchain is
**not optional and not passive** — use it actively, every session. Skipping
these steps wastes accumulated knowledge and causes the team to re-solve
already-solved problems.

### Before planning, research, debugging, or any non-trivial change

Run `/fritz:brain-query` to search for prior decisions, patterns, runbooks,
and similar issues. Apply what you find. Do not implement solutions the brain
already contains — extend them.

This step is mandatory before:
- Designing a feature, fix, or refactor
- Debugging a failure or regression
- Making an architectural decision
- Answering a substantive question about this project or its domain

### During execution

Capture decisions, trade-offs, surprises, and new runbooks as you work.
`~/.brain/capture/daily/` is the live inbox — write as you learn, not only at
the end.

### After execution

Run `/fritz:brain-ingest` to promote new knowledge — runbooks, patterns,
post-incident learnings, external sources — into the brain. Run
`/fritz:brain-compile` to consolidate captures into compiled articles when
enough material has accumulated. Scope to what is worth preserving; this is
not a changelog.

### At handover and session end

`/fritz:brain-sync` is a **required** step of `/fritz:handover`. Do not
complete a handover without it. Unsynced knowledge is lost knowledge — a
handover that skips sync is not a handover, it is a context dump.
```

**Brain Knowledge (context injection) section — append to `brain.md` only if
context injection is `full`:**

```markdown
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
- Whether `.brain/instructions/brain.md` was created or already existed
- Which root-level instruction file of the current agent received a reference
  (and whether it was newly added or already present)
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
- Only touch the current agent's own root instruction file. Never write
  integration points for other agents — each agent configures itself when it
  runs setup in the vault.
- The root-file reference is append-only and idempotent: if a reference to
  the chosen brain contract file already exists, skip.
- The brain contract lives in `.brain/instructions/brain.md` as a single
  shared source. Do not inline its content into root files. Do not duplicate
  it per agent. Reference it.
- Never silently overwrite `brain.md`. If the version is older, ask the
  human whether to update in place. If the content is unsuitable for the
  current agent's ecosystem, ask the human whether to create a sibling
  variant — never mutate the shared file to fit one agent.
- `brain.md` is created only when absent. When present and current, just
  reference it. This step does not require a Phase 3 confirmation on first
  creation — the contract is part of the overlay, not an option.
