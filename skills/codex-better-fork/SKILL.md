---
name: codex-better-fork
description: Use when the user wants to open or use the codex-better-fork app from Codex, especially by launching the Tauri app and opening its localhost UI in the Codex in-app browser.
---

# Codex Better Fork

Help the user open the `codex-better-fork` app from a Codex session. Today the
app is Tauri-first: the Tauri process starts the local bridge, and the Codex
in-app browser can view the app through the localhost UI.

## Launch For Use

When the user wants to use the app from Codex, do only the setup needed to put
the UI in front of them:

1. Prefer the installed app at `/Applications/codex-better-fork.app`.
2. If it is not installed, use the repo dev flow from
   `experiments/codex-better-fork`:

```bash
pnpm tauri dev
```

3. After the Tauri app is running, open the UI in Codex's in-app browser at:

```text
http://localhost:1420
```

The Tauri process owns the local HTTP bridge on `127.0.0.1:1421`; opening the
Vite UI alone is not enough if the Tauri process is not running.

When opening or inspecting the localhost UI, use the Browser plugin/in-app
browser workflow when available. Opening the UI is enough for ordinary "use it
in Codex" requests.

## App Workflow

The app itself lets the user:

1. List recent Codex sessions from `codex app-server`.
2. Select a session and a turn boundary.
3. Generate a handoff preview from the transcript up to that boundary.
4. Review the handoff.
5. Fork the source thread, roll back the new fork to the selected boundary, and
   start the fork with the approved handoff.
6. Open the resulting `codex://threads/<id>` link.

Do not operate this workflow for the user unless they explicitly ask for app
interaction or testing. If they only asked to open the app, stop once the
localhost UI is open in the Codex in-app browser.

## Troubleshooting

If `http://localhost:1420` does not load, check whether the Tauri app is running
and whether `http://127.0.0.1:1421/healthz` responds. If the bridge is missing,
start the Tauri app rather than treating the browser UI as standalone.

If the user explicitly asks to modify or debug the app, then read
`experiments/codex-better-fork/AGENTS.md` and
`experiments/codex-better-fork/README.md` before changing code.
