# Fritz Local — Agent Setup Instructions

You are setting up Fritz Local, an agent-agnostic brain overlay system. Follow these steps exactly.

## Prerequisites

- Python 3.10+ with `pyyaml` installed
- Git
- The fritz-ai-local repository cloned locally

## Step 1: Run the installer

```bash
cd <path-to-fritz-ai-local>
./install.sh
```

This creates `~/.brain/` with hooks, capture directories, and a registry template.

## Step 2: Configure the vault registry

Edit `~/.brain/registry.yaml`:
- Set vault paths to match this machine's directory structure
- Set `default_vault` to the primary vault for captures
- Mark the primary vault with `status: active`

## Step 3: Deploy overlays to vaults

For each vault that has an overlay template in `overlays/`:
- Run `./install.sh` (it deploys automatically)
- Or manually copy `overlays/<name>/` files to `<vault>/.brain/`

For vaults without a template, create `.brain/manifest.yaml` mapping brain concepts to the vault's existing folder structure. Use `overlays/vanillacore/manifest.yaml` as a reference.

## Step 4: Register hooks for your agent

### Claude Code

Add the contents of `hooks/claude-code-hooks.json` to `~/.claude/settings.json` under the `hooks` key. The four events are:
- `SessionStart` → `brain_session_start.py`
- `UserPromptSubmit` → `brain_prompt_check.py`
- `PreCompact` → `brain_capture.py`
- `Stop` → `brain_capture.py`

Then add to `~/.claude/CLAUDE.md` (create if it doesn't exist):

```markdown
## Brain System

Fritz Local brain overlay is active. Before answering questions about prior decisions, patterns, or domain knowledge:
1. Check `~/.brain/capture/daily/` for recent session captures
2. Search vault knowledge directories (paths in `~/.brain/registry.yaml`)
3. Use `/brain-query` to search across all vaults

Available brain skills: `/brain-query`, `/brain-compile`, `/brain-ingest`, `/brain-lint`
```

### OpenAI Codex CLI

Add to `~/.codex/config.toml`:

```toml
[hooks.SessionStart]
command = "python3 ~/.brain/hooks/brain_session_start.py"
timeout_ms = 5000

[hooks.Stop]
command = "python3 ~/.brain/hooks/brain_capture.py"
timeout_ms = 10000
```

Add to your project's `AGENTS.md` or create `~/.codex/AGENTS.md`:

```markdown
## Brain System

Fritz Local brain overlay is active. Before answering questions about prior decisions, patterns, or domain knowledge, search `~/.brain/capture/daily/` and vault knowledge directories listed in `~/.brain/registry.yaml`.
```

### Google Gemini CLI

Add to `~/.gemini/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": "python3 ~/.brain/hooks/brain_session_start.py"}]}],
    "PreCompress": [{"hooks": [{"type": "command", "command": "python3 ~/.brain/hooks/brain_capture.py"}]}],
    "SessionEnd": [{"hooks": [{"type": "command", "command": "python3 ~/.brain/hooks/brain_capture.py"}]}],
    "BeforeAgent": [{"hooks": [{"type": "command", "command": "python3 ~/.brain/hooks/brain_prompt_check.py"}]}]
  }
}
```

Add to `~/.gemini/GEMINI.md`:

```markdown
## Brain System

Fritz Local brain overlay is active. Before answering questions about prior decisions, patterns, or domain knowledge, search `~/.brain/capture/daily/` and vault knowledge directories listed in `~/.brain/registry.yaml`.
```

### Hermes Agent

Create `~/.hermes/hooks/brain-start/HOOK.yaml`:

```yaml
name: brain-start
description: Load brain context on session start
events: [session:start]
```

Create `~/.hermes/hooks/brain-start/handler.py`:

```python
import subprocess
from pathlib import Path
async def handle(event_type, context):
    subprocess.run(["python3", str(Path.home() / ".brain/hooks/brain_session_start.py")])
```

Create `~/.hermes/hooks/brain-capture/HOOK.yaml`:

```yaml
name: brain-capture
description: Capture session on end
events: [session:end]
```

Create `~/.hermes/hooks/brain-capture/handler.py`:

```python
import subprocess
from pathlib import Path
async def handle(event_type, context):
    subprocess.run(["python3", str(Path.home() / ".brain/hooks/brain_capture.py")])
```

Add to `~/.hermes/HERMES.md`:

```markdown
## Brain System

Fritz Local brain overlay is active. Before answering questions about prior decisions, patterns, or domain knowledge, search `~/.brain/capture/daily/` and vault knowledge directories listed in `~/.brain/registry.yaml`.
```

## Step 5: Verify

Start a new session with your agent. You should see brain system context injected (available vaults, recent captures). Ask a question — you should see a "BRAIN CHECK" reminder in the agent's context.

## Quick Start (paste this into any agent)

```
Clone and install Fritz Local:
git clone https://github.com/intelligenttools-ai/fritz-ai-local.git ~/fritz-ai-local
cd ~/fritz-ai-local && ./install.sh
Then read SETUP.md and complete steps 2-4 for this agent.
```
