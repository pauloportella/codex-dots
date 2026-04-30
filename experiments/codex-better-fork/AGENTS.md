# codex-better-fork Guidelines

This directory contains an experimental open-source Tauri app for handoff-backed
Codex forks.

## Scope

- Keep documentation public-facing. Do not include private local assumptions,
  personal names, or conversation-only context.
- Preserve the core workflow: select turn, generate handoff, review handoff,
  fork, rollback, start the new thread with the approved handoff.
- Treat `codex app-server` as the source of truth. Do not add mock sessions,
  fake fallback data, or silent fallback flows.
- Let protocol and runtime errors surface clearly so they can be fixed.

## Development

- Use the existing React, TypeScript, Vite, Tauri, and Rust structure.
- Keep UI visually close to Codex desktop: neutral surfaces, 13px UI text,
  tight rows, subtle borders, and restrained shadows.
- Use the extracted Codex desktop CSS already imported by `src/styles.css`
  before adding new visual primitives.
- Keep changes scoped to this experiment unless the parent repository needs a
  matching update.

## Verification

- Run `pnpm build` for frontend changes.
- Run `pnpm tauri build` when changing Tauri, Rust bridge code, icons, or app
  packaging.
- Commit completed changes with a Conventional Commit message.
- Do not push unless explicitly requested.
