// Tolerant parser for streaming tool-input JSON: closes open strings, arrays and objects
// so fields like `command` or `file_path` can be shown while they are still arriving.

export function parsePartialJson(src: string): unknown {
  if (!src) return {};
  try {
    return JSON.parse(src);
  } catch {
    /* fall through to repair */
  }
  const stack: string[] = [];
  let inString = false;
  let escaped = false;
  for (let i = 0; i < src.length; i++) {
    const c = src[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (c === '\\') escaped = true;
      else if (c === '"') inString = false;
      continue;
    }
    if (c === '"') inString = true;
    else if (c === '{') stack.push('}');
    else if (c === '[') stack.push(']');
    else if (c === '}' || c === ']') stack.pop();
  }
  let repaired = src;
  if (inString) {
    if (escaped) repaired = repaired.slice(0, -1);
    repaired += '"';
  }
  // Drop a dangling key or separator before closing.
  repaired = repaired.replace(/,\s*$/, '').replace(/,\s*"[^"]*"\s*:?\s*$/, '').replace(/:\s*$/, ': null');
  repaired += stack.reverse().join('');
  try {
    return JSON.parse(repaired);
  } catch {
    return null;
  }
}
