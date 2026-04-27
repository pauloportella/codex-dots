# Repository Guidelines

This repository publishes reusable Codex bundles.

## Layout

- Put hook bundles under `hooks/<bundle-name>/`.
- Each hook bundle should include `README.md`, `bundle.json`, `hooks.json`, and
  implementation files under `.codex/hooks/`.
- Keep runtime hook files flat inside `.codex/hooks/`; use kebab-case filenames.
- Keep tests in `tests/` inside the bundle.
- Put skill bundles under `skills/<skill-name>/`.
- A skill bundle should contain `SKILL.md` and optional `agents/openai.yaml`.
- Do not add per-skill README files unless the skill needs user-facing docs.

## Installing

Hooks install into the target repo:

- copy `.codex/hooks/*`
- merge `hooks.json`
- enable `[features].codex_hooks = true`
- trust the target project

Prefer hook commands that resolve from the target git root, for example:

```json
"command": "uv run --no-project --python '>=3.11' \"$(git rev-parse --show-toplevel)/.codex/hooks/fresh-deps.py\""
```

Skills install into `${CODEX_HOME:-$HOME/.codex}/skills/<skill-name>/`.
`quick-grill` also expects `default_mode_request_user_input = true`.

## Development

- Keep docs concise and installation-focused.
- Add or update tests when changing hook behavior.
- For `fresh-deps`, run:

```bash
uv run --no-project --python '>=3.11' python hooks/fresh-deps/tests/test-fresh-deps.py
```
