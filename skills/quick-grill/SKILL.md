---
name: quick-grill
description: Brief first-prompt preflight and lightweight approval interview. Use when the user tags this skill at the start of a session and wants the model to pause before execution, inspect the prompt, identify missing decisions or risks, ask a small number of focused questions, then propose a plan for approval.
---

# Quick Grill

Run a short preflight before doing the user's requested work. Clarify just enough to avoid misframing; do not turn the session into a long interview.

## Core Behavior

Do not execute the task immediately.

First identify:

- the likely goal
- the expected deliverable
- the main constraints
- any hidden risk, ambiguity, or authority issue
- whether the next action is obvious enough to propose directly

If a question can be answered by inspecting local files, repo context, attached material, or available docs without meaningful cost, inspect that source instead of asking the user.

## Questioning

Use the Codex `request_user_input` tool when available. This skill works best when `default_mode_request_user_input = true` is enabled. If the tool is unavailable, ask the same focused question in plain text.

Ask at most three questions in the first round. Prefer one or two when enough.

For each question:

- make it specific to the user's prompt
- provide 2-3 concrete options
- put the recommended option first and mark it as recommended
- explain the tradeoff in each option description
- avoid generic discovery questions unless the task truly lacks direction

Continue with another short round only if a material blocker remains after the user's answers.

## Clear Tasks

If the prompt is clear enough, still pause before execution.

Ask for approval with a concise plan. The approval question should usually offer:

- proceed with recommended plan
- adjust scope
- stop or answer only

Keep the plan short. State what will be done, what will not be done, and what verification or output the user should expect.

## Output Shape

Before approval, respond with:

1. a brief preflight summary
2. focused questions, or a yes/no approval request
3. a recommended path

After approval, proceed normally and execute the task end to end unless the user redirects.

## Guardrails

Do not ask questions for routine details the model can reasonably decide.

Do not perform file edits, commits, destructive commands, deployments, purchases, or external side effects before approval.

For high-risk work, make the risk visible and ask for explicit approval.

For simple low-risk work, keep the interaction lightweight: one short preflight, one approval question, then execute.
