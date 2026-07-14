import React, { useEffect, useRef, useState } from "react";

const KIND_LABEL = {
  thought: "thinking",
  action: "action",
  answer: "answer",
  error: "error",
};

export default function ChatPanel({ messages, onSend, connected, running }) {
  const [text, setText] = useState("");
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const submit = (e) => {
    e.preventDefault();
    const t = text.trim();
    if (!t || running) return;
    onSend(t);
    setText("");
  };

  return (
    <aside className="chat">
      <header className="chat-header">
        <span className="title">Browser Agent</span>
        <span className={`dot ${connected ? "on" : "off"}`} title={connected ? "connected" : "disconnected"} />
      </header>

      <div className="messages">
        {messages.length === 0 && (
          <p className="hint">
            Ask the agent to do something on the web, e.g.{" "}
            <em>"Search Hacker News for the top story about AI."</em>
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role} ${m.kind}`}>
            {m.role === "agent" && m.kind !== "user" && (
              <span className="tag">{KIND_LABEL[m.kind] || m.kind}</span>
            )}
            <div className="bubble">
              {m.text}
              {m.detail && <div className="detail">{m.detail}</div>}
            </div>
          </div>
        ))}
        {running && <div className="msg agent running"><div className="bubble typing">working…</div></div>}
        <div ref={endRef} />
      </div>

      <form className="composer" onSubmit={submit}>
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={running ? "Agent is working…" : "Message the agent…"}
          disabled={running}
        />
        <button type="submit" disabled={running || !text.trim()}>Send</button>
      </form>
    </aside>
  );
}
