mod app_server;
mod commands;
mod http_bridge;
mod state;

pub fn run() {
    let app_state = state::NativeAppState::default();
    http_bridge::start(app_state.clone()).expect("failed to start local HTTP bridge");

    tauri::Builder::default()
        .manage(app_state)
        .invoke_handler(tauri::generate_handler![
            commands::list_sessions,
            commands::read_thread_details,
            commands::start_fork_transaction,
            commands::generate_handoff_preview,
            commands::open_codex_deeplink,
        ])
        .run(tauri::generate_context!())
        .expect("failed to run codex-better-fork");
}
