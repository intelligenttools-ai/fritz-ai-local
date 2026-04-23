# Skills

Fritz Local ships eight `fritz:*` skills. Each is a markdown instruction
file under `skills/` that agents invoke as a slash-command or skill call.

This page summarises purpose and lifecycle position. Full skill text lives
in each skill's own `SKILL.md`.

## Lifecycle position

```
Before work        → /fritz:brain-query
While working      → (captures happen automatically)
Importing sources  → /fritz:brain-ingest
After work         → /fritz:brain-compile
Before exit        → /fritz:handover (calls compile + ingest + optional sync)
Pushing externally → /fritz:brain-sync
Periodic hygiene   → /fritz:brain-lint
Setup / add vault  → /fritz:brain-setup
Maintenance        → /fritz:update
```

## `/fritz:brain-query`

Search and synthesise knowledge across all vaults.

Reads the registry, walks each vault's `index.md` first (Karpathy's
"LLM-maintained index" approach), then grep-bodies only when the index
doesn't answer. Synthesises a response that cites source articles.

Use before planning, debugging, designing, or answering substantive
questions about the domain. The brain contract makes this step
mandatory for any non-trivial change.

## `/fritz:brain-compile`

Promote captures into compiled knowledge articles.

Reads `~/.brain/capture/` (global inbox), analyses each item, and routes
to the correct vault by content. Creates per-project structure on demand
when the first piece of knowledge for a new project arrives. Updates the
per-vault `index.md` after writing articles.

Use after a session's work produces durable learnings, or before a
handover so the receiving session inherits compiled knowledge rather than
a raw capture log.

## `/fritz:brain-ingest`

Import external sources — URLs, YouTube videos, papers, web pages,
handover documents — into the brain.

Fetches or reads the source, places the raw content in the vault's
`capture/inbox/`, and creates or updates a knowledge article with the
source URL in frontmatter. Updates the index.

Use for anything the brain should know that did not originate in an
agent session.

## `/fritz:brain-sync`

Push knowledge articles to external systems. The brain is the source of
truth; external systems (AFFiNE, Obsidian remote, wikis) are views.

Reads the registry for vaults where `sync` is not `local` or `none`.
Uses the vault's `log.md` to find the last sync timestamp, then pushes
articles modified since then.

Invoked automatically by `/fritz:handover` for vaults that have a sync
target configured. Vaults with `sync: local` or `sync: none` have no
external surface to push to, so handover skips this step for them — the
preservation path for those vaults is the local capture → compile
pipeline that runs earlier in the handover flow.

## `/fritz:brain-lint`

Health checks on vault integrity. Reports without fixing.

Checks:
- Frontmatter parses; `type` is present; `updated` is not older than 90
  days; low-confidence articles cite sources.
- Internal references resolve.
- Orphans (articles not referenced from any index).
- Registry entries point to directories that still exist.

Schedule periodically, or run before a release/handover to surface
accumulated drift.

## `/fritz:handover`

Produce a structured handover document so a fresh agent session can
continue work without losing context.

Before writing the document, compiles pending captures, ingests session
decisions and patterns, and runs sync if the vault is configured for it.
The receiving agent inherits compiled knowledge, not a TODO to compile.

Stores to `.handovers/` at the project root if inside a git repo,
`~/.brain/handovers/` otherwise.

Use when approaching a context limit, switching machines, or handing
work to a different agent or person.

## `/fritz:brain-setup`

Initialise a vault — interactive.

Phases: **Analyse** existing directory structure → **Present** findings
→ **Ask** questions (one at a time) → **Execute** based on answers →
**Report** what was created.

Writes the manifest, schema, supporting directories, the shared brain
contract (`.brain/instructions/brain.md`), and a reference from the
running agent's own conventional root instruction file pointing at
`brain.md`. Never touches other agents' root files.

Re-run when adding a project to an existing vault, or when `/fritz:update`
reports brain-contract drift.

## `/fritz:update`

Self-update Fritz Local.

Runs `git pull` in `~/.fritz-ai-local/`, symlinks any new skills into
each agent's skill directory, executes pending migrations, and reports.
Also scans registered vaults for brain-contract drift (`brain.md`
`brain_contract_version` older than the skill's current version) and
lists vaults needing a `/fritz:brain-setup` refresh. Read-only on vaults —
no silent overwrites.

Trigger manually or react to the update-available notice emitted by
`brain_session_start.py`.
