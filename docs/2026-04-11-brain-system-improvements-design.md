# Design: Brain System Improvements

**Date:** 2026-04-11
**Status:** Draft
**Branch:** main

## Problem Statement

Five observed gaps in fritz-ai-local after the public release:

1. **Vault folder structure not auto-created** — brain-setup discovers existing directories but never creates per-project structures or shared areas. Users must manually instruct agents to create folder hierarchies.
2. **Handover doesn't preserve knowledge** — the handover skill defers all brain ingestion and compilation to the receiving agent. If that agent forgets or the handover is abandoned, knowledge is lost.
3. **No update/upgrade mechanism** — no version tracking, no update detection, no upgrade workflow. Multi-agent installations have no awareness of version discrepancy.
4. **Agents don't query the brain** — `brain_prompt_check.py` injects advisory text ("BRAIN CHECK") that agents routinely ignore. No mechanism forces agents to use existing knowledge.
5. **Multi-agent coordination gaps** — compounds #3. Each agent installed independently with no shared version awareness.

## Design Decisions

### Boundaries

- Fritz Local = hooks, skills, subagents. No MCP servers.
- MCP integration belongs to Fritz (full) — the intelligence layer with embeddings, extraction pipelines, and multi-machine sync.
- All changes must work agent-agnostically: Claude Code, Codex CLI, Gemini CLI, Hermes, and future agents.

### Configuration Hierarchy

Project-local `.fritz-local.json` overrides global `~/.brain/registry.yaml` settings. Absence of config = today's behavior (backward compatible).

---

## Work Item 1: Vault Structure Auto-Creation

### Changes to `fritz:brain-setup`

**Current:** Discovers existing directories, maps them in manifest, creates `.brain/` overlay.

**New:** Analyze → Present findings → Ask questions → Execute based on answers.

Interactive flow:

1. **Analyze** — explore directory structure, detect patterns (daily/, knowledge/, PARA, Johnny Decimal, per-project dirs, etc.)
2. **Present findings** — show what was discovered, what's missing, what could be created
3. **Ask questions** (one at a time):
   - "Should I create the standard per-project structure for each project?"
   - "Should I create a `common/` area for shared knowledge?"
   - "Should I generate `index.md` files for every directory?"
   - "What domain is this vault?"
   - "Should I create `.fritz-local.json` files in project source directories?"
   - "Enable context injection for this project? (off / light / full)" — only asked when global `context_injection` is NOT set in `registry.yaml`. When global is set, brain-setup says: "Global context injection is `light`. Override for this project? [keep global / off / light / full]"
4. **Execute** — create only what the human approved, generate manifest, report

### Per-Project Structure Template

Created when the human approves during brain-setup, or on-the-fly during brain-compile when routing to a project that doesn't have it yet:

```
<project-name>/
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

Every directory gets an `index.md`. Indexes are maintained by brain-compile when articles are added/updated.

The per-project template is configurable per vault via `manifest.yaml`:

```yaml
project_structure:
  - index.md
  - feedback/
  - decisions/
  - runbooks/
  - context/
```

Vaults can override this list. The default is the structure above.

### Common/Shared Area

`common/` is a first-class directory in vaults that use per-project structure:

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

Created during brain-setup when the human approves. brain-compile routes cross-project knowledge here (patterns, research results, shared conventions).

### Changes to `fritz:brain-compile`

When routing an article to a project that doesn't have the per-project structure yet:

1. Create the project directory with the template from `manifest.yaml`
2. Create all subdirectories with `index.md` files
3. Register the project in `manifest.yaml` under `projects:`
4. Then write the article

When routing cross-project knowledge, use `common/` if it exists. If it doesn't exist but was approved in the manifest, create it.

### `.fritz-local.json`

A project-local configuration file placed in a source code repository to bind it to a vault and project:

```json
{
  "vault": "development",
  "project": "agent-email",
  "brain_home": "~/.brain",
  "context_injection": "off"
}
```

- **Optional** — if absent, cwd-matching works as today
- **Safe to commit** — contains no secrets, just a vault pointer
- `context_injection` defaults to `off` when absent
- Created by brain-setup when the human approves (per-project question)

`brain_session_start.py` reads `.fritz-local.json` first (if present), then falls back to cwd-matching against the registry.

---

## Work Item 2: Handover Preserves Knowledge

### Changes to `fritz:handover`

**Current:** Writes handover document, tells receiving agent to run brain-ingest later.

**New:** Preserve knowledge before writing the handover. "Leave nothing behind."

Updated workflow:

1. **Compile pending captures** — check `~/.brain/.compile-needed`. If present, run brain-compile (or spawn subagent to do it) before proceeding.
2. **Ingest session knowledge** — extract decisions, patterns, corrections from the current session and write them to the appropriate vault. This is inline brain-ingest, not deferred.
3. **Sync if configured** — check the active vault's `sync` setting in the registry. If sync target exists (affine, notion, git, etc.), run brain-sync for the articles just created/updated.
4. **Write the handover document** — as today, but receiving agent instructions change from "run brain-ingest" to:

```markdown
## Receiving Agent Instructions

1. Read this handover document completely
2. Verify the branch and file state described above still matches reality
3. Execute the next steps in order
4. Knowledge from the previous session has already been compiled and synced.
   If you discover additional insights while executing next steps, compile
   them before ending your session.
5. When all next steps are complete (or if the handover is no longer needed):
   - Delete this handover file: `rm {path-to-this-file}`
6. If work is still incomplete, create a new `/handover` before ending your session
```

### Rationale

The creating agent has full context — it knows what decisions were made, what patterns emerged, what corrections happened. The receiving agent starts fresh and may not even recognize what knowledge is worth preserving. Compiling at creation time captures knowledge at maximum fidelity.

---

## Work Item 3: Version Tracking and Auto-Upgrade

### Version File

Add `VERSION` to the repo root:

```
1.0.0
```

Semver. Tag releases in git (`v1.0.0`). Because the canonical install is a git clone to `~/.fritz-ai-local/`, the VERSION file is always present on the installed machine.

### Update Detection in `brain_session_start.py`

Add to the session-start hook:

1. Read `~/.fritz-ai-local/VERSION` (installed version)
2. Check if last update check was >24h ago (timestamp in `~/.brain/.update-check`)
3. If >24h: run `git -C ~/.fritz-ai-local fetch` (non-blocking, fast)
4. Compare local HEAD with `origin/main` — if behind, read the commit log for changes
5. Inject update notification with changelog:

```
Fritz Local update available (v1.0.0 → v1.1.0)

Changes:
- brain-setup now creates per-project structure interactively
- New: fritz:update skill for agent-driven upgrades
- handover now compiles/syncs before writing

Run /fritz:update to upgrade, or: git -C ~/.fritz-ai-local pull
```

6. Write current timestamp to `~/.brain/.update-check`

**Opt-out:** Disable via `~/.brain/registry.yaml`:

```yaml
settings:
  update_check: false
```

Default: `true` (on).

### New Skill: `fritz:update`

Agent-invocable skill that executes immediately when invoked (no second confirmation):

1. `git -C ~/.fritz-ai-local pull`
2. Check for new skills not yet symlinked — symlink them
3. Check for removed skills — warn but don't delete symlinks (human decision)
4. Run any pending migrations from `migrations/` directory (numbered scripts)
5. Report what changed

### Migrations

For breaking changes (new hooks, changed config format, new required fields):

```
migrations/
├── 001-add-context-injection.py
├── 002-add-common-directory.py
└── ...
```

Each migration:
- Has a number prefix for ordering
- Is idempotent (safe to run twice)
- Checks current state before modifying
- Logs to `~/.brain/log.md`

`brain_session_start.py` checks installed VERSION against repo VERSION. If repo is newer and migrations exist, suggests running `/fritz:update`.

### Multi-Agent Coordination

All agents read the same `~/.fritz-ai-local/VERSION` and `~/.fritz-ai-local/` symlinks. One agent updating benefits all agents (shared installation). The session-start hook logs which agent+version ran each session in `~/.brain/log.md` for audit.

---

## Work Item 4: Making Brain Queries Happen

### Architecture

Three levels of context injection, explicitly opt-in:

| Level | Mechanism | Token cost | What the agent gets |
|-------|-----------|------------|---------------------|
| `off` | Hook only | Minimal | Vault names + "BRAIN CHECK" reminder (today's behavior) |
| `light` | Hook reads filesystem | Low | File paths and names of relevant knowledge |
| `full` | Hook + subagent instruction | Higher | File paths + mandatory subagent spawned to read and synthesize |

### Configuration

**Per-project** in `.fritz-local.json`:

```json
{
  "context_injection": "light"
}
```

**Global** in `~/.brain/registry.yaml`:

```yaml
settings:
  context_injection: light
  max_injection_chars: 8000
```

**Precedence:**
- No `.fritz-local.json` → today's behavior (backward compatible)
- `.fritz-local.json` without `context_injection` → `off`
- `.fritz-local.json` with `context_injection` → uses that value
- Global `context_injection` in `registry.yaml` → all projects inherit unless overridden per-project
- When global is set and brain-setup asks about a new project, it mentions: "Global context injection is set to `light`. This project will inherit that. Override? [keep global / off / light / full]"

### Level: `off`

Exactly today's behavior. `brain_prompt_check.py` injects "BRAIN CHECK: search the knowledge base" as advisory text. No file reading by the hook.

### Level: `light`

`brain_prompt_check.py` enhanced:

1. Detect query/implementation keywords (as today)
2. If triggered, **read the vault registry and manifests**
3. **Grep knowledge directories** for matching filenames and headings (Python `os.walk` + simple string matching — not semantic, just keyword)
4. **Grep feedback directory** for the current project (if `.fritz-local.json` maps to a project)
5. Return `additionalContext` with specific file paths:

```
Brain knowledge relevant to your prompt:

Knowledge articles:
- ~/Notes/Development/agent-email/decisions/backend-credential-edit-ui.md
- ~/Notes/Development/common/patterns/self-improving-classification-patterns.md

Feedback (user corrections):
- ~/Notes/Development/agent-email/feedback/feedback_no_hardcoded_models.md
- ~/Notes/Development/agent-email/feedback/feedback_deploy_colima_only.md

Read these files before responding.
```

The agent gets a map. It knows what exists and where. Whether it reads them is up to the agent — but specific paths are much harder to ignore than "go search somewhere."

**Performance:** Grep on knowledge directories adds ~100-200ms per prompt. Only runs when keywords match. Configurable `max_injection_chars` (default 8000) caps output size.

### Level: `full`

Everything from `light`, plus:

The agent instruction files (`CLAUDE.md` / `AGENTS.md` / `GEMINI.md`) generated by brain-setup contain a mandatory instruction:

```markdown
## Brain Knowledge (context_injection: full)

When brain knowledge file paths appear in your context (injected by the brain hook),
you MUST spawn a subagent to read and synthesize them before responding.

The subagent should:
1. Read all listed knowledge files
2. Read all listed feedback files
3. Synthesize relevant information
4. Return a summary with citations

This is not optional. Skipping this step means ignoring prior decisions and
repeating known mistakes.
```

All three major agents (Claude Code, Codex CLI, Gemini CLI) support subagents natively. The instruction is agent-agnostic — each agent uses its own subagent mechanism.

### Changes to `brain_session_start.py`

When `.fritz-local.json` exists:

1. Read the project's `index.md` (small, navigational — lists what knowledge exists)
2. List feedback file names (not contents) so the agent knows corrections exist
3. If `common/` exists in the vault, mention it

This gives baseline awareness at session start. The per-prompt hook (`brain_prompt_check.py`) handles the detailed injection.

### Changes to agent instruction files

`brain-setup` generates stronger instructions in `CLAUDE.md` / `AGENTS.md` / `GEMINI.md`:

```markdown
## Brain Knowledge

This vault contains knowledge from prior sessions. Before making implementation
decisions, check the feedback/ directory for this project — these are user
corrections that prevent repeating mistakes.

Available knowledge directories:
- feedback/ — user corrections and working preferences (HIGHEST PRIORITY)
- decisions/ — architecture and design choices with rationale
- runbooks/ — operational fixes and debugging guides
- context/ — requirements, background, current state
- common/ — shared patterns, research, conventions
```

---

## File Changes Summary

### New files

| File | Purpose |
|------|---------|
| `VERSION` | Semver version tracking |
| `migrations/` | Numbered migration scripts for breaking changes |

### Modified skills

| Skill | Changes |
|-------|---------|
| `fritz:brain-setup` | Interactive flow (analyze→ask→create). Per-project structure creation. `common/` creation. `.fritz-local.json` creation. Context injection question. Index per directory. |
| `fritz:brain-compile` | On-the-fly structure creation when routing to new project. `common/` routing for cross-project knowledge. Index maintenance per directory. |
| `fritz:handover` | Compile→ingest→sync before writing handover. Updated receiving agent instructions. |

### New skill

| Skill | Purpose |
|-------|---------|
| `fritz:update` | Agent-driven upgrade: git pull, symlink new skills, run migrations, report changes. |

### Modified hooks

| Hook | Changes |
|------|---------|
| `brain_session_start.py` | Read `.fritz-local.json`. Update detection (24h check, changelog injection). Project index injection. |
| `brain_prompt_check.py` | Three-level context injection (off/light/full). Grep knowledge dirs for matching files. Feedback file listing for implementation prompts. `max_injection_chars` cap. |

### New config files

| File | Location | Purpose |
|------|----------|---------|
| `.fritz-local.json` | Per project source dir | Binds project to vault, configures context injection |

### Modified config files

| File | Changes |
|------|---------|
| `registry.yaml` template | New `settings:` block for `context_injection`, `max_injection_chars`, `update_check` |
| Vault `manifest.yaml` | `project_structure:` template field |

---

## Implementation Order

1. **VERSION + fritz:update skill** — foundation for everything else (enables iterative releases)
2. **`.fritz-local.json` + hook changes** — project-vault binding, context injection levels
3. **brain-setup interactive redesign** — structure creation, questions, common/ area
4. **brain-compile structure-on-demand** — on-the-fly project structure creation, index maintenance
5. **handover knowledge preservation** — compile→ingest→sync before handover
6. **Update detection in session-start** — changelog notification, opt-out config

Each item is independently shippable as a release.
