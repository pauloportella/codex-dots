use serde::{Deserialize, Serialize};
use std::{
    env,
    io::{BufRead, BufReader, Write},
    process::{Child, ChildStdin, ChildStdout, Command, Stdio},
    sync::{Arc, Mutex},
};

const HANDOFF_GENERATION_MODEL: &str = "gpt-5.5";
const HANDOFF_GENERATION_REASONING_EFFORT: &str = "high";

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionSummary {
    pub id: String,
    pub title: Option<String>,
    pub cwd: Option<String>,
    pub latest_activity_ms: Option<u64>,
    pub status: SessionStatus,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum SessionStatus {
    Idle,
    Running,
    Unknown,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ThreadTurn {
    pub id: String,
    pub app_turn_id: String,
    pub app_turn_index: u32,
    pub app_turn_count: u32,
    pub role: String,
    pub summary: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ThreadDetails {
    pub session_id: String,
    pub title: Option<String>,
    pub model: String,
    pub model_provider: String,
    pub service_tier: Option<String>,
    pub reasoning_effort: Option<String>,
    pub turns: Vec<ThreadTurn>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ForkTransactionRequest {
    pub source_session_id: String,
    pub base_turn_id: String,
    pub base_turn_index: u32,
    pub turn_count: u32,
    pub name: Option<String>,
    pub model: Option<String>,
    pub reasoning_effort: Option<String>,
    pub service_tier: Option<String>,
    pub handoff: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ForkTransaction {
    pub id: String,
    pub source_session_id: String,
    pub base_turn_id: String,
    pub status: ForkTransactionStatus,
    pub target_session_id: Option<String>,
    pub target_thread_url: Option<String>,
    pub rollback_turns: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum ForkTransactionStatus {
    Succeeded,
    Failed,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HandoffPreviewRequest {
    pub source_session_id: String,
    pub base_turn_id: String,
    pub base_turn_index: u32,
    pub turn_count: u32,
    pub model: Option<String>,
    pub reasoning_effort: Option<String>,
    pub service_tier: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HandoffPreview {
    pub source_session_id: String,
    pub base_turn_id: String,
    pub base_turn_index: u32,
    pub preview_thread_id: String,
    pub text: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DeeplinkRequest {
    pub session_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DeeplinkResult {
    pub url: String,
    pub opened: bool,
}

#[derive(Debug, Clone)]
pub struct AppServerConfig {
    pub command: String,
    pub args: Vec<String>,
}

impl Default for AppServerConfig {
    fn default() -> Self {
        Self {
            command: "codex".to_string(),
            args: vec!["app-server".to_string()],
        }
    }
}

#[derive(Debug)]
pub struct AppServerProcess {
    config: AppServerConfig,
    child: Option<Child>,
    stdin: Option<ChildStdin>,
    stdout: Option<BufReader<ChildStdout>>,
    next_request_id: i64,
    initialized: bool,
}

impl AppServerProcess {
    pub fn new(config: AppServerConfig) -> Self {
        Self {
            config,
            child: None,
            stdin: None,
            stdout: None,
            next_request_id: 0,
            initialized: false,
        }
    }

    pub fn ensure_started(&mut self) -> Result<(), String> {
        if self.initialized {
            return Ok(());
        }

        let shell = env::var("SHELL").unwrap_or_else(|_| "/bin/zsh".to_string());
        let app_server_command = shell_command_for_app_server(&self.config);
        let mut command = Command::new(shell);
        command
            .args(["-lc", app_server_command.as_str()])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());

        let mut child = command
            .spawn()
            .map_err(|error| format!("failed to spawn app-server over stdio: {error}"))?;

        self.stdin = Some(
            child
                .stdin
                .take()
                .ok_or_else(|| "failed to open app-server stdin".to_string())?,
        );
        self.stdout =
            Some(BufReader::new(child.stdout.take().ok_or_else(|| {
                "failed to open app-server stdout".to_string()
            })?));
        self.child = Some(child);
        self.initialize()?;
        Ok(())
    }

    fn initialize(&mut self) -> Result<(), String> {
        let initialize_id = self.next_request_id();
        self.send_message(serde_json::json!({
            "method": "initialize",
            "id": initialize_id,
            "params": {
                "clientInfo": {
                    "name": "codex_better_fork",
                    "title": "codex-better-fork",
                    "version": "0.1.0",
                }
            }
        }))?;
        self.read_response(initialize_id, "initialize")?;
        self.send_message(serde_json::json!({
            "method": "initialized"
        }))?;
        self.initialized = true;
        Ok(())
    }

    fn request(
        &mut self,
        method: &str,
        params: serde_json::Value,
    ) -> Result<serde_json::Value, String> {
        self.ensure_started()?;
        let id = self.next_request_id();
        self.send_message(serde_json::json!({
            "method": method,
            "id": id,
            "params": params
        }))?;
        self.read_response(id, method)
    }

    fn next_request_id(&mut self) -> i64 {
        let id = self.next_request_id;
        self.next_request_id += 1;
        id
    }

    fn send_message(&mut self, message: serde_json::Value) -> Result<(), String> {
        let stdin = self
            .stdin
            .as_mut()
            .ok_or_else(|| "app-server stdin is not open".to_string())?;
        serde_json::to_writer(&mut *stdin, &message)
            .map_err(|error| format!("failed to encode JSON-RPC message: {error}"))?;
        stdin
            .write_all(b"\n")
            .map_err(|error| format!("failed to write JSON-RPC newline: {error}"))?;
        stdin
            .flush()
            .map_err(|error| format!("failed to flush JSON-RPC message: {error}"))
    }

    fn read_response(&mut self, id: i64, method: &str) -> Result<serde_json::Value, String> {
        loop {
            let message = self.read_message(method)?;

            if message.get("method").is_some() && message.get("id").is_none() {
                continue;
            }

            let Some(message_id) = message.get("id").and_then(serde_json::Value::as_i64) else {
                return Err(format!(
                    "app-server sent unexpected JSON-RPC message while waiting for `{method}`: {message}"
                ));
            };
            if message_id != id {
                return Err(format!(
                    "app-server sent response id {message_id} while waiting for id {id} from `{method}`"
                ));
            }

            if let Some(error) = message.get("error") {
                let message = error
                    .get("message")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or("app-server JSON-RPC error");
                return Err(message.to_string());
            }

            return message.get("result").cloned().ok_or_else(|| {
                format!("app-server response to `{method}` did not include result")
            });
        }
    }

    fn read_message(&mut self, context: &str) -> Result<serde_json::Value, String> {
        let mut line = String::new();
        let bytes_read = self
            .stdout
            .as_mut()
            .ok_or_else(|| "app-server stdout is not open".to_string())?
            .read_line(&mut line)
            .map_err(|error| format!("failed to read app-server response: {error}"))?;
        if bytes_read == 0 {
            return Err(format!(
                "app-server closed stdout while waiting for `{context}`"
            ));
        }

        serde_json::from_str(line.trim())
            .map_err(|error| format!("failed to decode app-server JSON-RPC response: {error}"))
    }

    fn collect_agent_message_until_turn_completed(
        &mut self,
        thread_id: &str,
        turn_id: &str,
    ) -> Result<String, String> {
        let mut agent_text = String::new();

        loop {
            let message = self.read_message("turn/completed")?;
            let Some(method) = message.get("method").and_then(serde_json::Value::as_str) else {
                continue;
            };
            let params = message
                .get("params")
                .cloned()
                .unwrap_or(serde_json::Value::Null);

            match method {
                "item/agentMessage/delta" => {
                    if params.get("threadId").and_then(serde_json::Value::as_str) == Some(thread_id)
                        && params.get("turnId").and_then(serde_json::Value::as_str) == Some(turn_id)
                    {
                        if let Some(delta) = params.get("delta").and_then(serde_json::Value::as_str)
                        {
                            agent_text.push_str(delta);
                        }
                    }
                }
                "turn/completed" => {
                    if params.get("threadId").and_then(serde_json::Value::as_str) != Some(thread_id)
                    {
                        continue;
                    }
                    let Some(turn) = params.get("turn") else {
                        return Err("turn/completed notification did not include turn".to_string());
                    };
                    if turn.get("id").and_then(serde_json::Value::as_str) != Some(turn_id) {
                        continue;
                    }
                    if turn.get("status").and_then(serde_json::Value::as_str) == Some("failed") {
                        let message = turn
                            .get("error")
                            .and_then(|error| error.get("message"))
                            .and_then(serde_json::Value::as_str)
                            .unwrap_or("turn failed without an error message");
                        return Err(message.to_string());
                    }
                    return Ok(agent_text);
                }
                "error" => {
                    let error = params
                        .get("message")
                        .and_then(serde_json::Value::as_str)
                        .unwrap_or("app-server error notification");
                    return Err(error.to_string());
                }
                _ => {}
            }
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ThreadListResponse {
    data: Vec<AppThread>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ThreadResumeResponse {
    thread: AppThread,
    model: String,
    model_provider: String,
    service_tier: Option<String>,
    reasoning_effort: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ThreadForkResponse {
    thread: AppThread,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ThreadStartResponse {
    thread: AppThread,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ThreadRollbackResponse {
    thread: AppThread,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct TurnStartResponse {
    turn: AppTurnResponse,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct AppTurnResponse {
    id: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct AppThread {
    id: String,
    preview: String,
    cwd: serde_json::Value,
    updated_at: Option<f64>,
    status: serde_json::Value,
    name: Option<String>,
    #[serde(default)]
    turns: Vec<AppTurn>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct AppTurn {
    id: String,
    status: serde_json::Value,
    #[serde(default)]
    items: Vec<serde_json::Value>,
}

#[derive(Debug, Clone, PartialEq)]
struct SelectedUserBoundary {
    app_turn_index: u32,
}

#[derive(Debug, Clone)]
pub struct AppServerClient {
    process: Arc<Mutex<AppServerProcess>>,
}

impl AppServerClient {
    pub fn new(process: AppServerProcess) -> Self {
        Self {
            process: Arc::new(Mutex::new(process)),
        }
    }

    pub fn ensure_started(&self) -> Result<(), String> {
        self.process
            .lock()
            .map_err(|_| "app-server process lock poisoned".to_string())?
            .ensure_started()
    }

    pub fn list_sessions(&self) -> Result<Vec<SessionSummary>, String> {
        let mut process = self
            .process
            .lock()
            .map_err(|_| "app-server process lock poisoned".to_string())?;
        let result = process.request(
            "thread/list",
            serde_json::json!({
                "limit": 50,
                "sortKey": "updated_at",
                "sortDirection": "desc"
            }),
        )?;
        let response: ThreadListResponse = serde_json::from_value(result)
            .map_err(|error| format!("failed to decode thread/list response: {error}"))?;

        Ok(response
            .data
            .into_iter()
            .map(SessionSummary::from)
            .collect())
    }

    pub fn read_thread_details(&self, session_id: String) -> Result<ThreadDetails, String> {
        let mut process = self
            .process
            .lock()
            .map_err(|_| "app-server process lock poisoned".to_string())?;
        let result = process.request(
            "thread/resume",
            serde_json::json!({
                "threadId": session_id,
                "excludeTurns": false
            }),
        )?;
        let response: ThreadResumeResponse = serde_json::from_value(result)
            .map_err(|error| format!("failed to decode thread/resume response: {error}"))?;
        let title = thread_title(&response.thread);

        Ok(ThreadDetails {
            session_id: response.thread.id,
            title,
            model: response.model,
            model_provider: response.model_provider,
            service_tier: response.service_tier,
            reasoning_effort: response.reasoning_effort,
            turns: thread_turns_from_app_turns(response.thread.turns),
        })
    }

    pub fn start_fork_transaction(
        &self,
        request: ForkTransactionRequest,
    ) -> Result<ForkTransaction, String> {
        if request.base_turn_index == 0 {
            return Err("baseTurnIndex must be >= 1".to_string());
        }
        if request.turn_count == 0 {
            return Err("turnCount must be >= 1".to_string());
        }
        if request.base_turn_index > request.turn_count {
            return Err(format!(
                "baseTurnIndex {} is past turnCount {}",
                request.base_turn_index, request.turn_count
            ));
        }

        let mut process = self
            .process
            .lock()
            .map_err(|_| "app-server process lock poisoned".to_string())?;

        let source_result = process.request(
            "thread/resume",
            serde_json::json!({
                "threadId": request.source_session_id,
                "excludeTurns": false
            }),
        )?;
        let source_response: ThreadResumeResponse = serde_json::from_value(source_result)
            .map_err(|error| format!("failed to decode thread/resume response: {error}"))?;
        let selected_boundary =
            selected_user_boundary(&source_response.thread.turns, &request.base_turn_id)?;
        let rollback_turns = rollback_turns_to_before_selected_user(
            source_response.thread.turns.len() as u32,
            selected_boundary.app_turn_index,
        )?;

        let target_session_id = fork_thread_for_selected_boundary(
            &mut process,
            &request.source_session_id,
            request.model.as_deref(),
            request.reasoning_effort.as_deref(),
            request.service_tier.as_deref(),
            rollback_turns,
            false,
        )?;

        if let Some(handoff) = request
            .handoff
            .as_deref()
            .filter(|value| !value.trim().is_empty())
        {
            let turn_result = process.request(
                "turn/start",
                serde_json::json!({
                    "threadId": target_session_id,
                    "input": [{
                        "type": "text",
                        "text": handoff
                    }],
                    "model": request.model,
                    "effort": request.reasoning_effort,
                    "serviceTier": request.service_tier
                }),
            )?;
            let turn_response: TurnStartResponse = serde_json::from_value(turn_result)
                .map_err(|error| format!("failed to decode turn/start response: {error}"))?;
            if turn_response.turn.id.is_empty() {
                return Err(
                    "turn/start accepted the handoff but returned an empty turn id".to_string(),
                );
            }
        }

        let transaction_id = format!("{}:{target_session_id}", request.source_session_id);

        Ok(ForkTransaction {
            id: transaction_id,
            source_session_id: request.source_session_id,
            base_turn_id: request.base_turn_id,
            status: ForkTransactionStatus::Succeeded,
            target_session_id: Some(target_session_id.clone()),
            target_thread_url: Some(format!("codex://threads/{target_session_id}")),
            rollback_turns,
        })
    }

    pub fn deeplink_for_session(&self, request: DeeplinkRequest) -> Result<DeeplinkResult, String> {
        let url = format!("codex://threads/{}", request.session_id);
        open_url(&url)?;
        Ok(DeeplinkResult { url, opened: true })
    }

    pub fn generate_handoff_preview(
        &self,
        request: HandoffPreviewRequest,
    ) -> Result<HandoffPreview, String> {
        if request.base_turn_index == 0 {
            return Err("baseTurnIndex must be >= 1".to_string());
        }
        if request.turn_count == 0 {
            return Err("turnCount must be >= 1".to_string());
        }
        if request.base_turn_index > request.turn_count {
            return Err(format!(
                "baseTurnIndex {} is past turnCount {}",
                request.base_turn_index, request.turn_count
            ));
        }

        let mut process = self
            .process
            .lock()
            .map_err(|_| "app-server process lock poisoned".to_string())?;
        let result = process.request(
            "thread/resume",
            serde_json::json!({
                "threadId": request.source_session_id,
                "excludeTurns": false
            }),
        )?;
        let response: ThreadResumeResponse = serde_json::from_value(result)
            .map_err(|error| format!("failed to decode thread/resume response: {error}"))?;
        let handoff_inputs = handoff_inputs_from_selected_user_to_end(
            &response.thread.turns,
            &request.base_turn_id,
        )?;

        let thread_start_params = serde_json::json!({
            "model": HANDOFF_GENERATION_MODEL,
            "config": {
                "model_reasoning_effort": HANDOFF_GENERATION_REASONING_EFFORT
            },
            "ephemeral": true
        });
        let start_result = process.request("thread/start", thread_start_params)?;
        let start_response: ThreadStartResponse = serde_json::from_value(start_result)
            .map_err(|error| format!("failed to decode thread/start response: {error}"))?;
        let preview_thread_id = start_response.thread.id;

        let prompt = handoff_generation_prompt(
            handoff_inputs.selected_app_turn_index,
            response.thread.id.as_str(),
            handoff_inputs.transcript_from_selected_to_end.as_str(),
        )?;
        let turn_result = process.request(
            "turn/start",
            serde_json::json!({
                "threadId": preview_thread_id,
                "input": [{
                    "type": "text",
                    "text": prompt
                }],
                "model": HANDOFF_GENERATION_MODEL,
                "effort": HANDOFF_GENERATION_REASONING_EFFORT
            }),
        )?;
        let turn_response: TurnStartResponse = serde_json::from_value(turn_result)
            .map_err(|error| format!("failed to decode turn/start response: {error}"))?;
        let text = process.collect_agent_message_until_turn_completed(
            &preview_thread_id,
            &turn_response.turn.id,
        )?;
        let text = text.trim().to_string();
        if text.is_empty() {
            return Err("app-server generated an empty handoff preview".to_string());
        }

        Ok(HandoffPreview {
            source_session_id: request.source_session_id,
            base_turn_id: request.base_turn_id,
            base_turn_index: handoff_inputs.selected_app_turn_index,
            preview_thread_id,
            text,
        })
    }
}

impl From<AppThread> for SessionSummary {
    fn from(thread: AppThread) -> Self {
        let title = thread_title(&thread);
        let cwd = value_as_string(&thread.cwd);
        let status = session_status(&thread.status);

        Self {
            id: thread.id,
            title,
            cwd,
            latest_activity_ms: thread.updated_at.map(seconds_to_millis),
            status,
        }
    }
}

fn thread_title(thread: &AppThread) -> Option<String> {
    thread
        .name
        .clone()
        .or_else(|| (!thread.preview.is_empty()).then(|| thread.preview.clone()))
}

fn value_as_string(value: &serde_json::Value) -> Option<String> {
    value.as_str().map(str::to_string)
}

fn seconds_to_millis(seconds: f64) -> u64 {
    (seconds * 1000.0).max(0.0) as u64
}

fn session_status(status: &serde_json::Value) -> SessionStatus {
    match status.get("type").and_then(serde_json::Value::as_str) {
        Some("active") => SessionStatus::Running,
        Some("idle") => SessionStatus::Idle,
        Some("notLoaded") | Some("systemError") => SessionStatus::Unknown,
        _ => SessionStatus::Unknown,
    }
}

fn shell_command_for_app_server(config: &AppServerConfig) -> String {
    format!(
        "exec {}",
        std::iter::once(config.command.as_str())
            .chain(config.args.iter().map(String::as_str))
            .collect::<Vec<_>>()
            .join(" ")
    )
}

fn fork_thread_for_selected_boundary(
    process: &mut AppServerProcess,
    source_session_id: &str,
    model: Option<&str>,
    reasoning_effort: Option<&str>,
    service_tier: Option<&str>,
    rollback_turns: u32,
    ephemeral: bool,
) -> Result<String, String> {
    let mut fork_params = serde_json::json!({
        "threadId": source_session_id,
        "excludeTurns": false,
        "ephemeral": ephemeral
    });

    if let Some(model) = model.filter(|value| !value.is_empty()) {
        fork_params["model"] = serde_json::json!(model);
    }
    if let Some(service_tier) = service_tier.filter(|value| !value.is_empty()) {
        fork_params["serviceTier"] = serde_json::json!(service_tier);
    }
    if let Some(reasoning_effort) = reasoning_effort.filter(|value| !value.is_empty()) {
        fork_params["config"] = serde_json::json!({
            "model_reasoning_effort": reasoning_effort
        });
    }

    let fork_result = process.request("thread/fork", fork_params)?;
    let fork_response: ThreadForkResponse = serde_json::from_value(fork_result)
        .map_err(|error| format!("failed to decode thread/fork response: {error}"))?;
    let target_session_id = fork_response.thread.id;

    if rollback_turns > 0 {
        let rollback_result = process.request(
            "thread/rollback",
            serde_json::json!({
                "threadId": target_session_id,
                "numTurns": rollback_turns
            }),
        )?;
        let rollback_response: ThreadRollbackResponse = serde_json::from_value(rollback_result)
            .map_err(|error| format!("failed to decode thread/rollback response: {error}"))?;
        if rollback_response.thread.id != target_session_id {
            return Err(format!(
                "thread/rollback returned thread {} while rolling back {}",
                rollback_response.thread.id, target_session_id
            ));
        }
    }

    Ok(target_session_id)
}

#[derive(Debug)]
struct HandoffInputs {
    selected_app_turn_index: u32,
    transcript_from_selected_to_end: String,
}

fn selected_user_boundary(
    turns: &[AppTurn],
    selected_item_id: &str,
) -> Result<SelectedUserBoundary, String> {
    let inputs = handoff_inputs_from_selected_user_to_end(turns, selected_item_id)?;
    Ok(SelectedUserBoundary {
        app_turn_index: inputs.selected_app_turn_index,
    })
}

fn handoff_inputs_from_selected_user_to_end(
    turns: &[AppTurn],
    selected_item_id: &str,
) -> Result<HandoffInputs, String> {
    let mut transcript = String::new();
    let mut selected_app_turn_index = None;

    for (turn_index, turn) in turns.iter().enumerate() {
        let app_turn_index = turn_index as u32 + 1;

        if selected_app_turn_index.is_some() {
            append_projected_app_turn_transcript(&mut transcript, app_turn_index, turn, None)?;
            continue;
        }

        let found = append_projected_app_turn_transcript(
            &mut transcript,
            app_turn_index,
            turn,
            Some(selected_item_id),
        )?;
        if found {
            selected_app_turn_index = Some(app_turn_index);
        }
    }

    let Some(selected_app_turn_index) = selected_app_turn_index else {
        return Err(format!(
            "selected fork boundary item `{selected_item_id}` was not found in the source thread"
        ));
    };

    if transcript.trim().is_empty() {
        return Err("selected user message produced an empty handoff transcript".to_string());
    }

    Ok(HandoffInputs {
        selected_app_turn_index,
        transcript_from_selected_to_end: transcript,
    })
}

fn append_projected_app_turn_transcript(
    transcript: &mut String,
    app_turn_index: u32,
    turn: &AppTurn,
    start_item_id: Option<&str>,
) -> Result<bool, String> {
    let mut started = start_item_id.is_none();
    let mut found_start = start_item_id.is_none();

    for item in &turn.items {
        if !started && item_id(item).as_deref() == start_item_id {
            if user_message_text(item).is_none() {
                return Err(
                    "fork boundaries must be user messages; select a user message card".to_string(),
                );
            }
            started = true;
            found_start = true;
        }

        if started {
            if let Some(summary) = user_message_text(item) {
                transcript.push_str(&format!(
                    "User (app-server turn {app_turn_index}):\n{summary}\n\n"
                ));
            }
        }
    }

    if started {
        if let Some(summary) = last_agent_message_text(turn) {
            transcript.push_str(&format!(
                "Codex (app-server turn {app_turn_index}):\n{summary}\n\n"
            ));
        }
    }

    Ok(found_start)
}

fn rollback_turns_to_before_selected_user(
    turn_count: u32,
    selected_app_turn_index: u32,
) -> Result<u32, String> {
    if selected_app_turn_index == 0 {
        return Err("selected app-server turn index must be >= 1".to_string());
    }
    if selected_app_turn_index > turn_count {
        return Err(format!(
            "selected user message is in app-server turn {selected_app_turn_index}, but source thread has only {turn_count} turns"
        ));
    }

    Ok(turn_count - selected_app_turn_index + 1)
}

fn handoff_generation_prompt(
    base_turn_index: u32,
    source_thread_id: &str,
    transcript_from_selected_to_end: &str,
) -> Result<String, String> {
    if transcript_from_selected_to_end.trim().is_empty() {
        return Err("cannot generate a handoff prompt from an empty transcript".to_string());
    }

    Ok(format!(
        "Generate a compact handoff prompt for a new Codex fork based on the source-thread transcript from the selected user message through the end of the thread.\n\nSource thread: {source_thread_id}\nSelected app-server turn: {base_turn_index}\n\nRequirements:\n- Return only the handoff prompt text.\n- The handoff is context for a new Codex thread, not permission to begin executing.\n- Summarize the selected user request and all relevant work, findings, decisions, commits, files, validation, and current state represented after it.\n- Preserve concrete ids, commit hashes, URLs, commands, and file paths when they matter.\n- Make clear what has already been done and what the new thread should wait to do next.\n- End with a hard instruction that the new agent's first response must be exactly `Ok` and then wait for the user's next message.\n- Do not include the user's personal name; refer to them as \"the user\" only if needed.\n- Do not include commentary about generating the handoff.\n\nTranscript from selected user message through thread end:\n\n{transcript_from_selected_to_end}"
    ))
}

fn thread_turns_from_app_turns(turns: Vec<AppTurn>) -> Vec<ThreadTurn> {
    let turn_count = turns.len() as u32;
    turns
        .into_iter()
        .enumerate()
        .flat_map(|(index, turn)| thread_turns_from_app_turn(turn, index as u32 + 1, turn_count))
        .collect()
}

fn thread_turns_from_app_turn(
    turn: AppTurn,
    app_turn_index: u32,
    app_turn_count: u32,
) -> Vec<ThreadTurn> {
    let mut turns = Vec::new();
    let last_agent_message = last_agent_message(&turn);

    for (index, item) in turn.items.iter().enumerate() {
        let item_id = item_id(item).unwrap_or_else(|| format!("{}:{index}", turn.id));
        if let Some(summary) = user_message_text(item) {
            turns.push(ThreadTurn {
                id: item_id,
                app_turn_id: turn.id.clone(),
                app_turn_index,
                app_turn_count,
                role: "User".to_string(),
                summary: Some(summary),
            });
        }
    }

    if let Some((item_id, summary)) = last_agent_message {
        turns.push(ThreadTurn {
            id: item_id,
            app_turn_id: turn.id.clone(),
            app_turn_index,
            app_turn_count,
            role: "Codex".to_string(),
            summary: Some(summary),
        });
    }

    if turns.is_empty() {
        turns.push(ThreadTurn {
            id: turn.id.clone(),
            app_turn_id: turn.id.clone(),
            app_turn_index,
            app_turn_count,
            role: "Codex".to_string(),
            summary: turn_status_summary(&turn.status),
        });
    }

    turns
}

fn user_message_text(item: &serde_json::Value) -> Option<String> {
    (item_type(item) == Some("userMessage"))
        .then(|| item.get("content"))
        .flatten()
        .and_then(serde_json::Value::as_array)?
        .iter()
        .find_map(|content| {
            (item_type(content) == Some("text"))
                .then(|| content.get("text"))
                .flatten()
                .and_then(serde_json::Value::as_str)
                .map(str::to_string)
        })
}

fn agent_message_text(item: &serde_json::Value) -> Option<String> {
    (item_type(item) == Some("agentMessage"))
        .then(|| item.get("text"))
        .flatten()
        .and_then(serde_json::Value::as_str)
        .map(str::to_string)
}

fn last_agent_message(turn: &AppTurn) -> Option<(String, String)> {
    turn.items
        .iter()
        .enumerate()
        .rev()
        .find_map(|(index, item)| {
            let summary = agent_message_text(item)?;
            let item_id = item_id(item).unwrap_or_else(|| format!("{}:{index}", turn.id));
            Some((item_id, summary))
        })
}

fn last_agent_message_text(turn: &AppTurn) -> Option<String> {
    last_agent_message(turn).map(|(_, summary)| summary)
}

fn turn_status_summary(status: &serde_json::Value) -> Option<String> {
    status
        .as_str()
        .or_else(|| status.get("type").and_then(serde_json::Value::as_str))
        .map(|status| {
            format!("No renderable user or agent message returned for this turn. Status: {status}")
        })
}

fn item_type(item: &serde_json::Value) -> Option<&str> {
    item.get("type").and_then(serde_json::Value::as_str)
}

fn item_id(item: &serde_json::Value) -> Option<String> {
    item.get("id")
        .and_then(serde_json::Value::as_str)
        .map(str::to_string)
}

fn open_url(url: &str) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    let mut command = {
        let mut command = Command::new("open");
        command.arg(url);
        command
    };

    #[cfg(target_os = "windows")]
    let mut command = {
        let mut command = Command::new("cmd");
        command.args(["/C", "start", "", url]);
        command
    };

    #[cfg(all(unix, not(target_os = "macos")))]
    let mut command = {
        let mut command = Command::new("xdg-open");
        command.arg(url);
        command
    };

    command
        .status()
        .map_err(|error| format!("failed to open {url}: {error}"))
        .and_then(|status| {
            status
                .success()
                .then_some(())
                .ok_or_else(|| format!("failed to open {url}: opener exited with {status}"))
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn app_turn(items: Vec<serde_json::Value>) -> AppTurn {
        AppTurn {
            id: "turn-id".to_string(),
            status: json!("completed"),
            items,
        }
    }

    fn user_item(id: &str, text: &str) -> serde_json::Value {
        json!({
            "id": id,
            "type": "userMessage",
            "content": [{
                "type": "text",
                "text": text
            }]
        })
    }

    fn agent_item(id: &str, text: &str) -> serde_json::Value {
        json!({
            "id": id,
            "type": "agentMessage",
            "text": text
        })
    }

    fn reasoning_item(id: &str, summary: &str) -> serde_json::Value {
        json!({
            "id": id,
            "type": "reasoning",
            "summary": [summary]
        })
    }

    #[test]
    fn thread_turn_projection_keeps_users_and_last_agent_message_only() {
        let projected = thread_turns_from_app_turn(
            app_turn(vec![
                user_item("user-1", "first request"),
                reasoning_item("reasoning-1", "hidden thought"),
                agent_item("agent-1", "intermediate update"),
                user_item("user-2", "follow-up steer"),
                agent_item("agent-2", "final answer"),
            ]),
            1,
            1,
        );

        let roles_and_summaries: Vec<(&str, &str)> = projected
            .iter()
            .map(|turn| (turn.role.as_str(), turn.summary.as_deref().unwrap_or("")))
            .collect();

        assert_eq!(
            roles_and_summaries,
            vec![
                ("User", "first request"),
                ("User", "follow-up steer"),
                ("Codex", "final answer"),
            ]
        );
    }

    #[test]
    fn handoff_inputs_start_at_selected_user_and_continue_to_end() {
        let turns = vec![
            app_turn(vec![
                user_item("user-1", "first request"),
                agent_item("agent-1", "first answer"),
                user_item("user-2", "second request"),
                agent_item("agent-2", "second answer"),
            ]),
            app_turn(vec![
                user_item("user-3", "third request"),
                reasoning_item("reasoning-1", "hidden thought"),
                agent_item("agent-3", "third answer"),
            ]),
        ];

        let inputs = handoff_inputs_from_selected_user_to_end(&turns, "user-2").unwrap();

        assert_eq!(inputs.selected_app_turn_index, 1);
        assert!(!inputs
            .transcript_from_selected_to_end
            .contains("first request"));
        assert!(!inputs
            .transcript_from_selected_to_end
            .contains("first answer"));
        assert!(inputs
            .transcript_from_selected_to_end
            .contains("second request"));
        assert!(inputs
            .transcript_from_selected_to_end
            .contains("second answer"));
        assert!(inputs
            .transcript_from_selected_to_end
            .contains("third request"));
        assert!(inputs
            .transcript_from_selected_to_end
            .contains("third answer"));
        assert!(!inputs
            .transcript_from_selected_to_end
            .contains("hidden thought"));
    }

    #[test]
    fn handoff_inputs_reject_agent_message_boundaries() {
        let turns = vec![app_turn(vec![
            user_item("user-1", "first request"),
            agent_item("agent-1", "first answer"),
        ])];

        let error = handoff_inputs_from_selected_user_to_end(&turns, "agent-1").unwrap_err();

        assert!(error.contains("fork boundaries must be user messages"));
    }

    #[test]
    fn rollback_drops_selected_user_turn_and_newer_turns() {
        assert_eq!(rollback_turns_to_before_selected_user(2, 1).unwrap(), 2);
        assert_eq!(rollback_turns_to_before_selected_user(2, 2).unwrap(), 1);
    }

    #[test]
    fn handoff_prompt_requires_ok_only_acknowledgement() {
        let prompt = handoff_generation_prompt(
            2,
            "source-thread",
            "User (app-server turn 2):\ncontinue from here\n\nCodex (app-server turn 2):\ndone\n\n",
        )
        .unwrap();

        assert!(prompt.contains("first response must be exactly `Ok`"));
        assert!(prompt.contains("Transcript from selected user message through thread end"));
        assert!(prompt.contains("continue from here"));
    }
}
