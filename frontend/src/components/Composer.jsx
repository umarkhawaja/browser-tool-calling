import React, { useEffect, useImperativeHandle, useRef, useState } from "react";

const MAX_HEIGHT = 160;

/**
 * The message box. Grows with its content, sends on Enter, and gives focus up
 * after sending: once a turn is away you are watching rather than typing, and
 * a still-focused box would swallow the V and Esc shortcuts.
 */
export default function Composer({ ref, onSend, onStop, running }) {
  const [text, setText] = useState("");
  const textarea = useRef(null);

  useImperativeHandle(ref, () => ({ focus: () => textarea.current?.focus() }), []);

  // ⌘K / Ctrl+K focuses the box from anywhere.
  useEffect(() => {
    function onKey(event) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        textarea.current?.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function resize() {
    const node = textarea.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${Math.min(node.scrollHeight, MAX_HEIGHT)}px`;
  }

  function submit() {
    if (!text.trim()) return;
    onSend(text.trim());
    setText("");
    textarea.current?.blur();
    requestAnimationFrame(resize);
  }

  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <textarea
        ref={textarea}
        rows={1}
        value={text}
        onChange={(event) => {
          setText(event.target.value);
          resize();
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
        placeholder="Ask the agent to do something…"
      />
      <div className="composer-row">
        <span className="composer-hint">Enter to send · Shift+Enter for a line break</span>
        {running ? (
          <button type="button" className="btn stop" onClick={onStop}>Stop</button>
        ) : (
          <button type="submit" className="btn go" disabled={!text.trim()}>Send</button>
        )}
      </div>
    </form>
  );
}
