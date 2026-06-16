# Local Brain Service

Optional Dockerized service-mode add-on for Fritz Local.

The service wraps the brain core — compile, reconciliation, mirror, query,
sync, lint, and embeddings — behind a REST API (and an MCP server) with
scheduled processing, optional vector indexing, and federation support.

**Brain store**: Compile writes to `~/.brain/knowledge` in registry-free
(store) mode, or to registered vault manifests when configured. The typed
layout is `<store>/<scope>/<section>/*.md` (scope = `common` or a project
slug; sections = `decisions`, `lessons`, `runbooks`, `context`). Index
files (`index.md` MOCs and `archive.index.md`) are maintained automatically.

**Specialist-agent fleet**: Three Pydantic AI agents handle LLM-backed work.
All writes pass through a Python security/validation layer before touching disk.

| Agent | Role |
|-------|------|
| Compile agent | Reads captures, proposes article creates/updates |
| Reconciliation agent | Compares new articles against related existing ones; returns a verdict applied per `reconciliation_autonomy` |
| Mirror agent | Summarizes full-summary external targets; index-only targets get a stub capture with a `pointer` for live-fetch |

**Federation (external targets)**: Defined in `registry.yaml`
`external_targets:` and enabled with `MIRROR_ENABLED=true`. Supported kinds:
`local-vault`, `mcp`, `drive`, `offsite`. Mirror modes: `index-only` (default,
live-fetch at query time) or `full-summary` (agent summarizes into a capture).

**Key config knobs** beyond LLM and embedding settings:

| Setting | Default | What it controls |
|---|---|---|
| `RECONCILIATION_AUTONOMY` | `apply` | `apply` (auto) or `propose` (review-gate) |
| `BULK_SUPERSESSION_THRESHOLD` | `5` | Max auto-supersessions before escalation |
| `MIRROR_ENABLED` | `false` | Enable the mirror scheduler |
| `MIRROR_INTERVAL_MINUTES` | `60` | Mirror scheduler interval |
| `RERECONCILIATION_ENABLED` | `false` | Enable the re-reconciliation sweep |
| `RERECONCILIATION_DRY_RUN` | `true` | Dry-run for the re-reconciliation sweep |
| `MERGE_POLICY` | `brain-first` | How brain and live-fetched results are merged |

Compile loads `skills/brain-compile/SKILL.md` as agent task instructions and
wraps all captures as untrusted data; it cannot write directly. The compile
agent uses one bounded read-only context tool. Sync loads `skills/brain-sync/SKILL.md`
as policy context but execution is deterministic and limited to `none`, `local`,
and guarded `git` targets.

## Agent Operating Mode

When this service is enabled for a Fritz Local installation, agents should treat
it as the primary execution path for the workflows it provides:

- Compile: prefer MCP `brain_compile`, otherwise use `/v1/compile/run`.
- Sync: prefer MCP `brain_sync`, otherwise use `/v1/sync/run`.
- Search: prefer MCP `brain_search`, otherwise use `/v1/search/run`. This is the default for agent brain checks and uses the container-managed vector index when embeddings are enabled.
- Query compatibility: MCP `brain_query` and `/v1/query/run` perform exact read-only vault/capture lookup without building the vector index; use this only for exact/raw lookup or fallback.
- Lint: prefer MCP `brain_lint`, otherwise use `/v1/lint/run`.
- Embeddings: prefer MCP `brain_embeddings_status`, `brain_embeddings_probe`, and `brain_embeddings_index`,
  otherwise use `/v1/embeddings/status`, `/v1/embeddings/probe`, and `/v1/embeddings/index/run`. Capture/ingest hooks use `/v1/embeddings/index/schedule` internally to request debounced background refreshes.

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

## Provisioning (guided setup)

For a reproducible, agent-driven first-time setup — including the
`desired: docker` forcing model, per-OS autostart, and the `provision` /
`reconfigure` / `rollback` CLI — see
[SETUP.md — Docker brain provisioning](../../SETUP.md#docker-brain-provisioning--reproducible-setup-per-platform).

For reconfigure-on-drift and rollback-to-local procedures, see
[docs/operations.md — Docker brain service: reconfigure and rollback](../../docs/operations.md#docker-brain-service--reconfigure-and-rollback).

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
- `EMBEDDING_REFRESH_AFTER_COMPILE`: when embeddings are enabled, successful non-dry-run compile/ingest processing schedules a background vector-index refresh. Defaults to `true`.
- `EMBEDDING_REFRESH_DEBOUNCE_SECONDS`: minimum seconds between background refreshes scheduled by compile/ingest processing. Defaults to `300` so backlog drains do not rebuild vectors after every capture.
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

Agent- and instance-friendly service management is provided by the repository
script:

```bash
python3 scripts/local-brain-service.py start --build
python3 scripts/local-brain-service.py status
```

Install daemon autostart for the current OS user:

```bash
python3 scripts/local-brain-service.py install-autostart --enable-scheduler --apply
```

This writes a macOS LaunchAgent, Linux systemd user unit, or Windows Task
Scheduler logon task, sets `AUTOSTART_INSTALLED=true`, and leaves the same
Docker Compose service path in use for every agent. Remove it with:

```bash
python3 scripts/local-brain-service.py uninstall-autostart
```

On Windows, run the same commands with `python` if `python3` is not installed.
The Windows autostart task runs at user logon and calls this same repository
script, so agents do not need a separate host-specific entry point.

Embedding search is managed separately from compile runs:

```bash
python3 scripts/local-brain-service.py enable-embeddings
python3 scripts/local-brain-service.py disable-embeddings
```

The underlying Compose command remains:

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
and the container-built vector index. To keep brain checks responsive, this
endpoint does **not** rebuild a missing or stale vector index inline. Vector
freshness is owned by the compile/ingest processing path: successful non-dry-run
compile schedules a background debounced refresh, and capture/ingest hooks call
`/v1/embeddings/index/schedule` after writing raw captures. If the index is
still missing/stale, search returns exact/raw matches plus a `vector search: ...`
warning instead of blocking:

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
