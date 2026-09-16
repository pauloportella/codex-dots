# unslop-writing

Codex writing guard for substantial copy and first-person prose.

Compatibility: direct

## Events

- PostToolUse: reviews substantial prose added through `apply_patch`, `Edit`, or `Write`
- Stop: reviews substantial first-person prose before the turn finishes

## Behavior

The hook runs the bundled JavaScript cliché detector and asks Codex for one
revision pass when it finds candidate phrases. Edit hooks accept substantial
copy; Stop skips responses under 40 words, third-person text, fenced code, and
a second continuation.

Matches are review candidates, not proof that text was AI-generated. Codex
should preserve deliberate language and revise only actionable findings.

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
node hooks/unslop-writing/.codex/hooks/cliche-detector.mjs --self-test
node --test hooks/unslop-writing/tests/unslop-writing.test.mjs
```
