# Local Brain Service

Optional Dockerized service-mode add-on for Fritz Local.

The MVP is intentionally compile-only. It loads
`skills/fritz:brain-compile/SKILL.md` as the compile agent's task instructions,
wraps all captures as untrusted data, asks a Pydantic AI agent for structured
article write proposals, and applies only proposals that pass Python path and
policy validation.

The compile agent can use one bounded read-only context tool to load captures,
vaults, and existing article paths. It cannot write directly.

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
- The service cannot delete files or sync externally in the MVP.

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
- `LLM_ENDPOINT`: any OpenAI-compatible local or remote endpoint.
- `LLM_API_KEY`: optional. Leave empty for endpoints that do not
  require a key.
- `CAPTURE_MAX_CHARS`: maximum characters read from each capture before model
  input truncation. Defaults to `4000`.
- `COMPILE_MAX_CAPTURES`: default maximum captures per run when the request does
  not specify `max_captures`, also used by the scheduler. Defaults to `1`.

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

If `API_TOKEN` is set, add:

```bash
-H "authorization: Bearer $API_TOKEN"
```

## Roadmap

Deferred functionality is tracked in `.planning/roadmap.md` at the repository
root.
