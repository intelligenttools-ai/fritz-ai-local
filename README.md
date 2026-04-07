# Fritz Local

Agent-agnostic brain overlay system for personal knowledge vaults.

Fritz Local adds a `.brain/` overlay to existing vault structures (Obsidian, Joplin, Logseq, or plain folders) without moving or restructuring any files. It provides a universal schema that Claude Code, Codex, Gemini CLI, and Hermes Agent can all read and follow.

## Quick Start

Paste this into any agent (Claude Code, Codex, Gemini CLI, Hermes):

```
Clone and set up Fritz Local for this machine:
git clone https://github.com/intelligenttools-ai/fritz-ai-local.git ~/fritz-ai-local
cd ~/fritz-ai-local && ./install.sh
Then read SETUP.md and complete the setup steps for this agent.
```

For manual setup, see [SETUP.md](SETUP.md).

## What it does

- **Captures every conversation** — hooks fire on session end/compaction, save to `~/.brain/capture/daily/`
- **Compiles knowledge** — `/brain-compile` promotes captures into structured articles, routed to the correct vault by content analysis
- **Queries across vaults** — `/brain-query` searches all vaults and captures
- **Ingests external sources** — `/brain-ingest` imports URLs, videos, papers
- **Enforces brain-first** — `UserPromptSubmit` hook reminds agents to check the brain before answering
- **Validates integrity** — `/brain-lint` checks for stale, broken, or orphaned content

## Architecture

```
Every session → ~/.brain/capture/daily/  (dumb, always fires)
                        ↓
              /brain-compile  (smart, reads content)
                        ↓
         Routes to correct vault by content analysis
    ┌──────────┼──────────┼──────────┐
vanillacore  engineering  work    ai-agents
```

## Relationship to Fritz-AI

[Fritz-AI](https://github.com/intelligenttools-ai/fritz-ai) is the full hierarchical memory architecture (Agent Brain, Personal Brain, Team, Org) with MCP interface and dual-LLM extraction. Fritz Local is the **filesystem layer** — human-readable markdown that Fritz can index but that also works standalone without Fritz running.

## Structure

```
fritz-ai-local/
├── install.sh                  # Installer (symlinks hooks/skills, deploys overlays)
├── SETUP.md                    # Agent setup instructions (hooks, config, verification)
├── requirements.txt            # Python dependencies (pyyaml)
├── registry/
│   └── registry.template.yaml  # Vault registry template
├── templates/
│   └── schema.template.md      # Schema template (filled per vault by /brain-setup)
├── adapters/
│   ├── base.py                 # TranscriptAdapter interface + CaptureEntry format
│   ├── claude_code.py          # Parses Claude Code JSONL transcripts
│   ├── codex.py                # Stub — agent generates during setup
│   ├── gemini.py               # Stub — agent generates during setup
│   ├── hermes.py               # Stub — agent generates during setup
│   └── registry.py             # Detects agent, returns correct adapter
├── hooks/
│   ├── brain_common.py         # Shared utilities
│   ├── brain_session_start.py  # Injects brain context on session start
│   ├── brain_prompt_check.py   # Enforces brain-first on questions
│   ├── brain_capture.py        # Saves conversation summary on session end
│   └── claude-code-hooks.json  # Hook registrations for Claude Code
└── skills/
    ├── brain-setup/            # Agent-driven vault initialization
    ├── brain-compile/          # Promote captures → knowledge articles
    ├── brain-query/            # Search across all vaults
    ├── brain-ingest/           # Import external sources
    └── brain-lint/             # Validate vault health
```

## Supported agents

| Agent | Hooks | Instruction file | Status |
|-------|-------|-----------------|--------|
| Claude Code | SessionStart, UserPromptSubmit, PreCompact, Stop | CLAUDE.md | Active |
| Codex CLI | SessionStart, Stop | AGENTS.md | Setup instructions in SETUP.md |
| Gemini CLI | SessionStart, BeforeAgent, PreCompress, SessionEnd | GEMINI.md | Setup instructions in SETUP.md |
| Hermes Agent | session:start, session:end | HERMES.md | Setup instructions in SETUP.md |
