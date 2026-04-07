#!/usr/bin/env bash
set -euo pipefail

# Fritz Local — Brain Overlay Installer
# Creates ~/.brain/ directory, symlinks hooks/tools, deploys overlays to vaults.

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
BRAIN_HOME="${HOME}/.brain"

echo "Fritz Local — installing from ${REPO_DIR}"
echo "Target: ${BRAIN_HOME}"
echo ""

# 1. Create ~/.brain/ structure
mkdir -p "${BRAIN_HOME}"/{hooks,tools,registry,capture/daily,capture/sessions}
echo "Created ${BRAIN_HOME}/"

# Create global log.md if missing
if [ ! -f "${BRAIN_HOME}/log.md" ]; then
    echo "# Brain Operations Log" > "${BRAIN_HOME}/log.md"
    echo "" >> "${BRAIN_HOME}/log.md"
    echo "<!-- Global capture log. Vault-specific logs live in <vault>/.brain/log.md -->" >> "${BRAIN_HOME}/log.md"
    echo "$(date '+%Y-%m-%d %H:%M') | INIT | install.sh | Fritz Local installed" >> "${BRAIN_HOME}/log.md"
fi

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

# 4. Symlink skills into Claude Code and ICA skills directories
CLAUDE_SKILLS="${HOME}/.claude/skills"
ICA_SKILLS="${HOME}/.ica/official-skills/skills"
if [ -d "${REPO_DIR}/skills" ]; then
    for skill_dir in "${REPO_DIR}"/skills/*/; do
        [ -d "$skill_dir" ] || continue
        name="$(basename "$skill_dir")"
        # Claude Code skills
        if [ -d "${CLAUDE_SKILLS}" ]; then
            if [ -L "${CLAUDE_SKILLS}/${name}" ]; then
                rm "${CLAUDE_SKILLS}/${name}"
            fi
            if [ ! -e "${CLAUDE_SKILLS}/${name}" ]; then
                ln -sf "${skill_dir%/}" "${CLAUDE_SKILLS}/${name}"
                echo "Linked skill (claude): ${name}"
            fi
        fi
        # ICA skills
        if [ -d "${ICA_SKILLS}" ]; then
            if [ -L "${ICA_SKILLS}/${name}" ]; then
                rm "${ICA_SKILLS}/${name}"
            fi
            if [ ! -e "${ICA_SKILLS}/${name}" ]; then
                ln -sf "${skill_dir%/}" "${ICA_SKILLS}/${name}"
                echo "Linked skill (ica): ${name}"
            fi
        fi
    done
fi

# 6. Install Python dependencies
echo ""
echo "Installing Python dependencies..."
if command -v uv &>/dev/null; then
    uv pip install --system -r "${REPO_DIR}/requirements.txt" 2>/dev/null || \
    pip3 install --break-system-packages -r "${REPO_DIR}/requirements.txt" 2>/dev/null || true
elif command -v pip3 &>/dev/null; then
    pip3 install --break-system-packages -r "${REPO_DIR}/requirements.txt" 2>/dev/null || \
    pip3 install -r "${REPO_DIR}/requirements.txt" 2>/dev/null || true
fi

# 7. Copy registry template if none exists
if [ ! -f "${BRAIN_HOME}/registry.yaml" ] && [ -f "${REPO_DIR}/registry/registry.template.yaml" ]; then
    cp "${REPO_DIR}/registry/registry.template.yaml" "${BRAIN_HOME}/registry.yaml"
    echo "Created registry.yaml from template (edit paths for your machine)"
fi

# 8. Copy schema template
if [ -f "${REPO_DIR}/templates/schema.template.md" ]; then
    mkdir -p "${BRAIN_HOME}/templates"
    cp "${REPO_DIR}/templates/schema.template.md" "${BRAIN_HOME}/templates/schema.template.md"
    echo "Installed schema template"
fi

echo ""
echo "Done. Fritz Local installed at ${BRAIN_HOME}"
echo ""
echo "Next steps:"
echo "  1. Edit ${BRAIN_HOME}/registry.yaml — add your vault paths"
echo "  2. Run /brain-setup in your agent to initialize each vault"
echo "     (the agent explores the vault structure and generates the manifest)"
echo "  3. See SETUP.md for agent-specific hook registration"
