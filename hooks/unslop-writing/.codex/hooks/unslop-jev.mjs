import { readFileSync, statSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { request as httpsRequest } from 'node:https';

const DEFAULT_ENDPOINT = 'https://api.typesafe.ai/v1/systemone';
const DECISION_KEY = 'codex.unslop';
const MAX_STATE_BYTES = 24_000;
const MAX_RESPONSE_BYTES = 65_536;

function sensitiveField(name) {
  if (name.startsWith('"')) {
    try { name = JSON.parse(name); } catch { /* Non-JSON labels still use the literal name. */ }
  }
  name = name.toLowerCase().replace(/[^a-z0-9]/g, '');
  return /apikey|token|secret|password|passwd|credential|privatekey/.test(name)
    || /^(?:pwd|mysqlpwd|dbpass|databasepass|auth|authorization|proxyauthorization|cookie|setcookie|session|sessionid|csrf|xsrf|(?:priv|auth|service|account|client|db|database)key)$/.test(name);
}

// Read the whole value, including nested containers and escaped delimiters.
// An unfinished sensitive value is masked through EOF rather than leaking its tail.
function valueEnd(text, start) {
  const first = text[start];
  if (!first || /\s/.test(first)) return start;
  if (!'"\'`[{'.includes(first)) {
    return start + (text.slice(start).match(/^(?:\\[\s\S]|[^\s,;`}\]])+/)?.[0].length ?? 0);
  }
  const stack = [];
  let quote = '';
  for (let i = start; i < text.length; i++) {
    const char = text[i];
    if (quote) {
      if (char === '\\') i++;
      else if (char === quote) {
        quote = '';
        if (!stack.length) return i + 1;
      }
    } else if ('"\'`'.includes(char)) quote = char;
    else if (char === '{' || char === '[') stack.push(char === '{' ? '}' : ']');
    else if (char === '}' || char === ']') {
      if (stack.pop() !== char) return text.length;
      if (!stack.length) return i + 1;
    }
  }
  return text.length;
}

function redact(value, key = '') {
  const text = String(value).replace(/\\_/g, '_');
  const spans = [];
  const add = (start, end, marker) => { if (end > start) spans.push({ start, end, marker }); };
  if (key) {
    for (let at = text.indexOf(key); at !== -1; at = text.indexOf(key, at + key.length)) {
      add(at, at + key.length, '[REDACTED]');
    }
  }
  const fields = /("(?:\\[\s\S]|[^"\\])*"|'(?:\\[\s\S]|[^'\\])*'|[A-Za-z_$][\w.$-]*)[ \t]*(?::=|=>|[:=])[ \t]*/g;
  for (let match; (match = fields.exec(text));) {
    if (!sensitiveField(match[1])) continue;
    let start = fields.lastIndex;
    if (/[\r\n]/.test(text[start] ?? '')) {
      const next = start + (text.slice(start).match(/^\s*/)?.[0].length ?? 0);
      if (/^["']/.test(match[1]) || '"\'`[{'.includes(text[next] ?? '\0')) start = next;
    }
    const end = valueEnd(text, start);
    add(start, end, '[REDACTED]');
    fields.lastIndex = Math.max(fields.lastIndex, end);
  }
  // All detectors see original text; a replacement cannot corrupt another match.
  const rules = [
    [/-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)/g, '[REDACTED_PRIVATE_KEY]'],
    [/(\b(?:authorization|proxy-authorization|cookie|set-cookie)[ \t]*[:=][ \t]*)[^\r\n]+/gim, '[REDACTED]', 1],
    [/\b(?:Bearer|Basic)[ \t]+[A-Za-z0-9._~+/=-]+/gi, '[REDACTED_AUTH]'],
    [/\b(?:username|login|u:)[ \t]*:?[ \t]*\S+[ \t]+(?:password|pw|p:)[ \t]*:?[ \t]*[^\r\n]+/gi, '[REDACTED]'],
    [/\b[a-zA-Z][a-zA-Z0-9+.-]*:\/\/[^\s<>"'`\)]+/g, '[URL]'],
    [/[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/g, '[EMAIL]'],
    [/\/(?:Users|home)\/[^/\s"'`<>\)]+/g, '[HOME]'],
    [/\b(?:apikey_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{15,}|gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|AKIA[A-Z0-9]{16}|xox[baprs]-[A-Za-z0-9-]+)\b/g, '[REDACTED_TOKEN]'],
    [/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g, '[REDACTED_JWT]'],
  ];
  for (const [pattern, marker, prefix] of rules) {
    for (const match of text.matchAll(pattern)) {
      add(match.index + (prefix ? match[prefix].length : 0), match.index + match[0].length, marker);
    }
  }
  spans.sort((a, b) => a.start - b.start || b.end - a.end);
  const merged = [];
  for (const span of spans) {
    const previous = merged.at(-1);
    if (previous && span.start < previous.end) previous.end = Math.max(previous.end, span.end);
    else merged.push({ ...span });
  }
  // Generic opaque values are checked only in gaps outside recognized structures.
  const opaque = fragment => fragment.replace(/[A-Za-z0-9_+/=-]{40,}/g, '[REDACTED_OPAQUE_VALUE]');
  const chunks = [];
  let cursor = 0;
  for (const span of merged) {
    chunks.push(opaque(text.slice(cursor, span.start)), span.marker);
    cursor = span.end;
  }
  chunks.push(opaque(text.slice(cursor)));
  return chunks.join('');
}

function loadConfig(env = process.env, envFile = join(homedir(), '.config', 'jev.env')) {
  let endpoint = String(env.JEV_API_URL ?? '').trim();
  let key = String(env.TYPESAFE_API_KEY ?? '').trim() || String(env.JEV_API_KEY ?? '').trim();
  const values = {};
  if (!endpoint || !key) {
    let mode;
    try { mode = statSync(envFile).mode; } catch (error) {
      if (error?.code !== 'ENOENT') throw failure('config_error');
    }
    if (mode !== undefined) {
      if ((mode & 0o077) !== 0) throw failure('insecure_key_file');
      for (const line of readFileSync(envFile, 'utf8').split(/\r?\n/)) {
        const match = line.match(/^\s*([A-Z_]+)\s*=\s*(.*?)\s*$/);
        if (match) values[match[1]] = match[2].replace(/^['"]|['"]$/g, '');
      }
    }
  }
  endpoint ||= String(values.JEV_API_URL ?? '').trim() || DEFAULT_ENDPOINT;
  let url;
  try { url = new URL(endpoint); } catch { throw failure('invalid_endpoint'); }
  if (url.protocol !== 'https:' || url.username || url.password || url.hash) throw failure('invalid_endpoint');
  key ||= String(values.JEV_API_KEY ?? '').trim();
  if (endpoint === DEFAULT_ENDPOINT) {
    if (!key) throw failure('missing_key');
  }
  return { endpoint, key };
}

function failure(code) { return Object.assign(new Error(code), { code }); }

function requestJson(body, endpoint, key, version, deadlineMs, requestImpl = httpsRequest) {
  return new Promise((resolve, reject) => {
    let req;
    let settled = false;
    const settle = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) {
        try { req?.destroy(); } catch {}
        reject(error);
      } else resolve(value);
    };
    const timer = setTimeout(() => settle(failure('deadline')), deadlineMs);
    const headers = { 'Content-Type': 'application/json', 'X-Decision-Key': DECISION_KEY, 'X-Decision-Version': String(version) };
    if (endpoint === DEFAULT_ENDPOINT) headers.Authorization = `Bearer ${key}`;
    const options = { method: 'POST', headers };
    try {
      req = requestImpl(endpoint, options, response => {
        const chunks = [];
        let size = 0;
        response.on('error', () => settle(failure('request_error')));
        response.on('aborted', () => settle(failure('request_error')));
        if (response.statusCode !== 200) return settle(failure('http_error'));
        response.on('data', chunk => {
          if (settled) return;
          const part = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
          size += part.length;
          if (size > MAX_RESPONSE_BYTES) settle(failure('oversized_response'));
          else chunks.push(part);
        });
        response.on('end', () => {
          if (settled) return;
          try { settle(null, JSON.parse(Buffer.concat(chunks).toString('utf8'))); }
          catch { settle(failure('invalid_response')); }
        });
      });
      req.on('error', () => settle(failure('request_error')));
      req.end(JSON.stringify(body));
    } catch { settle(failure('request_error')); }
  });
}

async function classify(state, options = {}) {
  const config = options.endpoint
    ? loadConfig({ JEV_API_URL: options.endpoint, JEV_API_KEY: options.key ?? '' })
    : options.key !== undefined ? { endpoint: DEFAULT_ENDPOINT, key: options.key } : loadConfig();
  const { endpoint, key } = config;
  if (!state || typeof state.text !== 'string' || !state.text.trim()) throw failure('empty_text');
  const writingRequest = typeof state.writing_request === 'string' ? state.writing_request : '';
  const safeState = { writing_request: redact(writingRequest, key), text: redact(state.text, key) };
  const serialized = JSON.stringify(safeState);
  if (Buffer.byteLength(serialized) > MAX_STATE_BYTES) throw failure('oversized_state');
  let question;
  try { question = JSON.parse(readFileSync(new URL('./unslop-question.json', import.meta.url), 'utf8')); } catch { throw failure('missing_question'); }
  const body = { model: 'jev-latest', state: safeState, questions: { needs_revision: question } };
  const version = (createHash('sha256').update(JSON.stringify(question)).digest().readUInt32BE() & 0x7fffffff) || 1;
  const result = await requestJson(body, endpoint, key, version, options.deadlineMs ?? 3000, options.requestImpl ?? httpsRequest);
  const answer = result?.answers?.needs_revision;
  if (answer?.type !== 'noul' || typeof answer.noul !== 'number' || !Number.isFinite(answer.noul) || answer.noul < 0 || answer.noul > 1) throw failure('invalid_response');
  if (typeof result.model !== 'string' || !/^jev[-a-zA-Z0-9.]+$/.test(result.model)) throw failure('invalid_response');
  return answer.noul;
}

export { classify, redact, loadConfig };
