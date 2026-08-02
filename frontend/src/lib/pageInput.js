/**
 * Translating browser events into the input protocol the backend speaks.
 * Kept free of React so the mapping can be tested on its own.
 */

const MODIFIER_KEYS = ["Shift", "Control", "Alt", "Meta"];

/**
 * Map a pointer event on the scaled preview back to a page coordinate.
 *
 * The frame is captured at the browser's real viewport size and then scaled by
 * CSS, so undoing that scale is the whole conversion — no scroll offset is
 * involved, because the screencast captures the viewport and Playwright's mouse
 * coordinates are viewport-relative too.
 */
export function toViewport(event, image, frameSize) {
  const box = image.getBoundingClientRect();
  const width = frameSize?.width || box.width;
  const height = frameSize?.height || box.height;
  return {
    x: Math.round(((event.clientX - box.left) / box.width) * width),
    y: Math.round(((event.clientY - box.top) / box.height) * height),
  };
}

/**
 * Map a keydown onto either typed text or a named key press, or null when the
 * key should be ignored. Playwright's key names match `event.key` ("Enter",
 * "Backspace", "ArrowLeft"), so single characters become text and everything
 * else is pressed by name.
 */
export function toKeyEvent(event) {
  if (MODIFIER_KEYS.includes(event.key)) return null;

  const modifiers = [];
  if (event.ctrlKey) modifiers.push("Control");
  if (event.metaKey) modifiers.push("Meta");
  if (event.altKey) modifiers.push("Alt");

  // A plain character arrives already shifted — "A" is "A" — so re-applying
  // Shift here would double it up.
  if (event.key.length === 1 && modifiers.length === 0) {
    return { kind: "type", text: event.key };
  }

  if (event.shiftKey && event.key.length > 1) modifiers.push("Shift");
  return { kind: "key", key: [...modifiers, event.key].join("+") };
}

/** Drop the scheme and trailing slash; the address bar has little room. */
export function tidyUrl(url) {
  if (!url || url === "about:blank") return "";
  return url.replace(/^https?:\/\//, "").replace(/\/$/, "");
}
