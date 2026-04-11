---
name: fritz:handover
description: >
  Create a structured handover document for continuing work in a fresh agent session.
  Preserves knowledge before handing over: compiles pending captures, ingests session
  decisions/patterns, syncs if configured, then writes the handover document.
  Use when the user asks to hand over, hand off, create a handover, wrap up for
  continuation, save session state, prepare a fresh session, or run /handover.
---

# Handover

Create a self-contained handover document that allows a fresh agent session to
continue work seamlessly. **Preserves all knowledge before handing over** —
the receiving agent inherits compiled knowledge, not a TODO to compile it.

## Trigger

Activate when the user asks to:
- Create a handover / hand off / handover prompt
- Wrap up for a fresh session
- Save state for continuation
- Run `/handover`

## Storage

- **Global**: `~/.brain/handovers/`
- **Project-local**: `.handovers/` (relative to project root)

Default to project-local if inside a git repo, global otherwise.

## Workflow

### Phase 1: Preserve Knowledge ("leave nothing behind")

Before writing the handover document, preserve all knowledge from this session.

**Step 1: Compile pending captures**

Check `~/.brain/.compile-needed`. If it exists, run brain-compile (or spawn a
subagent to run it). Wait for completion before proceeding. This ensures all
prior session captures are promoted to knowledge articles.

**Step 2: Ingest session knowledge**

Extract from the current session:
- **Decisions** made (architecture choices, design trade-offs, tool selections)
- **Patterns** discovered (what worked, reusable approaches)
- **Corrections** from the user (feedback that should prevent future mistakes)
- **Facts** learned (domain knowledge, system behavior, configuration details)

Write these directly to the appropriate vault as knowledge articles, following
the brain-compile workflow (route by content, use frontmatter, update indexes).
This is inline ingestion — not deferred to a future agent.

**Step 3: Sync if configured**

Read `~/.brain/registry.yaml` to find the active vault. Check its `sync` setting.
If a sync target is configured (affine, notion, git, filesystem), run brain-sync
for the articles just created or updated in this session.

If no sync target or sync is `local`/`none`, skip this step.

### Phase 2: Write the Handover Document

Collect from the current session:

- **Goal**: What is the user trying to accomplish?
- **Status**: What has been done? (completed steps, files changed, decisions made)
- **Blockers**: What is stuck or unresolved?
- **Next steps**: What should the receiving agent do first? (concrete, actionable)
- **Key files**: Which files are central to the work? (paths + brief role)
- **Branch state**: Current git branch, uncommitted changes, recent commits

Create a timestamped file:

```
{storage}/handover-{YYYY-MM-DD}-{HHmm}-{slug}.md
```

Use this structure:

```markdown
---
type: handover
created: {ISO 8601 timestamp}
project: {project name or path}
branch: {current branch}
from_agent: {agent identifier}
status: pending
---

# Handover: {Brief title}

## Goal

{1-3 sentences}

## Completed

{Bulleted list of what was done this session}

## Current State

{Where things stand — working/broken/partially done}

## Key Files

{Table or list of files central to the work, with brief role}

## Open Questions / Blockers

{Anything unresolved}

## Next Steps

{Ordered list of concrete actions}

## Receiving Agent Instructions

1. Read this handover document completely
2. Verify the branch and file state described above still matches reality
3. Execute the next steps in order
4. Knowledge from the previous session has already been compiled and synced.
   If you discover additional insights while executing next steps, compile
   them before ending your session.
5. When all next steps are complete (or if the handover is no longer needed):
   - Delete this handover file: `rm {path-to-this-file}`
6. If work is still incomplete, create a new `/handover` before ending your session
```

### Phase 3: Provide the Kickoff Prompt

Present a ready-to-paste prompt as a fenced code block:

````markdown
```
Read the handover at {path-to-handover-file} and continue the work described
in it. Follow the receiving agent instructions at the end of the document.
```
````

## Important

- **Phase 1 is not optional.** Always compile and ingest before writing the handover.
- Keep handover documents under 200 lines — a briefing, not a transcript.
- Include only actionable context. Skip routine tool output and resolved tangents.
- Never include secrets, tokens, or credentials.
- Create `.handovers/` directory if it doesn't exist. Add to `.gitignore`.
- Set `status: pending` in frontmatter.
- A handover is ephemeral — it exists only to bridge two sessions.
