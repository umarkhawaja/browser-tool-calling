import { useEffect, useRef, useState } from "react";

const STEP_LABEL = { thought: "thinking", action: "action" };

export default function ChatPanel({ messages, onSend, onStop, connected, running }) {
  const [text, setText] = useState("");
  const endRef = useRef(null);
  const taRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, running]);

  function resize() {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 140) + "px";
  }

  function submit() {
    const t = text.trim();
    if (!t) return;
    onSend(t);
    setText("");
    requestAnimationFrame(resize);
  }

  function onKeyDown(e) {
    // Enter sends; Shift+Enter inserts a newline.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <aside className="chat">
      <header className="chat-header">
        <span className="title">Browser Agent</span>
        <span
          className={`dot ${connected ? "on" : "off"}`}
          title={connected ? "connected" : "disconnected"}
        />
      </header>

      <div className="messages">
        {messages.length === 0 && (
          <p className="hint">
            Chat with the agent, or ask it to do something on the web — e.g.{" "}
            <em>"Search Hacker News for the top AI story."</em>
          </p>
        )}
        {messages.map((m, i) => (
          <Message key={i} m={m} />
        ))}
        {running && (
          <div className="step">
            <span className="spinner" /> working…
          </div>
        )}
        <div ref={endRef} />
      </div>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <textarea
          ref={taRef}
          rows={1}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            resize();
          }}
          onKeyDown={onKeyDown}
          placeholder="Message the agent…  (Enter to send, Shift+Enter for a new line)"
        />
        {running ? (
          <button type="button" className="stop" onClick={onStop}>
            Stop
          </button>
        ) : (
          <button type="submit" disabled={!text.trim()}>
            Send
          </button>
        )}
      </form>
    </aside>
  );
}

/** One chat entry. Thoughts/actions render as compact muted step lines. */
function Message({ m }) {
  if (m.kind === "thought" || m.kind === "action") {
    return (
      <div className={`step ${m.kind}`}>
        <span className="step-tag">{STEP_LABEL[m.kind]}</span>
        <span className="step-text">
          {m.text}
          {m.detail ? ` — ${m.detail}` : ""}
        </span>
      </div>
    );
  }
  if (m.kind === "note") {
    return <div className="note">{m.text}</div>;
  }
  return (
    <div className={`msg ${m.role} ${m.kind}`}>
      <div className="bubble">{m.text}</div>
    </div>
  );
}
