import React, { useEffect, useRef, useState } from "react";
import ChatPanel from "./components/ChatPanel.jsx";
import PreviewWindow from "./components/PreviewWindow.jsx";

const WS_URL = import.meta.env.VITE_WS_URL || "ws://localhost:8008/ws";

export default function App() {
  const [messages, setMessages] = useState([]); // {role, kind, text}
  const [screenshot, setScreenshot] = useState(null); // base64 png
  const [connected, setConnected] = useState(false);
  const [running, setRunning] = useState(false);
  const wsRef = useRef(null);

  const push = (msg) => setMessages((m) => [...m, msg]);

  useEffect(() => {
    let closed = false;
    function connect() {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!closed) setTimeout(connect, 1500); // auto-reconnect
      };
      ws.onmessage = (ev) => {
        const e = JSON.parse(ev.data);
        switch (e.type) {
          case "screenshot":
            setScreenshot(e.data);
            break;
          case "status":
            setRunning(e.text === "running");
            break;
          case "thought":
            push({ role: "agent", kind: "thought", text: e.text });
            break;
          case "action":
            push({ role: "agent", kind: "action", text: e.text, detail: e.detail });
            break;
          case "answer":
            push({ role: "agent", kind: "answer", text: e.text });
            break;
          case "error":
            push({ role: "agent", kind: "error", text: e.text });
            break;
          default:
            break;
        }
      };
    }
    connect();
    return () => {
      closed = true;
      wsRef.current && wsRef.current.close();
    };
  }, []);

  const send = (task) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    push({ role: "user", kind: "user", text: task });
    wsRef.current.send(JSON.stringify({ task }));
  };

  return (
    <div className="app">
      <ChatPanel
        messages={messages}
        onSend={send}
        connected={connected}
        running={running}
      />
      <PreviewWindow screenshot={screenshot} running={running} />
    </div>
  );
}
