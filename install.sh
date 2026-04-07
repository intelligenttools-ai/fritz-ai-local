#!/usr/bin/env bash
set -euo pipefail

# Brain System Installer
# Creates ~/.brain/ directory and symlinks tools + skills from this repo.

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
BRAIN_HOME="${HOME}/.brain"

echo "Installing brain system from ${REPO_DIR}"
echo "Target: ${BRAIN_HOME}"
echo ""

# 1. Create ~/.brain/ structure
mkdir -p "${BRAIN_HOME}"/{hooks,tools,registry}
echo "Created ${BRAIN_HOME}/"

# 2. Symlink tools
if [ -d "${REPO_DIR}/tools" ]; then
    for tool in "${REPO_DIR}"/tools/*.py; do
        [ -f "$tool" ] || continue
        name="$(basename "$tool")"
        ln -sf "$tool" "${BRAIN_HOME}/tools/${name}"
        echo "Linked tool: ${name}"
    done
fi

# 3. Symlink hooks
if [ -d "${REPO_DIR}/hooks" ]; then
    for hook in "${REPO_DIR}"/hooks/*.py; do
        [ -f "$hook" ] || continue
        name="$(basename "$hook")"
        ln -sf "$hook" "${BRAIN_HOME}/hooks/${name}"
        echo "Linked hook: ${name}"
    done
fi

# 4. Install skill into Claude Code skills directory
CLAUDE_SKILLS="${HOME}/.claude/skills"
if [ -d "${CLAUDE_SKILLS}" ]; then
    for skill_dir in "${REPO_DIR}"/skills/*/; do
        [ -d "$skill_dir" ] || continue
        name="$(basename "$skill_dir")"
        # Remove stale symlink or directory, then create fresh symlink
        if [ -L "${CLAUDE_SKILLS}/${name}" ]; then
            rm "${CLAUDE_SKILLS}/${name}"
        fi
        if [ ! -e "${CLAUDE_SKILLS}/${name}" ]; then
            ln -sf "${skill_dir%/}" "${CLAUDE_SKILLS}/${name}"
            echo "Linked skill: ${name}"
        else
            echo "Skill already exists (not a symlink): ${name} (skipped)"
        fi
    done
fi

# 5. Install Python dependencies
echo ""
echo "Installing Python dependencies..."
if command -v uv &>/dev/null; then
    uv pip install --system youtube-transcript-api 2>/dev/null || \
    pip3 install --break-system-packages youtube-transcript-api 2>/dev/null || \
    pip3 install youtube-transcript-api
elif command -v pip3 &>/dev/null; then
    pip3 install --break-system-packages youtube-transcript-api 2>/dev/null || \
    pip3 install youtube-transcript-api
fi
echo "Dependencies installed."

# 6. Copy registry template if none exists
if [ ! -f "${BRAIN_HOME}/registry.yaml" ] && [ -f "${REPO_DIR}/registry/registry.template.yaml" ]; then
    cp "${REPO_DIR}/registry/registry.template.yaml" "${BRAIN_HOME}/registry.yaml"
    echo "Created registry.yaml from template (edit paths for your machine)"
fi

echo ""
echo "Done. Brain system installed at ${BRAIN_HOME}"
echo ""
echo "Next steps:"
echo "  1. Edit ${BRAIN_HOME}/registry.yaml to configure your vaults"
echo "  2. Run: python3 ${BRAIN_HOME}/tools/youtube_transcript.py --help"
echo "  3. In Claude Code, use /youtube-transcript to fetch video transcripts"
