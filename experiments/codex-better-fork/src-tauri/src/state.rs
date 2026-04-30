use crate::app_server::{AppServerClient, AppServerConfig, AppServerProcess};

#[derive(Debug, Clone)]
pub struct NativeAppState {
    app_server: AppServerClient,
}

impl Default for NativeAppState {
    fn default() -> Self {
        let process = AppServerProcess::new(AppServerConfig::default());

        Self {
            app_server: AppServerClient::new(process),
        }
    }
}

impl NativeAppState {
    pub fn app_server(&self) -> &AppServerClient {
        &self.app_server
    }
}
