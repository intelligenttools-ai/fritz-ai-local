---
name: brain-save
description: >
  Save durable operational knowledge — server URLs, credential/token locations,
  access procedures, project decisions, runbook-worthy fixes — into Fritz-Brain
  capture/inbox for future sessions. Use whenever the user provides or confirms
  durable, future-session-relevant facts, or runs /brain-save.
---

# Brain Save Fact

Save durable facts into Fritz-Brain `capture/inbox` for future sessions. This is
the shared-Python equivalent of the Pi extension's `brain_save_fact` tool, so the
behavior is identical across every platform (Pi, Claude Code, Codex, Gemini,
Hermes, ...).

## When to use

Use brain-save whenever the user provides or confirms durable operational
knowledge that future sessions should know:

- Server URLs, hostnames, and access endpoints
- Credential / token *locations* (and, when explicitly intended, values)
- Access procedures and authentication steps
- Project decisions worth remembering
- Runbook-worthy fixes

Guidelines (mirrors the role-model tool):

- Use brain-save **proactively**; do not wait for the user to say "ingest" when
  the information is clearly durable and future-session relevant.
- When saving secrets or tokens, set `sensitive=true` and avoid echoing secret
  values back to the user unless explicitly requested.

## Parameters

| Field       | Required | Description                                                        |
| ----------- | -------- | ------------------------------------------------------------------ |
| `title`     | yes      | Short descriptive title for the fact.                              |
| `body`      | yes      | Markdown body with the durable fact, paths, commands, URLs, caveats. |
| `source`    | no       | Where this came from (path, URL, or session note). Defaults to `pi-session`. |
| `sensitive` | no       | `true` if the fact contains secrets, tokens, credentials, or recovery codes. |
| `tags`      | no       | Search tags **without** leading `#`.                               |

## How to invoke

### Programmatically (importable function)

```python
from brain_save_fact import save_fact

path = save_fact(
    title="Forgejo server URL",
    body="The Forgejo server is at https://git.intelligenttools.ai",
    source="session-note",
    sensitive=False,
    tags=["FritzBrain", "Infra"],
)
```

### CLI — JSON on stdin

```bash
echo '{"title":"Forgejo token location","body":"PAT lives in ~/.config/secrets/forgejo-intelligenttools","sensitive":true,"tags":["FritzBrain","Access"]}' \
  | python hooks/brain_save_fact.py --json
```

### CLI — flags

```bash
python hooks/brain_save_fact.py \
  --title "Deploy runbook" \
  --body "Run ./deploy.sh from repo root; needs DOCKER_HOST set." \
  --tags "FritzBrain,Runbook"
```

## What it writes

A single Markdown file under `capture/inbox/`:

- Filename: `YYYY-MM-DD-<slug-of-title>.md`
- Directories created `0700`, the file written `0600`.
- Frontmatter (exact field order):

```yaml
---
type: capture
title: "<title>"
domain: work
sources:
  - "<source>"      # or: pi-session when no source is given
created: YYYY-MM-DD
agent_last_edit: pi
sensitive: false    # or true
---
# <title>

<body, trimmed>

Tags: #tag1 #tag2   # omitted entirely when no tags are given
```

It also appends an audit line to `~/.brain/log.md`:

```
YYYY-MM-DD HH:MM | INGEST | pi-extension | Auto-saved "<title>" to <file>
```

## Brain root

The brain root is resolved from the `BRAIN_HOME` environment variable when set,
otherwise `~/.brain`. The importable `save_fact` also accepts an explicit
`root=` argument (used by tests so the live `~/.brain` is never touched).

## Related

- `hooks/brain_autocapture.py` — automatic capture of durable session knowledge
  when the transcript shows both a durable signal and an explicit save intent.
