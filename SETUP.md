# Fritz Local — Agent Setup Instructions

You are an AI agent setting up Fritz Local, an agent-agnostic brain overlay system. Follow these steps exactly. All steps are performed by YOU (the agent), not the human.

## Paths

This repo should be cloned to a canonical location:

| OS | Repo path | Brain home |
|----|-----------|------------|
| macOS/Linux | `~/.fritz-ai-local/` | `~/.brain/` |
| Windows | `%USERPROFILE%\.fritz-ai-local\` | `%USERPROFILE%\.brain\` |

Throughout this document, `<repo>` means the repo path and `~` means the user's home directory. On Windows, use `%USERPROFILE%` for `~`, use `mklink` instead of `ln -sf`, and use `python` instead of `python3`.

## Prerequisites

Ensure Python 3.10+ is available. Install `pyyaml` if not present:
```
pip install pyyaml
```

## Step 1: Create the brain directory

Create `~/.brain/` with these subdirectories:
```
~/.brain/
├── capture/
│   ├── daily/
│   └── sessions/
├── hooks/
├── tools/
├── templates/
└── log.md          (create with header: "# Brain Operations Log")
```

## Step 2: Symlink hooks and skills

Symlink each `.py` file from `<repo>/hooks/` to `~/.brain/hooks/`:
```
~/.brain/hooks/brain_capture.py        → <repo>/hooks/brain_capture.py
~/.brain/hooks/brain_session_start.py  → <repo>/hooks/brain_session_start.py
~/.brain/hooks/brain_prompt_check.py   → <repo>/hooks/brain_prompt_check.py
~/.brain/hooks/brain_common.py         → <repo>/hooks/brain_common.py
~/.brain/hooks/brain_security.py       → <repo>/hooks/brain_security.py
```

On Windows, use `mklink` (requires admin) or copy the files instead.

Symlink each `fritz:*` skill directory from `<repo>/skills/` to your agent's skills directory:
- Claude Code: `~/.claude/skills/`
- Codex CLI: `~/.codex/skills/`
- Gemini CLI: `~/.gemini/skills/`
- Other agents: wherever skills are registered

All skills are prefixed with `fritz:` to avoid conflicts with other skill providers.

Copy `<repo>/templates/schema.template.md` to `~/.brain/templates/`.

## Step 3: Register hooks in your agent config

You know your own config format. Register these hooks:

| Event | Script | Purpose |
|-------|--------|---------|
| Session start | `python3 ~/.brain/hooks/brain_session_start.py` | Inject brain context |
| User prompt / before agent | `python3 ~/.brain/hooks/brain_prompt_check.py` | Brain-first enforcement |
| Pre-compact / pre-compress | `python3 ~/.brain/hooks/brain_capture.py` | Capture before context loss |
| Session end / stop | `python3 ~/.brain/hooks/brain_capture.py` | Capture on exit |

Reference hook configurations for each agent are in `<repo>/hooks/`:
- Claude Code: `claude-code-hooks.json` → merge into `~/.claude/settings.json` under `hooks`
- Codex CLI: `codex-hooks.toml` → append to `~/.codex/config.toml`
- Gemini CLI: `gemini-hooks.json` → merge into `~/.gemini/settings.json` under `hooks`

Use absolute paths (resolve `~` to the actual home directory). For other agents, use your native hook registration mechanism.

## Step 4: Add brain instructions to your global instruction file

Add this to your global instruction file (CLAUDE.md, AGENTS.md, GEMINI.md, HERMES.md — whichever you read):

```markdown
## Brain System

Fritz Local brain overlay is active. Before answering questions about prior decisions, patterns, or domain knowledge:
1. Check `~/.brain/capture/daily/` for recent session captures
2. Search vault knowledge directories (paths in `~/.brain/registry.yaml`)
3. Use `/fritz:brain-query` to search across all vaults

Available fritz skills: `/fritz:brain-query`, `/fritz:brain-compile`, `/fritz:brain-ingest`, `/fritz:brain-lint`, `/fritz:brain-sync`, `/fritz:brain-setup`, `/fritz:handover`
```

## Step 5: Create the vault registry

Copy `<repo>/registry/registry.template.yaml` to `~/.brain/registry.yaml`. Then ask the human which directories are their knowledge vaults and update the registry with the correct paths, domains, and sync settings.

## Step 6: Set up each vault

For each vault in the registry, run `/fritz:brain-setup` (or follow the fritz:brain-setup skill instructions). This explores the vault's directory structure and generates the manifest, schema, instruction files, and index.

## Step 7: Write your own transcript adapter (if needed)

Check `<repo>/adapters/` for your agent type. If it's a stub, implement it — you know your own transcript format better than anyone. The interface is in `<repo>/adapters/base.py`:

```python
class TranscriptAdapter:
    def parse(self, transcript_path: Path, max_messages: int = 200) -> CaptureEntry:
        ...
```

Register it in `<repo>/adapters/registry.py`. Consider opening a PR to contribute it back.

## Step 8: Verify

1. Start a new session — you should see brain context injected
2. Ask a question — you should see a "BRAIN CHECK" reminder
3. End the session — a capture should appear in `~/.brain/capture/daily/`

## Step 9: Configure per-project bindings (optional)

For each source code project that should be linked to a brain vault, create a `.fritz-local.json` file in the project root:

```json
{
  "vault": "<vault-name>",
  "project": "<project-name>",
  "brain_home": "~/.brain",
  "context_injection": "off"
}
```

Fields:
- `vault`: name of the vault in `~/.brain/registry.yaml`
- `project`: project directory name within the vault
- `brain_home`: path to brain directory (default `~/.brain`)
- `context_injection`: `off` (default) | `light` | `full`

This file is safe to commit to version control — it contains no secrets.

Context injection levels:
- `off`: advisory "BRAIN CHECK" reminder only (no token cost)
- `light`: hook searches knowledge dirs, injects matching file paths (low token cost)
- `full`: light + agent must spawn subagent to read/synthesize (higher token cost)

## Step 10: Configure global settings (optional)

Add a `settings` block to `~/.brain/registry.yaml` for global defaults:

```yaml
settings:
  # context_injection: off    # off | light | full — inherited by all projects
  # max_injection_chars: 8000 # cap on injected context size
  # update_check: true        # check for Fritz Local updates on session start
  # local_brain_service:      # optional Dockerized service, disabled by default
  #   enabled: false
  #   base_url: http://127.0.0.1:8765
  #   api_token_env: LOCAL_BRAIN_API_TOKEN
  #   allow_remote: false     # keep false unless intentionally using a trusted remote service
  #   suggest_setup: true      # agents may suggest setup for supported brain workflows
```

Per-project `.fritz-local.json` overrides global settings.

## Step 11: Offer the Dockerized Local Brain service (optional)

After the brain overlay and vault registry are working, ask the human whether they want to set up the optional Dockerized Local Brain service.

Use this exact decision prompt:

> Fritz Local can also run an optional Dockerized Local Brain service for compile, sync, query, lint, embeddings, MCP, and CLI workflows. It is disabled by default and uses dry-run/safety gates by default. Do you want me to configure and start it now?

If the human says no, skip Docker setup but still write `settings.local_brain_service` to `~/.brain/registry.yaml` with `enabled: false`. Ask whether future setup suggestions should remain enabled. Set `suggest_setup: true` if they want future suggestions, or `suggest_setup: false` if they do not. Do not leave the setting absent after asking. Do not start Docker automatically without explicit approval.

If the human says yes:

1. Confirm Docker or a compatible runtime is available.
2. Copy `<repo>/.env.example` to `<repo>/.env` if it does not already exist.
3. Edit `<repo>/.env` for the local machine:
   - `BRAIN_HOME=/data/brain`
   - `BRAIN_PATH_MAP=<host-notes-root>=/vaults/notes`, matching the path style used in `~/.brain/registry.yaml`
   - `LLM_PROTOCOL=openai-compatible` or `anthropic-compatible`
   - `LLM_ENDPOINT=<OpenAI-compatible or Anthropic-compatible endpoint>`
   - `LLM_MODEL=<model-name>`
   - Leave `API_HOST=127.0.0.1` unless the human explicitly asks to expose the service off-host.
   - Set `API_TOKEN` to a unique random value. All `/v1/*` endpoints require it.
   - Export the same value in the environment named by `api_token_env`, for example `LOCAL_BRAIN_API_TOKEN`.
4. Start the service:
   ```bash
   docker compose -f <repo>/services/local-brain/docker-compose.example.yml up --build -d
    ```
5. Record the rollout decision in `~/.brain/registry.yaml`:
   ```yaml
   settings:
     local_brain_service:
       enabled: true
       base_url: http://127.0.0.1:8765
       api_token_env: LOCAL_BRAIN_API_TOKEN
       allow_remote: false
       suggest_setup: true
   ```
6. Verify it:
   ```bash
   curl http://127.0.0.1:8765/health
   curl -H "authorization: Bearer $LOCAL_BRAIN_API_TOKEN" http://127.0.0.1:8765/v1/status
   curl -X POST http://127.0.0.1:8765/v1/compile/run \
     -H "authorization: Bearer $LOCAL_BRAIN_API_TOKEN" \
     -H 'content-type: application/json' \
     -d '{"dry_run": true, "max_captures": 1}'
    ```

Optional agent integrations:
- Prefer MCP for agents when the host supports MCP: run `fritz-local-brain-mcp` from the service package/container and expose the `brain_query`, `brain_compile`, `brain_sync`, `brain_lint`, `brain_embeddings_status`, and `brain_embeddings_probe` tools.
- For humans, CI, or shell-only agents, install the cross-platform Python CLI with `pipx install <repo>/services/local-brain`. The `fritz-brain` and `fritz-local-brain-cli` commands read `~/.brain/registry.yaml` and the configured token env automatically.

Important service safety notes:
- Manual compile and sync should be dry-run first.
- First real external sync and large compile batches require explicit approval configuration.
- The service must not write registry, manifest, schema, identity files, or excluded paths.
- Full service documentation is in `<repo>/services/local-brain/README.md`.

Agent operating rule when `settings.local_brain_service.enabled: true` and the service health check passes:
- Use the Dockerized service as the primary execution path for supported workflows: compile, sync, query, lint, embedding status/probe, MCP, and CLI operations.
- Do not run the equivalent local slash-skill workflow for the same supported operation in the same session unless the service is unavailable or the human explicitly asks for the non-service path.
- For handover preparation, use service-backed compile and sync where those steps are needed, then write the handover document. Do not duplicate compile/sync by also invoking `/fritz:brain-compile` or `/fritz:brain-sync` directly.
- Continue to use local hooks and slash skills for workflows the service does not provide, including capture hooks, setup, ingest, update, and writing the handover document itself.

If `settings.local_brain_service` is absent, agents must ask the human which behavior to use and then write the selected setting to `~/.brain/registry.yaml`. Absence means unconfigured, not disabled.

If `settings.local_brain_service.enabled` is false, agents must use the original local hooks and slash-skill behavior even if a process happens to be listening on the default service port.

When service mode is disabled and `settings.local_brain_service.suggest_setup` is not `false`, hooks may inject an advisory for supported brain workflows so agents can ask whether the human wants to configure the optional Docker stack. This advisory never enables the service by itself and must not block fallback local execution.

For safety, hooks only probe loopback service URLs by default (`127.0.0.1`, `localhost`, `::1`) and reject credential-bearing URLs, query strings, fragments, and non-root paths. Set `allow_remote: true` only when the human intentionally points agents at a trusted remote Local Brain service. `LOCAL_BRAIN_BASE_URL` is only accepted for loopback overrides; remote service URLs must be written explicitly in the registry. Availability checks use `/v1/status`, not unauthenticated `/health`; if the service uses an API token, expose it to agents through the configured `api_token_env` environment variable.

## Step 12: Keeping Fritz Local updated

Fritz Local checks for updates on session start (once per 24 hours). When an update is available, you'll see a notification with the changelog.

To update, run `/fritz:update` or manually:
```
git -C ~/.fritz-ai-local pull
```

Symlinked hooks and skills update immediately after pull. New skills are automatically symlinked by `/fritz:update`.

Existing installs receive a Local Brain service decision prompt through `/fritz:update` and the hooks when `settings.local_brain_service` is absent. The update path does not enable Docker or change execution behavior by default; it asks the human, writes the selected registry setting, and then follows that setting.
