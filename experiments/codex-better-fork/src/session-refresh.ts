import type { ForkState, ThreadTurn } from "./bridge";

export function mergeRefreshedForkState(
  current: ForkState | null,
  refreshed: ForkState,
  refreshedTurns: ThreadTurn[],
): ForkState {
  if (!current || current.sessionId !== refreshed.sessionId) {
    return refreshed;
  }

  const selectedTurnStillExists = current.selectedTurnId
    ? refreshedTurns.some((turn) => turn.id === current.selectedTurnId)
    : false;

  return {
    ...refreshed,
    selectedTurnId: selectedTurnStillExists ? current.selectedTurnId : refreshed.selectedTurnId,
    model: current.model,
    reasoning: current.reasoning,
    serviceTier: current.serviceTier,
    handoff: selectedTurnStillExists ? current.handoff : "",
  };
}
