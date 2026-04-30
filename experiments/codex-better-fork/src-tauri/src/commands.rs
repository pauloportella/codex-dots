use tauri::State;

use crate::{
    app_server::{
        DeeplinkRequest, DeeplinkResult, ForkTransaction, ForkTransactionRequest, HandoffPreview,
        HandoffPreviewRequest, SessionSummary, ThreadDetails,
    },
    state::NativeAppState,
};

#[tauri::command]
pub fn list_sessions(state: State<'_, NativeAppState>) -> Result<Vec<SessionSummary>, String> {
    state.app_server().ensure_started()?;
    state.app_server().list_sessions()
}

#[tauri::command]
pub fn read_thread_details(
    state: State<'_, NativeAppState>,
    session_id: String,
) -> Result<ThreadDetails, String> {
    state.app_server().read_thread_details(session_id)
}

#[tauri::command]
pub fn start_fork_transaction(
    state: State<'_, NativeAppState>,
    request: ForkTransactionRequest,
) -> Result<ForkTransaction, String> {
    state.app_server().start_fork_transaction(request)
}

#[tauri::command]
pub fn generate_handoff_preview(
    state: State<'_, NativeAppState>,
    request: HandoffPreviewRequest,
) -> Result<HandoffPreview, String> {
    state.app_server().generate_handoff_preview(request)
}

#[tauri::command]
pub fn open_codex_deeplink(
    state: State<'_, NativeAppState>,
    request: DeeplinkRequest,
) -> Result<DeeplinkResult, String> {
    state.app_server().deeplink_for_session(request)
}
