# Local Brain Service

Optional Dockerized service-mode add-on for Fritz Local.

The MVP starts with conservative compile and sync workflows. Compile loads
`skills/fritz:brain-compile/SKILL.md` as the compile agent's task instructions,
wraps all captures as untrusted data, asks a Pydantic AI agent for structured
article write proposals, and applies only proposals that pass Python path and
policy validation. Sync loads `skills/fritz:brain-sync/SKILL.md` as policy
context, but execution is deterministic and limited to `none`, `local`, and
guarded `git` targets.

The compile agent can use one bounded read-only context tool to load captures,
vaults, and existing article paths. It cannot write directly.

## Agent Operating Mode

When this service is enabled for a Fritz Local installation, agents should treat
it as the primary execution path for the workflows it provides:

- Compile: prefer MCP `brain_compile`, otherwise use `/v1/compile/run`.
- Sync: prefer MCP `brain_sync`, otherwise use `/v1/sync/run`.
- Search: prefer MCP `brain_search`, otherwise use `/v1/search/run`.
- Query compatibility: MCP `brain_query` and `/v1/query/run` perform exact read-only vault/capture lookup without building the vector index.
- Lint: prefer MCP `brain_lint`, otherwise use `/v1/lint/run`.
- Embeddings: prefer MCP `brain_embeddings_status`, `brain_embeddings_probe`, and `brain_embeddings_index`,
  otherwise use `/v1/embeddings/status`, `/v1/embeddings/probe`, and `/v1/embeddings/index/run`.

Do not duplicate those same operations by also running the equivalent local
slash-skill workflow in the same session, unless the service is unavailable or
the human explicitly asks for the non-service path. For example, handover
preparation should use service-backed compile/sync when those steps are needed,
then write the handover document; it should not also run `/fritz:brain-compile`
or `/fritz:brain-sync` for the same work.

The service does not replace capture hooks, vault setup, ingest, update, or the
act of writing a handover document. Use the existing Fritz Local hooks and skills
for workflows that the service does not provide.

## Safety Defaults

- Scheduler is disabled by default.
- When the scheduler is explicitly enabled, it stays in dry-run mode by default (`SCHEDULER_DRY_RUN=true`); set `SCHEDULER_DRY_RUN=false` to opt in to apply-mode runs.
- Manual compile defaults to `dry_run: true`.
- Agent runs are capped to a small request limit, the context tool may only be
  used once, and capture text is truncated by `CAPTURE_MAX_CHARS` before it
  reaches the model.
- LLM and embedding API keys are optional.
- Local Brain API auth is required for all `/v1/*` endpoints; keep the service
  bound to `127.0.0.1` unless it is intentionally protected by a trusted reverse
  proxy.
- The service cannot write `registry.yaml`, `.brain/manifest.yaml`,
  `.brain/schema.md`, identity files, or excluded paths.
- The service cannot delete files. External sync is limited to explicit `git`
  pushes, and first real external sync is blocked unless configured.

## Configure

Copy the repository-root `.env.example` to `.env` and adjust values. The Docker
compose example reads `../../.env` from the repository root.

Important settings:

- `BRAIN_HOME`: container path for `~/.brain`.
- `BRAIN_PATH_MAP`: host-to-container path mapping for vaults. Set the
  left side to the exact host path used in `~/.brain/registry.yaml`, for example
  `/Users/YOUR_USER/Notes=/vaults/notes`.
  Registry paths using `~/Notes/...` are mapped through this host root inside
  the container.
- `LLM_PROTOCOL`: `openai-compatible` or `anthropic-compatible`.
- `LLM_ENDPOINT`: protocol-compatible local or remote endpoint. The simple local path is an OpenAI-compatible host model server such as Ollama at `http://host.docker.internal:11434/v1`.
- `LLM_MODEL`: instruction model used for semantic capture compile/extraction. A small local instruction model in the 2B-active to 4B range can be sufficient for normal capture extraction when exposed by your local server; use a stronger 7B-9B or API model for messier captures. Set this to the exact model tag your endpoint serves.
- `LLM_API_KEY`: optional. Leave empty for endpoints that do not
  require a key.
- `EMBEDDING_ENABLED`: explicitly enables container-owned vector indexing and semantic search. Defaults to `false` because indexed text is sent to the configured embedding provider.
- `EMBEDDING_ENDPOINT`: OpenAI-compatible embedding endpoint, configured
  independently from `LLM_ENDPOINT`. Defaults to host Ollama at `http://host.docker.internal:11434/v1`.
- `EMBEDDING_MODEL`: embedding model name used for vector indexing and dimension probes. Defaults to `nomic-embed-text:latest`.
- `EMBEDDING_API_KEY`: optional embedding API key.
- `CAPTURE_MAX_CHARS`: maximum characters read from each capture before model
  input truncation. Defaults to `4000`.
- `SCHEDULER_ENABLED`: starts interval compile runs when the service process is running. Defaults to `false`.
- `SCHEDULER_DRY_RUN`: controls scheduler mode. Defaults to `true` so explicitly enabled scheduler processing simulates compile runs; set `false` to opt in to applying validated compile proposals.
- `AUTOSTART_INSTALLED`: set to `true` only after installing service autostart on this machine. `/v1/status` uses this to explain whether processing is daemon-backed or active only while a service/agent trigger runs.
- `BRAIN_INTERVAL_MINUTES`: interval between scheduler compile attempts. Defaults to `30`.
- `COMPILE_MAX_CAPTURES`: cap for captures per run when the request does not
  specify `max_captures`, also used by automatic hook compiles and the
  scheduler. Defaults to `25` to avoid repeatedly sending the full historical
  capture corpus; set to `all` only when intentionally compiling the full
  backlog oldest-first.
- `ALLOW_FIRST_EXTERNAL_SYNC`: allows the first non-dry-run external sync, such
  as `git push`, for vaults with no previous `SYNC` log. Defaults to `false`.
- `API_TOKEN`: required bearer token for all `/v1/*` endpoints. Use a unique
  random value and export the same value in the environment named by
  `settings.local_brain_service.api_token_env`, or store the same value in
  `settings.local_brain_service.api_token` on trusted local machines where
  agents should authenticate without manual shell environment setup.
- `APPROVAL_TOKEN`: separate high-impact approval token. Leave empty to block
  operations that require approval.
- `LARGE_BATCH_THRESHOLD`: compile proposal count above which non-dry-run
  compile requires `approval_token`. Defaults to `10`.

## Run

```bash
docker compose --env-file .env -f services/local-brain/docker-compose.example.yml up --build
```

Pass `--env-file .env` explicitly so Compose interpolates `API_TOKEN` and the
other service settings from the repository environment file.

## API

Health:

```bash
curl http://127.0.0.1:8765/health
```

Status:

```bash
curl -H "authorization: Bearer $LOCAL_BRAIN_API_TOKEN" \
  http://127.0.0.1:8765/v1/status
```

The status payload includes `service_running`, `scheduler_enabled`,
`scheduler_dry_run`, `processing_mode`, `processing_active`, `processing_note`,
`last_successful_compile_at`, pending capture counts by source, and the oldest
pending capture path/timestamp. Use it to verify processing is active: if
`scheduler_enabled` is false, captures are compiled only when an agent/service
trigger invokes compile; if `AUTOSTART_INSTALLED=false`, scheduler processing is
active only while the service process is running.

Dry-run compile:

```bash
curl -X POST http://127.0.0.1:8765/v1/compile/run \
  -H "authorization: Bearer $LOCAL_BRAIN_API_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"dry_run": true}'
```

Apply compile proposals:

```bash
curl -X POST http://127.0.0.1:8765/v1/compile/run \
  -H "authorization: Bearer $LOCAL_BRAIN_API_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"dry_run": false}'
```

Dry-run sync:

```bash
curl -X POST http://127.0.0.1:8765/v1/sync/run \
  -H "authorization: Bearer $LOCAL_BRAIN_API_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"dry_run": true}'
```

Sync one vault with git push enabled by registry config:

```bash
curl -X POST http://127.0.0.1:8765/v1/sync/run \
  -H "authorization: Bearer $LOCAL_BRAIN_API_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"dry_run": false, "vault": "engineering"}'
```

High-impact operations require an approval token in the request body. This covers
first real external sync and large non-dry-run compile batches. Delete,
registry, manifest, and schema writes are still not implemented by the service.

All `/v1/*` endpoints require:

```bash
-H "authorization: Bearer $LOCAL_BRAIN_API_TOKEN"
```

Recent runs:

```bash
curl -H "authorization: Bearer $LOCAL_BRAIN_API_TOKEN" \
  'http://127.0.0.1:8765/v1/runs/recent?limit=10'
```

Embedding status:

```bash
curl -H "authorization: Bearer $LOCAL_BRAIN_API_TOKEN" \
  http://127.0.0.1:8765/v1/embeddings/status
```

Probe embedding dimensions:

```bash
curl -X POST http://127.0.0.1:8765/v1/embeddings/probe \
  -H "authorization: Bearer $LOCAL_BRAIN_API_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"dry_run": false}'
```

Build or refresh the container-owned vector index:

```bash
curl -X POST http://127.0.0.1:8765/v1/embeddings/index/run \
  -H "authorization: Bearer $LOCAL_BRAIN_API_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"force": true}'
```

Agent search endpoint. This searches compiled vault knowledge, raw captures,
and the container-built vector index; if embeddings are enabled and the index is
missing, the service builds it before vector search:

```bash
curl -X POST http://127.0.0.1:8765/v1/search/run \
  -H "authorization: Bearer $LOCAL_BRAIN_API_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"query": "local brain", "limit": 5}'
```

Read-only query remains available for exact vault/capture lookup without vector-index writes:

```bash
curl -X POST http://127.0.0.1:8765/v1/query/run \
  -H "authorization: Bearer $LOCAL_BRAIN_API_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"query": "local brain", "limit": 5}'
```

Dry-run lint:

```bash
curl -X POST http://127.0.0.1:8765/v1/lint/run \
  -H "authorization: Bearer $LOCAL_BRAIN_API_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"dry_run": true}'
```

## MCP

MCP is the preferred agent-native integration. Run the stdio MCP server from the
same image when an MCP host needs direct tool access instead of REST:

```bash
docker compose --env-file .env -f services/local-brain/docker-compose.example.yml run --rm local-brain fritz-local-brain-mcp
```

Available tools mirror the safe service workflows:

- `brain_status`
- `brain_compile`
- `brain_sync`
- `brain_recent_runs`
- `brain_query`
- `brain_search`
- `brain_lint`
- `brain_embeddings_status`
- `brain_embeddings_probe`
- `brain_embeddings_index`

MCP tools require the same API token as `/v1/*` endpoints. Pass the token via
the `api_token` tool argument using the secret configured by your MCP host.

## CLI

The package includes a cross-platform REST CLI for humans, CI, and agents that
only have shell access. Install the package on Windows, Linux, or macOS with a
normal Python tool such as `pipx`:

```bash
pipx install ./services/local-brain
```

The CLI reads `~/.brain/registry.yaml` by default. It uses
`settings.local_brain_service.base_url` and resolves the bearer token from
`settings.local_brain_service.api_token`, or from the environment variable named
by `settings.local_brain_service.api_token_env`.
Explicit `--base-url`, `--token`, and `--token-env` arguments override registry
defaults.

The Docker image also includes the same CLI for manual operations inside the
container. `fritz-local-brain-cli status` exposes the same processing mode,
backlog, oldest pending capture, and last compile fields as `/v1/status`:

```bash
docker compose --env-file .env -f services/local-brain/docker-compose.example.yml exec local-brain \
  fritz-local-brain-cli status
```

Useful commands:

- `fritz-brain status`
- `fritz-brain search "local brain" --limit 5`
- `fritz-brain query "local brain" --limit 5`
- `fritz-local-brain-cli status`
- `fritz-local-brain-cli compile --max-captures 1`
- `fritz-local-brain-cli sync --vault engineering`
- `fritz-local-brain-cli recent-runs --limit 5`
- `fritz-local-brain-cli search "local brain" --limit 5`
- `fritz-local-brain-cli query "local brain" --limit 5`
- `fritz-local-brain-cli embeddings-index --force`
- `fritz-local-brain-cli lint`

## Roadmap

Deferred functionality is tracked in `.planning/roadmap.md` at the repository
root.
