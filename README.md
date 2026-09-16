# codex-dots

Reusable Codex bundles.

## Bundles

- `hooks/fresh-deps`: blocks dependency edits that use too-new package versions,
  stale versions, or known vulnerable versions before `apply_patch` lands. It
  also gives non-blocking advisories for unchanged dependencies in touched
  dependency files.
- `hooks/unslop-writing`: reports cliché candidates after prose edits and
  before a turn stops.
- `skills/github-issue-reporter`: searches for duplicate GitHub issues, follows
  live issue templates, drafts exact issue text, and waits for approval before
  posting.
- `skills/quick-grill`: adds a short preflight and approval step before work.
- `skills/codex-better-fork`: launches and supports the experimental
  handoff-backed fork helper, including opening its localhost UI in Codex's
  in-app browser.
- `skills/code-explainer`: inspects live implementation code and creates
  source-backed, Notion-style standalone HTML feature explainers.

## Install

Hook bundle, replacing `<bundle>` with `fresh-deps` or `unslop-writing`:

1. Copy `hooks/<bundle>/.codex/hooks/*` into your target repo's `.codex/hooks/`.
2. Merge `hooks/<bundle>/hooks.json` into your target repo's `.codex/hooks.json`.
3. Enable hooks and trust the target project:

```toml
[features]
hooks = true
```

Skill bundle:

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
cp -R skills/quick-grill "${CODEX_HOME:-$HOME/.codex}/skills/"
cp -R skills/github-issue-reporter "${CODEX_HOME:-$HOME/.codex}/skills/"
cp -R skills/codex-better-fork "${CODEX_HOME:-$HOME/.codex}/skills/"
cp -R skills/code-explainer "${CODEX_HOME:-$HOME/.codex}/skills/"
```

`quick-grill` works with the available preference-question tool or concise plain-text questions. Plan approval uses the environment's permitted approval mechanism; no feature flag is required.

## Verify

Run hook tests from this repository root:

```bash
uv run --no-project --python '>=3.11' python hooks/fresh-deps/tests/test-fresh-deps.py
node --test hooks/unslop-writing/tests/unslop-writing.test.mjs
node hooks/unslop-writing/.codex/hooks/cliche-detector.mjs --self-test
```

## License

0BSD. See `LICENSE`.
