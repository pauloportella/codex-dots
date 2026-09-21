import assert from 'node:assert/strict';
import { test } from 'node:test';
import { redact, sensitiveField } from '../.codex/hooks/unslop-jev.mjs';

// Fake, deliberately short credentials keep one rule from hiding another's failure.
const cases = [
  ['literal key, repeated and embedded', 'a fake.*[key] b prefixfake.*[key]suffix', 'a [REDACTED] b prefix[REDACTED]suffix', 'fake.*[key]'],
  ['Markdown escaped key', 'fake\\_key and fake_key', '[REDACTED] and [REDACTED]', 'fake_key'],
  ['empty key', 'Useful ordinary prose.', 'Useful ordinary prose.'],
  ['escaped underscores', 'some\\_identifier', 'some_identifier'],
  ['Bearer authentication', 'Use bEaReR abc.def_~+/=- next.', 'Use [REDACTED_AUTH] next.'],
  ['Basic authentication', 'Use BASIC dXNlcjpwYXNz next.', 'Use [REDACTED_AUTH] next.'],
  ['JWT alone', 'Before eyJhbGciOiJIUzI1NiJ9.e30.c2ln after', 'Before [REDACTED_JWT] after'],
  ['opaque lower boundary', 'a'.repeat(39), 'a'.repeat(39)],
  ['opaque boundary', 'a'.repeat(40), '[REDACTED_OPAQUE_VALUE]'],
  ['opaque upper boundary', 'a'.repeat(41), '[REDACTED_OPAQUE_VALUE]'],
  ['opaque punctuation', `(${'+/_=-'.repeat(8)})`, '([REDACTED_OPAQUE_VALUE])'],
  ['ordinary URL', 'See https://example.test/path?q=abc#fragment next.', 'See [URL] next.'],
  ['URL with userinfo', 'See postgres://writer:short-pass@db.test/name next.', 'See [URL] next.'],
  ['URL in Markdown', '[link](https://example.test/path)', '[link]([URL])'],
  ['URL with long host', `See https://${'a'.repeat(45)}.test/path next.`, 'See [URL] next.'],
  ['email plus tag', 'Contact first.last+tag@example.test today.', 'Contact [EMAIL] today.'],
  ['Unicode email local part', 'Contact müller@example.test today.', 'Contact [EMAIL] today.'],
  ['Unicode email domain', 'Contact writer@例子.test today.', 'Contact [EMAIL] today.'],
  ['Unicode sensitive field suffix', 'passwordé=short-private; next', 'passwordé=[REDACTED]; next'],
  ['Unicode sensitive field infix', 'apiékey=short-private; next', 'apiékey=[REDACTED]; next'],
  ['email with long local part', `Contact ${'a'.repeat(45)}@example.test today.`, 'Contact [EMAIL] today.'],
  ['macOS home', 'Open /Users/alice/private/file.md now.', 'Open [HOME]/private/file.md now.'],
  ['Linux home', 'Open /home/alice/private/file.md now.', 'Open [HOME]/private/file.md now.'],
  ['long home username', `Open /Users/${'a'.repeat(45)}/file.md now.`, 'Open [HOME]/file.md now.'],
  ['empty authorization preserves next line', 'Authorization:\nKeep this sentence.', 'Authorization:\nKeep this sentence.'],
  ['empty auth scheme preserves next line', 'Bearer\nKeep this sentence.', 'Bearer\nKeep this sentence.'],
  ['empty assignment preserves next line', 'PASSWORD=\nKeep this sentence.', 'PASSWORD=\nKeep this sentence.'],
  ['JSON cookie', '{"Cookie": "sid=short-secret", "message": "Keep this"}', '{"Cookie": [REDACTED], "message": "Keep this"}'],
  ['escaped double quote', String.raw`password="start\"secret-tail"; next`, 'password=[REDACTED]; next'],
  ['escaped single quote', String.raw`password='start\'secret-tail'; next`, 'password=[REDACTED]; next'],
  ['multiline quoted secret', 'SECRET="first\nsecond"\nKeep this.', 'SECRET=[REDACTED]\nKeep this.'],
  ['JSON assignment', '{"api_key":"short-secret","message":"Keep this"}', '{"api_key":[REDACTED],"message":"Keep this"}'],
  ['empty quoted assignment', 'password=""; next', 'password=[REDACTED]; next'],
  ['escaped backslash before closing quote', String.raw`password="short\\"; next`, 'password=[REDACTED]; next'],
  ['quoted delimiters in secret', 'secret="a,b;c=d `value`"; next', 'secret=[REDACTED]; next'],
  ['Unicode secret', 'password="東京 café 🙂"; next', 'password=[REDACTED]; next'],
  ['URL inside a quoted secret', "password='https://example.test/path secret-tail'; next", 'password=[REDACTED]; next'],
  ['home path inside a quoted secret', "password='/Users/alice secret-tail'; next", 'password=[REDACTED]; next'],
  ['URL at end of a quoted secret', "password='secret prefix https://example.test/path'; next", 'password=[REDACTED]; next'],
  ['home path at end of a quoted secret', "password='secret prefix /Users/alice'; next", 'password=[REDACTED]; next'],
  ['JSON final secret property', '{"password":"short-secret"}', '{"password":[REDACTED]}'],
  ['separate opaque values', `${'a'.repeat(39)} ${'b'.repeat(39)}`, `${'a'.repeat(39)} ${'b'.repeat(39)}`],
  ['short provider lookalikes', 'sk-12345678901234 AKIA1234567890ABCDE', 'sk-12345678901234 AKIA1234567890ABCDE'],
  ['nested sensitive object', '{"password": {"current": "short-private"}, "ok": true}', '{"password": [REDACTED], "ok": true}'],
  ['nested sensitive array', '{"credentials": ["short-private", {"value": ["other-private"]}], "ok": true}', '{"credentials": [REDACTED], "ok": true}'],
  ['structured value embedded in prose', 'Before password={"nested": {"value": "short-private"}}; after', 'Before password=[REDACTED]; after'],
  ['escaped JSON property name', String.raw`{"pass\u0077ord":"short-private"}`, String.raw`{"pass\u0077ord":[REDACTED]}`],
  ['newline before JSON value', '{"password":\n  {"current": "short-private"}, "ok": true}', '{"password":\n  [REDACTED], "ok": true}'],
  ['brackets inside quoted nested value', 'secret={"value": "} ] \\\" short-private", "nested": [1, 2]}; after', 'secret=[REDACTED]; after'],
  ['unterminated quoted secret', 'password="short-private\nprivate-tail', 'password=[REDACTED]'],
  ['unterminated nested secret', 'password={"value": "short-private",\n"tail":"private-tail"', 'password=[REDACTED]'],
  ['mismatched nested secret', 'password={"value": ["short-private"}\nprivate-tail', 'password=[REDACTED]'],
  ['escaped backtick secret', 'password=`first\\`short-private`; after', 'password=[REDACTED]; after'],
  ['literal key within URL', 'See https://example.test/short-private/path next.', 'See [URL] next.', 'short-private'],
  ['literal key within quoted secret', 'password="prefix short-private private-tail"; after', 'password=[REDACTED]; after', 'short-private'],
  ['opaque value inside URL', `See https://example.test/${'a'.repeat(45)} next.`, 'See [URL] next.'],
  ['opaque value after home username', `Open /Users/alice/${'a'.repeat(45)} now.`, 'Open [HOME][REDACTED_OPAQUE_VALUE] now.'],
  ['two adjacent values', 'pwd="one"; db_pass="two"', 'pwd=[REDACTED]; db_pass=[REDACTED]'],
];

for (const kind of ['', 'RSA ', 'EC ', 'OPENSSH ', 'ENCRYPTED ']) {
  const block = `-----BEGIN ${kind}PRIVATE KEY-----\r\nshort-private-body\r\n-----END ${kind}PRIVATE KEY-----`;
  cases.push([`${kind || 'PKCS8 '}private key blocks`, `Before\n${block}\nBetween\n${block}\nAfter`, 'Before\n[REDACTED_PRIVATE_KEY]\nBetween\n[REDACTED_PRIVATE_KEY]\nAfter']);
}
for (const header of ['Authorization', 'proxy-authorization', 'COOKIE', 'Set-Cookie']) {
  cases.push([`${header} header`, `${header}: short-secret; other=value\r\nKeep this.`, `${header}: [REDACTED]\r\nKeep this.`]);
  cases.push([`${header} JSON property`, `{"${header}": "short-secret", "message": "Keep this"}`, `{"${header}": [REDACTED], "message": "Keep this"}`]);
}
for (const name of ['API_KEY', 'apikey', 'api-key', 'JEV_API_KEY', 'access_token', 'CLIENT_SECRET', 'PASSWORD', 'passwd', 'credential', 'mysql_pwd', 'private_key', 'sessionid', 'session_id', 'pwd', 'db_pass', 'auth_key', 'service_key', 'account_key', 'client_key', 'db_key', 'database_key', 'priv_key', 'database_pass', 'csrf', 'XSRF-TOKEN']) {
  for (const quote of ['', '"', "'", '`']) {
    cases.push([`${name} assignment ${quote || 'unquoted'}`, `${name} = ${quote}short-secret${quote}; next`, `${name} = [REDACTED]; next`]);
  }
}
for (const token of [
  'apikey_short', 'sk-123456789012345',
  ...[...'pousr'].map(kind => `gh${kind}_short`), 'github_pat_short',
  'AKIA1234567890ABCDEF', ...[...'baprs'].map(kind => `xox${kind}-short`),
]) {
  cases.push([`${token.split('_')[0]} token family`, `Before ${token} after ${token}`, 'Before [REDACTED_TOKEN] after [REDACTED_TOKEN]']);
}
for (const prose of [
  '', 'The arithmetic is basic. Bring the letter.',
  'The API key, password, token, and secret are described here, without values.',
  'Alice lives at 12 Example Street. This is not anonymization.',
  'const count = 12;\nreturn items.map(item => item.name);',
  'Crème brûlée — 東京 — 🙂.\r\nKeep tabs\tand spacing  intact.',
  'Version 1.2.3; 2026-09-18; deadbeef; @writer; /usr/local/bin/node.',
  '-----BEGIN PUBLIC KEY-----\nshort-public-body\n-----END PUBLIC KEY-----',
]) cases.push([`preserves ordinary text: ${prose.slice(0, 35)}`, prose, prose]);

for (const [name, input, expected, key] of cases) {
  test(`redact: ${name}`, () => {
    const actual = redact(input, key);
    assert.equal(actual, expected);
    assert.equal(redact(actual, key), actual, 'redaction must be idempotent');
  });
}

test('redact: mixed secrets preserve the surrounding prose and line breaks', () => {
  assert.equal(redact('Start\npassword="short-secret"\nBearer short-auth\nwriter@example.test\n/home/alice/file\nEnd'),
    'Start\npassword=[REDACTED]\n[REDACTED_AUTH]\n[EMAIL]\n[HOME]/file\nEnd');
});

test('redact: auth-like prose is conservatively masked, without semantic judgment', () => {
  assert.equal(redact('The bearer of the letter arrived. Basic arithmetic still works.'),
    'The [REDACTED_AUTH] the letter arrived. [REDACTED_AUTH] still works.');
});

test('redact: natural username/password pairs leave no credential values', () => {
  const output = redact('Before\nusername: writer password short-private\nAfter');
  assert.ok(!output.includes('short-private'));
  assert.ok(output.startsWith('Before\n'));
  assert.ok(output.endsWith('\nAfter'));
  assert.equal(redact(output), output);
});

test('sensitiveField supports structured redaction without copying field rules', () => {
  for (const name of ['passwordé', 'apiékey', 'Authorization', 'session_id']) {
    assert.equal(sensitiveField(name), true, name);
  }
  assert.equal(sensitiveField('description'), false);
});
