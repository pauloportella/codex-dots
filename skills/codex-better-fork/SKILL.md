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

1. Resolve the source checkout and read its applicable instructions before running project commands. Prefer the installed app at `/Applications/codex-better-fork.app` and reuse an existing healthy process.
2. The Tauri process owns the HTTP bridge at `http://127.0.0.1:1421/healthz`. The installed app serves its bundled frontend in its native window; it does not start Vite on port 1420.
3. For in-app browser use, also ensure the Vite UI is available at `http://localhost:1420`. If the installed app supplies the bridge but Vite is absent, run `pnpm dev` from `experiments/codex-better-fork`. Reuse an existing UI server when healthy.
4. If the app is not installed and neither process is running, use `pnpm tauri dev` from that project. Its development command starts Vite and Tauri together. Avoid starting a second bridge process when one is already healthy.
5. Verify both the bridge and UI, then open the UI in Codex's in-app browser using the available browser-control tools. Confirm the actual app is visible before declaring it open.

Starting Vite alone does not supply the bridge. Starting the installed Tauri app alone does not supply the localhost browser UI. Follow existing dependency and permission requirements for either launch path.

## App Workflow

The app itself lets the user:

1. List recent Codex sessions from `codex app-server`.
2. Select a session and a turn boundary.
3. Generate a handoff preview from the selected user message through the end of the source transcript.
4. Review the handoff.
5. Fork the source thread, roll back the new fork to before the selected user message, and start it with the approved handoff.
6. Open the resulting `codex://threads/<id>` link.

Do not operate this workflow for the user unless they explicitly ask for app
interaction or testing. If they only asked to open the app, stop once the
localhost UI is open in the Codex in-app browser.

## Troubleshooting

Check UI and bridge availability independently. Restore the missing process through the matching launch path above; report actual errors without claiming a healthy bridge proves that the browser UI is available.

If the user explicitly asks to modify or debug the app, then read
`experiments/codex-better-fork/AGENTS.md` and
`experiments/codex-better-fork/README.md` before changing code.
