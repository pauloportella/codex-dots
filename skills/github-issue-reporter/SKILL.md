---
name: github-issue-reporter
description: Use when the user wants to report or file GitHub issues and needs duplicate search, live issue-template compliance, exact draft review, and explicit approval before posting with gh.
---

# GitHub Issue Reporter

Help the user file high-quality GitHub issues without creating duplicates or posting unapproved text.

## Core Workflow

1. Identify the target repository.
2. Inspect the live GitHub issue templates before drafting.
3. Search existing issues for similar reports.
4. Compare likely related issues and say whether the new report is a duplicate.
5. Draft the exact title and body using the right template.
6. Ask for any missing required fields.
7. Print the exact final text in the conversation.
8. Only after the draft is visible, ask for approval with `request_user_input`.
9. Post with `gh issue create` only after approval.
10. Return the created issue URL.

If the user supplies several reports, handle them one at a time unless they explicitly ask for batching.

## Template Handling

Prefer live repository data over local checkout assumptions. First check whether the local repo has `.github/ISSUE_TEMPLATE`; if not, fetch templates from GitHub:

```bash
gh api repos/OWNER/REPO/contents/.github/ISSUE_TEMPLATE --jq '.[] | {name,path,type,download_url}'
gh api repos/OWNER/REPO/contents/.github/ISSUE_TEMPLATE/TEMPLATE.yml --jq .content | base64 --decode
```

Use the template's labels, required fields, and section names. Do not invent a different format when a template exists.

## Duplicate Search

Run several focused searches, not just one. Vary terminology around product area, symptom, platform, and likely labels.

```bash
gh issue list --repo OWNER/REPO --state all --limit 50 --search "terms here" --json number,title,url,state,labels,updatedAt
gh issue view NUMBER --repo OWNER/REPO --json number,title,url,state,body,labels
```

When results are related but not duplicates, name the differences clearly. If an existing issue is a likely duplicate, recommend reacting or commenting there instead of creating a new issue.

## Drafting

Use the user's language as the source of truth, but normalize it into a clear issue:

- specific title with affected product, version, platform, and symptom when known
- actual behavior with concrete observations
- reproducible steps
- expected behavior
- additional information, including duplicate-search context when useful

For environment fields, collect exact values where possible with local commands such as `uname -mprs`, but do not guess subscription, app version, account type, or private details. Ask for missing required values.

## Approval Boundary

Never post, comment, close, label, or mutate a GitHub issue before the user approves the exact text.

Before asking for approval, always print the final artifact in the thread:

- for a new issue: show `Title:` and the full `Body:`
- for a comment: show the target issue or PR and the full comment body
- for any other mutation: show exactly what will be sent to GitHub

The approval question must come after that visible draft in the same turn. Do not ask "approve posting the drafted text shown in the conversation" unless the draft text is already visible above the question. If you asked too early, say the approval was invalid, show the draft, and ask again.

Use the Codex `request_user_input` tool when available. Ask a single approval question immediately after the draft with:

- `Approve (Recommended)`: post the drafted issue exactly as shown
- `Deny`: do not post the issue
- `Something else`: let the user provide edits or missing information in free-form text

If the tool is unavailable, ask the same approval question in plain text with `Approve`, `Deny`, or `Something else`.

Approval must be explicit through the approval question after the final text is visible. A user providing a missing field or edit is not approval to post unless they also clearly approve the revised final text.

After approval, use a body file or heredoc so formatting is preserved:

```bash
gh issue create --repo OWNER/REPO --title "Title" --body-file - <<'EOF'
Body
EOF
```

Report the resulting URL and stop.
