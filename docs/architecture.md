# Architecture

Fritz Local is deliberately small. It does three things:

1. **Captures** every agent conversation to a single inbox.
2. **Compiles** those captures into per-vault knowledge articles.
3. **Enforces** that agents consult the brain before planning and preserve
   new learnings before exit.

Everything else — queries, ingest, sync, handovers, lint, update — sits
around those three.

## Agent-agnostic boundary

Fritz Local runs on the agent's side of any integration. It never operates
as a server, never speaks MCP, and never requires the agent to embed a
specific tooling stack. The only things that touch the agent are:

- **Hooks** — Python scripts fired by the agent runtime on session
  lifecycle events.
- **Skills** — markdown skill files that the agent executes as slash
  commands or skill invocations.

Skills and hooks are symlinked into each agent's conventional locations
(`~/.claude/skills/`, `~/.codex/skills/`, `~/.gemini/skills/`, …) by the
install process and kept in sync by `/fritz:update`.

The consequence: adding support for a new agent means writing a transcript
adapter and a small hook-registration snippet, nothing else. Everything in
the data model (`~/.brain/`, vault overlays, the brain contract) is
agent-neutral.

## Hook lifecycle

Four events. Not every agent runtime exposes all four; the installer
registers whichever ones exist.

| Hook                        | Fires on                                     | What it does                                                                           |
|-----------------------------|----------------------------------------------|----------------------------------------------------------------------------------------|
| `brain_session_start.py`    | Session start                                | Injects brain context: recent captures, update-available notice, optional knowledge refs based on `context_injection`. |
| `brain_prompt_check.py`     | Before each user prompt / before agent turn  | Emits a `BRAIN CHECK` reminder so the agent queries the brain before planning.         |
| `brain_capture.py`          | Pre-compact / pre-compress / session end     | Summarises the current conversation and appends to `~/.brain/capture/daily/`.          |
| (`brain_security.py`)       | Library, not a hook                          | Enforces the four security tiers for any hook/skill that performs writes.              |

All hooks read `~/.brain/registry.yaml` and, when the working directory has
one, `.fritz-local.json`. They never modify vault content directly —
writes go through skills.

## Adapter layer

Agents produce transcripts in different formats. The capture hook needs to
read the current session's transcript to summarise it. That translation
happens in `adapters/`:

- `adapters/base.py` — `TranscriptAdapter` interface and `CaptureEntry`
  data class.
- `adapters/claude_code.py` — implemented (Claude Code JSONL format).
- `adapters/pi_agent.py` — implemented (pi-coding-agent JSONL session
  format with tree structure).
- `adapters/codex.py`, `adapters/gemini.py`, `adapters/hermes.py` —
  stubs. The installing agent generates its own adapter during setup,
  because it knows its own transcript format better than anyone.
- `adapters/registry.py` — agent detection + adapter selection. Raises
  `KeyError` for unknown agents; callers must handle fallback explicitly.

A new-agent integration is: implement the adapter, register it, optionally
PR it back.

## Capture → compile flow

```
session → brain_capture.py → ~/.brain/capture/daily/YYYY-MM-DD.md
                                        │
                                        │  (human runs /fritz:brain-compile)
                                        ▼
                      ┌─────────────────────────────────────┐
                      │ Analyse content of each capture     │
                      │ Route to the correct vault by topic │
                      └─────────────────────────────────────┘
                                        │
             ┌───────────────┬──────────┴──────────┬───────────────┐
             ▼               ▼                     ▼               ▼
         work vault    engineering vault      personal vault   research vault
         knowledge/     knowledge/              knowledge/       knowledge/
```

The capture step is deliberately dumb. It does not care which directory
the session ran in, which vault the work belonged to, or which project
was open. It just saves.

Routing is the compile step's job. `/fritz:brain-compile` reads each
capture item, matches it to vault domains, and creates or updates articles
in the right vault. New per-project structure is created on demand if a
compile run finds the first piece of knowledge for a new project.

## Knowledge surfaces

Four read surfaces exist, serving different purposes:

| Surface                     | Purpose                                                        | Written by                         |
|-----------------------------|----------------------------------------------------------------|------------------------------------|
| Capture inbox (daily/sessions) | Raw, time-ordered session record                             | `brain_capture.py`                 |
| Vault knowledge articles    | Durable, topically organised knowledge                         | `brain-compile`, `brain-ingest`    |
| Vault index (`index.md`)    | Catalogue / table of contents for a vault                      | `brain-compile`, `brain-ingest`    |
| Brain contract (`brain.md`) | Operational rules agents follow while working in a vault       | `brain-setup`                      |

Queries go index-first: `/fritz:brain-query` reads each vault's index
before grepping bodies. This avoids a vector store — the LLM maintains
the index well enough that index reads are fast and accurate.

## Context injection tiers

Controlled by `context_injection` in the registry or per-project
`.fritz-local.json`:

- **`off`** (default) — the `BRAIN CHECK` reminder is printed; the agent
  is trusted to call `/fritz:brain-query` when relevant. Zero token cost.
- **`light`** — the session-start hook scans knowledge paths matching
  keywords from the working directory context and injects a list of
  matching file paths into the agent's prompt.
- **`full`** — `light`, plus the brain contract instructs the agent to
  spawn a subagent that reads and synthesises those files before
  responding. Higher token cost but the strongest brain-first behaviour.

Tiers are opt-in. The default is `off` so installation costs no tokens.

## Versioning

Two independent version numbers:

- **Fritz Local version** — in `~/.fritz-ai-local/VERSION`. The whole
  installation. Bumped on releases. Migrations keyed off it run via
  `/fritz:update`.
- **Brain contract version** — declared inline in the
  `/fritz:brain-setup` skill, stored in each vault's
  `.brain/instructions/brain.md` frontmatter as `brain_contract_version`.
  Governs drift of the agent-facing contract.

`/fritz:update` detects brain contract drift and reports affected vaults;
refresh happens when the human runs `/fritz:brain-setup` in those vaults.
