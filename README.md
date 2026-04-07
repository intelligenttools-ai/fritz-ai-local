# Fritz Local

Agent-agnostic brain overlay system for personal knowledge vaults.

## Install

Paste this into your agent (Claude Code, Codex, Gemini CLI, Hermes, or any other):

```
Clone https://github.com/intelligenttools-ai/fritz-ai-local.git
Then read SETUP.md and follow all steps to install Fritz Local on this machine for this agent.
```

That's it. The agent reads SETUP.md, creates `~/.brain/`, symlinks hooks and skills, registers them in its config, sets up the vault registry, and runs `/brain-setup` for each vault. No shell scripts, works on any OS.

## What it does

- **Captures every conversation** — hooks fire on session end, save to `~/.brain/capture/daily/`
- **Compiles knowledge** — `/brain-compile` promotes captures into articles, routed by content
- **Queries across vaults** — `/brain-query` searches all vaults and captures
- **Ingests external sources** — `/brain-ingest` imports URLs, videos, papers
- **Enforces brain-first** — hook reminds agents to check the brain before answering
- **Validates integrity** — `/brain-lint` checks for stale, broken, or orphaned content
- **Syncs externally** — `/brain-sync` pushes to any target the agent has tools for
- **Sets up vaults** — `/brain-setup` explores any directory and generates the manifest

## Architecture

```
Every session → ~/.brain/capture/daily/  (dumb, always fires)
                        ↓
              /brain-compile  (smart, reads content)
                        ↓
         Routes to correct vault by content analysis
    ┌──────────┼──────────┼──────────┐
 vault-a    vault-b    vault-c    vault-d
```

## Relationship to Fritz-AI

[Fritz-AI](https://github.com/intelligenttools-ai/fritz-ai) is the intelligence layer — extraction pipelines, SQLite + embeddings, N-tier hierarchy, multi-machine sync. Fritz Local is the **capture and integration layer** that Fritz-AI builds on:

- Fritz Local captures raw transcripts and routes them to vaults
- Fritz-AI picks up captures, runs dual-LLM extraction, stores in the memory hierarchy
- Fritz Local works standalone. Fritz-AI adds intelligence on top.

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
│   └── claude-code-hooks.json  # Hook registration reference
├── skills/
│   ├── brain-setup/            # Agent-driven vault initialization
│   ├── brain-compile/          # Promote captures → knowledge articles
│   ├── brain-query/            # Search across all vaults
│   ├── brain-ingest/           # Import external sources
│   ├── brain-lint/             # Validate vault health (schedulable)
│   └── brain-sync/             # Push to external systems (target-agnostic)
├── registry/
│   └── registry.template.yaml  # Vault registry template
└── docs/
    └── security-model.md       # 4-tier zero-trust security model
```

## Supported agents

Any agent that can read markdown and run Python:

| Agent | Hooks | Transcript adapter |
|-------|-------|--------------------|
| Claude Code | SessionStart, UserPromptSubmit, PreCompact, Stop | Implemented |
| Codex CLI | SessionStart, Stop | Stub — agent generates |
| Gemini CLI | SessionStart, BeforeAgent, PreCompress, SessionEnd | Stub — agent generates |
| Hermes Agent | session:start, session:end | Stub — agent generates |
