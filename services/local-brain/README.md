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

- Compile: use `/v1/compile/run`, `brain_compile`, or `fritz-local-brain-cli compile`.
- Sync: use `/v1/sync/run`, `brain_sync`, or `fritz-local-brain-cli sync`.
- Query: use `/v1/query/run`, `brain_query`, or `fritz-local-brain-cli query`.
- Lint: use `/v1/lint/run` or `fritz-local-brain-cli lint`.
- Embeddings: use `/v1/embeddings/status` and `/v1/embeddings/probe`.

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
- Manual compile defaults to `dry_run: true`.
- Agent runs are capped to a small request limit, the context tool may only be
  used once, and capture text is truncated by `CAPTURE_MAX_CHARS` before it
  reaches the model.
- API keys are optional.
- API auth is optional, but bind to `127.0.0.1` unless protected by a reverse
  proxy or token.
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
- `LLM_ENDPOINT`: protocol-compatible local or remote endpoint.
- `LLM_API_KEY`: optional. Leave empty for endpoints that do not
  require a key.
- `EMBEDDING_ENABLED`: enables embedding endpoint probes. Defaults to `false`.
- `EMBEDDING_ENDPOINT`: OpenAI-compatible embedding endpoint, configured
  independently from `LLM_ENDPOINT`.
- `EMBEDDING_MODEL`: embedding model name used for dimension probes.
- `EMBEDDING_API_KEY`: optional embedding API key.
- `CAPTURE_MAX_CHARS`: maximum characters read from each capture before model
  input truncation. Defaults to `4000`.
- `COMPILE_MAX_CAPTURES`: default maximum captures per run when the request does
  not specify `max_captures`, also used by the scheduler. Defaults to `1`.
- `ALLOW_FIRST_EXTERNAL_SYNC`: allows the first non-dry-run external sync, such
  as `git push`, for vaults with no previous `SYNC` log. Defaults to `false`.
- `APPROVAL_TOKEN`: separate high-impact approval token. Leave empty to block
  operations that require approval.
- `LARGE_BATCH_THRESHOLD`: compile proposal count above which non-dry-run
  compile requires `approval_token`. Defaults to `10`.

## Run

```bash
docker compose -f services/local-brain/docker-compose.example.yml up --build
```

## API

Health:

```bash
curl http://127.0.0.1:8765/health
```

Status:

```bash
curl http://127.0.0.1:8765/v1/status
```

Dry-run compile:

```bash
curl -X POST http://127.0.0.1:8765/v1/compile/run \
  -H 'content-type: application/json' \
  -d '{"dry_run": true, "max_captures": 1}'
```

Apply compile proposals:

```bash
curl -X POST http://127.0.0.1:8765/v1/compile/run \
  -H 'content-type: application/json' \
  -d '{"dry_run": false, "max_captures": 1}'
```

Dry-run sync:

```bash
curl -X POST http://127.0.0.1:8765/v1/sync/run \
  -H 'content-type: application/json' \
  -d '{"dry_run": true}'
```

Sync one vault with git push enabled by registry config:

```bash
curl -X POST http://127.0.0.1:8765/v1/sync/run \
  -H 'content-type: application/json' \
  -d '{"dry_run": false, "vault": "engineering"}'
```

High-impact operations require an approval token in the request body. This covers
first real external sync and large non-dry-run compile batches. Delete,
registry, manifest, and schema writes are still not implemented by the service.

If `API_TOKEN` is set, add:

```bash
-H "authorization: Bearer $API_TOKEN"
```

Recent runs:

```bash
curl 'http://127.0.0.1:8765/v1/runs/recent?limit=10'
```

Embedding status:

```bash
curl http://127.0.0.1:8765/v1/embeddings/status
```

Probe embedding dimensions after setting `EMBEDDING_ENABLED=true`:

```bash
curl -X POST http://127.0.0.1:8765/v1/embeddings/probe \
  -H 'content-type: application/json' \
  -d '{"dry_run": false}'
```

Read-only query:

```bash
curl -X POST http://127.0.0.1:8765/v1/query/run \
  -H 'content-type: application/json' \
  -d '{"query": "local brain", "limit": 5}'
```

Dry-run lint:

```bash
curl -X POST http://127.0.0.1:8765/v1/lint/run \
  -H 'content-type: application/json' \
  -d '{"dry_run": true}'
```

## MCP

Run the stdio MCP server from the same image when an MCP host needs direct tool
access instead of REST:

```bash
docker compose -f services/local-brain/docker-compose.example.yml run --rm local-brain fritz-local-brain-mcp
```

Available tools mirror the safe service workflows:

- `brain_status`
- `brain_compile`
- `brain_sync`
- `brain_recent_runs`
- `brain_query`

## CLI

The image also includes a small REST CLI for manual operations:

```bash
docker compose -f services/local-brain/docker-compose.example.yml exec local-brain \
  fritz-local-brain-cli status
```

Useful commands:

- `fritz-local-brain-cli status`
- `fritz-local-brain-cli compile --max-captures 1`
- `fritz-local-brain-cli sync --vault engineering`
- `fritz-local-brain-cli recent-runs --limit 5`
- `fritz-local-brain-cli query "local brain" --limit 5`
- `fritz-local-brain-cli lint`

## Roadmap

Deferred functionality is tracked in `.planning/roadmap.md` at the repository
root.
