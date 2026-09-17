import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


HOOK_PATH = Path(__file__).resolve().parents[1] / '.codex' / 'hooks' / 'jev-stop.py'
SPEC = importlib.util.spec_from_file_location('jev_stop', HOOK_PATH)
hook = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hook)


def message(role, text, phase=None):
    return {'type': 'response_item', 'payload': {
        'type': 'message', 'role': role, 'phase': phase,
        'content': [{'type': 'text', 'text': text}],
    }}


class JevStopTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.key = 'synthetic-jev-key'
        self.transcript = self.root / 'transcript.jsonl'
        self.transcript.write_text(json.dumps(message('user', 'Please finish the change.')) + '\n')
        self.event = {
            'hook_event_name': 'Stop', 'stop_hook_active': False,
            'session_id': 'test-session', 'turn_id': 'test-turn',
            'transcript_path': str(self.transcript),
            'last_assistant_message': 'I will finish it.',
        }
        for patcher in (
            patch.object(hook, 'ENV_FILE', self.root / 'jev.env'),
            patch.object(hook, 'LOG_DIRECTORY', self.root / 'logs'),
            patch.dict(os.environ, {'JEV_API_KEY': self.key}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.connection_patch = patch.object(hook.http.client, 'HTTPSConnection')
        self.connection_factory = self.connection_patch.start()
        self.addCleanup(self.connection_patch.stop)
        self.connection = self.connection_factory.return_value
        self.connection.getresponse.return_value.status = 200
        self.reply(0.9)

    def reply(self, score):
        self.connection.getresponse.return_value.read.return_value = json.dumps({
            'model': 'jev-test', 'answers': {'premature_stop': {'noul': score}},
        }).encode()

    def run_hook(self):
        output = io.StringIO()
        with patch.object(hook.sys, 'stdin', io.StringIO(json.dumps(self.event))), contextlib.redirect_stdout(output):
            hook.main()
        return json.loads(output.getvalue())

    def test_threshold_and_one_continuation(self):
        self.reply(0.79)
        self.assertEqual(self.run_hook(), {})
        self.reply(0.8)
        self.assertEqual(self.run_hook(), {'decision': 'block', 'reason': hook.CONTINUATION})
        self.connection_factory.reset_mock()
        self.event['stop_hook_active'] = True
        self.assertEqual(self.run_hook(), {})
        self.connection_factory.assert_not_called()

    def test_outbound_payload_filters_context_and_redacts_credentials(self):
        rows = [
            message('system', 'private system instructions'),
            message('developer', 'private developer instructions'),
            message('user', '# AGENTS.md instructions\nprivate injected instructions'),
            message('assistant', 'private reasoning', 'analysis'),
            {'type': 'response_item', 'payload': {'type': 'function_call_output', 'output': 'private tool result'}},
            message('user', f'Fix it using {self.key}, API_KEY=synthetic-secret and alice@example.invalid.'),
            message('assistant', 'I will finish it.', 'final'),
        ]
        self.transcript.write_text('\n'.join(json.dumps(row) for row in rows))
        self.run_hook()
        self.connection_factory.assert_called_once_with('api.typesafe.ai', timeout=3)
        method, path, body, headers = self.connection.request.call_args.args
        self.assertEqual((method, path), ('POST', '/v1/systemone'))
        self.assertEqual(headers['Authorization'], 'Bearer ' + self.key)
        payload = json.loads(body)
        self.assertEqual(payload['questions']['premature_stop'], json.loads(hook.QUESTION_FILE.read_text()))
        self.assertEqual(payload['state']['conversation'], [{
            'role': 'user', 'text': 'Fix it using [REDACTED], API_KEY=[REDACTED] and [EMAIL].',
        }])
        self.assertEqual(payload['state']['proposed_final_response'], 'I will finish it.')

    def test_redaction_is_not_anonymization(self):
        text = 'Alice Example lives at 123 Example Street. Client budget is 120000.'
        self.assertEqual(hook.redact(text), text)
        self.assertEqual(hook.redact('/Users/sam/client/pay.csv'), '[HOME]/client/pay.csv')

    def test_tool_heavy_turn_preserves_the_user_request(self):
        rows = [
            message('user', 'Please finish the change.'),
            {'type': 'response_item', 'payload': {
                'type': 'function_call_output', 'output': 'tool output ' * 800_000,
            }},
        ]
        self.transcript.write_text('\n'.join(json.dumps(row) for row in rows))
        self.assertGreater(self.transcript.stat().st_size, 8 * 1024 * 1024)
        self.assertEqual(self.run_hook()['decision'], 'block')
        state = json.loads(self.connection.request.call_args.args[2])['state']
        self.assertEqual(state['conversation'], [{'role': 'user', 'text': 'Please finish the change.'}])
        log_path = next(hook.LOG_DIRECTORY.glob('*.jsonl'))
        audit = json.loads(log_path.read_text().splitlines()[-1])
        self.assertEqual(audit['transcript_start'], 0)
        self.assertEqual(audit['omitted_message_count'], 0)
        self.assertTrue(audit['opening_request_retained'])

    def test_small_dialogue_is_not_limited_to_sixteen_messages(self):
        rows = [message('user', f'Clarification {number}.') for number in range(40)]
        self.transcript.write_text('\n'.join(json.dumps(row) for row in rows))
        audit = {}
        state = hook.read_state(self.event, self.key, audit)
        self.assertEqual(len(state['conversation']), 40)
        self.assertFalse(state['earlier_history_omitted'])
        self.assertEqual(audit['omitted_message_count'], 0)

    def test_bounded_history_preserves_opening_and_latest_scope(self):
        opening = f'Fix the importer using {self.key}.'
        latest = 'Cancel the fix. Only explain what you found.'
        rows = [message('user', opening)] + [
            message('assistant', f'Prior discussion {number}. ' * 30, 'final')
            for number in range(20)
        ] + [message('user', latest)]
        self.transcript.write_text('\n'.join(json.dumps(row) for row in rows))
        audit = {}
        with patch.object(hook, 'MAX_STATE_BYTES', 2_000):
            state = hook.read_state(self.event, self.key, audit)
            self.assertLessEqual(len(json.dumps(state, ensure_ascii=False).encode()), 2_000)
            self.assertEqual(state['opening_request'], 'Fix the importer using [REDACTED].')
            self.assertEqual(state['conversation'][-1]['text'], latest)
            self.assertTrue(state['earlier_history_omitted'])
            self.assertGreater(audit['omitted_message_count'], 0)
            self.assertEqual(audit['dialogue_message_count'], len(rows))
            self.assertEqual(audit['omitted_message_count'], len(rows) - len(state['conversation']) - 1)
            rows.append(message('assistant', self.event['last_assistant_message'], 'final'))
            self.transcript.write_text('\n'.join(json.dumps(row) for row in rows))
            self.assertEqual(hook.read_state(self.event, self.key, {}), state)

    def test_opening_after_an_assistant_message_is_not_duplicated(self):
        rows = [message('assistant', 'Earlier answer.', 'final'), message('user', 'Fix it.')]
        self.transcript.write_text('\n'.join(json.dumps(row) for row in rows))
        audit = {}
        state = hook.read_state(self.event, self.key, audit)
        self.assertNotIn('opening_request', state)
        self.assertEqual(audit['omitted_message_count'], 0)
        self.assertTrue(audit['opening_request_retained'])

    def test_history_deadline_skips_api_and_logs_the_reason(self):
        with patch.object(hook, 'HISTORY_TIMEOUT', -1):
            self.assertEqual(self.run_hook(), {})
        self.connection_factory.assert_not_called()
        log_path = next(hook.LOG_DIRECTORY.glob('*.jsonl'))
        audit = json.loads(log_path.read_text().splitlines()[-1])
        self.assertEqual(audit['reason'], 'history_timeout')

    def test_oversized_current_request_skips_api(self):
        self.transcript.write_text(json.dumps(message('user', 'Review this. ' * 6_000)))
        self.assertEqual(self.run_hook(), {})
        self.connection_factory.assert_not_called()

    def test_private_env_file_and_environment_precedence(self):
        hook.ENV_FILE.write_text('JEV_API_KEY=file-test-key\n')
        hook.ENV_FILE.chmod(0o600)
        self.assertEqual(hook.load_key(), self.key)
        with patch.dict(os.environ, {'JEV_API_KEY': ''}):
            self.assertEqual(hook.load_key(), 'file-test-key')
            hook.ENV_FILE.chmod(0o644)
            with self.assertRaises(PermissionError):
                hook.load_key()

    def test_missing_key_or_transcript_skips_api(self):
        with patch.dict(os.environ, {'JEV_API_KEY': ''}):
            self.assertEqual(self.run_hook(), {})
        self.event['transcript_path'] = None
        self.assertEqual(self.run_hook(), {})
        self.connection_factory.assert_not_called()

    def test_api_failures_allow_stop_without_logging_response_or_exception_text(self):
        for score in (True, -1, 1.1, float('nan'), '0.9'):
            with self.subTest(score=score):
                self.reply(score)
                self.assertEqual(self.run_hook(), {})
        self.connection.getresponse.return_value.status = 503
        self.connection.getresponse.return_value.read.return_value = b'private API error'
        self.assertEqual(self.run_hook(), {})
        for error in (TimeoutError('private timeout detail'), OSError('private connection detail')):
            self.connection.request.side_effect = error
            self.assertEqual(self.run_hook(), {})
        log_path = next(hook.LOG_DIRECTORY.glob('*.jsonl'))
        log_text = log_path.read_text()
        for private_text in ('private', self.key, 'Please finish', 'I will finish'):
            self.assertNotIn(private_text, log_text)
        self.assertEqual(log_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(hook.LOG_DIRECTORY.stat().st_mode & 0o777, 0o700)
        last = json.loads(log_text.splitlines()[-1])
        self.assertEqual(last['error_type'], 'OSError')
        self.assertEqual(last['transcript_path'], str(self.transcript))


if __name__ == '__main__':
    unittest.main()
