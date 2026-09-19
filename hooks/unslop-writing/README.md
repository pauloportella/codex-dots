# unslop-writing

Codex writing guard for substantial copy and first-person prose.

Compatibility: direct

## Events

- PostToolUse: reviews substantial prose added through `apply_patch`, `Edit`, or `Write`
- Stop: reviews substantial first-person prose before the turn finishes

## Behavior

The hook uses Jev to judge the 38 cliché categories in `unslop-question.json`,
including labeled examples and legitimate counterexamples. It judges every
eligible passage without a phrase-match prefilter. A score of at least **0.50**
requests a revision; Codex writes the revision. The legacy detector is retained
for evaluation comparisons and is not used by the runtime hook.

Edit hooks accept substantial copy. Stop skips responses under 40 words,
third-person text, fenced code, and a second continuation. These existing scope
gates are unchanged.

Judgments are review candidates, not proof that text was AI-generated. Codex
should preserve deliberate language and revise only actionable findings.

See the [direct Jev evaluation of all 38 cliché categories](evaluation/detector-categories/README.md),
with explicit teaching examples and 152 separate tests. The selected 0.50 boundary
caught 36/38 direct cliché cases and preserved 73/76 deliberately challenging
legitimate/quoted cases; it is not a calibrated guarantee for everyday writing.

## Jev access and data

Requires Node.js 18+. `JEV_API_URL` selects an HTTPS provider; the default is
`https://api.typesafe.ai/v1/systemone`. The default requires a TypeSafe credential,
read from `TYPESAFE_API_KEY`, then `JEV_API_KEY`, or `~/.config/jev.env`.
The env file must have private permissions (`chmod 600 ~/.config/jev.env`).

Only the candidate prose and a fixed neutral review brief are sent using
`jev-latest`. Conversation history is not read. Fenced code is removed and common
credentials, URLs, email addresses, and home paths are redacted. Redaction is not
anonymization; other private prose can remain. The serialized state is limited to
24 KB and is never logged.

TypeSafe credentials are sent only to the default TypeSafe URL. Custom endpoints
need no caller key, receive no TypeSafe authorization header, and are never
followed by an automatic fallback. Missing required credentials, oversized input,
invalid replies, and network errors allow the turn to continue. Requests have a
three-second deadline, with no redirects, retries, or regex fallback. Request
bodies and credentials are not printed.

## Install

Copy `hooks/unslop-writing/.codex/hooks/*` into the target project and merge
`hooks/unslop-writing/hooks.json` into the project `.codex/hooks.json`.

Enable hooks in the current Codex config:

```toml
[features]
hooks = true
```

Trust the hook with `/hooks` before relying on it.

## Verify

```bash
node --test hooks/unslop-writing/tests/*.test.mjs
```
