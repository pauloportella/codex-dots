import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

import {
  editedText,
  extractAddedText,
  handlePayload,
  isFirstPersonProse,
  isSubstantialProse
} from '../.codex/hooks/unslop-writing.mjs';

const HOOK_PATH = fileURLToPath(new URL('../.codex/hooks/unslop-writing.mjs', import.meta.url));
const clean = 'I walked to the station early this morning and bought coffee from the kiosk beside platform three. My train was delayed, so I called my sister and watched the maintenance crew replace a broken light. I reached the office before nine and finished the report by lunch.';
const sloppy = `${clean} It did not make the morning quieter. It made my attention feel like mine again.`;
const thirdPersonCopy = 'The studio serves independent designers who need a quiet place to meet clients, review samples, and prepare presentations. Members can reserve a private room, print large-format drafts, borrow lighting equipment, and store materials overnight. Located in the heart of downtown, it is a hidden gem that empowers creative teams to do their best work.';
const fencedCliche = `${clean}\n\n\`\`\`text\nIt did not make life quieter. It made my attention feel like mine again.\n\`\`\``;

function stopPayload(message, stopHookActive = false) {
  return {
    hook_event_name: 'Stop',
    stop_hook_active: stopHookActive,
    last_assistant_message: message
  };
}

function editPayload(text) {
  const added = text.split('\n').map(line => `+${line}`).join('\n');
  return {
    hook_event_name: 'PostToolUse',
    tool_name: 'apply_patch',
    tool_input: {
      command: `*** Begin Patch\n*** Add File: draft.txt\n${added}\n*** End Patch`
    }
  };
}

test('recognizes substantial first-person prose', () => {
  assert.equal(isSubstantialProse(thirdPersonCopy), true);
  assert.equal(isFirstPersonProse(clean), true);
  assert.equal(isFirstPersonProse(thirdPersonCopy), false);
  assert.equal(isFirstPersonProse('I wrote a short reply.'), false);
});

test('extracts only added patch lines', () => {
  assert.equal(extractAddedText(' context\n-old\n+new\n+++ metadata'), 'new');
});

test('extracts prose from supported edit tools', () => {
  assert.equal(editedText({ tool_name: 'Edit', tool_input: { new_string: 'replacement' } }), 'replacement');
  assert.equal(editedText({ tool_name: 'Write', tool_input: { content: 'document' } }), 'document');
});

test('Stop continues once for a cliché candidate', () => {
  const result = handlePayload(stopPayload(sloppy));
  assert.equal(result.decision, 'block');
  assert.match(result.reason, /not-just/);
  assert.match(result.reason, /Negative parallelisms/);
  assert.match(result.reason, /direct, affirmative prose/);
  assert.deepEqual(handlePayload(stopPayload(sloppy, true)), {});
});

test('Stop allows clean prose and non-copy responses', () => {
  assert.deepEqual(handlePayload(stopPayload(clean)), {});
  assert.deepEqual(handlePayload(stopPayload(thirdPersonCopy)), {});
  assert.deepEqual(handlePayload(stopPayload(fencedCliche)), {});
  assert.deepEqual(handlePayload(stopPayload('The report is complete.')), {});
});

test('PostToolUse reviews prose added by apply_patch', () => {
  const result = handlePayload(editPayload(sloppy));
  assert.equal(result.decision, 'block');
  assert.match(result.reason, /edited text/);
});

test('PostToolUse reviews prose added by Edit and Write', () => {
  for (const [tool_name, tool_input] of [
    ['Edit', { new_string: sloppy }],
    ['Write', { content: sloppy }]
  ]) {
    assert.equal(handlePayload({ hook_event_name: 'PostToolUse', tool_name, tool_input }).decision, 'block');
  }
});

test('PostToolUse reviews substantial copy without first-person language', () => {
  const result = handlePayload({
    hook_event_name: 'PostToolUse',
    tool_name: 'Write',
    tool_input: { content: thirdPersonCopy }
  });
  assert.equal(result.decision, 'block');
  assert.match(result.reason, /edited text/);
});

test('PostToolUse ignores clichés confined to fenced code', () => {
  assert.deepEqual(handlePayload({
    hook_event_name: 'PostToolUse',
    tool_name: 'Write',
    tool_input: { content: fencedCliche }
  }), {});
});

test('command hook emits valid Stop JSON', () => {
  const result = spawnSync(process.execPath, [HOOK_PATH], {
    input: JSON.stringify(stopPayload(sloppy)),
    encoding: 'utf8'
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).decision, 'block');
});
