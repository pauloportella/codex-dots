use std::{
    collections::HashMap,
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    thread,
};

use crate::app_server::{DeeplinkRequest, ForkTransactionRequest, HandoffPreviewRequest};
use crate::state::NativeAppState;

const BRIDGE_ADDR: &str = "127.0.0.1:1421";
const DEV_ORIGIN: &str = "http://localhost:1420";

pub fn start(state: NativeAppState) -> Result<(), String> {
    let listener = TcpListener::bind(BRIDGE_ADDR)
        .map_err(|error| format!("failed to bind HTTP bridge on {BRIDGE_ADDR}: {error}"))?;

    thread::Builder::new()
        .name("codex-http-bridge".to_string())
        .spawn(move || {
            for stream in listener.incoming() {
                match stream {
                    Ok(stream) => {
                        let state = state.clone();
                        thread::spawn(move || {
                            if let Err(error) = handle_connection(stream, state) {
                                eprintln!("HTTP bridge request failed: {error}");
                            }
                        });
                    }
                    Err(error) => eprintln!("HTTP bridge accept failed: {error}"),
                }
            }
        })
        .map_err(|error| format!("failed to spawn HTTP bridge thread: {error}"))?;

    Ok(())
}

fn handle_connection(mut stream: TcpStream, state: NativeAppState) -> Result<(), String> {
    let request = read_request(&mut stream)?;
    let response = route_request(&request, &state);
    stream
        .write_all(response.as_bytes())
        .map_err(|error| format!("failed to write HTTP response: {error}"))?;
    stream
        .flush()
        .map_err(|error| format!("failed to flush HTTP response: {error}"))
}

fn read_request(stream: &mut TcpStream) -> Result<HttpRequest, String> {
    let mut buffer = [0_u8; 1024];
    let mut bytes = Vec::new();

    loop {
        let count = stream
            .read(&mut buffer)
            .map_err(|error| format!("failed to read HTTP request: {error}"))?;
        if count == 0 {
            break;
        }
        bytes.extend_from_slice(&buffer[..count]);
        if bytes.windows(4).any(|window| window == b"\r\n\r\n") {
            break;
        }
        if bytes.len() > 16 * 1024 {
            return Err("HTTP request headers exceeded 16 KiB".to_string());
        }
    }

    let header_end = bytes
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .map(|position| position + 4)
        .ok_or_else(|| "HTTP request did not include header terminator".to_string())?;
    let headers = String::from_utf8(bytes[..header_end].to_vec())
        .map_err(|error| format!("HTTP request headers were not UTF-8: {error}"))?;
    let content_length = headers
        .lines()
        .find_map(|line| {
            let (name, value) = line.split_once(':')?;
            name.eq_ignore_ascii_case("content-length")
                .then(|| value.trim().parse::<usize>().ok())
                .flatten()
        })
        .unwrap_or(0);
    if content_length > 64 * 1024 {
        return Err("HTTP request body exceeded 64 KiB".to_string());
    }

    let expected_len = header_end + content_length;
    while bytes.len() < expected_len {
        let count = stream
            .read(&mut buffer)
            .map_err(|error| format!("failed to read HTTP request body: {error}"))?;
        if count == 0 {
            break;
        }
        bytes.extend_from_slice(&buffer[..count]);
    }
    if bytes.len() < expected_len {
        return Err("HTTP request body ended before Content-Length was satisfied".to_string());
    }

    let body = String::from_utf8(bytes[header_end..expected_len].to_vec())
        .map_err(|error| format!("HTTP request body was not UTF-8: {error}"))?;

    let request = headers;
    let Some(request_line) = request.lines().next() else {
        return Err("HTTP request did not include a request line".to_string());
    };
    let mut parts = request_line.split_whitespace();
    let method = parts
        .next()
        .ok_or_else(|| "HTTP request did not include a method".to_string())?
        .to_string();
    let target = parts
        .next()
        .ok_or_else(|| "HTTP request did not include a target".to_string())?
        .to_string();

    Ok(HttpRequest {
        method,
        target,
        body,
    })
}

fn route_request(request: &HttpRequest, state: &NativeAppState) -> String {
    if request.method == "OPTIONS" {
        return empty_response(204, "No Content");
    }

    if request.method != "GET" && request.method != "POST" {
        return json_error(405, "Method Not Allowed", "method not allowed");
    }

    let (path, query) = split_target(&request.target);

    match path {
        "/healthz" => match state.app_server().ensure_started() {
            Ok(()) => json_response(200, "OK", &serde_json::json!({ "ok": true })),
            Err(error) => json_error(502, "Bad Gateway", &error),
        },
        "/api/sessions" => match state.app_server().list_sessions() {
            Ok(sessions) => json_response(200, "OK", &sessions),
            Err(error) => json_error(502, "Bad Gateway", &error),
        },
        "/api/thread" => {
            if request.method != "GET" {
                return json_error(405, "Method Not Allowed", "method not allowed");
            }
            let params = parse_query(query);
            let Some(session_id) = params.get("sessionId").filter(|value| !value.is_empty()) else {
                return json_error(
                    400,
                    "Bad Request",
                    "missing required query param `sessionId`",
                );
            };

            match state.app_server().read_thread_details(session_id.clone()) {
                Ok(thread) => json_response(200, "OK", &thread),
                Err(error) => json_error(502, "Bad Gateway", &error),
            }
        }
        "/api/fork" => {
            if request.method != "POST" {
                return json_error(405, "Method Not Allowed", "method not allowed");
            }
            let fork_request: ForkTransactionRequest = match serde_json::from_str(&request.body) {
                Ok(request) => request,
                Err(error) => {
                    return json_error(
                        400,
                        "Bad Request",
                        &format!("failed to decode fork request body: {error}"),
                    );
                }
            };

            match state.app_server().start_fork_transaction(fork_request) {
                Ok(transaction) => json_response(200, "OK", &transaction),
                Err(error) => json_error(502, "Bad Gateway", &error),
            }
        }
        "/api/handoff" => {
            if request.method != "POST" {
                return json_error(405, "Method Not Allowed", "method not allowed");
            }
            let handoff_request: HandoffPreviewRequest = match serde_json::from_str(&request.body) {
                Ok(request) => request,
                Err(error) => {
                    return json_error(
                        400,
                        "Bad Request",
                        &format!("failed to decode handoff request body: {error}"),
                    );
                }
            };

            match state.app_server().generate_handoff_preview(handoff_request) {
                Ok(preview) => json_response(200, "OK", &preview),
                Err(error) => json_error(502, "Bad Gateway", &error),
            }
        }
        "/api/open" => {
            if request.method != "POST" {
                return json_error(405, "Method Not Allowed", "method not allowed");
            }
            let deeplink_request: DeeplinkRequest = match serde_json::from_str(&request.body) {
                Ok(request) => request,
                Err(error) => {
                    return json_error(
                        400,
                        "Bad Request",
                        &format!("failed to decode open request body: {error}"),
                    );
                }
            };

            match state.app_server().deeplink_for_session(deeplink_request) {
                Ok(result) => json_response(200, "OK", &result),
                Err(error) => json_error(502, "Bad Gateway", &error),
            }
        }
        _ => json_error(404, "Not Found", "not found"),
    }
}

fn split_target(target: &str) -> (&str, &str) {
    target
        .split_once('?')
        .map_or((target, ""), |(path, query)| (path, query))
}

fn parse_query(query: &str) -> HashMap<String, String> {
    query
        .split('&')
        .filter(|pair| !pair.is_empty())
        .filter_map(|pair| {
            let (key, value) = pair.split_once('=').unwrap_or((pair, ""));
            Some((percent_decode(key)?, percent_decode(value)?))
        })
        .collect()
}

fn percent_decode(value: &str) -> Option<String> {
    let mut bytes = Vec::with_capacity(value.len());
    let mut chars = value.as_bytes().iter().copied();

    while let Some(byte) = chars.next() {
        match byte {
            b'+' => bytes.push(b' '),
            b'%' => {
                let high = chars.next()?;
                let low = chars.next()?;
                bytes.push(hex_pair(high, low)?);
            }
            _ => bytes.push(byte),
        }
    }

    String::from_utf8(bytes).ok()
}

fn hex_pair(high: u8, low: u8) -> Option<u8> {
    Some(hex_digit(high)? * 16 + hex_digit(low)?)
}

fn hex_digit(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

fn json_response<T: serde::Serialize>(status: u16, reason: &str, body: &T) -> String {
    match serde_json::to_string(body) {
        Ok(body) => response(status, reason, "application/json", &body),
        Err(error) => json_error(
            500,
            "Internal Server Error",
            &format!("failed to encode JSON response: {error}"),
        ),
    }
}

fn json_error(status: u16, reason: &str, message: &str) -> String {
    let body = serde_json::json!({ "error": message });
    let body = serde_json::to_string(&body)
        .unwrap_or_else(|_| "{\"error\":\"failed to encode error response\"}".to_string());
    response(status, reason, "application/json", &body)
}

fn empty_response(status: u16, reason: &str) -> String {
    response(status, reason, "text/plain", "")
}

fn response(status: u16, reason: &str, content_type: &str, body: &str) -> String {
    format!(
        "HTTP/1.1 {status} {reason}\r\n\
         Content-Type: {content_type}\r\n\
         Content-Length: {}\r\n\
         Access-Control-Allow-Origin: {DEV_ORIGIN}\r\n\
         Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n\
         Access-Control-Allow-Headers: Content-Type\r\n\
         Connection: close\r\n\
         \r\n\
         {body}",
        body.len()
    )
}

struct HttpRequest {
    method: String,
    target: String,
    body: String,
}
