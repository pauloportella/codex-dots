import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { editedText, extractAddedText, handlePayload, isFirstPersonProse, isSubstantialProse } from '../.codex/hooks/unslop-writing.mjs';

const HOOK_PATH = fileURLToPath(new URL('../.codex/hooks/unslop-writing.mjs', import.meta.url));
const clean = 'I walked to the station early this morning and bought coffee from the kiosk beside platform three. My train was delayed, so I called my sister and watched the maintenance crew replace a broken light. I reached the office before nine and finished the report by lunch.';
const thirdPersonCopy = 'The studio serves independent designers who need a quiet place to meet clients, review samples, and prepare presentations. Members can reserve a private room, print large-format drafts, borrow lighting equipment, and store materials overnight. Located in the heart of downtown, it is a hidden gem that empowers creative teams to do their best work.';
const fencedCliche = `${clean}\n\n\`\`\`text\nIt did not make life quieter. It made my attention feel like mine again.\n\`\`\``;
const fencedOnly = '\`\`\`text\nIt did not make life quieter. It made my attention feel like mine again.\n\`\`\`';
const stopPayload = (message, stop_hook_active = false) => ({ hook_event_name: 'Stop', stop_hook_active, last_assistant_message: message });
const editPayload = text => ({ hook_event_name: 'PostToolUse', tool_name: 'apply_patch', tool_input: { command: `*** Begin Patch\n*** Add File: draft.txt\n${text.split('\n').map(line => `+${line}`).join('\n')}\n*** End Patch` } });
const blockingJudge = async input => { assert.equal(typeof input.writing_request, 'string'); assert.equal(typeof input.text, 'string'); return 0.8; };

test('retains prose gates and extraction helpers', () => {
  assert.equal(isSubstantialProse(thirdPersonCopy), true); assert.equal(isFirstPersonProse(clean), true); assert.equal(isFirstPersonProse(thirdPersonCopy), false); assert.equal(isFirstPersonProse('I wrote a short reply.'), false);
  assert.equal(extractAddedText(' context\n-old\n+new\n+++ metadata'), 'new'); assert.equal(editedText({ tool_name: 'Edit', tool_input: { new_string: 'replacement' } }), 'replacement'); assert.equal(editedText({ tool_name: 'Write', tool_input: { content: 'document' } }), 'document');
});

test('Stop blocks at 0.5 and gives contextual rewrite guidance', async () => {
  const result = await handlePayload(stopPayload(clean), { judge: async () => 0.5 }); assert.equal(result.decision, 'block'); assert.match(result.reason, /contextual cliché policy/i); assert.match(result.reason, /revision|revise/i); assert.match(result.reason, /literal|technical|quoted|requested style/i); assert.deepEqual(await handlePayload(stopPayload(clean, true), { judge: blockingJudge }), {});
});

test('scores below threshold allow and prose without a phrase prefilter is judged', async () => {
  let calls = 0; const judge = async ({ text }) => { calls += 1; assert.equal(text, clean); return 0.49; }; assert.deepEqual(await handlePayload(stopPayload(clean), { judge }), {}); assert.equal(calls, 1);
});

test('Stop gates avoid judging short, non-first-person, and fenced-code-only responses', async () => {
  let calls = 0; const judge = async () => { calls += 1; return 1; }; assert.deepEqual(await handlePayload(stopPayload('I wrote a short reply.'), { judge }), {}); assert.deepEqual(await handlePayload(stopPayload(thirdPersonCopy), { judge }), {}); assert.deepEqual(await handlePayload(stopPayload(fencedOnly), { judge }), {}); assert.deepEqual(await handlePayload(stopPayload('The report is complete.'), { judge }), {}); assert.equal(calls, 0);
});

test('PostToolUse judges apply_patch, Edit, and Write prose', async () => {
  for (const payload of [editPayload(clean), { hook_event_name: 'PostToolUse', tool_name: 'Edit', tool_input: { new_string: clean } }, { hook_event_name: 'PostToolUse', tool_name: 'Write', tool_input: { content: clean } }]) { const result = await handlePayload(payload, { judge: blockingJudge }); assert.equal(result.decision, 'block'); assert.match(result.reason, /edited text/i); }
});

test('PostToolUse sends fenced-code-stripped text to the judge', async () => {
  let judged; const result = await handlePayload({ hook_event_name: 'PostToolUse', tool_name: 'Write', tool_input: { content: fencedCliche } }, { judge: async ({ text }) => { judged = text; return 0.8; } }); assert.equal(result.decision, 'block'); assert.equal(judged.trim(), clean); assert.doesNotMatch(judged, /```/);
});

test('malformed payloads and judge failures fail open', async () => {
  for (const payload of [null, {}, { hook_event_name: 'Stop' }, { hook_event_name: 'Stop', last_assistant_message: 42 }]) assert.deepEqual(await handlePayload(payload, { judge: blockingJudge }), {});
  for (const value of [NaN, Infinity, -0.1, 1.1, '0.8', null]) assert.deepEqual(await handlePayload(stopPayload(clean), { judge: async () => value }), {});
  assert.deepEqual(await handlePayload(stopPayload(clean), { judge: async () => { throw new Error('provider down'); } }), {});
  assert.deepEqual(await handlePayload({ hook_event_name: 'PostToolUse', tool_name: 'Write', tool_input: { content: 42 } }, { judge: blockingJudge }), {});
});

test('command hook emits {} without credentials', () => {
  const home = mkdtempSync(`${tmpdir()}/unslop-writing-`);
  try {
    const options = { encoding: 'utf8', env: { ...process.env, HOME: home, JEV_API_KEY: '', TYPESAFE_API_KEY: '' } };
    const result = spawnSync(process.execPath, [HOOK_PATH], { ...options, input: JSON.stringify(stopPayload(clean)) });
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(JSON.parse(result.stdout), {});
    const malformed = spawnSync(process.execPath, [HOOK_PATH], { ...options, input: '{' });
    assert.equal(malformed.status, 0, malformed.stderr);
    assert.deepEqual(JSON.parse(malformed.stdout), {});
    const oversized = spawnSync(process.execPath, [HOOK_PATH], { ...options, input: 'x'.repeat(2_000_001) });
    assert.equal(oversized.status, 0, oversized.stderr);
    assert.deepEqual(JSON.parse(oversized.stdout), {});
  } finally { rmSync(home, { recursive: true, force: true }); }
});
