# jev-stop

Codex Stop guard that asks Jev whether a final response leaves authorized work
unfinished.

Compatibility: direct. Requires macOS or Linux, Git, uv, Python 3.11+, and a
TypeSafe API key. The deadline uses Unix signals; Windows is not supported.

## Events

- Stop: checks for an acknowledgment, promise, or partial result that abandons
  an outstanding action, and requests at most one continuation.

## Behavior

The hook sends up to 16 recent user/final-assistant messages and the proposed
final response to `https://api.typesafe.ai/v1/systemone`, using `jev-latest`
and the adjacent `jev-stop-question.json`. It reads the last 8 MiB of the
transcript and trims dialogue to a 24 KB state budget. Older obligations can
be missed.

A score of at least 0.80 asks Codex to continue already-authorized work or ask
the necessary clarification. The question respects explanation-only requests,
explicit pauses, scope limits, and genuine blockers. It evaluates the stopping
point, not code correctness. `stop_hook_active` prevents a second continuation.

Missing credentials, missing or unusable history, invalid responses, and API
failures allow the stop. The API has a three-second deadline and no retry;
the hook timeout is five seconds.

## Install

Copy `hooks/jev-stop/.codex/hooks/*` into the target project's `.codex/hooks/`
and merge `hooks/jev-stop/hooks.json` into its `.codex/hooks.json`.

Set `JEV_API_KEY` in Codex's environment, or create `~/.config/jev.env` with
mode `0600` containing:

```dotenv
JEV_API_KEY=replace-with-your-key
```

The environment takes precedence. The file is read as data, never executed.
Keep the real key outside Git. This env file and the log directory below use
the user's home directory, even when `CODEX_HOME` points elsewhere.

Enable hooks in Codex config:

```toml
[features]
hooks = true
```

Trust the project and review the hook with `/hooks`. Start a fresh task after
installation. Disable the Jev entry in `/hooks` to stop the check. See the
[Codex hook reference](https://learn.chatgpt.com/docs/hooks#stop) for Stop behavior.

## Privacy and logs

Enabling this hook sends conversation text to TypeSafe. Tool calls/results,
reasoning, compaction summaries, and recognized injected instruction wrappers
are excluded. Known credential formats, secret assignments, private keys,
URLs, and emails are redacted before sending.

Redaction is pattern-based, not anonymization. Names, street addresses, business
details, code quoted in dialogue, some project paths, and unrecognized secrets
can still be sent. Use it only with conversations you can share with that
provider.

Daily decision logs are created at `~/.codex/log/jev-stop/YYYY-MM-DD.jsonl`
with mode `0600`. They contain task/turn IDs, the full local transcript path,
hashes, score/model when available, decision/reason, and timing. They exclude
conversation text, keys, and raw API/error bodies. Logs are retained until
removed; keep logs, transcripts, and credentials out of public bundles.

## Verify

Run from the repository root:

```bash
uv run --no-project --python '>=3.11' python hooks/jev-stop/tests/test-jev-stop.py
```

Tests use synthetic transcripts, temporary credential files, and a mocked API.
They do not read your key or send real conversations.
