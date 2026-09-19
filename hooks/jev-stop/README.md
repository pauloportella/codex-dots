# jev-stop

Codex Stop guard that asks Jev whether a final response leaves authorized work
unfinished.

Compatibility: direct. Requires macOS or Linux, Git, uv, and Python 3.11+.
The public default uses TypeSafe and requires its API key. The deadline uses
Unix signals; Windows is not supported.

## Events

- Stop: checks for an acknowledgment, promise, or partial result that abandons
  an outstanding action, and requests at most one continuation.

## Behavior

The hook streams a fixed snapshot of the task transcript and extracts user
messages and final assistant replies before applying a 64 KB redacted-state
budget. Large tool outputs cannot evict the user's request. When dialogue exceeds the budget, it
keeps a recent suffix and the opening user request if that request is at most
8 KB. The classifier is told that the opening request may have been completed
or superseded. Oversized context without a usable recent user message is skipped.

It sends that dialogue and the proposed final response to `JEV_API_URL`, using
`jev-latest` and the adjacent question JSON files. The default is
`https://api.typesafe.ai/v1/systemone`. Original dialogue survives resume and
compaction when present in the transcript; no additional prompt log or cross-task
memory is used.
The transcript format is a Codex implementation detail, so extraction needs
checking after format changes. Omitted middle history can still hide obligations,
and indirect completeness questions can remain below threshold with full history.

A premature-stop score of at least 0.80 asks Codex to continue already-authorized
work or ask the necessary clarification. The threshold drops to 0.70 when the
latest user message has a frustration probability of at least 0.50. Separate
checks identify questions, corrections, and instructions; more than one can apply.
These checks and frustration guide the continuation message. They do not grant
permission to stop or expand the task. The stop question respects explanation-only requests,
explicit pauses, scope limits, and genuine blockers. It evaluates the stopping
point, not code correctness. `stop_hook_active` prevents a second continuation.

Missing credentials, missing or unusable history, invalid responses, and API
failures allow the stop. History collection has a one-second deadline; exceeding
it skips classification and logs `history_timeout`. The API has a three-second
deadline and no retry; the hook timeout is five seconds.

## Install

Copy `hooks/jev-stop/.codex/hooks/*` into the target project's `.codex/hooks/`
and merge `hooks/jev-stop/hooks.json` into its `.codex/hooks.json`.

Set values in Codex's environment, or create `~/.config/jev.env` with mode
`0600` containing:

```dotenv
JEV_API_KEY=replace-with-your-key
# Optional: JEV_API_URL=https://jev.example.com/api/jev
```

The environment takes precedence. `JEV_API_KEY` is required and sent only when
the destination is the default TypeSafe URL. Custom HTTPS endpoints need no
caller TypeSafe key, receive no TypeSafe authorization header, and are never
followed by an automatic fallback. The file is read as data, never executed.
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

Enabling this hook sends conversation text to the configured provider. Tool calls/results,
reasoning, compaction summaries, and recognized injected instruction wrappers
are excluded. Known credential formats, secret assignments, private keys,
URLs, and emails are redacted before sending.

Redaction is pattern-based, not anonymization. Names, street addresses, business
details, code quoted in dialogue, some project paths, and unrecognized secrets
can still be sent. Use it only with conversations you can share with that
provider.

Daily decision logs are created at `~/.codex/log/jev-stop/YYYY-MM-DD.jsonl`
with mode `0600`. They contain task/turn IDs, the full local transcript path,
hashes, scores/model when available, the selected threshold, decision/reason, and timing. Coverage metadata
includes available/selected dialogue counts, omitted-message count, whether the
opening request survived, redacted-state bytes, and history-collection time.
Logs exclude conversation text, keys, and raw API/error bodies. Logs are retained
until removed; keep logs, transcripts, and credentials out of public bundles.

## Verify

Run from the repository root:

```bash
uv run --no-project --python '>=3.11' python hooks/jev-stop/tests/test-jev-stop.py
```

Tests use synthetic transcripts, temporary credential files, and a mocked API.
They do not read your key or send real conversations.
