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


class RedactTests(unittest.TestCase):
    def test_sensitive_field_aliases_and_delimiters(self):
        names = ('API_KEY', 'JEV_API_KEY', 'access_token', 'CLIENT_SECRET', 'PASSWORD', 'passwd',
                 'credential', 'mysql_pwd', 'private_key', 'sessionid', 'session_id', 'pwd', 'db_pass',
                 'auth_key', 'service_key', 'account_key', 'client_key', 'db_key', 'database_key',
                 'priv_key', 'database_pass', 'csrf', 'XSRF-TOKEN')
        for name in names:
            for quote in ('', '"', "'", '`'):
                with self.subTest(name=name, quote=quote):
                    original = f'{name} = {quote}short-private{quote}; after'
                    expected = f'{name} = [REDACTED]; after'
                    self.assertEqual(hook.redact(original), expected)
                    self.assertEqual(hook.redact(expected), expected)

    def test_additional_sensitive_names_and_structures(self):
        cases = [
            ('mysql_pwd=short', 'mysql_pwd=[REDACTED]'),
            ('private_key: short', 'private_key: [REDACTED]'),
            ('sessionid=short', 'sessionid=[REDACTED]'),
            ('{"nested":{"password":"short"}}', '{"nested":{"password":[REDACTED]}}'),
            ('pwd=short', 'pwd=[REDACTED]'),
            ('db_pass=short', 'db_pass=[REDACTED]'),
            ('secret=`short secret`', 'secret=[REDACTED]'),
            ('username: writer password: short-private', '[REDACTED]'),
            ('username: writer password short-private', '[REDACTED]'),
            ('{"password": {"current": "short-private"}, "ok": true}', '{"password": [REDACTED], "ok": true}'),
            ('{"password":\n {"current": "short-private"}, "ok": true}', '{"password":\n [REDACTED], "ok": true}'),
            ('password="short-private\nprivate-tail', 'password=[REDACTED]'),
            ('password={"value": ["short-private"}\nprivate-tail', 'password=[REDACTED]'),
            ('password="short" next', 'password=[REDACTED] next'),
            ('author="ordinary name"', 'author="ordinary name"'),
            ('{"\\u0070assword":"short"}', '{"\\u0070assword":[REDACTED]}'),
            ('{"password":[{"token":"short"}]}', '{"password":[REDACTED]}'),
        ]
        for original, expected in cases:
            with self.subTest(original=original):
                self.assertEqual(hook.redact(original), expected)
                self.assertEqual(hook.redact(expected), expected)

    def test_confirmed_redaction_regressions(self):
        cases = [
            ('escaped double quote', r'password="start\"secret-tail"; next', 'password=[REDACTED]; next'),
            ('escaped single quote', r"password='start\'secret-tail'; next", 'password=[REDACTED]; next'),
            ('escaped backslash', r'password="short\\"; next', 'password=[REDACTED]; next'),
            ('multiline', 'SECRET="first\nsecond"\nKeep this.', 'SECRET=[REDACTED]\nKeep this.'),
            ('empty header', 'Authorization:\nKeep this sentence.', 'Authorization:\nKeep this sentence.'),
            ('empty assignment', 'PASSWORD=\nKeep this sentence.', 'PASSWORD=\nKeep this sentence.'),
            ('empty auth scheme', 'Bearer\nKeep this sentence.', 'Bearer\nKeep this sentence.'),
            ('long URL', f"See https://{'a' * 45}.test/path next.", 'See [URL] next.'),
            ('long email', f"Contact {'a' * 45}@example.test today.", 'Contact [EMAIL] today.'),
            ('long home username', f"Open /Users/{'a' * 45}/file.md now.", 'Open [HOME]/file.md now.'),
            ('URL inside quoted secret', "password='secret prefix https://example.test/path'; next", 'password=[REDACTED]; next'),
            ('home inside quoted secret', "password='secret prefix /Users/alice'; next", 'password=[REDACTED]; next'),
            ('last JSON property', '{"password":"short-secret"}', '{"password":[REDACTED]}'),
        ]
        for name in ('Authorization', 'proxy-authorization', 'COOKIE', 'Set-Cookie'):
            cases.append((name + ' JSON', f'{{"{name}": "short-secret", "message": "Keep this"}}',
                          f'{{"{name}": [REDACTED], "message": "Keep this"}}'))
            cases.append((name + ' header', f'{name}: short-secret; other=value\r\nKeep this.',
                          f'{name}: [REDACTED]\r\nKeep this.'))
        for name, original, expected in cases:
            with self.subTest(name=name):
                actual = hook.redact(original)
                self.assertEqual(actual, expected)
                self.assertEqual(hook.redact(actual), actual)

    def test_preserves_prose_and_existing_redaction_boundaries(self):
        cases = [
            ('Useful ordinary prose.\nCrème brûlée — 東京 — 🙂.', 'Useful ordinary prose.\nCrème brûlée — 東京 — 🙂.'),
            ('  Keep outer whitespace behavior.\n', 'Keep outer whitespace behavior.'),
            ('a' * 39, 'a' * 39),
            ('a' * 40, '[REDACTED_OPAQUE_VALUE]'),
            ('Bearer short-auth', '[REDACTED_AUTH]'),
            ('eyJhbGciOiJIUzI1NiJ9.e30.c2ln', '[REDACTED_JWT]'),
            ('ghp_short', '[REDACTED_TOKEN]'),
            ('-----BEGIN PRIVATE KEY-----\nshort-body\n-----END PRIVATE KEY-----', '[REDACTED_PRIVATE_KEY]'),
        ]
        for original, expected in cases:
            with self.subTest(original=original):
                self.assertEqual(hook.redact(original), expected)
        self.assertEqual(hook.redact('fake\\_key and fake_key', 'fake_key'), '[REDACTED] and [REDACTED]')


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
        self.connection.getresponse.return_value.getheader.return_value = None
        self.reply(0.9)

    def reply(self, score, frustration=0, **request_types):
        scores = {
            'premature_stop': score, 'frustration': frustration,
            **{'request_' + name: request_types.get(name, 0) for name in hook.REQUEST_GUIDANCE},
        }
        self.connection.getresponse.return_value.read.return_value = json.dumps({
            'model': 'jev-test', 'answers': {
                name: {'type': 'noul', 'noul': value} for name, value in scores.items()
            },
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
        self.reply(0.99, frustration=0.99, correction=0.99)
        self.event['stop_hook_active'] = True
        self.assertEqual(self.run_hook(), {})
        self.connection_factory.assert_not_called()

    def test_frustration_lowers_threshold_without_replacing_stop_score(self):
        cases = (
            (0.79, 0.49, 0.80, False),
            (0.80, 0.49, 0.80, True),
            (0.69, 0.99, 0.70, False),
            (0.70, 0.50, 0.70, True),
            (0.75, 0.99, 0.70, True),
            (0.01, 1.00, 0.70, False),
        )
        for score, frustration, threshold, blocked in cases:
            with self.subTest(score=score, frustration=frustration):
                self.reply(score, frustration=frustration)
                output = self.run_hook()
                self.assertEqual(output.get('decision') == 'block', blocked)
                audit = json.loads(next(hook.LOG_DIRECTORY.glob('*.jsonl')).read_text().splitlines()[-1])
                self.assertEqual(audit['threshold'], threshold)
                self.assertEqual(audit['frustration'], frustration)
                self.assertEqual(audit['reason'], 'above_threshold' if blocked else 'below_threshold')
                if blocked:
                    self.assertEqual(hook.FRUSTRATION_GUIDANCE in output['reason'], frustration >= 0.50)

    def test_overlapping_request_types_and_frustration_guide_the_message(self):
        self.reply(0.75, frustration=0.99, question=0.95, correction=0.90, instruction=0.20)
        output = self.run_hook()
        self.assertEqual(output['decision'], 'block')
        self.assertIn(hook.FRUSTRATION_GUIDANCE, output['reason'])
        self.assertIn(hook.REQUEST_GUIDANCE['question'], output['reason'])
        self.assertIn(hook.REQUEST_GUIDANCE['correction'], output['reason'])
        self.assertNotIn(hook.REQUEST_GUIDANCE['instruction'], output['reason'])
        self.assertIn(hook.CONTINUATION, output['reason'])
        audit = json.loads(next(hook.LOG_DIRECTORY.glob('*.jsonl')).read_text().splitlines()[-1])
        self.assertEqual(audit['request_types'], {'question': 0.95, 'correction': 0.90, 'instruction': 0.20})
        self.reply(0.80, instruction=0.99)
        self.assertIn(hook.REQUEST_GUIDANCE['instruction'], self.run_hook()['reason'])
        self.reply(0.79, question=1, correction=1, instruction=1)
        self.assertEqual(self.run_hook(), {})

    def test_every_question_revision_changes_the_request_version(self):
        questions = hook.load_questions()
        hook.classify({}, questions, hook.DEFAULT_API_URL, self.key)
        original = self.connection.request.call_args.args[3]['X-Decision-Version']
        for name in questions:
            with self.subTest(question=name):
                revised = json.loads(json.dumps(questions))
                revised[name]['instructions'] += '\nSynthetic revision.'
                hook.classify({}, revised, hook.DEFAULT_API_URL, self.key)
                version = self.connection.request.call_args.args[3]['X-Decision-Version']
                self.assertNotEqual(version, original)
                self.assertGreater(int(version), 0)
        self.run_hook()
        audit = json.loads(next(hook.LOG_DIRECTORY.glob('*.jsonl')).read_text().splitlines()[-1])
        self.assertEqual(str(audit['question_version']), original)

    def test_invalid_or_missing_context_answers_allow_stop(self):
        for name in ('frustration', 'request_question', 'request_correction', 'request_instruction'):
            for bad_answer in (None, {'type': 'score', 'noul': 1}, {'type': 'noul', 'noul': True}, {'type': 'noul', 'noul': 1.1}):
                with self.subTest(question=name, answer=bad_answer):
                    self.reply(0.99)
                    payload = json.loads(self.connection.getresponse.return_value.read.return_value)
                    if bad_answer is None:
                        del payload['answers'][name]
                    else:
                        payload['answers'][name] = bad_answer
                    self.connection.getresponse.return_value.read.return_value = json.dumps(payload).encode()
                    self.assertEqual(self.run_hook(), {})

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
        self.connection_factory.assert_called_once_with('api.typesafe.ai', None, timeout=3)
        method, path, body, headers = self.connection.request.call_args.args
        self.assertEqual((method, path), ('POST', '/v1/systemone'))
        self.assertEqual(headers['Authorization'], 'Bearer ' + self.key)
        self.assertEqual(headers['X-Decision-Key'], 'codex.stop')
        self.assertGreater(int(headers['X-Decision-Version']), 0)
        payload = json.loads(body)
        self.assertEqual(payload['questions'], hook.load_questions())
        self.assertEqual(payload['questions']['premature_stop'], json.loads(hook.QUESTION_FILE.read_text()))
        self.assertEqual(payload['state']['conversation'], [{
            'role': 'user', 'text': 'Fix it using [REDACTED], API_KEY=[REDACTED] and [EMAIL].',
        }])
        self.assertEqual(payload['state']['proposed_final_response'], 'I will finish it.')

    def test_outbound_payload_redacts_escaped_multiline_and_json_secrets(self):
        text = 'Start\npassword="prefix\\"secret-tail"\nSECRET="first\nsecond"\n{"Cookie":"sid=short-secret"}\nEnd'
        expected = 'Start\npassword=[REDACTED]\nSECRET=[REDACTED]\n{"Cookie":[REDACTED]}\nEnd'
        self.transcript.write_text(json.dumps(message('user', text)) + '\n')
        self.event['last_assistant_message'] = text
        self.run_hook()
        body = self.connection.request.call_args.args[2]
        state = json.loads(body)['state']
        self.assertEqual(state['conversation'], [{'role': 'user', 'text': expected}])
        self.assertEqual(state['proposed_final_response'], expected)
        self.assertEqual(self.connection.request.call_args.args[3]['Authorization'], 'Bearer ' + self.key)
        self.assertEqual(self.event['last_assistant_message'], text)

    def test_outbound_payload_redacts_nested_values_and_credential_aliases(self):
        text = 'mysql_pwd="short-private"\nprivate_key="short-private"\n{"sessionid":"short-private"}\n{"password":{"current":"short-private"}}\npwd="short-private"\ndb_pass="short-private"\npassword=`short-private`\nusername: writer password short-private'
        expected = 'mysql_pwd=[REDACTED]\nprivate_key=[REDACTED]\n{"sessionid":[REDACTED]}\n{"password":[REDACTED]}\npwd=[REDACTED]\ndb_pass=[REDACTED]\npassword=[REDACTED]\n[REDACTED]'
        self.transcript.write_text(json.dumps(message('user', text)) + '\n')
        self.event['last_assistant_message'] = text
        self.run_hook()
        body = self.connection.request.call_args.args[2]
        state = json.loads(body)['state']
        self.assertEqual(state['conversation'], [{'role': 'user', 'text': expected}])
        self.assertEqual(state['proposed_final_response'], expected)
        self.assertNotIn('short-private', body.decode())

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
        opening = f'Fix the importer using {self.key}.\npassword="prefix\\"secret-tail"'
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
            self.assertEqual(state['opening_request'], 'Fix the importer using [REDACTED].\npassword=[REDACTED]')
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
        self.assertEqual(hook.load_config(), (hook.DEFAULT_API_URL, self.key))
        with patch.dict(os.environ, {'JEV_API_KEY': ''}):
            self.assertEqual(hook.load_config(), (hook.DEFAULT_API_URL, 'file-test-key'))
            hook.ENV_FILE.chmod(0o644)
            with self.assertRaises(PermissionError):
                hook.load_config()

    def test_custom_endpoint_uses_no_provider_credential_and_logs_run_id(self):
        endpoint = 'https://example.invalid/api/jev'
        self.connection.getresponse.return_value.getheader.return_value = 'run-synthetic-123'
        self.event['last_assistant_message'] = 'The known credential is ' + self.key
        with patch.dict(os.environ, {'JEV_API_URL': endpoint, 'JEV_API_KEY': self.key}):
            self.assertEqual(self.run_hook()['decision'], 'block')
        self.connection_factory.assert_called_once_with('example.invalid', None, timeout=3)
        method, path, _body, headers = self.connection.request.call_args.args
        self.assertEqual((method, path), ('POST', '/api/jev'))
        self.assertNotIn('Authorization', headers)
        self.assertNotIn(self.key, self.connection.request.call_args.args[2].decode())
        self.assertEqual(headers['X-Decision-Key'], 'codex.stop')
        self.assertGreater(int(headers['X-Decision-Version']), 0)
        audit = json.loads(next(hook.LOG_DIRECTORY.glob('*.jsonl')).read_text().splitlines()[-1])
        self.assertEqual(audit['decision_run_id'], 'run-synthetic-123')

    def test_custom_endpoint_can_come_from_private_env_without_a_key(self):
        hook.ENV_FILE.write_text('JEV_API_URL=https://example.invalid/api/jev\n')
        hook.ENV_FILE.chmod(0o600)
        with patch.dict(os.environ, {'JEV_API_URL': '', 'JEV_API_KEY': ''}):
            self.assertEqual(hook.load_config(), ('https://example.invalid/api/jev', ''))

    def test_missing_private_file_is_optional_when_environment_is_sufficient(self):
        with patch.dict(os.environ, {'JEV_API_URL': '', 'JEV_API_KEY': self.key}):
            self.assertEqual(hook.load_config(), (hook.DEFAULT_API_URL, self.key))
        with patch.dict(os.environ, {'JEV_API_URL': 'https://example.invalid/api/jev', 'JEV_API_KEY': ''}):
            self.assertEqual(hook.load_config(), ('https://example.invalid/api/jev', ''))

    def test_custom_environment_endpoint_merges_file_key_for_redaction(self):
        hook.ENV_FILE.write_text('JEV_API_KEY=file-test-key\n')
        hook.ENV_FILE.chmod(0o600)
        with patch.dict(os.environ, {'JEV_API_URL': 'https://example.invalid/api/jev', 'JEV_API_KEY': ''}):
            endpoint, key = hook.load_config()
        self.assertEqual(endpoint, 'https://example.invalid/api/jev')
        self.assertEqual(hook.redact('Value file-test-key', key), 'Value [REDACTED]')

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
