# Fritz Local

Agent-agnostic brain overlay system for personal knowledge vaults.

## Install

Paste this into your agent (Claude Code, Codex, Gemini CLI, Hermes, pi, or any other):

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

## Local Brain service

An optional Dockerized Local Brain service lives in [`services/local-brain/`](services/local-brain/). The service starts with safe compile, search, vector-index, and sync workflows: compile loads `fritz:brain-compile` as Pydantic AI agent instructions and applies only policy-validated knowledge/index/log writes; search covers compiled vault knowledge, raw captures, archived processed inbox captures, and the container-owned vector index; sync supports no-op local targets plus guarded git pushes. Configure the service with an OpenAI-/Anthropic-compatible LLM endpoint for semantic compile, using either a local small instruction model exposed through an OpenAI-compatible server or an API provider with an optional key. Enable service-mode routing explicitly in `~/.brain/registry.yaml` with `settings.local_brain_service.enabled: true`; when enabled and reachable, agents use the service as the primary execution path for supported workflows instead of duplicating those same operations with local slash skills. If disabled or unreachable, the original local hook and slash-skill behavior remains the fallback.

Agent integrations should prefer authorized MCP tools (`brain_search`, `brain_query`, `brain_compile`, `brain_sync`, `brain_lint`, `brain_embeddings_index`) when an MCP host is available, then HTTP calls to the service (`/v1/search/run` for agent search). Agents resolve service auth from `settings.local_brain_service.api_token` or the configured `api_token_env`. The repository script `python3 scripts/local-brain-service.py` is the reusable agent-friendly entry point for service start/stop/status, macOS LaunchAgent, Linux systemd-user, or Windows Task Scheduler autostart, and embedding enable/disable. The optional cross-platform Python CLI (`fritz-brain` / `fritz-local-brain-cli`) is for humans, CI, and shell-only agents after the package is installed; hooks and skills must not assume it exists on the host PATH. `fritz-local-brain-cli status` and `/v1/status` show whether the scheduler is enabled, whether it is dry-run or apply mode, the last successful compile time, pending captures by source, and whether processing depends on a running service/agent trigger because autostart is not installed.

Existing installs that do not yet have `settings.local_brain_service` are treated as unconfigured, not disabled. `/fritz:update` and brain hooks surface a decision prompt so the human can choose whether to enable the Docker service, keep local workflows with future setup suggestions, or keep local workflows without suggestions. Agents then write the selected setting to the registry; they do not start Docker or enable service routing without explicit approval.

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
│   ├── pi_agent.py             # pi-coding-agent session format (tree)
│   ├── codex.py                # Stub — agent generates during setup
│   ├── gemini.py               # Stub — agent generates during setup
│   ├── hermes.py               # Hermes Agent JSONL parser
│   └── registry.py             # Agent detection + adapter selection
├── hooks/
│   ├── brain_common.py         # Shared utilities
│   ├── brain_session_start.py  # Injects brain context on session start
│   ├── brain_prompt_check.py   # Enforces brain-first on questions
│   ├── brain_capture.py        # Saves conversation summary on session end
│   ├── brain_security.py       # Tier enforcement library
│   ├── claude-code-hooks.json  # Claude Code hook registration reference
│   ├── codex-hooks.toml        # Codex CLI hook registration reference
│   ├── gemini-hooks.json       # Gemini CLI hook registration reference
│   ├── hermes-hooks.yaml       # Hermes Agent hook registration reference
│   ├── pi-extension.ts         # pi-coding-agent extension hook bridge
│   ├── hermes_brain_context.py # Hermes wrapper for context injection
│   └── hermes_brain_capture.py # Hermes wrapper for session-finalize capture
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
| pi          | session_start, before_agent_start, session_before_compact, session_shutdown via `hooks/pi-extension.ts` | Implemented |
| Codex CLI   | SessionStart, Stop | Stub — agent generates |
| Gemini CLI  | SessionStart, BeforeAgent, PreCompress, SessionEnd | Stub — agent generates |
| Hermes Agent | pre_llm_call, on_session_finalize shell hooks | Implemented via wrappers |
