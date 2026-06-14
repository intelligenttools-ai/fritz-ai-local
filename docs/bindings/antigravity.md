# Binding note — Antigravity

Runtime-specific research note for building a Fritz binding for **Antigravity**.
This is a *guidance* note, not a verified spec: where a fact could not be
confirmed against this runtime's documentation from within this repo, it is
flagged as an **assumption** or under **Open unknowns**. Confirming those is part
of building the binding.

> **Status: UNVERIFIED.** No Antigravity runtime, SDK, or documentation was
> available while writing this note. Everything below the "Mechanism" heading is
> a working assumption derived from the role model (`bindings/pi/index.ts`) and
> the four first-class runtimes. Treat it as a starting hypothesis to validate,
> not as fact.

## (a) Native extension / hook mechanism

**Assumption.** Antigravity is presumed to be an IDE-integrated / agentic coding
environment. Such runtimes typically expose **one** of:

- an **extension/plugin API** with lifecycle callbacks and tool registration
  (closest to pi — model on `bindings/pi/index.ts`); or
- a **YAML/TOML hook config** loaded at startup, similar to Hermes
  (`hooks/hermes-hooks.yaml`) — opt-in shell hooks keyed by lifecycle phase.

Given the IDE framing, an **extension API** is the more likely shape, so the
role-model pi binding is the closest reference. The binding would register a save
tool, a status/install command, and lifecycle handlers that shell out to the
Python hooks.

**Open unknown:** whether Antigravity exposes programmatic lifecycle events at
all, or only file-watcher / command-palette extension points. If it has no
session/turn lifecycle events, only a reduced binding is possible (see risks).

## (b) Canonical-event mapping

Assumed mapping (to be confirmed against Antigravity's real event names). The
left column is the Fritz canonical event from
[`docs/integration-contract.md` §1](../integration-contract.md#1-canonical-events).

| Canonical event | Assumed Antigravity event | Hook to run |
|-----------------|---------------------------|-------------|
| C1 session start | session/workspace-open callback (assumed) | `brain_session_start.py` |
| C2 before-turn / BRAIN CHECK | pre-prompt / pre-LLM callback (assumed; may be folded into C1 like Hermes) | `brain_prompt_check.py` |
| C3 turn / agent end | turn-complete callback (assumed) | auto-capture (`brain_autocapture.py`) |
| C4 session end / compact | session-close / finalize callback (assumed) | `brain_capture.py` |
| C5 explicit save | native tool registration (assumed) | `brain_save_fact` |

**Assumption:** if Antigravity, like Hermes, lacks a dedicated session-start
event, fold C1 context injection into the first C2 pre-turn call (the
`hermes_brain_context.py` pattern). A transcript adapter
(`adapters/antigravity.py`) will be required for C4 if Antigravity stores
transcripts in a non-JSONL format.

## (c) Open unknowns / risks

- **Lifecycle event availability (HIGH):** unknown whether Antigravity exposes
  session-start, pre-turn, turn-end, and session-end events. This is the single
  biggest risk — without pre-turn and session-end events, capabilities 2/4/5 are
  degraded. Confirm the event catalog before committing to a design.
- **Subprocess capability (HIGH):** the binding must be able to spawn
  `python3` to run the hooks. If Antigravity sandboxes extensions without
  subprocess access, the Python ports (`brain_save_fact.py`,
  `brain_autocapture.py`) must be re-implemented in the extension's language, kept
  byte-identical to the role model.
- **Context injection primitive (MEDIUM):** whether a hidden (non-displayed)
  context message can be delivered to the model. If not, prepend to the first
  user turn.
- **Transcript format (MEDIUM):** unknown; likely needs a new adapter for C4.
- **Skill naming (LOW):** unknown name shape; add the correct prefix to
  `PLATFORM_PREFIXES` once confirmed. **Assumption:** hyphen (`fritz-`), matching
  pi, since many IDE extension hosts reject colons in identifiers.
- **Config store (MEDIUM):** confirm Antigravity does not force its own settings
  store; the binding must thread `cwd` into the hooks and reuse the shared
  `registry.yaml` + `.fritz-local.json` model rather than inventing config.
