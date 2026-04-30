import type { ForkTransaction } from "./bridge";

export function requireForkTargetSessionId(transaction: ForkTransaction): string {
  if (transaction.status !== "succeeded") {
    throw new Error("Cannot navigate to a fork from a failed transaction.");
  }

  const targetSessionId = transaction.targetSessionId?.trim();
  if (!targetSessionId) {
    throw new Error("Fork transaction succeeded without a target session id.");
  }

  return targetSessionId;
}
