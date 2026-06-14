# Fritz Capability Spec

This document is the **written contract that every Fritz binding targets**. It
distills the behavior of the role-model binding — the Pi extension incorporated
verbatim (then minimally re-pointed) at [`bindings/pi/index.ts`](../bindings/pi/index.ts)
— into a runtime-agnostic capability bar.

The role model is the reference implementation. When a new binding is written for
another agent runtime (Claude Code, Codex, Gemini, Hermes, etc.), it is
"Fritz-complete" only when it satisfies all nine capabilities below. Each
capability is described twice:

- **Role-model behavior** — exactly what `bindings/pi/index.ts` does, with the
  concrete functions / event hooks that implement it.
- **Generic-runtime mapping** — how a binding for any other runtime should
  express the same capability against whatever lifecycle hooks and tool/skill
  registration that runtime provides.

The capability bar originates in epic #55. The role-model incorporation is
issue #57; the location-independent path resolution it shares with the Python
hooks is issue #56.

A note on storage: all durable artifacts live under `~/.brain` (`BRAIN_HOME`).
The Fritz repo itself is resolved location-independently — `FRITZ_REPO_PATH` env
var first, otherwise from the binding file's own location
(`dirname(dirname(dirname(fileURLToPath(import.meta.url))))`, since
`bindings/pi/index.ts` sits two directory levels below the repo root). Bindings
must never hardcode a clone path.

---

## 1. Context injection at session start

**Role-model behavior.** On the `session_start` event the binding runs the
Python hook `~/.brain/hooks/brain_session_start.py` via `runHook(...)`, passing
`{ cwd, hook_event_name: "SessionStart" }` on stdin. The hook's
`additionalContext` (parsed from `hookSpecificOutput.additionalContext` or
`additionalContext`) is injected into the next turn via
`pi.sendMessage({ customType: "fritz-brain-context", content: brainContext,
display: false }, { deliverAs: "nextTurn" })`. The injected brain context is
hidden from the user (`display: false`) but visible to the model.

**Generic-runtime mapping.** At the runtime's session-start lifecycle hook,
execute `brain_session_start.py` with the session `cwd` and a `SessionStart`
event name, then feed the returned context into the model's first turn as a
non-displayed system/context message. Runtimes that lack a "deliver next turn"
primitive should prepend the context to the first user turn or inject it as a
system message.

## 2. Brain-first guardrail before each turn ("BRAIN CHECK")

**Role-model behavior.** On `before_agent_start` the binding builds a
`brainAutomationPrompt` (the Fritz-Brain automatic memory policy) and also runs
`~/.brain/hooks/brain_prompt_check.py` with the user prompt and a
`UserPromptSubmit` event. The two are concatenated and returned as a hidden
`fritz-brain-prompt-check` message so the model re-checks, before every turn,
whether durable knowledge in the prompt must be **saved, not merely answered**.
This is the BRAIN CHECK guardrail: the policy explicitly instructs the model to
call `brain_save_fact` during the turn when server URLs, token/credential
locations, access procedures, decisions, or runbook-worthy fixes appear, and to
target `~/.brain/capture/inbox` rather than any notes vault.

**Generic-runtime mapping.** At the runtime's pre-turn / user-prompt hook,
assemble the same brain-first policy text plus the output of
`brain_prompt_check.py`, and inject it as a hidden per-turn guardrail message.
The guardrail's intent — "if it's durable, capture it this turn" — must reach
the model on every turn, not just at session start.

## 3. Explicit save — `brain_save_fact` → `~/.brain/capture/inbox/`

**Role-model behavior.** The binding registers a `brain_save_fact` tool
(`pi.registerTool`) with parameters `title`, `body`, optional `source`,
`sensitive`, and `tags`. Its `execute` calls `writeBrainInboxFact(...)`, which
writes a YAML-frontmatter capture file to
`~/.brain/capture/inbox/<YYYY-MM-DD>-<slug>.md` (mode `0o600`, dirs `0o700`) and
appends an `INGEST` line to `~/.brain/log.md`. The tool's `promptGuidelines`
tell the model to save durable knowledge proactively and to set `sensitive=true`
for secrets without echoing them.

**Generic-runtime mapping.** Register an equivalent model-callable tool with the
same five parameters and the same `~/.brain/capture/inbox/` target, private file
modes, and operations-log append. The runtime-correct tool name and
description/guidelines should carry the same proactive-save and
secret-handling guidance.

## 4. Auto-capture of durable knowledge on session/turn end

**Role-model behavior.** On `agent_end` the binding calls
`maybeAutoCapture(event.messages, cwd)`. That function joins the recent
transcript (last ~20k chars), and requires **both** a durable signal
(`hasDurableSignal`: regex for `git.`, forgejo/gitea/gitlab, PAT/api-token/access
token, "server is", "token location", "credential", "recovery code") **and** an
intent signal (`hasInstruction`: regex for remember/save/ingest/brain/future
session/etc.). Only when both match does it auto-save. It dedups via a SHA-256
hash of the transcript: a `.seen` marker file at
`~/.brain/capture/auto/<hash>.seen` is checked first and written after, so the
same content is never auto-captured twice. The captured fact is marked
`sensitive` when token/credential/secret/password/pat/api-key appears.

**Generic-runtime mapping.** At the runtime's turn/agent-end hook, run the same
signal-plus-intent detection over the recent transcript and, on a match, write a
capture file — guarded by the identical `.seen` content-hash dedup under
`~/.brain/capture/auto/`. Auto-capture must be conservative (both signals
required) and idempotent (dedup marker).

## 5. Session capture on end/compact → `~/.brain/capture/daily/`

**Role-model behavior.** On `session_before_compact` and on
`session_shutdown` the binding takes the transcript path from
`ctx.sessionManager.getSessionFile()` and runs `~/.brain/hooks/brain_capture.py`
with the transcript path and the event name (`PiSessionBeforeCompact` /
`PiSessionShutdown`). `brain_capture.py` is the component that distills the
session into the daily capture store. (In the role model the capture hook owns
the daily-rollup destination; the binding's job is to fire it on both the
compact and shutdown lifecycle points so no session is lost.)

**Generic-runtime mapping.** Wire the runtime's "session ending" and "about to
compact/summarize" lifecycle hooks to invoke `brain_capture.py` with the
transcript file and an event name, so each session is rolled up into the daily
capture store. If a runtime fires only one of the two events, fire capture on
whichever it has; if it fires both, fire on both (the dedup inside the hook
chain prevents duplication).

## 6. Mode detection (full vs minimal) with graceful degradation

**Role-model behavior.** `fritzMode()` returns `"full-fritz-local"` when
`fullFritzAvailable()` is true — i.e. `~/.brain/registry.yaml` exists **and**
every entry in `REQUIRED_HOOKS` is an OK (non-broken) path — otherwise
`"minimal-capture"`. Every hook invocation (`runHook`) first checks
`hookAvailable()` and silently returns `""` if the hook is missing or a broken
symlink, with one-time error warnings tracked in `failedHookWarnings`. The
before-turn policy text adapts its wording to the current mode. So when full
Fritz is not initialized, explicit `brain_save_fact` and auto-capture still
work, while hook-based context/query/compile/sync degrade gracefully instead of
erroring.

**Generic-runtime mapping.** Implement the same two-state detection (registry +
required hooks present ⇒ full, else minimal) and the same fail-soft hook
execution: a missing or broken hook must never break a turn. Capture
capabilities that don't depend on the hooks (explicit save, auto-capture) must
remain functional in minimal mode.

## 7. Bootstrap / health (install / repair / status / smoke-test)

**Role-model behavior.** The binding registers a `/fritz` command
(`pi.registerCommand("fritz", ...)`) with sub-commands `status`, `init`,
`repair-hooks`, and `smoke-test` (default `status`). `init` clones
`FRITZ_REMOTE` into `FRITZ_REPO` if missing (behind a confirm unless `--yes`),
then `ensureBrainDirs()`, `installHooks()` (symlinks the `REQUIRED_HOOKS` from
`<repo>/hooks` into `~/.brain/hooks`, preserving existing non-symlinks),
`installTemplates()`, `ensureRegistryTemplate()`, `installPiSkills()`, runs
`smokeTest()`, and reloads. `status` prints `fritzStatusLines()` (mode, repo,
brain home, registry, skills dir, each hook's state, setup skill).
`repair-hooks` re-links hooks only. `smokeTest()` runs
`brain_session_start.py` with a synthetic `SessionStart` payload and reports
PASS/FAIL.

**Generic-runtime mapping.** Expose an install/repair/status/smoke-test surface
(a slash command, CLI subcommand, or equivalent) that: clones/locates the repo,
creates the `~/.brain` directory tree, links the required hooks, seeds templates
and registry, installs skills, and runs a smoke test (execute one hook against a
synthetic event and report PASS/FAIL). Status must report mode and the presence
of each moving part.

## 8. Skills installed with runtime-correct names

**Role-model behavior.** `installPiSkills()` reads `<repo>/skills`, and for every
upstream skill named `fritz:*` it installs a copy into the Pi skills dir
(`~/.agents/skills`) under a **runtime-correct name**: `piSkillName()` rewrites
the `fritz:` prefix to `fritz-` (Pi uses hyphenated skill names), and
`transformFritzSkillForPi()` rewrites `fritz:` references inside the skill body
to `fritz-`. So `fritz:update` is installed as `fritz-update`, etc.

**Generic-runtime mapping.** Translate the canonical `fritz:*` skill names in
`<repo>/skills` into whatever naming convention the target runtime requires
(hyphenated, namespaced, flat, etc.) and install them into that runtime's skills
location, rewriting in-body skill references to match. The canonical source of
truth is the repo's `skills/` directory; each binding owns only the name mapping.

## 9. Centralized config + per-project override

**Role-model behavior.** Configuration is centralized under `~/.brain`: the
binding reads `~/.brain/registry.yaml` (centralized vault/config registry,
seeded by `ensureRegistryTemplate()` from
`<repo>/registry/registry.template.yaml`, falling back to a minimal
`vaults: {}`) and operates relative to `BRAIN_HOME`. Per-project override is
honored by the hook layer it drives: the hooks resolve a `.fritz-local.json`
walked up from `cwd` (see the Python `load_fritz_local`), and the binding always
passes the working directory `cwd` into every hook call, so a project can
override behavior locally without changing global config.

**Generic-runtime mapping.** Read the central `~/.brain/registry.yaml` for
global config and always pass the session `cwd` into the hooks so the
per-project `.fritz-local.json` override is applied. A binding must not invent
its own config store; it threads global (`registry.yaml`) + per-project
(`.fritz-local.json` via `cwd`) through the shared hook layer.

---

## Conformance checklist

A binding is Fritz-complete when it implements all nine:

1. [ ] Context injection at session start
2. [ ] Brain-first guardrail before each turn (BRAIN CHECK)
3. [ ] Explicit save — `brain_save_fact` → `~/.brain/capture/inbox/`
4. [ ] Auto-capture of durable knowledge (signal + intent, `.seen` dedup)
5. [ ] Session capture on end/compact → `~/.brain/capture/daily/`
6. [ ] Mode detection (full vs minimal) with graceful degradation
7. [ ] Bootstrap / health (install / repair / status / smoke-test)
8. [ ] Skills installed with runtime-correct names
9. [ ] Centralized config + per-project override
