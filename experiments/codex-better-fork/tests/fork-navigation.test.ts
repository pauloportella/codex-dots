import assert from "node:assert/strict";
import { test } from "node:test";
import type { ForkTransaction } from "../src/bridge.ts";
import { requireForkTargetSessionId } from "../src/fork-navigation.ts";

function transaction(overrides: Partial<ForkTransaction> = {}): ForkTransaction {
  return {
    id: "source-session:target-session",
    sourceSessionId: "source-session",
    baseTurnId: "turn-1",
    status: "succeeded",
    targetSessionId: "target-session",
    targetThreadUrl: "codex://threads/target-session",
    rollbackTurns: 0,
    ...overrides,
  };
}

test("returns the fork target session id for successful fork navigation", () => {
  assert.equal(requireForkTargetSessionId(transaction()), "target-session");
});

test("rejects fork navigation when a succeeded transaction has no target session id", () => {
  assert.throws(
    () => requireForkTargetSessionId(transaction({ targetSessionId: null })),
    /target session id/,
  );
});

test("rejects fork navigation from failed transactions", () => {
  assert.throws(
    () => requireForkTargetSessionId(transaction({ status: "failed" })),
    /failed transaction/,
  );
});
