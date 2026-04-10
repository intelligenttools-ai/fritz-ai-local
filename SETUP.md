# Fritz Local — Agent Setup Instructions

You are an AI agent setting up Fritz Local, an agent-agnostic brain overlay system. Follow these steps exactly. All steps are performed by YOU (the agent), not the human.

## Paths

This repo should be cloned to a canonical location:

| OS | Repo path | Brain home |
|----|-----------|------------|
| macOS/Linux | `~/.fritz-ai-local/` | `~/.brain/` |
| Windows | `%USERPROFILE%\.fritz-ai-local\` | `%USERPROFILE%\.brain\` |

Throughout this document, `<repo>` means the repo path and `~` means the user's home directory. On Windows, use `%USERPROFILE%` for `~`, use `mklink` instead of `ln -sf`, and use `python` instead of `python3`.

## Prerequisites

Ensure Python 3.10+ is available. Install `pyyaml` if not present:
```
pip install pyyaml
```

## Step 1: Create the brain directory

Create `~/.brain/` with these subdirectories:
```
~/.brain/
├── capture/
│   ├── daily/
│   └── sessions/
├── hooks/
├── tools/
├── templates/
└── log.md          (create with header: "# Brain Operations Log")
```

## Step 2: Symlink hooks and skills

Symlink each `.py` file from `<repo>/hooks/` to `~/.brain/hooks/`:
```
~/.brain/hooks/brain_capture.py        → <repo>/hooks/brain_capture.py
~/.brain/hooks/brain_session_start.py  → <repo>/hooks/brain_session_start.py
~/.brain/hooks/brain_prompt_check.py   → <repo>/hooks/brain_prompt_check.py
~/.brain/hooks/brain_common.py         → <repo>/hooks/brain_common.py
~/.brain/hooks/brain_security.py       → <repo>/hooks/brain_security.py
```

On Windows, use `mklink` (requires admin) or copy the files instead.

Symlink each `fritz:*` skill directory from `<repo>/skills/` to your agent's skills directory:
- Claude Code: `~/.claude/skills/`
- Codex CLI: `~/.codex/skills/`
- Gemini CLI: `~/.gemini/skills/`
- Other agents: wherever skills are registered

All skills are prefixed with `fritz:` to avoid conflicts with other skill providers.

Copy `<repo>/templates/schema.template.md` to `~/.brain/templates/`.

## Step 3: Register hooks in your agent config

You know your own config format. Register these hooks:

| Event | Script | Purpose |
|-------|--------|---------|
| Session start | `python3 ~/.brain/hooks/brain_session_start.py` | Inject brain context |
| User prompt / before agent | `python3 ~/.brain/hooks/brain_prompt_check.py` | Brain-first enforcement |
| Pre-compact / pre-compress | `python3 ~/.brain/hooks/brain_capture.py` | Capture before context loss |
| Session end / stop | `python3 ~/.brain/hooks/brain_capture.py` | Capture on exit |

Reference hook configurations for each agent are in `<repo>/hooks/`:
- Claude Code: `claude-code-hooks.json` → merge into `~/.claude/settings.json` under `hooks`
- Codex CLI: `codex-hooks.toml` → append to `~/.codex/config.toml`
- Gemini CLI: `gemini-hooks.json` → merge into `~/.gemini/settings.json` under `hooks`

Use absolute paths (resolve `~` to the actual home directory). For other agents, use your native hook registration mechanism.

## Step 4: Add brain instructions to your global instruction file

Add this to your global instruction file (CLAUDE.md, AGENTS.md, GEMINI.md, HERMES.md — whichever you read):

```markdown
## Brain System

Fritz Local brain overlay is active. Before answering questions about prior decisions, patterns, or domain knowledge:
1. Check `~/.brain/capture/daily/` for recent session captures
2. Search vault knowledge directories (paths in `~/.brain/registry.yaml`)
3. Use `/fritz:brain-query` to search across all vaults

Available fritz skills: `/fritz:brain-query`, `/fritz:brain-compile`, `/fritz:brain-ingest`, `/fritz:brain-lint`, `/fritz:brain-sync`, `/fritz:brain-setup`, `/fritz:handover`
```

## Step 5: Create the vault registry

Copy `<repo>/registry/registry.template.yaml` to `~/.brain/registry.yaml`. Then ask the human which directories are their knowledge vaults and update the registry with the correct paths, domains, and sync settings.

## Step 6: Set up each vault

For each vault in the registry, run `/fritz:brain-setup` (or follow the fritz:brain-setup skill instructions). This explores the vault's directory structure and generates the manifest, schema, instruction files, and index.

## Step 7: Write your own transcript adapter (if needed)

Check `<repo>/adapters/` for your agent type. If it's a stub, implement it — you know your own transcript format better than anyone. The interface is in `<repo>/adapters/base.py`:

```python
class TranscriptAdapter:
    def parse(self, transcript_path: Path, max_messages: int = 200) -> CaptureEntry:
        ...
```

Register it in `<repo>/adapters/registry.py`. Consider opening a PR to contribute it back.

## Step 8: Verify

1. Start a new session — you should see brain context injected
2. Ask a question — you should see a "BRAIN CHECK" reminder
3. End the session — a capture should appear in `~/.brain/capture/daily/`
