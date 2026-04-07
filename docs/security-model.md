# Fritz Local Security Model

Zero-trust layered access following Medin's principle: start with read-only, add capabilities incrementally.

## Tiers

### Tier 0 — Read (default)

Any agent can read any non-excluded file in any vault.

### Tier 1 — Capture

Any agent can:
- Write to `~/.brain/capture/daily/` and `~/.brain/capture/sessions/`
- Append to `~/.brain/log.md`
- Append to vault `.brain/log.md`

### Tier 2 — Knowledge

Trusted agents (compile, ingest operations) can:
- Create and update files in the vault's `knowledge` path
- Update the vault's index file
- Write to vault `.brain/capture/inbox/`

### Tier 3 — Structure

Only human or admin agent can:
- Modify `.brain/manifest.yaml`
- Modify `.brain/schema.md`
- Modify identity files (soul, user, memory)
- Create or delete vaults in the registry
- Modify `~/.brain/registry.yaml`

## Exclusions

Each vault manifest has an `exclude` list. Files matching these patterns are invisible to the brain system — never indexed, captured, synced, or referenced.

Common exclusions:
- `.obsidian/`, `.trash/` — platform internals
- Directories containing keys, credentials, secrets
- Binary files, attachments (unless explicitly included)

## Agent Attribution

Every write operation records the acting agent:
- In YAML frontmatter: `agent_last_edit: <agent-name>`
- In `.brain/log.md`: timestamp, operation, agent, summary

## Sync Security

- `sync: none` vaults are never synced externally
- `sync: local` vaults have no external sync
- The `exclude` list is respected during sync — excluded content never leaves the machine
- Brain markdown is always the source of truth — external systems are read-only views

## Enforcement

- `hooks/brain_security.py` — library for tier checking (used by other hooks/skills)
- `hooks/brain_prompt_check.py` — reminds agents to check brain before answering
- Instruction files at vault root — soft enforcement via agent instructions
- Hooks — hard enforcement for agents that support them

Enforcement is best-effort for agents without hook support. The security model is documented in `.brain/schema.md` for every vault, so agents that read their instructions will follow the tiers.
