#!/usr/bin/env python3
"""Nudge an unfinished Codex task once; send only redacted dialogue to Jev."""

import datetime
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import signal
import sys
import time
from collections import deque

THRESHOLD = 0.80
MAX_STATE_BYTES = 64_000
MAX_OPENING_BYTES = 8_000
HISTORY_TIMEOUT = 1
API_TIMEOUT = 3
ENV_FILE = Path.home() / '.config' / 'jev.env'
LOG_DIRECTORY = Path.home() / '.codex' / 'log' / 'jev-stop'
QUESTION_FILE = Path(__file__).with_name('jev-stop-question.json')
CONTINUATION = (
    'Acknowledging the issue does not complete the outstanding task. '
    'Continue the already-authorized work. If progress genuinely requires a user '
    'decision or permission, ask the specific necessary question. '
    'Respect explicit pauses and scope limits.'
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def sensitive_field(name):
    if name.startswith('"'):
        try:
            name = json.loads(name)
        except ValueError:
            pass
    name = re.sub(r'[^a-z0-9]', '', name.lower())
    return bool(
        re.search(r'apikey|token|secret|password|passwd|credential|privatekey', name)
        or re.fullmatch(r'(?:pwd|mysqlpwd|dbpass|databasepass|auth|authorization|proxyauthorization|cookie|setcookie|session|sessionid|csrf|xsrf|(?:priv|auth|service|account|client|db|database)key)', name)
    )


def value_end(text, start):
    # Mask incomplete sensitive values through EOF, including any private tail.
    if start >= len(text) or text[start].isspace():
        return start
    if text[start] not in '\"\'`[{':
        match = re.match(r'(?:\\[\s\S]|[^\s,;`}\]])+', text[start:])
        return start + len(match[0]) if match else start
    stack = []
    quote = ''
    index = start
    while index < len(text):
        char = text[index]
        if quote:
            if char == '\\':
                index += 1
            elif char == quote:
                quote = ''
                if not stack:
                    return index + 1
        elif char in '\"\'`':
            quote = char
        elif char in '{[':
            stack.append('}' if char == '{' else ']')
        elif char in '}]':
            if not stack or stack.pop() != char:
                return len(text)
            if not stack:
                return index + 1
        index += 1
    return len(text)


def redact(text, api_key=''):
    text = text.replace('\\_', '_')
    spans = []
    if api_key:
        for match in re.finditer(re.escape(api_key), text):
            spans.append((match.start(), match.end(), '[REDACTED]'))
    fields = re.compile(r"""("(?:\\[\s\S]|[^"\\])*"|'(?:\\[\s\S]|[^'\\])*'|[A-Za-z_$][\w.$-]*)[ \t]*(?::=|=>|[:=])[ \t]*""")
    cursor = 0
    while match := fields.search(text, cursor):
        cursor = match.end()
        if not sensitive_field(match[1]):
            continue
        start = cursor
        if start < len(text) and text[start] in '\r\n':
            following = start + len(re.match(r'\s*', text[start:])[0])
            if match[1][0] in '\"\'' or (following < len(text) and text[following] in '\"\'`[{'):
                start = following
        end = value_end(text, start)
        if end > start:
            spans.append((start, end, '[REDACTED]'))
        cursor = max(cursor, end)
    # Detect on original text, then merge overlaps before replacing anything.
    rules = (
        (r'-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)', '[REDACTED_PRIVATE_KEY]', False),
        (r'(?im)(\b(?:authorization|proxy-authorization|cookie|set-cookie)[ \t]*[:=][ \t]*)[^\r\n]+', '[REDACTED]', True),
        (r'(?i)\b(?:Bearer|Basic)[ \t]+[A-Za-z0-9._~+/=-]+', '[REDACTED_AUTH]', False),
        (r'(?i)\b(?:username|login|u:)[ \t]*:?[ \t]*\S+[ \t]+(?:password|pw|p:)[ \t]*:?[ \t]*[^\r\n]+', '[REDACTED]', False),
        (r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s<>\"'`\)]+", '[URL]', False),
        (r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[EMAIL]', False),
        (r"/(?:Users|home)/[^/\s\"'`<>\)]+", '[HOME]', False),
        (r'\b(?:apikey_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{15,}|gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|AKIA[A-Z0-9]{16}|xox[baprs]-[A-Za-z0-9-]+)\b', '[REDACTED_TOKEN]', False),
        (r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '[REDACTED_JWT]', False),
    )
    for pattern, marker, prefix in rules:
        for match in re.finditer(pattern, text):
            start = match.start() + (len(match[1]) if prefix else 0)
            if match.end() > start:
                spans.append((start, match.end(), marker))
    spans.sort(key=lambda span: (span[0], -span[1]))
    merged = []
    for start, end, marker in spans:
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]), merged[-1][2])
        else:
            merged.append((start, end, marker))
    output = []
    cursor = 0
    opaque = re.compile(r'[A-Za-z0-9_+/=-]{40,}')
    for start, end, marker in merged:
        output.extend((opaque.sub('[REDACTED_OPAQUE_VALUE]', text[cursor:start]), marker))
        cursor = end
    output.append(opaque.sub('[REDACTED_OPAQUE_VALUE]', text[cursor:]))
    return ''.join(output).strip()


def user_text(text):
    if any(marker in text for marker in (
        '# AGENTS.md instructions', '<environment_context>', '<skill>', '<subagent_notification>',
    )):
        return ''
    for tag in ('recommended_plugins', 'in-app-browser-context', 'image'):
        text = re.sub(r'<' + tag + r'\b[\s\S]*?</' + tag + r'>', '', text)
    if '<response-annotations>' in text:
        match = re.search(r'<response-annotations>([\s\S]*?)</response-annotations>', text)
        annotations = json.loads(match.group(1))
        comments = ['Comment on ' + json.dumps(a.get('text', '')) + ': ' + a.get('annotation', '') for a in annotations]
        tail = text.split('## My request:', 1)[1] if '## My request:' in text else ''
        return '\n'.join(comments + [tail])
    if '# Browser comments:' in text:
        comments = []
        for part in re.split(r'## User Comment \d+', text)[1:]:
            if 'Comment:' in part:
                comments.append(part.split('Comment:', 1)[1].split('## My request:', 1)[0].split('The next image', 1)[0])
        tail = text.split('## My request:', 1)[1] if '## My request:' in text else ''
        return '\n'.join(comments + [tail.split('The next image', 1)[0]])
    if '## My request:' in text and '# Diff comments:' not in text:
        return text.split('## My request:', 1)[1]
    return text


def load_key():
    key = os.environ.get('JEV_API_KEY', '').strip()
    if key:
        return key
    if ENV_FILE.stat().st_mode & 0o077:
        raise PermissionError('Global env must be private')
    for line in ENV_FILE.read_text().splitlines():
        name, separator, value = line.partition('=')
        if separator and name.strip() == 'JEV_API_KEY':
            key = value.strip().strip('\"\'')
            if key:
                return key
    raise ValueError('JEV_API_KEY is missing')


def read_state(event, api_key, audit):
    path = event.get('transcript_path')
    final = event.get('last_assistant_message')
    if not isinstance(path, str) or not isinstance(final, str) or not final.strip():
        return None
    started = time.monotonic()
    history = deque()
    history_bytes = 0
    opening = None
    pending = None
    count = 0
    final = redact(final, api_key)

    def retain(message):
        nonlocal history_bytes, count
        history.append(message)
        history_bytes += len(json.dumps(message, ensure_ascii=False).encode())
        count += 1
        while len(history) > 1 and history_bytes > MAX_STATE_BYTES:
            history_bytes -= len(json.dumps(history.popleft(), ensure_ascii=False).encode())

    with Path(path).open('rb') as stream:
        size = os.fstat(stream.fileno()).st_size
        audit.update(transcript_path=path, transcript_bytes=size, transcript_start=0)
        # Read a fixed snapshot: later writes must not change the evidence for this stop.
        # Filter before budgeting: tool-heavy turns must not evict the user's task.
        while stream.tell() < size:
            if time.monotonic() - started > HISTORY_TIMEOUT:
                audit.update(reason='history_timeout', history_elapsed_ms=round((time.monotonic() - started) * 1000))
                return None
            line = stream.readline(size - stream.tell())
            try:
                record = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if record.get('type') != 'response_item':
                continue
            message = record.get('payload', {})
            if message.get('type') != 'message':
                continue
            role = message.get('role')
            if role not in ('user', 'assistant'):
                continue
            if role == 'assistant' and message.get('phase') not in ('final', 'final_answer'):
                continue
            text = '\n'.join(c.get('text', '') for c in message.get('content', []) if c.get('type') in ('input_text', 'output_text', 'text'))
            text = redact(user_text(text) if role == 'user' else text, api_key)
            if text:
                message = {'role': role, 'text': text}
                if opening is None and role == 'user':
                    opening = message
                if pending is not None:
                    retain(pending)
                pending = message
    if time.monotonic() - started > HISTORY_TIMEOUT:
        audit.update(reason='history_timeout', history_elapsed_ms=round((time.monotonic() - started) * 1000))
        return None
    # The final may already have been flushed to the rollout before Stop fires.
    if pending is not None and pending != {'role': 'assistant', 'text': final}:
        retain(pending)
    conversation = list(history)
    state = {'conversation': conversation, 'proposed_final_response': final, 'earlier_history_omitted': count > len(conversation)}
    # ponytail: retain the opening request plus a recent suffix; older middle context
    # can still be lost. Add a task summary only if logged misses justify that cost.
    keep_opening = opening is not None and len(opening['text'].encode()) <= MAX_OPENING_BYTES
    opening_in_history = any(message is opening for message in conversation)
    if keep_opening and not opening_in_history:
        state['opening_request'] = opening['text']
    while conversation and len(json.dumps(state, ensure_ascii=False).encode()) > MAX_STATE_BYTES:
        if conversation.pop(0) is opening:
            opening_in_history = False
        state['earlier_history_omitted'] = True
        if keep_opening and not opening_in_history:
            state['opening_request'] = opening['text']
    audit.update(
        history_elapsed_ms=round((time.monotonic() - started) * 1000),
        dialogue_message_count=count, message_count=len(conversation),
        omitted_message_count=count - len(conversation) - int('opening_request' in state),
        opening_request_retained=bool('opening_request' in state or opening_in_history),
        earlier_history_omitted=state['earlier_history_omitted'],
        state_bytes=len(json.dumps(state, ensure_ascii=False).encode()),
        final_sha256=digest(final),
    )
    if not any(m['role'] == 'user' for m in conversation):
        return None
    audit['input_sha256'] = digest(state)
    return state


def deadline_expired(_signum, _frame):
    raise TimeoutError('Jev deadline')


def classify(state, question, api_key):
    body = json.dumps({'model': 'jev-latest', 'state': state, 'questions': {'premature_stop': question}}, ensure_ascii=False).encode()
    connection = http.client.HTTPSConnection('api.typesafe.ai', timeout=API_TIMEOUT)
    previous_handler = signal.signal(signal.SIGALRM, deadline_expired)
    signal.setitimer(signal.ITIMER_REAL, API_TIMEOUT)
    try:
        # http.client does not redirect a bearer credential to a different host.
        connection.request('POST', '/v1/systemone', body, {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + api_key})
        response = connection.getresponse()
        if response.status != 200:
            return {'reason': 'api_http_error', 'http_status': response.status}
        payload = response.read(65_537)
        if len(payload) > 65_536:
            raise ValueError('Oversized API response')
        result = json.loads(payload)
        score = result['answers']['premature_stop']['noul']
        model = result['model']
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError('Invalid score')
        if not isinstance(model, str) or not re.fullmatch(r'jev[-a-zA-Z0-9.]+', model):
            raise ValueError('Invalid model')
        return {'score': score, 'model': model, 'reason': 'above_threshold' if score >= THRESHOLD else 'below_threshold'}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        connection.close()


def write_log(audit):
    now = datetime.datetime.now(datetime.timezone.utc)
    audit['timestamp'] = now.isoformat()
    LOG_DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = LOG_DIRECTORY / (now.strftime('%Y-%m-%d') + '.jsonl')
    # One append per event keeps concurrent tasks' records separate.
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, (json.dumps(audit, separators=(',', ':')) + '\n').encode())
    finally:
        os.close(descriptor)


def main():
    started = time.monotonic()
    audit = {'decision': 'allow', 'threshold': THRESHOLD}
    output = {}
    try:
        event = json.load(sys.stdin)
        audit.update(session_id=event.get('session_id'), turn_id=event.get('turn_id'))
        if event.get('hook_event_name') != 'Stop':
            audit['reason'] = 'wrong_event'
        elif event.get('stop_hook_active'):
            audit['reason'] = 'continuation_guard'
        else:
            question = json.loads(QUESTION_FILE.read_text())
            audit['policy_sha256'] = digest(question)
            audit['hook_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            api_key = load_key()
            state = read_state(event, api_key, audit)
            if state is None:
                audit.setdefault('reason', 'missing_history')
            else:
                audit.update(classify(state, question, api_key))
                if audit.get('score', 0) >= THRESHOLD:
                    audit['decision'] = 'block'
                    output = {'decision': 'block', 'reason': CONTINUATION}
    except TimeoutError:
        audit['reason'] = 'api_timeout'
    except Exception as error:
        # Exception messages can contain credentials, URLs or conversation fragments.
        audit.update(reason='check_error', error_type=type(error).__name__)
    audit['elapsed_ms'] = round((time.monotonic() - started) * 1000)
    try:
        write_log(audit)
    except OSError:
        print('jev-stop: could not write decision log', file=sys.stderr)
    print(json.dumps(output))


if __name__ == '__main__':
    main()
