# Fritz Local

Agent-agnostic brain overlay system for personal knowledge vaults.

## Install

Paste this into your agent (Claude Code, Codex, Gemini CLI, Hermes, or any other):

```
Clone https://github.com/intelligenttools-ai/fritz-ai-local.git to ~/.fritz-ai-local
(on Windows: %USERPROFILE%\.fritz-ai-local)
Then read SETUP.md in that repo and follow all steps to install Fritz Local on this machine for this agent.
```

That's it. The agent reads SETUP.md, creates `~/.brain/`, symlinks hooks and skills, registers them in its config, sets up the vault registry, and runs `/fritz:brain-setup` for each vault. No shell scripts, works on any OS.

## What it does

- **Sets up vaults** — `/fritz:brain-setup` explores any directory and generates the manifest
- **Captures every conversation** — hooks fire on session end, save to `~/.brain/capture/daily/`
- **Ingests external sources** — `/fritz:brain-ingest` imports URLs, videos, papers
- **Compiles knowledge** — `/fritz:brain-compile` promotes captures into articles, routed by content
- **Queries across vaults** — `/fritz:brain-query` searches all vaults and captures
- **Syncs externally** — `/fritz:brain-sync` pushes to any target the agent has tools for
- **Validates integrity** — `/fritz:brain-lint` checks for stale, broken, or orphaned content
- **Enforces brain-first** — hook reminds agents to check the brain before answering
- **Stays up to date** — `/fritz:update` pulls the latest version and runs pending migrations

## Session handover

`/fritz:handover` produces a structured handover document so you can continue work in a fresh agent session without losing context. Before writing the document it compiles pending captures and ingests session decisions and patterns, so the knowledge is preserved in the brain — not just in the handover file. Use it when you're about to hit a context limit, switch machines, or hand work off to another agent.

## Architecture

```
Every session → ~/.brain/capture/daily/  (dumb, always fires)
                        ↓
              /fritz:brain-compile  (smart, reads content)
                        ↓
         Routes to correct vault by content analysis
    ┌──────────┼──────────┼──────────┐
 vault-a    vault-b    vault-c    vault-d
```

## Structure

```
fritz-ai-local/
├── SETUP.md                    # The agent reads this to install everything
├── requirements.txt            # pyyaml
├── templates/
│   └── schema.template.md      # Schema template (filled per vault)
├── adapters/
│   ├── base.py                 # TranscriptAdapter interface + CaptureEntry
│   ├── claude_code.py          # Claude Code JSONL parser
│   ├── codex.py                # Stub — agent generates during setup
│   ├── gemini.py               # Stub — agent generates during setup
│   ├── hermes.py               # Stub — agent generates during setup
│   └── registry.py             # Agent detection + adapter selection
├── hooks/
│   ├── brain_common.py         # Shared utilities
│   ├── brain_session_start.py  # Injects brain context on session start
│   ├── brain_prompt_check.py   # Enforces brain-first on questions
│   ├── brain_capture.py        # Saves conversation summary on session end
│   ├── brain_security.py       # Tier enforcement library
│   ├── claude-code-hooks.json  # Claude Code hook registration reference
│   ├── codex-hooks.toml        # Codex CLI hook registration reference
│   └── gemini-hooks.json       # Gemini CLI hook registration reference
├── skills/
│   ├── fritz:brain-setup/      # Agent-driven vault initialization
│   ├── fritz:brain-compile/    # Promote captures → knowledge articles
│   ├── fritz:brain-query/      # Search across all vaults
│   ├── fritz:brain-ingest/     # Import external sources
│   ├── fritz:brain-lint/       # Validate vault health (schedulable)
│   ├── fritz:brain-sync/       # Push to external systems (target-agnostic)
│   ├── fritz:handover/         # Structured session handover documents
│   └── fritz:update/           # Self-update + pending migrations
├── registry/
│   └── registry.template.yaml  # Vault registry template
└── docs/
    ├── README.md               # Documentation index
    ├── concepts.md             # Vaults, manifest, brain contract, captures
    ├── architecture.md         # Hooks, capture→compile flow, adapter layer
    ├── skills.md               # Per-skill purpose and lifecycle position
    ├── operations.md           # Updating, drift, troubleshooting
    └── security-model.md       # 4-tier zero-trust security model
```

## Documentation

See [`docs/`](docs/) for reference documentation. Start with
[`docs/README.md`](docs/README.md) for the full table of contents.

## Supported agents

Any agent that can read markdown and run Python:

| Agent | Hooks | Transcript adapter |
|-------|-------|--------------------|
| Claude Code | SessionStart, UserPromptSubmit, PreCompact, Stop | Implemented |
| Codex CLI | SessionStart, Stop | Stub — agent generates |
| Gemini CLI | SessionStart, BeforeAgent, PreCompress, SessionEnd | Stub — agent generates |
| Hermes Agent | session:start, session:end | Stub — agent generates |
