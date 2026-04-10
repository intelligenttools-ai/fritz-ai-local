---
name: fritz:handover
description: >
  Create a structured handover document for continuing work in a fresh agent session.
  Use when the user asks to hand over, hand off, create a handover, wrap up for
  continuation, save session state, prepare a fresh session, or run /handover.
  Produces a self-contained markdown file in ~/.brain/handovers/ (global) or
  .handovers/ (project-local) that a receiving agent can ingest, act on, and clean up.
---

# Handover

Create a self-contained handover document that allows a fresh agent session to
continue work seamlessly — with full context, next steps, and cleanup instructions.

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
The user may override with explicit path preference.

## Workflow

### 1. Gather context

Collect from the current session:

- **Goal**: What is the user trying to accomplish? (the overarching objective)
- **Status**: What has been done so far? (completed steps, files changed, decisions made)
- **Blockers**: What is stuck or unresolved? (errors, open questions, pending decisions)
- **Next steps**: What should the receiving agent do first? (concrete, actionable)
- **Key files**: Which files are central to the work? (paths + brief role description)
- **Branch state**: Current git branch, uncommitted changes, recent commits relevant to the work

### 2. Write the handover document

Create a timestamped file:

```
{storage}/handover-{YYYY-MM-DD}-{HHmm}-{slug}.md
```

Where `{slug}` is a 2-3 word lowercase hyphenated summary (e.g., `auth-migration`, `brain-compile-fix`).

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

{What the user is trying to accomplish — 1-3 sentences}

## Completed

{Bulleted list of what was done this session}

## Current State

{Where things stand right now — working/broken/partially done}
{Include any error messages or test output if relevant}

## Key Files

{Table or list of files central to the work, with brief role}

## Open Questions / Blockers

{Anything unresolved that the receiving agent needs to decide or ask about}

## Next Steps

{Ordered list of concrete actions for the receiving agent}

## Receiving Agent Instructions

1. Read this handover document completely
2. Verify the branch and file state described above still matches reality
3. Execute the next steps in order
4. When all next steps are complete (or if the handover is no longer needed):
   - Run `/fritz:brain-ingest` on this handover to capture any decisions or knowledge worth keeping
   - Delete this handover file: `rm {path-to-this-file}`
5. If work is still incomplete, create a new `/handover` before ending your session
```

### 3. Provide the kickoff prompt

After writing the handover, present the user with a ready-to-paste prompt
for starting a fresh session. Format it as a fenced code block:

````markdown
```
Read the handover at {path-to-handover-file} and continue the work described in it. Follow the receiving agent instructions at the end of the document.
```
````

If the handover is project-local, remind the user to start the new session
from the same project directory.

## Important

- Keep handover documents **concise** — under 200 lines. A handover is a briefing, not a transcript.
- Include **only actionable context**. Skip routine tool output, conversation filler, and resolved tangents.
- **Never** include secrets, tokens, or credentials in handover documents.
- If the `.handovers/` or `~/.brain/handovers/` directory doesn't exist, create it.
- Use `.gitignore` to exclude `.handovers/` from version control if it doesn't exist yet.
- Set `status: pending` in frontmatter. The receiving agent should update to `status: active` when starting and `status: completed` before deleting.
- A handover document is **ephemeral** — it exists only to bridge two sessions. Once ingested and acted upon, it should be removed.
