---
name: brain-service-setup
description: >
  Interactive runbook to provision or reconfigure the Fritz Local Brain Docker
  service. Asks the full question set (LLM backend, embeddings, scheduler,
  autostart, optional one-time backlog drain, API token), confirms a
  summary, then delegates ALL execution to the PROV1 provisioning engine
  (scripts/local-brain-service.py provision). Does not write any config or
  start any container before every answer is confirmed.
  Use when the user wants to set up, provision, or reconfigure the Local Brain
  Docker service, or run /brain-service-setup.
---

# Brain Service Setup

Interactive provisioning runbook for the Fritz Local Brain Docker service.
This skill collects all required answers, then delegates execution to the
idempotent PROV1 engine — it never duplicates provisioning logic.

## Trigger

Activate when the user asks to:
- Set up, provision, or reconfigure the Local Brain Docker service
- "Configure the brain service" or "start the brain Docker stack"
- Run `/brain-service-setup`

## Hard rules (read before proceeding)

1. **Do NOT write any file** (`.env`, `registry.yaml`, or any other config)
   before all questions in Phase 1 have been answered and the human has
   confirmed the summary in Phase 2.
2. **Do NOT run any Docker command** or call `local-brain-service.py` before
   Phase 2 confirmation.
3. **Do NOT implement provisioning logic yourself.** Delegate every execution
   step to the PROV1 engine (see Phase 3).
4. Ask questions **one at a time**. Wait for the answer before proceeding to
   the next question.
5. If the human declines to answer a required question, explain why it is
   needed and ask again. Do not skip it silently.
6. If the human asks to abort at any point before Phase 2 confirmation, stop
   immediately — write nothing.

## Phase 1: Question set

Work through this ordered checklist. Show progress after each answer
(e.g., `[3/10 answered]`). Do not bundle questions.

---

**Q1 — LLM protocol / source**

> "Which LLM backend will the service use?
>
>   a) OpenAI-compatible endpoint (self-hosted, Azure OpenAI, OpenRouter, etc.)
>   b) Ollama running locally on this machine (will pull the model now)
>   c) Official OpenAI API (api.openai.com)
>
> Type a, b, or c."

- Record the answer as `llm_source`.
- If `b` (Ollama), note you will ask for the model name and offer to pull it
  via `ollama pull <model>` **after** the full provision completes.
- If `c`, set `llm_protocol=openai-compatible`,
  `llm_endpoint=https://api.openai.com/v1`.

---

**Q2 — LLM endpoint URL** (skip if `llm_source=b` — the engine defaults to
`http://host.docker.internal:11434/v1`)

> "What is the full base URL for the LLM API?
> (e.g. `http://host.docker.internal:11434/v1` for a local Ollama,
> `https://openrouter.ai/api/v1` for OpenRouter)"

Validate that the URL starts with `http://` or `https://` and contains no
credentials, query strings, or fragments. Ask again if invalid.

---

**Q3 — LLM model name**

> "Which model should the service use?
> (e.g. `llama3.2:latest`, `gpt-4o-mini`, `claude-3-5-haiku-20241022`)"

---

**Q4 — LLM API key**

> "Does this endpoint require an API key?
>
>   - Enter the key now (it will be stored in the repo-root `.env` at mode
>     0600, never committed)
>   - Or enter `none` / leave blank if no key is needed (local Ollama, etc.)"

Accept blank or `none` as equivalent to no key.

---

**Q5 — Embeddings**

> "Do you want to enable embeddings? This indexes compiled knowledge articles
> for semantic search. **Note: indexed text (article excerpts) is sent to the
> embedding provider you configure here** — do not enable if your knowledge
> is sensitive and the provider is external.
>
>   yes / no"

If yes, ask two follow-up sub-questions (still one at a time):

**Q5a — Embedding endpoint**

> "Embedding endpoint URL? (default: same as LLM endpoint,
> `http://host.docker.internal:11434/v1`)"

Accept blank to reuse the LLM endpoint.

**Q5b — Embedding model**

> "Embedding model name? (e.g. `nomic-embed-text:latest`, `text-embedding-3-small`)"

---

**Q6 — Scheduler**

> "Should the service run an automated background scheduler that periodically
> compiles and syncs?
>
>   yes / no"

If yes, ask two follow-up sub-questions:

**Q6a — Interval**

> "How often should the scheduler run? Enter minutes (default: 30)"

Validate it is a positive integer.

**Q6b — Scheduler mode**

> "Should the scheduler apply changes automatically (`apply`) or only propose
> them for your review (`propose` / dry-run)? (apply / propose)"

Record `scheduler_dry_run = (answer == "propose")`.

---

**Q7 — Autostart**

> "Should the service start automatically when you log in (OS-level autostart
> via launchd / systemd / Windows Task Scheduler)?
>
>   yes / no"

---

**Q8 — Persistent scheduler approval token (optional)**

> "The scheduler uses an approval token to gate large-batch compiles. Should a
> persistent APPROVAL_TOKEN be written to `.env` now?
>
>   - Enter a token string to set it, OR
>   - Leave blank to skip (the scheduler will run without large-batch gating
>     unless you edit `.env` manually later)
>
>   enter a token string / leave blank"

Map a provided token to `--approval-token <token>`.

**Important — persistent vs. one-time:**
- `--approval-token <token>` writes `APPROVAL_TOKEN` to `.env` permanently.
  The running scheduler reads it on every large-batch compile decision.
- This is **completely separate** from `--drain-approval-token`, which is only
  forwarded in the single one-time backlog-drain POST and is never persisted.

---

**Q9 — Reconciliation autonomy**

> "When new knowledge is reconciled against existing knowledge, should the
> reconciliation agent apply changes automatically or only propose them?
>
>   a) apply  — changes are written automatically (default)
>   b) propose — changes are proposed for your review (dry-run)
>
>   apply / propose"

Map to `--reconciliation-autonomy <value>`. Defaults to `apply` if left blank.

---

**Q10 — One-time backlog drain (optional)**

> "Do you want to trigger a one-time apply-mode compile immediately after
> provisioning, to drain any pending captures from the backlog?
>
>   yes / no  (default: no)"

If yes, ask one follow-up sub-question:

**Q10a — Drain approval token** (only if Q10 = yes)

> "If the backlog is large (above the service's batch threshold), the compile
> will require an approval token to proceed. Enter a token string now, or leave
> blank to let the compile proceed without one (it will be rejected with 403 if
> the batch is too large and no token is supplied).
>
>   enter a token string / leave blank"

**Important — what this does and does not do:**
- `--drain-backlog` triggers a **single, one-time** apply-mode compile POST at
  the end of the provision run. It is not a standing scheduler setting.
- `--drain-approval-token <token>` is included in **that one POST request only**.
  It is **not** written to `.env`, it is **not** persisted anywhere, and it has
  **no effect if `--drain-backlog` is omitted**.
- The persistent `APPROVAL_TOKEN` is set via `--approval-token` (Q8), not here.

---

**Q11 — API token**

> "The service exposes a local REST API protected by a Bearer token.
>
>   - Enter a token you want to use, OR
>   - Leave blank to auto-generate a cryptographically random token
>
> (The token will be written to `.env` and to `~/.brain/registry.yaml`)"

---

**Q12 — Telemetry (usage dashboard)**

> "The service can record usage telemetry that powers the `/dashboard` view.
>
>   - Enable usage telemetry? (yes / no, default: yes)
>   - Store the full query text in telemetry? (yes / no, default: yes —
>     answer no for privacy; only counts/metadata are kept)
>   - Telemetry retention in days? (default: 90; 0 = keep forever)"

Records `telemetry_enabled`, `telemetry_store_query_text`,
`telemetry_retention_days`. Defaults: enabled, store query text, 90-day
retention.

---

After all twelve questions (plus sub-questions) are answered, proceed to Phase 2.

---

## Phase 2: Summary and confirmation

Print a structured summary of every answer collected in Phase 1. Use a format
like:

```
Brain Service Setup — Proposed Configuration
=============================================

LLM
  Protocol : openai-compatible
  Endpoint : http://host.docker.internal:11434/v1
  Model    : llama3.2:latest
  API key  : (none)

Embeddings
  Enabled  : yes
  Endpoint : http://host.docker.internal:11434/v1  (shared with LLM)
  Model    : nomic-embed-text:latest
  Note     : indexed text will be sent to this endpoint

Scheduler
  Enabled  : yes
  Interval : 30 minutes
  Mode     : dry-run (propose only)

Autostart              : no
Reconciliation autonomy: apply
Drain backlog          : no  (skip one-time post-provision compile)
API token              : (auto-generate)

Execution plan
  1. Preflight: check docker, docker compose, python, port 8765
  2. Write / merge repo-root .env  (mode 0600)
  3. Write / merge ~/.brain/registry.yaml
  4. docker compose build + up -d
  5. Poll /health until ready, then probe LLM reachability
  6. (autostart skipped)

No files will be written until you confirm.
```

Then ask:

> "Proceed with provisioning? (yes / no  — or change any answer by naming the
> question number)"

If the human names a question number to change, re-ask that question (and any
affected sub-questions), update the summary, and ask for confirmation again.

Do NOT proceed until the human explicitly confirms with `yes`.

---

## Phase 3: Delegate to PROV1

Once the human confirms, invoke the PROV1 engine. Do NOT implement any
provisioning logic yourself.

The engine entry point is:

```
python scripts/local-brain-service.py provision \
  --llm-protocol <protocol> \
  --llm-endpoint <endpoint> \
  --llm-model <model> \
  [--llm-api-key <key>] \
  [--embedding-enabled] \
  [--embedding-endpoint <endpoint>] \
  [--embedding-model <model>] \
  [--scheduler-enabled] \
  [--scheduler-apply] \
  --api-port 8765 \
  [--install-autostart] \
  [--api-token <token>] \
  [--approval-token <token>] \
  [--reconciliation-autonomy apply|propose] \
  [--telemetry-enabled | --no-telemetry-enabled] \
  [--telemetry-store-query-text | --no-telemetry-store-query-text] \
  [--telemetry-retention-days <days>] \
  [--drain-backlog [--drain-approval-token <token>]]
```

Run this from the fritz-ai-local repo root. The engine is idempotent — it is
safe to re-run to reconfigure. Pass flags that match the confirmed answers:

| Answer | Flag(s) |
|---|---|
| embeddings yes | `--embedding-enabled` |
| scheduler yes + apply mode | `--scheduler-enabled --scheduler-apply` |
| scheduler yes + propose mode | `--scheduler-enabled` (dry-run is the default) |
| autostart yes | `--install-autostart` |
| API token provided | `--api-token <token>` (omit to auto-generate) |
| persistent approval token provided (Q8) | `--approval-token <token>` |
| reconciliation autonomy = propose (Q9) | `--reconciliation-autonomy propose` |
| reconciliation autonomy = apply (Q9) | `--reconciliation-autonomy apply` (default; may omit) |
| telemetry enabled (Q12) | `--telemetry-enabled` (default; may omit) |
| telemetry disabled (Q12) | `--no-telemetry-enabled` |
| store query text (Q12) | `--telemetry-store-query-text` (default; may omit) |
| do not store query text (Q12) | `--no-telemetry-store-query-text` |
| telemetry retention (Q12) | `--telemetry-retention-days <days>` (default 90; 0 = keep forever) |
| drain backlog yes | `--drain-backlog` |
| drain backlog yes + one-time approval token | `--drain-backlog --drain-approval-token <token>` |

**Token flag semantics — keep these distinct:**
- `--approval-token <token>` writes `APPROVAL_TOKEN` **persistently to `.env`**.
  The running scheduler reads it on every large-batch compile decision. This is
  the persistent setting that gates ongoing scheduler runs.
- `--drain-approval-token <token>` is forwarded in the **single one-time
  backlog-drain POST** only; it is **not written to `.env`** and has no effect
  outside that one request. Omitting `--drain-backlog` makes this flag inert.
- These two flags are independent and serve different purposes. Do not conflate
  them.

The `provision` sub-command is also aliased as `setup`, so
`python scripts/local-brain-service.py setup …` is equivalent.

Do not pass flags not supported by the engine. Do not invent provisioning
behavior the engine does not implement. Refer to `scripts/provision_engine.py`
(`ProvisionConfig` dataclass + `provision(...)` function) for the canonical
list of supported inputs.

---

## Phase 4: Post-provision

After the engine returns, report the result to the human.

### If the engine succeeds (`overall: ok` or `overall: already_provisioned`)

Print each step with its status marker:

```
Provision result: ok

  ✓ preflight     : docker ok; docker compose ok; python ok; port 8765: free
  ✓ write_env     : .env: merged 20 keys
  ✓ write_registry: ~/.brain/registry.yaml: wrote local_brain_service
  ✓ docker_start  : build + up -d completed
  ✓ verify        : service healthy; LLM reachable
  – install_autostart: autostart not requested
```

Then show the operational state:

```
Service is running at http://127.0.0.1:8765
API token: <token> (also in .env as API_TOKEN and in ~/.brain/registry.yaml)

Other brain-* skills (brain-compile, brain-query, brain-sync) now route
through the service automatically when it is reachable.
```

Then register the Claude Code capture hooks. The binding is a directory-source
marketplace, so its skills load but its hooks do NOT auto-register — without
this step Claude records 0 captures. Run the installer (idempotent; merges into
`~/.claude/settings.json`, preserving other plugins' hooks):

```
python3 <REPO>/hooks/install_claude_hooks.py
```

If `llm_source=b` (Ollama) and the user agreed to pull the model, offer:

> "Would you like me to run `ollama pull <model>` now to ensure the model
> is available locally?"

### If the engine returns `overall: partial` or `overall: failed`

Report each failed/warning step. Do not attempt to fix failures yourself —
surface the error and tell the human how to re-run after resolving it:

```
Some steps failed. Fix the errors above, then re-run:
  python scripts/local-brain-service.py provision [same flags]

The engine is idempotent — steps that already completed will be skipped.
```

### Reconfigure / Rollback

#### Changing LLM backend, embeddings, or scheduler settings

To change any configuration (e.g. switch from Ollama to OpenAI, enable
embeddings, change the scheduler interval):

1. **Check for drift first** (optional but informative):

   ```
   python scripts/local-brain-service.py reconfigure \
     --llm-model gpt-4o-mini \
     --llm-endpoint https://api.openai.com/v1 \
     --llm-api-key sk-... \
     [other flags] \
     --force
   ```

   The `reconfigure` sub-command (alias: `re-provision`) detects which keys
   in the running container differ from the desired config (drift detection),
   then re-applies provisioning via the PROV1 engine. It never duplicates
   provisioning logic — it delegates to `provision()` internally.

   - **Drift detected** → re-provision runs automatically (build + up + verify).
   - **No drift + no `--force`** → returns `no_drift`; nothing runs.
   - **`--force`** → re-provision always runs regardless of drift state.

2. **Or run provision directly** (equivalent, and always idempotent):

   ```
   python scripts/local-brain-service.py provision \
     --llm-model gpt-4o-mini \
     --llm-endpoint https://api.openai.com/v1 \
     --llm-api-key sk-...
   ```

   The engine merges new values into the existing `.env` without destroying
   unrelated keys, then rebuilds and restarts the container. Steps that are
   already correct are skipped.

Both paths handle container restart (via `docker compose build + up -d`) and
verify reachability after the change.

#### Rolling back to local-only mode (no data loss)

To disable the Docker service and fall back to local slash-skill brain
workflows without losing any capture or knowledge data:

```
python scripts/local-brain-service.py rollback
```

What `rollback` does:
- Sets `settings.local_brain_service.desired: local` and
  `settings.local_brain_service.enabled: false` in `~/.brain/registry.yaml`,
  preserving all other keys (vaults, external targets, other settings, the
  API token, etc.).
- Runs `docker compose down` to stop the container (pass `--no-stop` to
  leave it running while only updating the registry).
- Does **NOT** delete `~/.brain/capture/` or `~/.brain/knowledge/` — all
  data is preserved on the host volume.
- Does **NOT** modify `.env`.

After rollback:
- `get_local_brain_service_desired()` returns `"local"` — agents no longer
  inject the forcing instruction.
- `local_brain_service_enabled()` returns `False` — service routing is off.
- Brain operations fall back to local slash-skill workflows automatically.

To re-enable the service after a rollback, re-run `provision` (or this skill)
with your desired configuration. The `--api-token` flag accepts the token
previously stored in `~/.brain/registry.yaml`; if omitted, the existing token
in `.env` is preserved automatically.

## Important

- This skill collects answers and confirms them before delegating to PROV1.
  It never implements or duplicates provisioning steps.
- The PROV1 engine handles `.env` writes, registry updates, Docker build/start,
  health polling, and autostart installation. Trust it.
- The engine is idempotent: steps that are already correct are skipped without
  side effects.
- If `settings.local_brain_service` is already configured in
  `~/.brain/registry.yaml`, note this at the start of Phase 1:
  > "The service is already configured. I will ask the same questions so you
  > can change any setting. Unchanged values can be confirmed as-is."
- Do not invent extra provisioning steps beyond what the engine supports. Check
  `scripts/provision_engine.py` (`ProvisionConfig`) for the authoritative list.
