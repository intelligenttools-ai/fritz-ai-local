---
name: fritz:brain-compile
description: >
  Promote raw captures into compiled knowledge articles. Reads from the global
  capture directory (~/.brain/capture/) and routes knowledge to the correct vault
  based on content, not working directory. Creates per-project structure on-the-fly
  when routing to a new project. Maintains per-directory index files.
  Use when the user asks to compile, flush, or promote brain captures, process
  daily logs into knowledge, update the knowledge base, or run /fritz:brain-compile.
---

# Brain Compile

Promote raw captures into compiled knowledge articles. Reads from the global
capture directory (`~/.brain/capture/`) and routes knowledge to the correct vault
based on **content**, not working directory.

## Trigger

Activate when the user asks to:
- Compile, flush, or promote brain captures
- Process daily logs into knowledge
- Update the knowledge base from recent sessions
- Run `/fritz:brain-compile`

## Architecture

Captures are dumb — every conversation is saved to `~/.brain/capture/daily/`
regardless of where the session happened. The compile step is where intelligence
lives: it reads captures, analyzes content, and routes each piece of knowledge
to the appropriate vault.

## Workflow

### Service-first gate

If this skill is invoked by an interactive agent, first read `~/.brain/registry.yaml`. If `settings.local_brain_service` is absent, ask the human which behavior they want, then write the selected setting before choosing the compile path. The choices are: enable and configure the Docker service, keep local workflows with future setup suggestions, or keep local workflows without suggestions.

After the setting exists, check `settings.local_brain_service.enabled`. It must be boolean `true`; strings such as `"true"` or `"false"` do not enable service routing. If enabled, check whether the configured service is reachable with `GET <base_url>/v1/status`, using the token from `settings.local_brain_service.api_token_env` when that environment variable is set. Only use loopback URLs unless `settings.local_brain_service.allow_remote: true` is explicitly configured. Reject service URLs with credentials, query strings, fragments, or non-root paths. If enabled and reachable, use the service-backed compile path first: `fritz-local-brain-cli --base-url <base_url> compile` (plus `--token "$<api_token_env>"` when a token is configured) or `POST <base_url>/v1/compile/run`. Do not also perform the manual compile workflow below for the same captures unless the service is unavailable or the human explicitly requests the non-service path.

If the service is disabled or not reachable, continue with the manual workflow below. The Docker service is optional and absence of the service is not an error.

If service mode is disabled and `settings.local_brain_service.suggest_setup` is not `false`, you may briefly offer to configure the optional Docker stack when compile is repeated, slow, or part of a larger automated brain workflow. Do not block the compile task if the human declines.

If these instructions are being used inside the Local Brain service compile agent itself, do not call the service again. Continue with the workflow below and return the requested structured compile output.

### 1. Read the vault registry

Read `~/.brain/registry.yaml` to get all available vaults and their domains.
Each vault has a `.brain/manifest.yaml` mapping brain concepts to actual paths.

### 2. Find unprocessed captures

Read `~/.brain/log.md` to find the last COMPILE operation timestamp. Find all
capture files in `~/.brain/capture/daily/` newer than that date.

If no previous COMPILE exists, process all captures.

### 3. Analyze and route

For each capture file, read the content and for each promotable item determine:

**Which vault does this belong in?** Route based on content:
- VanillaCore business operations → `vanillacore` vault
- Engineering runbooks, infrastructure → `engineering` vault
- Personal notes, ideas → `privat` vault
- AI agent development, research → `ai-agents` vault
- Software/code project knowledge → `development` vault
- General work topics → `work` vault

Use the `cwd` recorded in the capture as a hint, but the **content** is the
primary signal.

**Is this worth promoting?** Extract:
- **Decisions** that affect future work
- **Patterns** that solved real problems
- **Facts** about the domain not previously known
- **Corrections** to existing knowledge
- **Lessons from failures**

Skip ephemeral content: routine Q&A, tool outputs without insight, status checks.

**Is this project-specific or cross-project?**
- Project-specific knowledge → route to the project directory
- Cross-project patterns, research, conventions → route to `common/`

### 4. Ensure target structure exists

Before writing an article, check if the target directory structure exists.

**For project-specific articles:**
If the target project directory doesn't have the per-project structure, check
the vault's `manifest.yaml` for a `project_structure` field. If present, create
the full structure:

```
<project>/
├── index.md
├── feedback/
│   └── index.md
├── decisions/
│   └── index.md
├── runbooks/
│   └── index.md
└── context/
    └── index.md
```

Register the new project in `manifest.yaml` under `projects:`.

If no `project_structure` is defined in the manifest, create only the specific
subdirectory needed (e.g., `<project>/runbooks/`).

**For cross-project articles:**
If `common/` doesn't exist but the manifest indicates it should (presence of
`project_structure` field implies per-project vault), create:

```
common/
├── index.md
├── patterns/
│   └── index.md
├── research/
│   └── index.md
└── conventions/
    └── index.md
```

### 5. Create or update knowledge articles

For each promotable item, read the target vault's manifest to find its
`knowledge` path, then:

**Check if an article already covers this topic:**
- Search the vault's `knowledge/` by filename and content
- Check the vault's index for related entries

**If article exists — UPDATE it:**
- Add new information to the appropriate section
- Update `updated` date in frontmatter
- Add the capture file to `sources`

**If no article exists — CREATE one:**
- Place in the appropriate subfolder
- Use descriptive filename: `<topic-slug>.md` (lowercase, hyphenated)
- Include full frontmatter:

```yaml
---
type: article
title: "Descriptive title"
domain: <vault domain>
sources:
  - ~/.brain/capture/daily/YYYY-MM-DD.md
related:
  - <paths to related articles>
  - vault://<other-vault>/knowledge/<topic>
tags: [<relevant tags>]
confidence: medium
status: active
created: <today>
updated: <today>
promoted_from: ~/.brain/capture/daily/YYYY-MM-DD.md
agent_last_edit: <agent>
---
```

### 6. Update indexes

**Per-directory index maintenance:**
After creating or updating an article, update the `index.md` in the same
directory. The index should list all articles in that directory with their
titles and a one-line summary.

If the directory has no `index.md`, create one.

**Vault-level index:**
Update the vault's main index (at the manifest's `index` path) to reflect
new projects, new articles, and updated counts.

### 7. Log

- Append to `~/.brain/log.md`:
```
YYYY-MM-DD HH:MM | COMPILE | <agent> | Processed N captures → X articles across Y vaults
```
- Append to each affected vault's `.brain/log.md` as well

## Important

- Do NOT compile if there are no new captures since the last COMPILE
- Do NOT create articles for trivial or ephemeral content
- DO cross-reference related articles across vaults using `vault://` URIs
- DO preserve existing article content when updating — integrate, never overwrite
- A single capture file may produce knowledge for multiple vaults
- Each compile run should be idempotent
- When creating project structure on-the-fly, follow the vault's `project_structure` template
- Always update per-directory index files after writing articles
