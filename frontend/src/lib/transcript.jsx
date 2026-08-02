/**
 * Turning what the backend streams into what the transcript renders:
 * a tiny prose renderer, and the grouping of narration into trace blocks.
 */

const INLINE_PATTERN = /(`[^`]+`|\*\*[^*]+\*\*)/g;
const BULLET_PATTERN = /^\s*(?:[-*•]|\d+[.)])\s+(.*)$/;

/** Inline `code` and **bold**, built as React nodes so nothing is injected. */
function renderInline(text) {
  const parts = [];
  let cursor = 0;
  let key = 0;
  let match;

  while ((match = INLINE_PATTERN.exec(text))) {
    if (match.index > cursor) parts.push(text.slice(cursor, match.index));
    const token = match[0];
    parts.push(
      token.startsWith("`")
        ? <code key={key++}>{token.slice(1, -1)}</code>
        : <strong key={key++}>{token.slice(2, -2)}</strong>
    );
    cursor = match.index + token.length;
  }
  if (cursor < text.length) parts.push(text.slice(cursor));
  return parts;
}

/**
 * The little structure a local model actually produces: paragraphs and lists.
 * Answers to "top stories" are lists nine times in ten, and rendering them as
 * one grey wall costs more legibility than this costs complexity.
 */
export function Prose({ text }) {
  const blocks = [];
  let list = null;

  for (const line of String(text).split("\n")) {
    const bullet = BULLET_PATTERN.exec(line.trimEnd());
    if (bullet) {
      (list ||= []).push(bullet[1]);
      continue;
    }
    if (list) {
      blocks.push({ type: "list", items: list });
      list = null;
    }
    if (line.trim()) blocks.push({ type: "paragraph", text: line.trimEnd() });
  }
  if (list) blocks.push({ type: "list", items: list });

  return blocks.map((block, i) =>
    block.type === "list" ? (
      <ul key={i}>{block.items.map((item, j) => <li key={j}>{renderInline(item)}</li>)}</ul>
    ) : (
      <p key={i}>{renderInline(block.text)}</p>
    )
  );
}

const NARRATION = ["thought", "action"];

/**
 * Fold consecutive narration into one trace block and time each entry from the
 * one before it, so a step's duration covers the model call plus the action.
 */
export function groupIntoBlocks(entries) {
  const blocks = [];

  entries.forEach((entry, i) => {
    const elapsedMs = i > 0 ? entry.at - entries[i - 1].at : 0;
    const timed = { ...entry, elapsedMs };

    if (!NARRATION.includes(entry.kind)) {
      blocks.push(timed);
      return;
    }
    const last = blocks[blocks.length - 1];
    if (last && last.kind === "trace") last.items.push(timed);
    else blocks.push({ kind: "trace", items: [timed] });
  });

  return blocks;
}
