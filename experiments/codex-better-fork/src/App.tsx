import { useEffect, useMemo, useState } from "react";
import {
  ForkData,
  ForkState,
  ForkTransaction,
  ReasoningEffort,
  ServiceTier,
  ThreadSession,
  ThreadTurn,
  generateHandoffPreview,
  loadForkData,
  loadSessions,
  loadThreadDetails,
  openCodexDeeplink,
  startForkTransaction,
} from "./bridge";
import { requireForkTargetSessionId } from "./fork-navigation";
import { mergeRefreshedForkState } from "./session-refresh";

const modelOptions = ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex"];
const reasoningOptions: ReasoningEffort[] = ["minimal", "low", "medium", "high", "xhigh"];
const serviceTierOptions: ServiceTier[] = ["fast", "flex"];
const handoffGenerationModel = "gpt-5.5";
const handoffGenerationReasoning: ReasoningEffort = "high";
const initialSessionCount = 10;
const activeSessionRefreshMs = 3000;

export function App() {
  const [data, setData] = useState<ForkData | null>(null);
  const [forkState, setForkState] = useState<ForkState | null>(null);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [transaction, setTransaction] = useState<ForkTransaction | null>(null);
  const [isForking, setIsForking] = useState(false);
  const [isGeneratingHandoff, setIsGeneratingHandoff] = useState(false);
  const [isHandoffReviewOpen, setIsHandoffReviewOpen] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [showAllSessions, setShowAllSessions] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);

  useEffect(() => {
    void loadForkData()
      .then((nextData) => {
        setData(nextData);
        setForkState(nextData.forkState);
        setActiveSessionId(nextData.forkState?.sessionId ?? null);
        setLoadError(null);
      })
      .catch((error: unknown) => {
        setLoadError(formatError(error));
      });
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    const dark = theme === "dark";
    root.classList.toggle("dark", dark);
    root.classList.toggle("electron-dark", dark);
    root.classList.toggle("light", !dark);
    root.classList.toggle("electron-light", !dark);
  }, [theme]);

  useEffect(() => {
    if (!activeSessionId || isForking || isGeneratingHandoff) {
      return;
    }

    let cancelled = false;

    async function refreshActiveSession() {
      if (!activeSessionId) {
        return;
      }

      const [sessions, details] = await Promise.all([
        loadSessions(),
        loadThreadDetails(activeSessionId),
      ]);

      if (cancelled) {
        return;
      }

      setData((current) =>
        current
          ? {
              ...current,
              sessions,
              turns: details.turns,
              forkThreadUrl: `codex://threads/${activeSessionId}`,
            }
          : current,
      );
      setForkState((current) => mergeRefreshedForkState(current, details.forkState, details.turns));
      setLoadError(null);
    }

    const refreshId = window.setInterval(() => {
      void refreshActiveSession().catch((error: unknown) => {
        if (!cancelled) {
          setLoadError(formatError(error));
        }
      });
    }, activeSessionRefreshMs);

    return () => {
      cancelled = true;
      window.clearInterval(refreshId);
    };
  }, [activeSessionId, isForking, isGeneratingHandoff]);

  const activeSession = useMemo(() => {
    if (!data) {
      return undefined;
    }

    if (activeSessionId) {
      return data.sessions.find((session) => session.id === activeSessionId);
    }

    return data.sessions[0];
  }, [activeSessionId, data?.sessions]);

  const selectedTurn = useMemo(() => {
    return data?.turns.find((turn) => turn.id === forkState?.selectedTurnId);
  }, [data?.turns, forkState?.selectedTurnId]);

  const modelChoices = useMemo(() => {
    const currentModel = forkState?.model;
    if (!currentModel || modelOptions.includes(currentModel)) {
      return modelOptions;
    }

    return [currentModel, ...modelOptions];
  }, [forkState?.model]);

  const visibleSessions = useMemo(() => {
    if (!data || showAllSessions) {
      return data?.sessions ?? [];
    }

    return data.sessions.slice(0, initialSessionCount);
  }, [data, showAllSessions]);

  if (loadError) {
    return (
      <div className="loading-shell error-shell">
        app-server bridge failed: {loadError}
      </div>
    );
  }

  if (!data) {
    return <div className="loading-shell">Loading fork workspace</div>;
  }

  const handoffGenerated = Boolean(forkState?.handoff.trim());
  const canUseForkActions = Boolean(activeSession && selectedTurn);
  const canStartFork = canUseForkActions && handoffGenerated && !isForking && !isGeneratingHandoff;
  const hasForkResult = transaction?.status === "succeeded";

  function updateForkState(patch: Partial<ForkState>) {
    setForkState((current) => (current ? { ...current, ...patch } : current));
  }

  async function selectSession(session: ThreadSession) {
    const details = await loadThreadDetails(session.id);
    setActiveSessionId(session.id);
    setTransaction(null);
    setIsHandoffReviewOpen(false);
    setData((current) =>
      current
        ? {
            ...current,
            turns: details.turns,
            forkThreadUrl: `codex://threads/${session.id}`,
          }
        : current,
    );
    setForkState(details.forkState);
  }

  function selectTurn(turn: ThreadTurn) {
    updateForkState({ selectedTurnId: turn.id, handoff: "" });
    setTransaction(null);
    setIsHandoffReviewOpen(false);
  }

  async function generatePreview() {
    if (!activeSession || !selectedTurn || !forkState) {
      throw new Error("Cannot generate handoff without an active session and selected turn.");
    }
    if (selectedTurn.author !== "User") {
      throw new Error("Fork boundaries must be user messages.");
    }

    setIsGeneratingHandoff(true);
    setTransaction(null);
    setIsHandoffReviewOpen(false);
    updateForkState({ handoff: "" });
    try {
      const preview = await generateHandoffPreview({
        sourceSessionId: activeSession.id,
        baseTurnId: selectedTurn.id,
        baseTurnIndex: selectedTurn.appTurnIndex,
        turnCount: selectedTurn.appTurnCount,
        model: handoffGenerationModel,
        reasoningEffort: handoffGenerationReasoning,
      });
      updateForkState({ handoff: preview.text });
      setIsHandoffReviewOpen(true);
    } catch (error) {
      throw error;
    } finally {
      setIsGeneratingHandoff(false);
    }
  }

  async function runForkTransaction() {
    if (!activeSession || !selectedTurn || !forkState) {
      throw new Error("Cannot fork without an active session and selected turn.");
    }
    if (selectedTurn.author !== "User") {
      throw new Error("Fork boundaries must be user messages.");
    }
    if (!forkState.handoff.trim()) {
      throw new Error("Cannot fork without a generated handoff.");
    }

    setIsForking(true);
    setTransaction(null);
    try {
      const nextTransaction = await startForkTransaction({
        sourceSessionId: activeSession.id,
        baseTurnId: selectedTurn.id,
        baseTurnIndex: selectedTurn.appTurnIndex,
        turnCount: selectedTurn.appTurnCount,
        name: `${activeSession.repo} fork from turn ${selectedTurn.appTurnIndex}`,
        model: forkState.model,
        reasoningEffort: forkState.reasoning,
        serviceTier: forkState.serviceTier,
        handoff: forkState.handoff,
      });
      setTransaction(nextTransaction);
      const targetSessionId = requireForkTargetSessionId(nextTransaction);
      const [sessions, details] = await Promise.all([
        loadSessions(),
        loadThreadDetails(targetSessionId),
      ]);
      setActiveSessionId(targetSessionId);
      setData((current) =>
        current
          ? {
              ...current,
              sessions,
              turns: details.turns,
              forkThreadUrl: nextTransaction.targetThreadUrl ?? `codex://threads/${targetSessionId}`,
            }
          : current,
      );
      setForkState(details.forkState);
      setIsHandoffReviewOpen(false);
      await openCodexDeeplink(targetSessionId);
    } catch (error) {
      throw error;
    } finally {
      setIsForking(false);
    }
  }

  async function openActiveThread() {
    const targetSessionId = transaction?.targetSessionId ?? activeSession?.id;
    if (!targetSessionId) {
      throw new Error("Cannot open a thread without an active session.");
    }

    await openCodexDeeplink(targetSessionId);
  }

  return (
    <div className={`fork-shell ${isSidebarOpen ? "is-sidebar-open" : "is-sidebar-collapsed"}`}>
      {isSidebarOpen ? (
        <aside className="fork-sidebar">
          <div className="titlebar">
            <img
              alt=""
              aria-hidden="true"
              className="app-logo"
              src="/codex-better-fork-logo.png"
            />
            <div>
              <div className="app-title">codex-better-fork</div>
            </div>
            <button
              aria-label="Hide sessions"
              className="sidebar-icon-button"
              onClick={() => setIsSidebarOpen(false)}
              title="Hide sessions"
              type="button"
            >
              <span aria-hidden="true" className="sidebar-hide-icon" />
            </button>
          </div>

          <label className="search-field">
            <span className="sr-only">Search sessions</span>
            <input placeholder="Type to search sessions" />
          </label>

          <section className="sessions-panel" aria-labelledby="latest-sessions-heading">
            <div className="rail-label" id="latest-sessions-heading">
              Latest sessions
            </div>
            <nav className="session-list" aria-label="Sessions">
              {data.sessions.length > 0 ? (
                visibleSessions.map((session) => (
                  <button
                    className={`session-row ${session.id === activeSession?.id ? "is-active" : ""}`}
                    key={session.id}
                    onClick={() => {
                      void selectSession(session).catch((error: unknown) => {
                        setLoadError(formatError(error));
                      });
                    }}
                    title={session.title}
                    type="button"
                  >
                    <span className="session-main">
                      <span className="session-title">{session.title}</span>
                      <span className="session-time">{session.updatedLabel}</span>
                    </span>
                    <span className="session-meta">{session.repo}</span>
                  </button>
                ))
              ) : (
                <div className="empty-state compact">No Codex sessions returned by app-server</div>
              )}
            </nav>
            {!showAllSessions && data.sessions.length > initialSessionCount ? (
              <button
                className="show-more-sessions"
                onClick={() => setShowAllSessions(true)}
                type="button"
              >
                Show more
              </button>
            ) : null}
          </section>
        </aside>
      ) : null}

      {!isSidebarOpen ? (
        <button
          aria-label="Show sessions"
          className="sidebar-icon-button floating-sidebar-toggle"
          onClick={() => setIsSidebarOpen(true)}
          title="Show sessions"
          type="button"
        >
          <span aria-hidden="true" className="sidebar-show-icon" />
        </button>
      ) : null}

      <main className="main-surface fork-main">
        <header className="fork-header app-header-tint">
          <div className="header-leading">
            {!isSidebarOpen ? (
              <img
                alt=""
                aria-hidden="true"
                className="header-app-logo"
                src="/codex-better-fork-logo.png"
              />
            ) : null}
            <div>
              <h1>{activeSession?.title ?? "No session selected"}</h1>
              <div className="header-meta">
                {activeSession?.repo ?? "No Codex sessions returned by app-server"}
              </div>
            </div>
          </div>
          <div className="header-actions">
            <button className="codex-button ghost" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} type="button">
              {theme === "dark" ? "Light" : "Dark"}
            </button>
            <button
              className="codex-button"
              disabled={!canStartFork || isHandoffReviewOpen || hasForkResult}
              onClick={() => {
                setIsHandoffReviewOpen(true);
              }}
              type="button"
            >
              Review Handoff
            </button>
            {data.forkThreadUrl ? (
              <button
                className="codex-button primary"
                onClick={() => {
                  void openActiveThread().catch((error: unknown) => {
                    setLoadError(formatError(error));
                  });
                }}
                type="button"
              >
                Open Thread
              </button>
            ) : (
              <button className="codex-button primary" disabled type="button">
                Open Thread
              </button>
            )}
          </div>
        </header>

        <section className="turns" aria-label="Conversation turns">
          {activeSession ? (
            data.turns.length > 0 ? (
              data.turns.map((turn) => (
                <TurnCard
                  canGenerateHandoff={
                    canUseForkActions && turn.author === "User" && !isGeneratingHandoff && !isForking
                  }
                  handoffGenerated={handoffGenerated}
                  isSelected={turn.id === forkState?.selectedTurnId}
                  key={turn.id}
                  onGenerateHandoff={() => {
                    void generatePreview();
                  }}
                  onSelect={() => selectTurn(turn)}
                  turn={turn}
                />
              ))
            ) : (
              <div className="empty-state">No turns returned by app-server for this session</div>
            )
          ) : (
            <div className="empty-state">No Codex sessions returned by app-server</div>
          )}
        </section>
      </main>

      {isGeneratingHandoff ? (
        <div className="codex-dialog-overlay handoff-modal-overlay" role="presentation">
          <div className="codex-dialog handoff-loading-modal" role="status" aria-live="polite">
            <div className="handoff-modal-eyebrow">Generating handoff</div>
            <h2>Building the fork context</h2>
            <p>
              Codex is reading the selected branch of the thread and writing the handoff preview.
            </p>
            <div className="handoff-loading-bar" aria-hidden="true" />
          </div>
        </div>
      ) : null}

      {isHandoffReviewOpen && handoffGenerated && !hasForkResult ? (
        <div className="codex-dialog-overlay handoff-modal-overlay" role="presentation">
          <div className="codex-dialog handoff-review-modal" role="dialog" aria-modal="true" aria-labelledby="handoff-review-title">
            <div className="handoff-modal-head">
              <div>
                <h2 id="handoff-review-title">Handoff preview</h2>
                <div className="handoff-modal-meta">
                  Fork base: {selectedTurn?.author ?? "No turn"} turn {selectedTurn?.index ?? "-"} in{" "}
                  {activeSession?.repo ?? "No session"}
                </div>
              </div>
              <button
                className="codex-button ghost"
                onClick={() => setIsHandoffReviewOpen(false)}
                type="button"
              >
                Close
              </button>
            </div>
            <textarea
              className="handoff-modal-editor"
              readOnly
              spellCheck={false}
              value={forkState?.handoff ?? ""}
            />
            <div className="handoff-modal-actions">
              <button
                className="codex-button"
                disabled={!canUseForkActions || isForking}
                onClick={() => {
                  void generatePreview();
                }}
                type="button"
              >
                Regenerate
              </button>
              <div className="handoff-modal-primary-actions">
                <ForkSettingsDropdown
                  modelOptions={modelChoices}
                  onModelChange={(model) => updateForkState({ model })}
                  onReasoningChange={(reasoning) =>
                    updateForkState({ reasoning: reasoning as ReasoningEffort })
                  }
                  onServiceTierChange={(serviceTier) =>
                    updateForkState({ serviceTier: serviceTier as ServiceTier })
                  }
                  reasoningOptions={reasoningOptions}
                  serviceTierOptions={serviceTierOptions}
                  state={forkState}
                />
                <button
                  className="codex-button primary"
                  disabled={!canStartFork}
                  onClick={() => {
                    void runForkTransaction();
                  }}
                  type="button"
                >
                  {isForking ? "Forking" : "Fork"}
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function formatError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function TurnCard({
  canGenerateHandoff,
  handoffGenerated,
  isSelected,
  onGenerateHandoff,
  onSelect,
  turn,
}: {
  canGenerateHandoff: boolean;
  handoffGenerated: boolean;
  isSelected: boolean;
  onGenerateHandoff: () => void;
  onSelect: () => void;
  turn: ThreadTurn;
}) {
  const canUseAsForkBoundary = turn.author === "User";

  return (
    <article
      className={`turn ${turn.author === "User" ? "user" : "assistant"} ${isSelected ? "is-selected" : ""}`}
      onClick={() => {
        if (canUseAsForkBoundary) {
          onSelect();
        }
      }}
      onKeyDown={(event) => {
        if (canUseAsForkBoundary && (event.key === "Enter" || event.key === " ")) {
          event.preventDefault();
          onSelect();
        }
      }}
      tabIndex={canUseAsForkBoundary ? 0 : -1}
    >
      <div className="turn-label">
        <span>
          {turn.author} - turn {turn.index}
        </span>
        {isSelected ? <span className="selected-pill">fork base</span> : null}
      </div>
      <p>{turn.body}</p>
      {isSelected ? (
        <>
          <div className="turn-handoff-actions">
            <button
              className="codex-button primary"
              disabled={!canGenerateHandoff}
              onClick={(event) => {
                event.stopPropagation();
                onGenerateHandoff();
              }}
              type="button"
            >
              {handoffGenerated ? "Regenerate Handoff" : "Generate Handoff"}
            </button>
          </div>
        </>
      ) : null}
    </article>
  );
}

function ForkSettingsDropdown({
  modelOptions,
  onModelChange,
  onReasoningChange,
  onServiceTierChange,
  reasoningOptions,
  serviceTierOptions,
  state,
}: {
  modelOptions: readonly string[];
  onModelChange: (value: string) => void;
  onReasoningChange: (value: string) => void;
  onServiceTierChange: (value: string) => void;
  reasoningOptions: readonly ReasoningEffort[];
  serviceTierOptions: readonly ServiceTier[];
  state: ForkState | null;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const model = state?.model ?? modelOptions[0] ?? "model";
  const reasoning = state?.reasoning ?? reasoningOptions[0] ?? "low";
  const serviceTier = state?.serviceTier ?? serviceTierOptions[0] ?? "fast";

  return (
    <div
      className="fork-settings-dropdown"
      onBlur={(event) => {
        const nextTarget = event.relatedTarget;
        if (!(nextTarget instanceof Node) || !event.currentTarget.contains(nextTarget)) {
          setIsOpen(false);
        }
      }}
    >
      <button
        aria-expanded={isOpen}
        aria-haspopup="menu"
        aria-label="New fork settings"
        className="fork-settings-trigger"
        onClick={() => setIsOpen((open) => !open)}
        type="button"
      >
        <span aria-hidden="true" className="fork-settings-bolt" />
        <span>{shortModelName(model)}</span>
        <span className="fork-settings-trigger-muted">{formatReasoning(reasoning)}</span>
        <span aria-hidden="true" className="fork-settings-chevron" />
      </button>
      {isOpen ? (
        <div className="fork-settings-menu" role="menu">
          <div className="fork-settings-menu-label">Intelligence</div>
          {reasoningOptions.map((option) => (
            <button
              className={option === reasoning ? "is-active" : ""}
              key={`reasoning-${option}`}
              onClick={() => {
                onReasoningChange(option);
              }}
              role="menuitemradio"
              aria-checked={option === reasoning}
              type="button"
            >
              <span>{formatReasoning(option)}</span>
              {option === reasoning ? (
                <span aria-hidden="true" className="fork-settings-check" />
              ) : null}
            </button>
          ))}
          <div className="fork-settings-menu-separator" />
          <div className="fork-settings-menu-label">Model</div>
          {modelOptions.map((option) => (
            <button
              className={option === model ? "is-active" : ""}
              key={`model-${option}`}
              onClick={() => {
                onModelChange(option);
              }}
              role="menuitemradio"
              aria-checked={option === model}
              type="button"
            >
              <span>{option}</span>
              {option === model ? <span aria-hidden="true" className="fork-settings-check" /> : null}
            </button>
          ))}
          <div className="fork-settings-menu-separator" />
          <div className="fork-settings-menu-label">Speed</div>
          {serviceTierOptions.map((option) => (
            <button
              className={option === serviceTier ? "is-active" : ""}
              key={`service-tier-${option}`}
              onClick={() => {
                onServiceTierChange(option);
              }}
              role="menuitemradio"
              aria-checked={option === serviceTier}
              type="button"
            >
              <span>{formatServiceTier(option)}</span>
              {option === serviceTier ? (
                <span aria-hidden="true" className="fork-settings-check" />
              ) : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function shortModelName(model: string): string {
  return model.replace(/^gpt-/, "").replace("-codex", "");
}

function formatReasoning(reasoning: ReasoningEffort): string {
  if (reasoning === "xhigh") {
    return "Extra High";
  }

  return reasoning.charAt(0).toUpperCase() + reasoning.slice(1);
}

function formatServiceTier(serviceTier: ServiceTier): string {
  if (serviceTier === "flex") {
    return "Standard";
  }

  return serviceTier.charAt(0).toUpperCase() + serviceTier.slice(1);
}
