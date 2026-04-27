# fresh-deps

Codex PreToolUse dependency freshness and security guard.

Compatibility: direct

## Events

- PreToolUse: blocks unsafe changed dependency specs
- PostToolUse: advises on existing unsafe dependencies in touched files

## Behavior

Before `apply_patch` lands, the hook checks only added or changed dependency
specs and blocks when:

- the requested version was published less than seven days ago
- a newer stable registry version exists that is at least seven days old
- the requested version has known vulnerabilities in OSV

Supported files:

- `package.json`
- `requirements.txt`
- `pyproject.toml` PEP 621, dependency groups, and Poetry dependencies
- `Cargo.toml`
- `go.mod`
- Python scripts with PEP 723 inline dependencies

Security advisory checks use the OSV API for npm, PyPI, crates.io, and Go
module versions.

After `apply_patch` lands, unchanged dependencies in touched dependency files
are checked as advisory context only. They do not block the edit.

## Install

Copy `hooks/fresh-deps/.codex/hooks/*` into the target project and merge
`hooks/fresh-deps/hooks.json` into the project `.codex/hooks.json`.

`[features].codex_hooks = true` must be enabled in Codex config.

## Verify

Run the hook directly with a Codex `PreToolUse` payload:

```bash
uv run --no-project --python '>=3.11' hooks/fresh-deps/.codex/hooks/fresh-deps.py < payload.json
```

Run tests from the repository root:

```bash
uv run --no-project --python '>=3.11' python hooks/fresh-deps/tests/test-fresh-deps.py
```
