# Configuration

Fritz Local has a single configuration model shared across every platform
binding (Claude Code, Pi, and any future adapters). There is **one resolution
path** for project-overridable settings, implemented by
`get_setting()` in `hooks/brain_common.py`.

## Precedence

For any project-overridable setting, the value is resolved in this order:

1. **Project** — the per-project `.fritz-local.json` file (walked up from the
   current working directory).
2. **Central** — the `settings:` block in `~/.brain/registry.yaml`.
3. **Default** — the built-in default for that setting.

> **project (`.fritz-local.json`) > central (`registry.yaml settings:`) > defaults**

### "Present but null/empty" semantics

A configuration layer is considered to *provide* a value only when it contains
the key **and** the value is not `null`/`None`. A layer that omits the key, or
sets it explicitly to `null`, falls through to the next layer. So a present
`.fritz-local.json` that does not mention a key does **not** mask the central
value or the default — it simply does not contribute that key.

(The two injection getters layer stricter validation on top of this lookup;
see [Edge cases](#edge-cases-for-the-injection-getters) below.)

## Central settings schema (`~/.brain/registry.yaml`)

All settings live under the top-level `settings:` key and are optional. Shown
with their defaults:

```yaml
settings:
  # Context injection level: off | light | full   (default: off)
  #   off   advisory "BRAIN CHECK" reminder only
  #   light hook searches knowledge dirs, injects matching file paths
  #   full  light + agent must spawn a subagent to read/synthesize
  context_injection: off

  # Max characters for context-injection output (default: 8000)
  max_injection_chars: 8000

  # Check for Fritz Local updates on session start (default: true)
  update_check: true

  # Optional Dockerized Local Brain service (service-only plumbing, see note).
  local_brain_service:
    enabled: false
    base_url: http://127.0.0.1:8765
    # api_token: replace-with-random-token   # resolved before api_token_env
    api_token_env: LOCAL_BRAIN_API_TOKEN
    allow_remote: false
    auto_compile_on_ingest: true
    suggest_setup: true
```

### Known settings keys

| Key                              | Type   | Default                  | Project-overridable | Resolver                                       |
| -------------------------------- | ------ | ------------------------ | ------------------- | ---------------------------------------------- |
| `context_injection`              | enum   | `off`                    | yes                 | `get_context_injection_level` → `get_setting`  |
| `max_injection_chars`            | int    | `8000`                   | yes                 | `get_max_injection_chars` → `get_setting`      |
| `update_check`                   | bool   | `true`                   | yes (via resolver)  | `get_setting("update_check", True)`            |
| `local_brain_service`            | block  | unset (disabled)         | **no** (see note)   | `get_local_brain_service_config` (service-only)|

> **Note on `local_brain_service`:** this block is **service/machine-level
> plumbing**, not a per-project override. It configures the optional Dockerized
> Local Brain service (base URL, API token, remote policy, scheduler hints).
> These values describe how this machine talks to a service and must not be
> silently overridable by an arbitrary checked-out project's
> `.fritz-local.json` (a trust/security boundary). It is therefore intentionally
> resolved by its own helper (`get_local_brain_service_config`) and is **not**
> routed through `get_setting`.

## External targets (`external_targets:` in `registry.yaml`)

The `external_targets:` block is **optional and service-mode only**. It
describes off-brain systems that the optional Docker mirror agent can pull
data from. The brain core (compile, query, captures) functions fully when
this key is absent.

### Schema

Each entry under `external_targets:` is keyed by a short name and may carry:

| Field | Type | Default | Description |
|---|---|---|---|
| `kind` | enum | required | `local-vault` \| `mcp` \| `drive` \| `offsite` |
| `connection` | string | `null` | Path, URL, URI, or MCP server reference |
| `auth` | any | `null` | Token value, env-var name, or credentials ref (opaque; resolved by the service) |
| `mirror_mode` | enum | `index-only` | `index-only` — title/path index only; `full-summary` — full-text summary mirrored in |

Unknown per-kind extra fields are allowed (passed through to the mirror agent
without validation).

### Example

```yaml
external_targets:
  team-vault:
    kind: local-vault
    connection: ~/Notes/TeamVault
    mirror_mode: full-summary
  team-mcp:
    kind: mcp
    connection: mcp://obsidian-bridge
    auth: OBSIDIAN_MCP_TOKEN
    mirror_mode: index-only
  shared-drive:
    kind: drive
    connection: /mnt/shared/knowledge
  offsite-affine:
    kind: offsite
    connection: https://affine.example.com/workspace/<id>
    auth: AFFINE_TOKEN
    mirror_mode: full-summary
```

> **Note:** `external_targets` is loaded and validated by
> `load_external_targets()` in `registry.py`. An invalid `kind` or
> `mirror_mode` raises a `RegistryError` at load time. Mirror execution
> (fetching/summarising) is performed by the Docker mirror agent and is out
> of scope here.

## Per-project override (`.fritz-local.json`)

A `.fritz-local.json` file placed in (or above) a project's working directory
overrides central settings for that project. The resolver walks up from `cwd`
until it finds one. Recognized fields:

```json
{
  "vault": "engineering",
  "project": "fritz-ai-local",
  "context_injection": "full",
  "max_injection_chars": 12000
}
```

- `vault` — bind this project to a named vault from `registry.yaml`.
- `project` — project label used for project-specific context injection.
- `context_injection` — override the central injection level (`off`/`light`/`full`).
- `max_injection_chars` — override the central character budget.

Any project-overridable setting key may appear here; unknown keys are ignored.

### Trust boundary

Project bindings (`vault`, and the per-project context behavior) are only
honored when `cwd` is within a trusted location, as enforced by
`resolve_project_vault()`. The generic `get_setting()` resolver applies the
project layer when given a `fritz_local` dict or a `cwd`; callers that need the
trust check resolve the vault first and pass the resulting `fritz_local`.

## Worked example

Central `~/.brain/registry.yaml`:

```yaml
settings:
  context_injection: light
  max_injection_chars: 8000
```

Project `~/code/acme/.fritz-local.json`:

```json
{
  "context_injection": "full"
}
```

Resolved values when working in `~/code/acme`:

| Setting               | Project value | Central value | Default | **Resolved** |
| --------------------- | ------------- | ------------- | ------- | ------------ |
| `context_injection`   | `full`        | `light`       | `off`   | **`full`**   |
| `max_injection_chars` | _absent_      | `8000`        | `8000`  | **`8000`**   |
| `update_check`        | _absent_      | _absent_      | `true`  | **`true`**   |

The project wins where it specifies a value; otherwise central wins; otherwise
the default.

### Edge cases for the injection getters

`get_context_injection_level` keeps a stricter rule than the generic resolver,
preserved for backward compatibility: when a `.fritz-local.json` **is present**,
its value is authoritative and never falls through to central — a present
project file without a valid `context_injection` resolves to `off`, even if
central is `full`. Only when **no** project file is present does it consult the
central value (via `get_setting`). `get_max_injection_chars` follows the plain
project > central > default precedence and coerces the result to `int`.

## The single resolver

```python
def get_setting(key: str, default=None, *, fritz_local: dict | None = None, cwd: str | None = None):
    """project (.fritz-local.json) > central (registry.yaml settings:) > default."""
```

Every Python binding resolves project-overridable configuration through this
one function (directly, or through the thin `get_context_injection_level` /
`get_max_injection_chars` wrappers that delegate to it). No binding reads an
overridable setting "its own way."

## Cross-platform consistency (Pi binding)

The Pi binding (`bindings/pi/index.ts`) does **not** re-implement settings
resolution in TypeScript. It invokes the same Python hooks
(`brain_session_start.py`, `brain_prompt_check.py`) as a subprocess, so all
context-injection configuration is resolved by the Python `get_setting()` path
— the single source of truth. The Pi binding only reads `registry.yaml` for
existence/health checks (e.g. `/fritz status`), never to resolve overridable
settings. This guarantees the precedence rule is identical on every platform by
construction: there is exactly one resolver, and every binding goes through it.
