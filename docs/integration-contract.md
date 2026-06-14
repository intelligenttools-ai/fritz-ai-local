# Fritz Integration Contract

This is the **canonical integration contract** for Fritz Local. It is what any
runtime's agent needs to build a *conformant binding* — one that wires a coding
agent runtime (Claude Code, Codex, pi, Hermes, Gemini, or some other runtime)
into the shared Fritz brain.

It does **not** restate the whole system; it *distills* the existing artifacts
into a single binding-facing contract. Read these first, and treat them as
authoritative when this document is terse:

- [`docs/capability-spec.md`](capability-spec.md) — the 9-point capability bar
  with role-model behaviors. The [capability checklist](#7-capability-checklist)
  below references these one-to-one.
- [`docs/configuration.md`](configuration.md) — the full config model
  (`registry.yaml settings:` + `.fritz-local.json`, precedence
  project > central > defaults).
- [`adapters/base.py`](../adapters/base.py) — the `TranscriptAdapter` interface
  and `CaptureEntry` shape.
- [`hooks/claude-code-hooks.json`](../hooks/claude-code-hooks.json),
  [`hooks/codex-hooks.toml`](../hooks/codex-hooks.toml),
  [`hooks/gemini-hooks.json`](../hooks/gemini-hooks.json),
  [`hooks/hermes-hooks.yaml`](../hooks/hermes-hooks.yaml) — the per-platform
  event → hook registrations.
- [`bindings/pi/index.ts`](../bindings/pi/index.ts) — the role-model binding.

A binding is **conformant** when it satisfies all nine items of the
[capability checklist](#7-capability-checklist), drives the hooks via the
[JSON protocol](#2-hook-stdinstdout-json-protocol) described here, and threads
config through the [single config model](#5-config-model). The four first-class
bindings (Claude Code, Codex, pi, Hermes) already do this; the contract below is
written to match what they actually do — not an aspirational protocol.

> **GUARDRAIL.** All durable state lives under `~/.brain` (`BRAIN_HOME`). A
> binding must never hardcode a clone path: resolve the repo from
> `FRITZ_REPO_PATH` first, otherwise from the binding file's own location.

---

## 1. Canonical events

Fritz models the agent lifecycle as **five canonical events**. Every runtime has
its own native event names; a binding maps each native event onto a canonical
one and fires the corresponding hook.

| # | Canonical event | When it fires | Hook the binding runs | Purpose |
|---|-----------------|---------------|-----------------------|---------|
| C1 | **session start** | a new agent session begins | `brain_session_start.py` | inject vault/project context into the first turn |
| C2 | **before-turn / BRAIN CHECK** | a user prompt is submitted, before the model answers | `brain_prompt_check.py` | brain-first guardrail; inject relevant knowledge / "save, don't just answer" reminder |
| C3 | **turn / agent end** | the model finishes a turn | (binding-side) auto-capture; optionally `brain_capture.py` | auto-capture durable knowledge (signal + intent, dedup) |
| C4 | **session end / compact** | session shuts down, or transcript is about to be compacted/summarized | `brain_capture.py` | roll the session up into the daily capture store |
| C5 | **explicit save** | the model calls the save tool during a turn | `brain_save_fact.py` (or in-binding `writeBrainInboxFact`) | write a durable fact to `capture/inbox/` |

C1, C2, C4 are **hook-driven** (the binding shells out to a Python hook and
injects its stdout). C3 (auto-capture) and C5 (explicit save) are **capability
operations** the binding performs directly (role model implements them inline;
Python ports exist as `brain_autocapture.py` and `brain_save_fact.py`). A binding
may either re-implement C3/C5 natively (as pi does in TypeScript) or shell out to
the Python ports — both are conformant as long as the on-disk result is
byte-identical (see [§4](#4-memory--capture-sources)).

### Mapping table — native event → canonical event

Derived from the `hooks/*-hooks.*` files and `bindings/pi/index.ts`.

| Canonical event | Claude Code | Codex | pi | Hermes | Gemini | Generic runtime |
|-----------------|-------------|-------|----|--------|--------|-----------------|
| C1 session start | `SessionStart` | `SessionStart` | `session_start` | (see note) | `SessionStart` | session-begin lifecycle hook |
| C2 before-turn / BRAIN CHECK | `UserPromptSubmit` | `UserPromptSubmit` | `before_agent_start` | `pre_llm_call` | `BeforeAgent` | pre-turn / user-prompt hook |
| C3 turn / agent end | `Stop` | `Stop` | `agent_end` | `on_session_finalize` | `SessionEnd` | turn/agent-end hook |
| C4 session end / compact | `Stop`, `PreCompact` | `Stop` | `session_shutdown`, `session_before_compact` | `on_session_finalize` | `SessionEnd` (compact case via `PreCompress` detection marker, not a registered hook) | session-end and/or about-to-compact hook |
| C5 explicit save | `brain_save_fact` tool | `brain_save_fact` tool | `brain_save_fact` tool | (tool/CLI) | `brain_save_fact` tool | model-callable tool |

**Notes & known divergences (verified against the registration files):**

- **Claude Code / Codex / Gemini** wire C3 *and* C4 to the same capture hook
  (`brain_capture.py`): Claude on both `Stop` and `PreCompact`, Codex on `Stop`,
  Gemini on `SessionEnd`. `gemini-hooks.json` registers only `SessionStart`,
  `BeforeAgent`, and `SessionEnd` — there is **no** registered `PreCompress`
  hook; `PreCompress` is Gemini's adapter-level *detection marker* (in
  `adapters/base.py`) for the compact case, not a hook wiring. They do **not**
  run a separate auto-capture step in the registration files — auto-capture
  (C3) is the role model's inline behavior and is available to these runtimes via
  `brain_autocapture.py` if their binding chooses to wire it.
- **pi** is the role model and is the most granular: it distinguishes C3
  (`agent_end` → inline `maybeAutoCapture`) from C4 (`session_before_compact` and
  `session_shutdown` → `brain_capture.py`), giving distinct pi-specific event
  names on the wire (`PiSessionBeforeCompact`, `PiSessionShutdown`).
- **Hermes** has only two shell hooks: `pre_llm_call` (mapped to C2) and
  `on_session_finalize` (mapped to C4, and serving as the C3 catch-all). Hermes
  has **no dedicated session-start hook** in `hermes-hooks.yaml`; context
  injection is folded into `pre_llm_call` via `hermes_brain_context.py`. A new
  binding for a runtime that likewise lacks a session-start event should follow
  this pattern (inject context on the first pre-turn call).
- **Generic runtimes:** if a runtime fires only one of C3/C4, wire capture on
  whichever it has. If it fires both, wire both — the capture chain's dedup
  prevents duplication.

---

## 2. Hook stdin→stdout JSON protocol

The hooks (`brain_session_start.py`, `brain_prompt_check.py`,
`brain_capture.py`) are standalone Python scripts. A binding runs them as a
subprocess, **writes a JSON object to the hook's stdin**, and **reads a JSON
object from its stdout**. The binding then injects the returned
`additionalContext` into the model.

### Hook input (stdin)

The input is a single JSON object. `read_hook_input()` in
[`hooks/brain_common.py`](../hooks/brain_common.py) does `json.load(sys.stdin)`
and returns `{}` on parse error, so a malformed payload degrades to a no-op
rather than crashing. Recognized fields:

| Field | Type | Used by | Meaning |
|-------|------|---------|---------|
| `cwd` | string | all | the session working directory — drives vault resolution and `.fritz-local.json` lookup. **Always pass this.** |
| `hook_event_name` | string | all | canonical-ish event name; echoed back in `hookSpecificOutput.hookEventName` |
| `transcript_path` | string | `brain_capture.py` | path to the runtime's transcript/session file to parse |
| `user_prompt` | string | `brain_prompt_check.py` | the user's prompt text (preferred field) |
| `message` | string \| `{content}` | `brain_prompt_check.py` | fallback prompt source when `user_prompt` is absent |
| `event_type` | string | adapter detect | Hermes-style event marker (drives agent detection) |
| `permission_mode` | string | adapter detect | Codex marker (Codex sets this and omits `hook_event_name`) |

Example stdin for **session start** (C1):

```json
{ "cwd": "/Users/me/code/acme", "hook_event_name": "SessionStart" }
```

Example stdin for **before-turn / BRAIN CHECK** (C2):

```json
{
  "cwd": "/Users/me/code/acme",
  "hook_event_name": "UserPromptSubmit",
  "user_prompt": "how did we decide to do auth?",
  "message": { "content": "how did we decide to do auth?" }
}
```

Example stdin for **session end / compact** (C4):

```json
{
  "cwd": "/Users/me/code/acme",
  "hook_event_name": "PreCompact",
  "transcript_path": "/Users/me/.claude/projects/acme/session-abc.jsonl"
}
```

### Hook output (stdout)

`brain_session_start.py` and `brain_prompt_check.py` print exactly one JSON
object to stdout. The shape is the Claude Code hook convention:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "# Brain System Active\n\nKnowledge base at `~/.brain/` ..."
  }
}
```

The binding extracts the context with this precedence (matching the role model's
`runHook`):

```
output.hookSpecificOutput.additionalContext  ||  output.additionalContext  ||  ""
```

and injects that string into the model as a **hidden** (not user-displayed)
context/system message. The role model delivers it as
`pi.sendMessage({ customType: "...", content, display: false }, { deliverAs: "nextTurn" })`.
A runtime without a "deliver next turn" primitive should prepend the context to
the first user turn or inject it as a system message.

`brain_capture.py` (C4) writes to disk and exits 0; it produces **no stdout
JSON** for the binding to inject — the binding fires it and ignores stdout.

### Fail-soft requirement

Hook execution must never break a turn. If a hook is missing, is a broken
symlink, times out, exits non-zero, or prints non-JSON, the binding must treat
the result as the empty string and continue (warn once, not every turn). This is
capability 6 (graceful degradation) — capture capabilities that do not depend on
the hooks (explicit save, auto-capture) must keep working in minimal mode.

---

## 3. Adapter interface (transcript source)

The session-capture hook (`brain_capture.py`, C4) needs to read each runtime's
transcript in its native format. That is the job of a **`TranscriptAdapter`** —
see [`adapters/base.py`](../adapters/base.py).

```python
@dataclass
class CaptureEntry:
    topics: list[str] = field(default_factory=list)        # user prompts / subjects
    key_responses: list[str] = field(default_factory=list) # notable assistant lines
    tools_used: set[str] = field(default_factory=set)      # tool names invoked
    cwd: str = ""                                          # set from hook_input["cwd"]
    agent: str = "unknown"                                 # the adapter's agent_name

    def is_empty(self) -> bool:
        return not self.topics and not self.key_responses


class TranscriptAdapter:
    agent_name: str = "unknown"

    def parse(self, transcript_path: Path, max_messages: int = 200) -> CaptureEntry:
        """Read the runtime's transcript and return a normalized CaptureEntry."""
        raise NotImplementedError

    @staticmethod
    def detect(hook_input: dict) -> str:
        """Return the agent identifier string from hook input markers."""
```

To add a new runtime to the capture path:

1. **Implement `parse()`** — read the runtime's transcript file (JSONL, tree,
   YAML, …) and return a `CaptureEntry`. Set `agent = self.agent_name`. Existing
   examples: `adapters/claude_code.py` (JSONL with nested
   `message.content` blocks, extracting `tool_use` names), `adapters/pi_agent.py`,
   `adapters/hermes.py`.
2. **Extend `detect()`** so `hook_input` is classified to your `agent_name`.
   Detection today keys off: `hook_event_name == "PreCompress"` → gemini;
   presence of `event_type` → hermes; `permission_mode` set with no
   `hook_event_name` → codex; `GEMINI_SESSION_ID` / `CODEX_SESSION_ID` env vars;
   and `.pi/agent/sessions` substring in `cwd`/`transcript_path` → pi. Unknown
   input returns `"unknown"`, and the caller (`brain_capture.py`) falls back to a
   minimal `CaptureEntry(agent="unknown")` so the hook never crashes.
3. **Register it** in `adapters/registry.py` (`ADAPTERS["<agent>"] = …`).

A binding for a brand-new runtime is still conformant *without* a transcript
adapter — C4 will write a minimal `agent="unknown"` daily entry. But shipping an
adapter is strongly recommended so captured sessions carry real topics/tools.

---

## 4. Memory / capture sources

All durable state is under `~/.brain` (`BRAIN_HOME`; overridable via the
`BRAIN_HOME` env var, which tests use so the live brain is never touched). The
capture layout a binding writes to:

| Path | Written by | Format |
|------|-----------|--------|
| `~/.brain/capture/inbox/<YYYY-MM-DD>-<slug>.md` | C5 explicit save, C3 auto-capture | YAML-frontmatter capture file, mode `0o600` |
| `~/.brain/capture/auto/<hash>.seen` | C3 auto-capture dedup | marker file (`sha256(text)[:16]`) |
| `~/.brain/capture/daily/<YYYY-MM-DD>.md` | C4 session capture | daily rollup of `CaptureEntry`s |
| `~/.brain/log.md` | C3/C4/C5 | append-only operations log (`INGEST` / `CAPTURE` lines) |

**Explicit save (C5)** — `brain_save_fact.py` `save_fact()` / the role model's
`writeBrainInboxFact`. Parameters: `title`, `body`, optional `source`,
`sensitive`, `tags` (and `agent`). The two implementations are
**byte-identical** by design (the Python port mirrors `JSON.stringify` quoting,
the UTC `today_str`, the local-time `timestamp`, and the `slugify` rule). Dirs
are created `0o700`, files `0o600`.

**Auto-capture (C3)** — `brain_autocapture.py` `maybe_auto_capture(text, cwd)` /
the role model's `maybeAutoCapture`. A capture is written **only when both** a
durable-signal regex (git URLs, forgejo/gitea/gitlab, PAT/api-token/access token,
"server is", "token location", credential, recovery code) **and** a save-intent
regex (remember/save/ingest/brain/future session/…) match the last ~20k chars
of the transcript. It dedups on `sha256(text)[:16]` via a `.seen` marker, and
marks the fact `sensitive` when token/credential/secret/password/pat/api-key
appears. Auto-capture must be conservative (both signals) and idempotent (dedup).

---

## 5. Config model

There is **one** resolution path for project-overridable settings —
`get_setting()` in `hooks/brain_common.py`. A binding must **not** invent its own
config store. Full detail in [`docs/configuration.md`](configuration.md); the
binding-relevant summary:

**Precedence:** `project (.fritz-local.json) > central (registry.yaml settings:) > default`.

- **Project** — `.fritz-local.json`, walked up from `cwd`. Honored only when
  `cwd` is in a trusted location (`resolve_project_vault()`).
- **Central** — the `settings:` block in `~/.brain/registry.yaml`.
- **Default** — the built-in default.

Key settings: `context_injection` (`off`|`light`|`full`, default `off`),
`max_injection_chars` (default `8000`), `update_check` (default `true`). The
`local_brain_service` block is **machine-level plumbing, not project-overridable**
(a trust boundary) and is resolved by its own helper.

**What a binding must do:** always pass the session `cwd` into every hook call so
the `.fritz-local.json` per-project override is applied by the shared hook layer.
A binding that re-implements logic in another language (as pi does) must still
delegate setting resolution to the Python hooks rather than re-implementing
precedence — the hooks are the single source of truth, which guarantees identical
behavior on every platform by construction.

---

## 6. Skill-naming rule

The repo's [`skills/`](../skills) directory is the **single source of truth** and
uses **plain** names (e.g. `brain-query`, `handover`, `update`). Each runtime
accepts a different name shape, so the generator
[`hooks/setup_hyphenated_skills.py`](../hooks/setup_hyphenated_skills.py) emits a
per-platform variant by **prefixing** the plain base name:

| Platform | Prefix | Example |
|----------|--------|---------|
| claude | `fritz:` | `brain-query` → `fritz:brain-query` |
| codex | `fritz:` | `brain-query` → `fritz:brain-query` |
| pi | `fritz-` | `brain-query` → `fritz-brain-query` |

claude and codex share the colon namespace; pi uses the hyphen form because its
runtime rejects colons. Each generated `SKILL.md` rewrites **three** things
consistently: (a) the directory name, (b) the `name:` frontmatter field, and
(c) intra-skill slash references (`/<plain>` → `/<prefix><plain>`). A consistency
validator (`validate_variant` / `validate_variants`) checks a generated tree and
fails on stale wrong-platform references.

**For a new binding:** add the runtime's prefix/shape to `PLATFORM_PREFIXES`
(and the installer's `AGENT_PLATFORM` / `AGENT_SKILLS_DIR` maps), then install
the generated variants into the runtime's skills location. The canonical source
is always the repo `skills/`; each binding owns only the name mapping.

---

## 7. Capability checklist

A binding is **Fritz-complete (conformant)** when it satisfies all nine
capabilities from [`docs/capability-spec.md`](capability-spec.md). Use this as a
checkable acceptance list:

1. [ ] **Context injection** at session start (C1 → `brain_session_start.py`, inject `additionalContext`).
2. [ ] **Guardrail** before each turn — the BRAIN CHECK (C2 → `brain_prompt_check.py`, hidden per-turn guardrail).
3. [ ] **Save** — explicit `brain_save_fact` tool → `~/.brain/capture/inbox/` (C5).
4. [ ] **Auto-capture** of durable knowledge — signal + intent, `.seen` dedup (C3).
5. [ ] **Capture** on session end/compact → `~/.brain/capture/daily/` (C4 → `brain_capture.py`).
6. [ ] **Mode detection** (full vs minimal) with graceful degradation — fail-soft hooks.
7. [ ] **Bootstrap / health** — install / repair / status / smoke-test surface (see `scripts/install.py`).
8. [ ] **Skills** installed with runtime-correct names (the generator + the [naming rule](#6-skill-naming-rule)).
9. [ ] **Config** — centralized `registry.yaml` + per-project `.fritz-local.json` via `cwd`.

Each keyword above (context injection, guardrail, save, auto-capture, capture,
mode detection, bootstrap, skills, config) maps to one capability. See the named
sections of `docs/capability-spec.md` for the role-model behavior and the
generic-runtime mapping of each.

---

## Building a conformant binding

The starting kit lives in [`bindings/_template/`](../bindings/_template). To
build a binding for a new runtime, hand
[`bindings/_template/INITIAL_PROMPT.md`](../bindings/_template/INITIAL_PROMPT.md)
to an agent loop: it references this contract, the capability checklist, the
installer `scripts/install.py`, and the skill generator, and walks the agent
through producing and verifying a conformant binding. Runtime-specific research
notes (native hook mechanism, event mapping, open unknowns) live under
[`docs/bindings/`](bindings/).
