# Fritz Local

Agent-agnostic brain overlay system for personal knowledge vaults.

Fritz Local adds a `.brain/` overlay to existing vault structures (Obsidian, Joplin, Logseq, or plain folders) without moving or restructuring any files. It provides a universal schema that Claude Code, Codex, Gemini CLI, and Hermes Agent can all read and follow.

## What it does

- **Manifest**: Maps brain concepts (capture, knowledge, index) to your existing folder paths
- **Schema**: Defines agent behavior contracts (session start, capture, ingest, promote, query, lint)
- **Adapters**: Generates per-agent instruction files (CLAUDE.md, AGENTS.md, GEMINI.md) from the schema
- **Registry**: Tracks multiple vaults across domains (work, personal, engineering, research)
- **Hooks**: Shared Python hook scripts wired into each agent's lifecycle (Phase 2)

## Install

```bash
git clone https://github.com/intelligenttools-ai/fritz-ai-local.git
cd fritz-ai-local
./install.sh
```

This creates `~/.brain/` and deploys overlays to detected vaults.

## Relationship to Fritz-AI

[Fritz-AI](https://github.com/intelligenttools-ai/fritz-ai) is the full hierarchical memory architecture (Agent Brain, Personal Brain, Team, Org) with MCP interface and dual-LLM extraction. Fritz Local is the **filesystem layer** — human-readable markdown that Fritz can index but that also works standalone without Fritz running.

## Structure

```
fritz-ai-local/
├── install.sh                  # Installer
├── registry/
│   └── registry.template.yaml  # Vault registry template
├── overlays/
│   └── vanillacore/            # VanillaCore vault overlay
│       ├── manifest.yaml
│       ├── schema.md
│       ├── CLAUDE.md
│       ├── AGENTS.md
│       └── GEMINI.md
├── hooks/                      # Shared Python hooks (Phase 2)
└── adapters/                   # Adapter generation (Phase 2)
```
