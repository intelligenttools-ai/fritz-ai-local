# Binding note — OpenClaw

Runtime-specific research note for building a Fritz binding for **OpenClaw**.
This is a *guidance* note, not a verified spec: where a fact could not be
confirmed against this runtime's documentation from within this repo, it is
flagged as an **assumption** or under **Open unknowns**. Confirming those is part
of building the binding.

> **Status: UNVERIFIED.** No OpenClaw runtime, SDK, or documentation was
> available while writing this note. Everything below the "Mechanism" heading is
> a working assumption derived from the role model (`bindings/pi/index.ts`) and
> the patterns of the four first-class runtimes. Treat it as a starting
> hypothesis to validate, not as fact.

## (a) Native extension / hook mechanism

**Assumption.** OpenClaw is presumed to be a Claude-Code-lineage / "claw"-family
coding agent that exposes either:

- a **settings-file hook registry** (like Claude Code's
  `~/.claude/settings.json` `hooks` block — see `hooks/claude-code-hooks.json`),
  registering shell/command hooks per lifecycle event; and/or
- a **native extension API** with `registerTool` / `registerCommand` /
  lifecycle event handlers (like pi — see `bindings/pi/index.ts`).

**If settings-driven** (most likely given the "claw" lineage), the binding is a
registration file modeled on `hooks/claude-code-hooks.json` plus reuse of the
existing Python hooks unchanged — OpenClaw shells out to
`python3 ~/.brain/hooks/<hook>.py`. **If extension-driven**, model the binding on
`bindings/pi/index.ts`.

**Open unknown:** which of the two it is, the exact config file path, and whether
hooks receive the hook-input JSON on stdin (the protocol the Python hooks
require). Verify the stdin contract first — it is load-bearing.

## (b) Canonical-event mapping

Assumed mapping (to be confirmed against OpenClaw's real event names). The
left column is the Fritz canonical event from
[`docs/integration-contract.md` §1](../integration-contract.md#1-canonical-events).

| Canonical event | Assumed OpenClaw event | Hook to run |
|-----------------|------------------------|-------------|
| C1 session start | `SessionStart` (assumed; Claude-Code-style) | `brain_session_start.py` |
| C2 before-turn / BRAIN CHECK | `UserPromptSubmit` (assumed) | `brain_prompt_check.py` |
| C3 turn / agent end | `Stop` (assumed) | auto-capture (`brain_autocapture.py`) |
| C4 session end / compact | `Stop` + `PreCompact` (assumed) | `brain_capture.py` |
| C5 explicit save | native tool registration (assumed) | `brain_save_fact` |

If OpenClaw mirrors Claude Code exactly, `hooks/claude-code-hooks.json` can be
reused almost verbatim (re-pointed at OpenClaw's settings file). A transcript
adapter (`adapters/openclaw.py`) will be needed if OpenClaw's transcript format
differs from Claude Code's JSONL — extend `TranscriptAdapter.detect()` to
recognize it.

## (c) Open unknowns / risks

- **Config surface (HIGH):** unknown config file path and schema for hook
  registration. Blocks the whole binding until confirmed.
- **Stdin JSON contract (HIGH):** the Python hooks read a JSON hook-input on
  stdin and emit `hookSpecificOutput.additionalContext`. If OpenClaw passes event
  data via env vars or argv instead of stdin, a small shim per hook is required
  (as Hermes does with `hermes_brain_context.py`).
- **Context injection primitive (MEDIUM):** whether OpenClaw can inject a hidden
  (non-displayed) context message into the next turn. If not, fall back to
  prepending context to the first user turn.
- **Transcript format (MEDIUM):** unknown; a new adapter may be required for C4.
- **Skill naming (LOW):** unknown whether OpenClaw accepts colon (`fritz:`) or
  hyphen (`fritz-`) names. Add the correct prefix to `PLATFORM_PREFIXES` once
  confirmed. **Assumption:** colon, if Claude-Code-lineage.
- **Tool registration (MEDIUM):** unknown API for registering the model-callable
  `brain_save_fact` tool; if OpenClaw lacks tool registration, expose save via a
  slash command instead.
