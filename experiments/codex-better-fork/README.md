# codex-better-fork

Experimental open-source desktop app for creating handoff-backed Codex forks.

Codex can already fork a thread. This app explores a more guided workflow:
select a turn, generate a compact handoff from the conversation up to that
point, review it, then create a new fork that starts with that handoff.

## What It Does

- Lists recent Codex sessions from `codex app-server`.
- Opens a session and renders user and Codex turns.
- Lets you select a turn as the fork boundary.
- Generates a handoff preview from the selected conversation slice.
- Requires review before starting the fork.
- Forks the thread, rolls the fork back to the selected turn boundary, and
  starts the new thread with the approved handoff.
- Opens the resulting thread through the `codex://threads/<id>` deeplink.

## Why This Exists

Long agent sessions often split into several useful paths: exploration,
debugging, implementation, review, and follow-up work. Plain forking is useful,
but the next fork still needs enough context to continue cleanly.

`codex-better-fork` treats the handoff as part of the fork operation. The goal is
to make branch-style Codex workflows easier to use without manually copying a
summary between threads.

## Current Flow

1. Start the app.
2. Pick a recent Codex session from the sidebar.
3. Select the turn that should become the fork base.
4. Click `Generate Handoff` inside the selected turn card.
5. Review the generated handoff in the modal.
6. Click `Fork` to create the fork, roll it back, send the handoff, and open
   the resulting thread through the Codex deeplink.
7. Use the top `Open Thread` button when you want to open the fork manually.

The handoff generation currently uses `gpt-5.5` with `high` reasoning. The fork
itself uses the visible fork settings derived from the source thread unless
changed before forking.

## Architecture

- Frontend: React, TypeScript, Vite.
- Desktop shell: Tauri v2.
- Native bridge: Rust.
- Runtime source of truth: `codex app-server` over JSON-RPC stdio.
- Dev browser bridge: local HTTP bridge on `127.0.0.1:1421`.
- Styling: extracted Codex desktop webview CSS plus local layout styles.

## Development

Install dependencies from this folder:

```bash
pnpm install
```

Run the Vite dev surface:

```bash
pnpm dev
```

Run the Tauri app:

```bash
pnpm tauri dev
```

Build the frontend:

```bash
pnpm build
```

Build the packaged desktop app:

```bash
pnpm tauri build
```

The Tauri build writes bundles under:

```text
src-tauri/target/release/bundle/
```

## Notes

- Forking from an older turn is implemented as `thread/fork` followed by
  `thread/rollback` on the new fork.
- Rollback affects conversation state only. It does not revert filesystem
  changes made during the original thread.
