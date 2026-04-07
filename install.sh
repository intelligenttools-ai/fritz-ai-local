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

# 8. Deploy overlay to a vault (optional: pass vault path as argument)
deploy_overlay() {
    local vault_path="$1"
    local overlay_name="$2"
    local overlay_src="${REPO_DIR}/overlays/${overlay_name}"

    if [ ! -d "$overlay_src" ]; then
        echo "No overlay template found for: ${overlay_name}"
        return 1
    fi

    local brain_dir="${vault_path}/.brain"
    mkdir -p "${brain_dir}"/{adapters,capture/sessions,capture/inbox}

    # Copy overlay files (don't overwrite existing)
    for f in manifest.yaml schema.md; do
        if [ -f "${overlay_src}/${f}" ] && [ ! -f "${brain_dir}/${f}" ]; then
            cp "${overlay_src}/${f}" "${brain_dir}/${f}"
            echo "  Created ${f}"
        fi
    done

    # Deploy adapter files
    mkdir -p "${brain_dir}/adapters"
    for adapter in CLAUDE.md AGENTS.md GEMINI.md; do
        if [ -f "${overlay_src}/${adapter}" ]; then
            cp "${overlay_src}/${adapter}" "${brain_dir}/adapters/${adapter}"
        fi
    done

    # Create log.md if missing
    if [ ! -f "${brain_dir}/log.md" ]; then
        echo "# Brain Operations Log" > "${brain_dir}/log.md"
        echo "" >> "${brain_dir}/log.md"
        echo "<!-- Append-only. Each entry: YYYY-MM-DD HH:MM | OPERATION | agent | summary -->" >> "${brain_dir}/log.md"
        echo "$(date '+%Y-%m-%d %H:%M') | INIT | install.sh | Brain overlay created" >> "${brain_dir}/log.md"
    fi

    # Symlink adapters to vault root
    for adapter in CLAUDE.md AGENTS.md GEMINI.md; do
        if [ -f "${brain_dir}/adapters/${adapter}" ]; then
            ln -sf ".brain/adapters/${adapter}" "${vault_path}/${adapter}" 2>/dev/null || true
        fi
    done

    echo "  Overlay deployed to ${vault_path}"
}

# Deploy VanillaCore overlay if the vault exists
if [ -d "${HOME}/Notes/VanillaCore" ]; then
    echo ""
    echo "Deploying VanillaCore overlay..."
    deploy_overlay "${HOME}/Notes/VanillaCore" "vanillacore"
fi

echo ""
echo "Done. Fritz Local installed at ${BRAIN_HOME}"
echo ""
echo "Next steps:"
echo "  1. Edit ${BRAIN_HOME}/registry.yaml to configure your vaults"
echo "  2. Overlays are deployed to vault/.brain/ directories"
echo "  3. Each vault gets CLAUDE.md, AGENTS.md, GEMINI.md symlinks at root"
