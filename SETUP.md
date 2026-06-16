# Fritz Local — Setup

This guide installs Fritz Local — the agent-agnostic brain — and wires one of
the four first-class platforms (Claude Code, pi, Codex, Hermes) into it. Each
platform has its own walkthrough below; pick yours.

The authoritative per-platform reference is each binding's own README under
`bindings/`. This guide is the consolidated walkthrough; the binding READMEs go
deeper.

## Location independence — no required clone path

The repository can live **anywhere** on disk. There is no required location.

- **Durable data** lives under `~/.brain` (override with the `BRAIN_HOME`
  environment variable).
- **The repo** is resolved dynamically: `FRITZ_REPO_PATH` if set, otherwise from
  each hook/binding file's own location.

Throughout this guide `<repo>` means *wherever you cloned Fritz*. A common
optional example is `~/.fritz-ai-local`, but any path works equally well —
substitute your actual clone path. Do not treat `~/.fritz-ai-local` as required.

## Prerequisites

- Python 3.10+
- `pyyaml` (`pip install pyyaml`)

Clone the repo wherever you like, e.g.:

```bash
git clone https://git.intelligenttools.ai/intelligenttools-ai/fritz-ai-local.git
cd fritz-ai-local
```

## Shared bootstrap — `scripts/install.py`

Every platform except Hermes shares one installer. It creates the `~/.brain`
layout, symlinks the canonical Python hooks into `~/.brain/hooks/`, and installs
the per-platform skill variants.

```bash
# Bootstrap the brain and wire a plugin/extension platform:
python3 scripts/install.py install --agent claude     # or: codex | pi
python3 scripts/install.py install --agent codex
python3 scripts/install.py install --agent pi

# Read-only health snapshot:
python3 scripts/install.py status

# Run a key hook against a synthetic event and assert PASS:
python3 scripts/install.py smoke-test

# Preview what install would do without writing anything:
python3 scripts/install.py install --agent claude --dry-run
```

`--agent` accepts **`claude`, `codex`, or `pi`**. **Hermes is not an `--agent`**:
it is a non-coding gateway with no skills, so `install --agent hermes` is
deliberately rejected. Hermes is bootstrapped by the YAML profile merge described
in its section below (the hook symlinks it needs are installed by *any*
`install.py install` run, since the Hermes wrappers are in the shared
`REQUIRED_HOOKS` list).

---

## Platform: Claude Code

Claude Code is wired through a **self-registering plugin** loaded from a local
`directory` marketplace source (`bindings/claude/`). Enabling the plugin
registers all hooks and skills — there are **no manual `~/.claude/settings.json`
hook edits**.

1. Bootstrap the brain and install the Claude skill variants:

   ```bash
   python3 scripts/install.py install --agent claude
   ```

2. Inside Claude Code, add the local marketplace and enable the plugin:

   ```
   /plugin marketplace add <repo>/bindings/claude
   /plugin install fritz-brain@fritz-local
   ```

   Enabling the plugin registers the `SessionStart`, `UserPromptSubmit`,
   `PreCompact`, and `Stop` hooks via the plugin's own `hooks/hooks.json`
   (which references the hooks through `${CLAUDE_PLUGIN_ROOT}`). **Do not edit
   `~/.claude/settings.json` hooks by hand** — the plugin does it.

3. Verify:

   ```bash
   python3 scripts/install.py smoke-test
   ```

Full detail: [`bindings/claude/README.md`](bindings/claude/README.md). The
plugin satisfies the full nine-capability bar.

---

## Platform: pi (`pi-coding-agent`)

pi is wired through a **native extension** (`bindings/pi/index.ts`) written
against the `@earendil-works` pi SDK
(`@earendil-works/pi-coding-agent`). It registers the `brain_save_fact` tool,
the `/fritz` command, and the session/agent lifecycle hooks.

1. Bootstrap the brain and install the pi skill variants:

   ```bash
   python3 scripts/install.py install --agent pi
   ```

   (Equivalently, from inside a pi session: `/fritz init`.)

2. Install the extension into pi's extensions directory so pi loads it, e.g.:

   ```bash
   mkdir -p ~/.pi/agent/extensions/fritz-brain
   cp bindings/pi/index.ts ~/.pi/agent/extensions/fritz-brain/index.ts
   ```

   The binding resolves the Fritz repo independently (`FRITZ_REPO_PATH` or
   `import.meta.url`), so it does not need to live inside the repo.

3. Bootstrap and verify from inside a pi session:

   ```
   /fritz
   /fritz smoke-test
   ```

`/fritz` exposes `status`, `init`, `repair-hooks`, and `smoke-test`. Full
detail: [`bindings/pi/README.md`](bindings/pi/README.md). pi is the role-model
binding — it defines the nine-capability bar.

---

## Platform: Codex

Codex is wired through a **plugin** (`bindings/codex/`). The plugin/skills half
is verified against `codex-cli 0.139.0`; the hook half (session/turn lifecycle)
is the documented open capability and is marked
**REQUIRES-IN-CODEX-VERIFICATION** in the binding README — do not assume the
hooks are live until confirmed inside a real Codex session.

1. Bootstrap the brain and install the Codex skill variants:

   ```bash
   python3 scripts/install.py install --agent codex
   ```

2. Register and install the plugin from the local marketplace:

   ```bash
   codex plugin marketplace add <repo>/bindings/codex
   codex plugin add fritz-brain@fritz-local
   codex plugin list
   ```

3. **Hooks (open capability).** Codex has a real hook subsystem, but its config
   schema is not introspectable from the local CLI. The candidate wiring and the
   verified `notify` turn-end fallback are annotated in
   [`bindings/codex/hooks/config-hooks.toml.example`](bindings/codex/hooks/config-hooks.toml.example).
   Add the candidate block to `~/.codex/config.toml`, run `codex doctor`, and if
   the config loads, verify capture/guardrail/context inside a real Codex
   session. Do not overstate the hooks as working until this passes.

Full detail, the per-capability verified/open table, and the manual in-Codex
acceptance test: [`bindings/codex/README.md`](bindings/codex/README.md).

---

## Platform: Hermes

Hermes is a **non-coding gateway agent**. It has no plugin or skills mechanism,
so its binding is **shell-hook only**: a YAML block merged into the Hermes
profile `config.yaml` plus three committed wrapper scripts. There is no
`--agent hermes` skills install.

1. Install the brain hooks (any `--agent` works; this symlinks the canonical
   wrappers, including the Hermes ones, into `~/.brain/hooks/`):

   ```bash
   python3 scripts/install.py install --agent claude
   ```

   (The `--agent` only affects the skills install, which Hermes does not use.)

2. **Merge the YAML hook block** from
   [`bindings/hermes/hermes-hooks.yaml`](bindings/hermes/hermes-hooks.yaml) into
   the Hermes profile `config.yaml` — usually `~/.hermes/config.yaml`, or a
   profile-specific config such as `~/.hermes-infra/config.yaml`:

   ```yaml
   hooks_auto_accept: true
   hooks:
     pre_llm_call:
       - command: "python3 ~/.brain/hooks/hermes_brain_context.py"
         timeout: 15
     on_session_finalize:
       - command: "python3 ~/.brain/hooks/hermes_brain_capture.py"
         timeout: 30
       - command: "python3 ~/.brain/hooks/hermes_brain_autocapture.py"
         timeout: 30
   ```

   `hooks_auto_accept: true` lets non-interactive gateway sessions load these
   managed local hooks without a trust prompt.

3. **Honor `HERMES_HOME`.** The finalize wrappers resolve the transcript from
   `$HERMES_HOME/sessions` (falling back to `~/.hermes/sessions`). For a
   non-default profile root, point `HERMES_HOME` at it:

   ```bash
   export HERMES_HOME=~/.hermes-infra
   ```

4. **Restart** Hermes / the gateway so it picks up the merged config.

Because Hermes has no skill runtime, the explicit-save capability is the
`brain_save_fact.py` CLI directly:

```bash
echo '{"title":"Gateway deploy note","body":"Restart via systemctl.","tags":["FritzBrain"]}' \
  | python3 ~/.brain/hooks/brain_save_fact.py --json
```

Full detail, the canonical→Hermes event map, and the manual acceptance test:
[`bindings/hermes/README.md`](bindings/hermes/README.md). Hermes meets C1–C6 and
C9; **C8 (skills) is N/A** — a gateway has nothing to install skills into.

---

## Capture layout

After install, durable artifacts land under `~/.brain/capture/`:

| Path | Role |
|------|------|
| `capture/inbox/` | **Explicit** saves and auto-captured durable facts |
| `capture/daily/` | **Automatic** per-session rollups, one file per day |
| `capture/auto/` | `.seen` content-hash **dedup markers** for auto-capture |
| `log.md` | Human-readable **audit** log |

## Configuration

Configuration follows one resolution path — **project (`.fritz-local.json`) >
central (`registry.yaml` `settings:`) > defaults**. See
[`docs/configuration.md`](docs/configuration.md) for the full reference.

Central defaults live under `settings:` in `~/.brain/registry.yaml` (seeded from
`registry/registry.template.yaml`):

```yaml
settings:
  context_injection: off       # off | light | full
  max_injection_chars: 8000
  update_check: true
  # local_brain_service:       # optional Dockerized service, disabled by default
  #   enabled: false
```

A per-project `.fritz-local.json` (walked up from the working directory) overrides
central settings for that project. Safe to commit — no secrets:

```json
{
  "vault": "engineering",
  "project": "my-service",
  "context_injection": "off"
}
```

## Adopting a non-first-class runtime

To wire a runtime that is **not** one of the four first-class platforms, build a
conformant binding from the kit:

1. Read [`docs/integration-contract.md`](docs/integration-contract.md) — the
   canonical events, the hook JSON protocol, the adapter interface, the config
   model, the skill-naming rule, and the nine-item capability checklist.
2. Copy [`bindings/_template/`](bindings/_template/README.md) to
   `bindings/<runtime>/` and hand the self-contained brief
   [`bindings/_template/INITIAL_PROMPT.md`](bindings/_template/INITIAL_PROMPT.md)
   to an agent loop.
3. Satisfy all nine capabilities and verify with
   `python3 scripts/install.py install/smoke-test --agent <runtime>` against a
   temp `BRAIN_HOME`.

## Optional: Dockerized Local Brain service

After the brain and a platform binding are working, you can optionally enable the
Dockerized Local Brain service for compile, semantic search, sync, lint, and
embeddings. It is disabled by default and enabled explicitly in
`~/.brain/registry.yaml`:

```yaml
settings:
  local_brain_service:
    enabled: true
    base_url: http://127.0.0.1:8765
    api_token: replace-with-a-unique-random-token
    api_token_env: LOCAL_BRAIN_API_TOKEN
    allow_remote: false
```

Start it through the repository script and verify:

```bash
python3 scripts/local-brain-service.py start --build
curl http://127.0.0.1:8765/health
curl -H "authorization: Bearer $LOCAL_BRAIN_API_TOKEN" http://127.0.0.1:8765/v1/status
```

When enabled and reachable, agents prefer the service for supported workflows
(compile, search/query, sync, lint, embeddings); otherwise the local hooks and
slash skills remain the fallback. For safety, hooks probe only loopback service
URLs by default and `allow_remote: false` keeps it on-host. Full service
documentation: [`services/local-brain/README.md`](services/local-brain/README.md).

---

## Docker brain provisioning — reproducible setup per platform

This section describes the **reproducible, agent-driven provisioning flow**
introduced in the PROV epic. Use it when you want the service set up from
scratch in a single guided pass rather than editing `.env` by hand.

### Preflight requirements (all platforms)

| Requirement | Check |
|---|---|
| Docker Desktop (or Docker Engine) | `docker --version` |
| Docker Compose v2 (`docker compose`) | `docker compose version` |
| Python 3.10+ | `python3 --version` |
| Port 8765 free | `lsof -i :8765` (macOS/Linux) |

On **Windows**, use `python` if `python3` is not in PATH.

### The desired:docker + forcing model (PROV3)

The provisioning flow is driven by a single registry key:

```yaml
# in ~/.brain/registry.yaml, under settings:
settings:
  local_brain_service:
    desired: docker    # "docker" | "local" (default: "local")
```

When `desired: docker` is set and the service is **not operational**, every
session start injects a mandatory `REQUIRED ACTION: /fritz:brain-service-setup`
instruction into the agent context. The forcing fires regardless of the
`enabled` flag — it stops only when the service actually answers on its
endpoint.  Once operational, the instruction does not appear.

You may set `desired: docker` in `~/.brain/registry.yaml` before or after
installing the service; the agent will be prompted to run setup on the next
session start.

### Running setup

**Option A — guided skill (recommended):** inside a Fritz-wired session, run:

```
/fritz:brain-service-setup
```

The skill asks a question set (LLM endpoint, model, API key, scheduler
preferences, approval token, autostart) and delegates to the provision engine.

**Option B — provision CLI directly** (for CI or scripted re-runs):

```bash
.venv/bin/python scripts/local-brain-service.py provision \
  --llm-protocol openai-compatible \
  --llm-endpoint http://host.docker.internal:11434/v1 \
  --llm-model llama3.2:latest \
  [--llm-api-key KEY] \
  [--embedding-enabled] \
  [--embedding-endpoint URL] \
  [--embedding-model MODEL] \
  [--embedding-api-key KEY] \
  [--scheduler-enabled] \
  [--scheduler-apply] \           # sets SCHEDULER_DRY_RUN=false
  [--api-token TOKEN] \           # omit to auto-generate
  [--api-port PORT] \
  [--api-token-env VAR_NAME] \
  [--approval-token TOKEN] \      # persistent APPROVAL_TOKEN for large-batch gating
  [--install-autostart] \         # see per-OS autostart below
  [--drain-backlog] \
  [--drain-approval-token TOKEN]

# Alias — identical to provision:
.venv/bin/python scripts/local-brain-service.py setup ...
```

`provision` writes `.env` (merging managed keys without discarding unrelated
entries), updates `~/.brain/registry.yaml` `settings.local_brain_service`,
builds and starts the container, then probes `/v1/status` and the configured
LLM endpoint to verify. If the LLM is not reachable the step is a warning
(not a failure) — the container is running but compile will 502 until a model
is served.

> **Note**: `reconciliation_autonomy` is **not** settable via the provision
> CLI. Set it directly in `registry.yaml` under `settings:`.

### Per-OS autostart with `--install-autostart`

Pass `--install-autostart` (or enable it through the setup skill) to register
a daemon that starts the service at login / boot for the current OS user:

| Platform | Mechanism | What is written |
|---|---|---|
| **macOS** | launchd LaunchAgent | `~/Library/LaunchAgents/ai.fritz.local-brain.plist` |
| **Linux** | systemd user unit | `~/.config/systemd/user/fritz-local-brain.service` |
| **Windows** | Task Scheduler logon task | task wrapper batch + registered task |

All three platforms use the same Docker Compose service path. Set
`AUTOSTART_INSTALLED=true` in `.env` afterwards (the installer sets it
automatically). Remove the daemon with:

```bash
python3 scripts/local-brain-service.py uninstall-autostart
```

### Verify

After provisioning completes:

```bash
curl http://127.0.0.1:8765/health
curl -H "authorization: Bearer $LOCAL_BRAIN_API_TOKEN" http://127.0.0.1:8765/v1/status
```

Start a new session — with `desired: docker` and the service now operational,
the forcing instruction will **not** appear.

---

## Verify

1. Start a new session — brain context should be injected (C1).
2. Submit a knowledge-seeking prompt — the BRAIN CHECK guardrail should appear (C2).
3. End the session — a daily capture should appear under `~/.brain/capture/daily/` (C5).
4. `python3 scripts/install.py smoke-test` reports `ALL PASS`.

## Keeping Fritz Local updated

Fritz checks for updates on session start (once per 24h). To apply an update,
run `/fritz:update` (or `/fritz-update` on hyphenated runtimes), or `git pull` in
your clone. Symlinked hooks and skills update immediately; re-run
`scripts/install.py install --agent <agent>` (or `/fritz:update`) to pick up any
new skills and run pending migrations.
