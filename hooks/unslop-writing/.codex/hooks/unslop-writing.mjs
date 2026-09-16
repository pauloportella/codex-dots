import { realpathSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

import { analyzeCliches } from './cliche-detector.mjs';

const MIN_WORDS = 40;

function stripFencedCode(text) {
  return text.replace(/```[\s\S]*?```/g, ' ');
}

function extractAddedText(patch) {
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

function continuation(matches, source) {
  const findings = matches.slice(0, 8).map(match =>
    `- [${match.patternId}] ${JSON.stringify(match.text)}: ${match.description}`
  ).join('\n');
  const location = source === 'Stop' ? 'final response' : 'edited text';

  return {
    decision: 'block',
    reason: `The ${location} contains cliché candidates:\n${findings}\nRewrite every actionable match as direct, affirmative prose. Preserve deliberate language, then check the complete revision against every finding before finishing.`
  };
}

function handlePayload(payload) {
  if (payload.hook_event_name === 'Stop') {
    if (payload.stop_hook_active || typeof payload.last_assistant_message !== 'string') return {};
    const text = stripFencedCode(payload.last_assistant_message);
    if (!isFirstPersonProse(text)) return {};
    const matches = analyzeCliches(text);
    return matches.length ? continuation(matches, 'Stop') : {};
  }

  if (payload.hook_event_name === 'PostToolUse') {
    const text = stripFencedCode(editedText(payload));
    if (!isSubstantialProse(text)) return {};
    const matches = analyzeCliches(text);
    return matches.length ? continuation(matches, 'PostToolUse') : {};
  }

  return {};
}

async function readStdin() {
  let input = '';
  process.stdin.setEncoding('utf8');
  for await (const chunk of process.stdin) input += chunk;
  return input;
}

async function main() {
  const payload = JSON.parse(await readStdin());
  console.log(JSON.stringify(handlePayload(payload)));
}

export { editedText, extractAddedText, handlePayload, isFirstPersonProse, isSubstantialProse, stripFencedCode };

const isMain = process.argv[1]
  && import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href;

if (isMain) {
  main().catch(error => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
