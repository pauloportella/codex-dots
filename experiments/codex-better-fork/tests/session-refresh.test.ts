import assert from "node:assert/strict";
import { test } from "node:test";
import type { ForkState, ThreadTurn } from "../src/bridge.ts";
import { mergeRefreshedForkState } from "../src/session-refresh.ts";

function forkState(overrides: Partial<ForkState> = {}): ForkState {
  return {
    sessionId: "source-session",
    selectedTurnId: "turn-1",
    model: "gpt-5.5",
    reasoning: "high",
    serviceTier: "fast",
    handoff: "",
    ...overrides,
  };
}

function turn(id: string): ThreadTurn {
  return {
    id,
    appTurnId: id,
    appTurnIndex: 1,
    appTurnCount: 1,
    index: 1,
    author: "Codex",
    body: id,
  };
}

test("preserves the selected fork point and handoff across active-session refreshes", () => {
  const current = forkState({
    selectedTurnId: "turn-1",
    model: "gpt-5.4",
    handoff: "approved handoff",
  });
  const refreshed = forkState({
    selectedTurnId: "turn-2",
    model: "gpt-5.5",
    handoff: "",
  });

  const merged = mergeRefreshedForkState(current, refreshed, [turn("turn-1"), turn("turn-2")]);

  assert.equal(merged.selectedTurnId, "turn-1");
  assert.equal(merged.model, "gpt-5.4");
  assert.equal(merged.handoff, "approved handoff");
});

test("falls back to the refreshed fork point when the selected turn disappears", () => {
  const current = forkState({
    selectedTurnId: "turn-1",
    handoff: "stale handoff",
  });
  const refreshed = forkState({
    selectedTurnId: "turn-2",
  });

  const merged = mergeRefreshedForkState(current, refreshed, [turn("turn-2")]);

  assert.equal(merged.selectedTurnId, "turn-2");
  assert.equal(merged.handoff, "");
});

test("uses refreshed state when the active session changes", () => {
  const current = forkState({
    sessionId: "old-session",
    selectedTurnId: "old-turn",
    handoff: "old handoff",
  });
  const refreshed = forkState({
    sessionId: "new-session",
    selectedTurnId: "new-turn",
  });

  const merged = mergeRefreshedForkState(current, refreshed, [turn("new-turn")]);

  assert.deepEqual(merged, refreshed);
});
