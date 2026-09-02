# codex-dots

Reusable Codex bundles.

## Bundles

- `hooks/fresh-deps`: blocks dependency edits that use too-new package versions,
  stale versions, or known vulnerable versions before `apply_patch` lands. It
  also gives non-blocking advisories for unchanged dependencies in touched
  dependency files.
- `skills/github-issue-reporter`: searches for duplicate GitHub issues, follows
  live issue templates, drafts exact issue text, and waits for approval before
  posting.
- `skills/quick-grill`: adds a short preflight and approval step before work.
- `skills/codex-better-fork`: launches and supports the experimental
  handoff-backed fork helper, including opening its localhost UI in Codex's
  in-app browser.

## Install

Hook bundle:

1. Copy `hooks/fresh-deps/.codex/hooks/*` into your target repo's `.codex/hooks/`.
2. Merge `hooks/fresh-deps/hooks.json` into your target repo's `.codex/hooks.json`.
3. Enable hooks and trust the target project:

```toml
[features]
codex_hooks = true
```

Skill bundle:

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
cp -R skills/quick-grill "${CODEX_HOME:-$HOME/.codex}/skills/"
cp -R skills/github-issue-reporter "${CODEX_HOME:-$HOME/.codex}/skills/"
cp -R skills/codex-better-fork "${CODEX_HOME:-$HOME/.codex}/skills/"
```

For `quick-grill`, enable:

```toml
default_mode_request_user_input = true
```

This lets the skill run interview prompts outside Plan mode.

## Verify

Run hook tests from this repository root:

```bash
uv run --no-project --python '>=3.11' python hooks/fresh-deps/tests/test-fresh-deps.py
```

## License

0BSD. See `LICENSE`.
