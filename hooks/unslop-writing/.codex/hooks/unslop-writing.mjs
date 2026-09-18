import { realpathSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

import { classify } from './unslop-jev.mjs';

const MIN_WORDS = 40;
const THRESHOLD = 0.50;
const MAX_INPUT_BYTES = 1_048_576;
const WRITING_REQUEST = 'Review this prose for the specified contextual cliché mechanisms. Preserve literal meaning, concrete technical distinctions, and language quoted for discussion.';

function stripFencedCode(text) {
  return text.replace(/```[\s\S]*?```/g, ' ');
}

function extractAddedText(patch) {
  if (typeof patch !== 'string') return '';
  return patch
    .split(/\r?\n/)
    .filter(line => line.startsWith('+') && !line.startsWith('+++'))
    .map(line => line.slice(1))
    .join('\n');
}

function editedText(payload) {
  if (payload.tool_name === 'apply_patch') return extractAddedText(payload.tool_input?.command ?? '');
  if (payload.tool_name === 'Edit') return payload.tool_input?.new_string ?? '';
  if (payload.tool_name === 'Write') return payload.tool_input?.content ?? '';
  return '';
}

function isSubstantialProse(text) {
  const prose = stripFencedCode(text);
  const words = prose.match(/[\p{L}\p{N}][\p{L}\p{N}\u2019'-]*/gu) ?? [];
  return words.length >= MIN_WORDS;
}

function isFirstPersonProse(text) {
  const prose = stripFencedCode(text);
  return isSubstantialProse(prose)
    && /(?:^|[\s\u201c"'(])(?:I|I['\u2019](?:m|ve|d|ll)|my|mine|me)(?=$|[\s.,!?;:\u201d"')])/im.test(prose);
}

function continuation(source) {
  const location = source === 'Stop' ? 'final response' : 'edited text';

  return {
    decision: 'block',
    reason: `Jev's contextual cliché policy flagged the ${location}. Review it against the categories and examples in .codex/hooks/unslop-question.json, then make one revision as direct, affirmative prose. Preserve literal and technical wording, useful factual contrasts and lists, quotations, and explicitly requested styles. Revise only actual cliché uses; a familiar word alone is not a finding.`
  };
}

async function handlePayload(payload, { judge = classify } = {}) {
  if (!payload || typeof payload !== 'object') return {};
  let text;
  if (payload.hook_event_name === 'Stop') {
    if (payload.stop_hook_active || typeof payload.last_assistant_message !== 'string') return {};
    text = stripFencedCode(payload.last_assistant_message);
    if (!isFirstPersonProse(text)) return {};
  } else if (payload.hook_event_name === 'PostToolUse') {
    const edited = editedText(payload);
    if (typeof edited !== 'string') return {};
    text = stripFencedCode(edited);
    if (!isSubstantialProse(text)) return {};
  } else {
    return {};
  }
  try {
    const score = await judge({ writing_request: WRITING_REQUEST, text });
    if (typeof score === 'number' && Number.isFinite(score) && score >= THRESHOLD && score <= 1) {
      return continuation(payload.hook_event_name);
    }
  } catch {
    // Remote failure must not trap an editing session; never fall back to regex judgments.
  }
  return {};
}

async function readStdin() {
  const chunks = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > MAX_INPUT_BYTES) throw new Error('input_too_large');
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString('utf8');
}

async function main() {
  const payload = JSON.parse(await readStdin());
  console.log(JSON.stringify(await handlePayload(payload)));
}

export { editedText, extractAddedText, handlePayload, isFirstPersonProse, isSubstantialProse, stripFencedCode };

const isMain = process.argv[1]
  && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href;

if (isMain) {
  main().catch(() => {
    // Malformed input and I/O errors also fail open without exposing prose or secrets.
    console.log('{}');
  });
}
