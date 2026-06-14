# Operations

Day-to-day running of Fritz Local.

## Updating Fritz Local

The session-start hook checks once per 24 hours for a new version. When
one is available the hook prints a notification on the next session
start.

To apply an update:

```
/fritz:update
```

The skill `git pull`s the repo, symlinks any new `fritz:*` skills into
each installed agent's skill directory, refreshes managed hook symlinks,
runs pending migrations, scans vaults for brain-contract drift, and prints a
report.

If you prefer manual control, run `git pull` in your clone. Fritz resolves its
repo root dynamically (via `FRITZ_REPO_PATH` or each hook/binding file's own
location), so the clone can live **anywhere** — there is no required path.
`~/.fritz-ai-local` is only an optional example; substitute your actual clone
path:

```
git -C <repo> pull        # e.g. git -C ~/.fritz-ai-local pull
```

Symlinked hooks update immediately. Re-run
`python3 scripts/install.py install --agent <claude|codex|pi>` (or
`/fritz:update`) afterwards to pick up new skills and run migrations.

## Brain contract drift

After a `/fritz:update` that bumps the contract version, vaults that have
an older `brain.md` are listed in the update report under *brain contract
drift*. Refresh them one at a time:

```
cd <vault-path>
/fritz:brain-setup
```

Setup detects the older `brain_contract_version`, asks whether to update
in place, and otherwise leaves the vault alone. Root-file references are
not rewritten — they already point at `brain.md`.

If a contract change is unsuitable for a specific agent's ecosystem,
setup offers to create a sibling variant (e.g.
`.brain/instructions/brain-<agent>.md`) and rewrites only the current
agent's root-file reference to point at the variant.

## Adding a new vault

1. Pick a directory. It does not have to be empty — Fritz Local overlays
   what is already there.
2. Add an entry to `~/.brain/registry.yaml`:
   ```yaml
   vaults:
     <name>:
       path: <absolute-or-~-path>
       domain: <label>
       sync: <local|affine|custom>
   ```
3. `cd` into the vault directory and run `/fritz:brain-setup`. Answer
   the questions. Review what gets created.

## Adding a project binding to a source repo

In the project root, create `.fritz-local.json`:

```json
{
  "vault": "engineering",
  "project": "my-service",
  "brain_home": "~/.brain",
  "context_injection": "off"
}
```

Commit it — no secrets. From then on, sessions inside that directory
capture into the named vault and may (depending on `context_injection`)
have knowledge injected at session start.

## Changing context injection

Global default, in `~/.brain/registry.yaml`:

```yaml
settings:
  context_injection: light   # off | light | full
```

Per-project override, in `.fritz-local.json`:

```json
{ "context_injection": "full" }
```

Tier trade-offs: `off` is free but trusts the agent; `light` injects
file paths (small token cost, good signal); `full` instructs the agent
to synthesise (larger token cost, strongest enforcement).

## Sync

Run `/fritz:brain-sync` to push changes to external views (AFFiNE,
Obsidian Git, etc.). The sync skill is target-agnostic: it delegates to
whatever tools the agent has for the configured target. Configure the
target in the vault's registry entry (`sync:` field) and any target-
specific identifiers (e.g. `affine_workspace_id`).

`/fritz:handover` runs sync automatically as its last preservation step
for vaults that have a sync target configured. Vaults set to `sync: local`
or `sync: none` skip the push — their preservation path is the local
capture → compile pipeline that runs earlier in handover.

## Troubleshooting

### The session-start hook does not inject context

- Check that `~/.brain/registry.yaml` exists and parses as YAML.
- Check that the working directory matches a vault's `path`, or that a
  `.fritz-local.json` is present and points at a registered vault.
- Run the hook manually: `python3 ~/.brain/hooks/brain_session_start.py`.
  It prints what it would inject.
- For Hermes Agent profiles, run the wrapper instead and pass the active cwd:
  `python3 ~/.brain/hooks/hermes_brain_context.py <<<'{"cwd":"/path/to/project"}'`.

### Captures are not being written

- Verify the binding is installed for your platform (the Claude/Codex plugin is
  enabled, the pi extension is loaded, or the Hermes YAML block is merged) — see
  the per-platform walkthroughs in [`../SETUP.md`](../SETUP.md).
- Run `python3 scripts/install.py status` to confirm the brain layout and hook
  symlinks are healthy, and `smoke-test` to exercise a hook.
- Check `~/.brain/log.md` for errors from previous runs.
- Confirm the transcript adapter for your agent is implemented
  (`adapters/<agent>.py`). Stubs return empty captures.
- For Hermes Agent profiles, ensure `HERMES_HOME` points at the profile whose
  `sessions/` directory contains the active JSONL transcript.

### A skill is not found

- The per-platform skill variants are generated from the canonical `skills/`
  directory and installed by `scripts/install.py install --agent <agent>` (and,
  for the directory-source plugins, committed under the binding). Claude/Codex
  keep `fritz:*`; pi uses `fritz-*`.
- `ls ~/.claude/skills/` (or `~/.codex/skills/`, `~/.agents/skills/` for pi) —
  the `fritz:*` / `fritz-*` skill directories should be present.
- Re-run `python3 scripts/install.py install --agent <agent>` or `/fritz:update`
  to (re)install them. Hermes has no skills mechanism, so no skills are expected
  there.

### `/fritz:update` says "not a git repo"

The repo was installed by copy rather than clone. Re-install with a clone (it can
live anywhere — `FRITZ_REPO_PATH` resolves it), or accept that updates must be
applied manually.

### Brain contract drift keeps reappearing

The vault's `brain.md` was left at an old version because setup was
declined. Either re-run `/fritz:brain-setup` and accept the update, or
delete `brain.md` and re-run setup to regenerate from scratch.

## Logs

- `~/.brain/log.md` — global operations log. All skills append here.
- `<vault-path>/.brain/log.md` — per-vault log, used by
  `/fritz:brain-sync` to determine the last sync time.
