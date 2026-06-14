# Implementation Loop

Paste the block below into a **fresh agent session started in this repo's root**. It works through the
open issues autonomously. Each iteration takes one self-contained work item end-to-end via sub-agents
(implement → critical review → merge → deploy → test); problems found during deploy/test are filed as
new issues and picked up on a later pass.

```
Run in a fresh session (cwd = the repo clone). Loop until there are no ready work items left.

Repo: intelligenttools-ai/fritz-ai-local at https://git.intelligenttools.ai
API token: `tr -d '\n\r ' < ~/.config/secrets/forgejo-intelligenttools` (header `Authorization: token <TOKEN>`). Use curl/python.

Model selection — always match the model to the task:
- Haiku for simple/mechanical tasks
- Sonnet for moderately complex tasks
- Opus for reviews and very complex tasks
Assess each item's complexity when spawning its sub-agent and choose accordingly.

Each iteration handles ONE self-contained work item, end to end:
1. Pick the next open issue that is ready to start (its prerequisites are done).
2. Spawn an IMPLEMENTATION sub-agent (Haiku / Sonnet / Opus by the item's complexity): implement on a new branch, with tests, open a PR.
3. Spawn a REVIEW sub-agent (Opus): critically review against the issue's acceptance criteria.
4. If the review has findings -> spawn an implementation sub-agent (model by complexity) to address them, then review again (Opus).
   Repeat develop<->review until no findings, OR 3 iterations are reached.
5. If the review passed: merge the PR.
6. Spawn a DEPLOY sub-agent (Haiku/Sonnet by complexity) to deploy/install the change, then a TEST sub-agent (Sonnet) to verify the result.
7. Any problems from deploy/test -> create new issues (picked up in a later iteration).
8. Move to the next issue.

Guardrails:
- Never modify the live ~/.brain or its capture files; test against copies.
- Verify a replacement works before disabling whatever it replaces.
- If an issue is unclear or its prerequisites aren't done, skip it; if nothing is ready, stop and report.
- If 3 develop<->review iterations don't clear the findings, stop on that item and file a follow-up issue instead of merging.
- No "Co-Authored-By" or "Generated with" trailers on commits or PRs.
```

## Notes

- **Runtime-native items** (e.g. the Codex and Hermes binding issues) are best run by a loop operating
  *inside that runtime* so the sub-agent can introspect its own hook/skill APIs.
- The loop is self-contained per item: discovery and triage happen against the live issue list, so newly
  filed issues are picked up automatically on later passes.
