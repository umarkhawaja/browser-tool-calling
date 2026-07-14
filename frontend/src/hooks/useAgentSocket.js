import { useEffect, useRef, useState } from "react";

const WS_URL = import.meta.env.VITE_WS_URL || "ws://localhost:8008/ws";

/**
 * Manages the WebSocket connection to the backend agent.
 * Tracks the chat messages, the latest browser screenshot, and connection
 * state, and exposes `send(task)` to kick off a new agent run.
 */
export function useAgentSocket() {
  const [messages, setMessages] = useState([]);
  const [screenshot, setScreenshot] = useState(null);
  const [connected, setConnected] = useState(false);
  const [running, setRunning] = useState(false);
  const wsRef = useRef(null);

  useEffect(() => {
    let closed = false;

    function handleEvent(e) {
      if (e.type === "screenshot") return setScreenshot(e.data);
      if (e.type === "status") return setRunning(e.text === "running");
      // thought / action / answer / error all become chat messages.
      setMessages((m) => [
        ...m,
        { role: "agent", kind: e.type, text: e.text, detail: e.detail },
      ]);
    }

    function connect() {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!closed) setTimeout(connect, 1500); // auto-reconnect
      };
      ws.onmessage = (ev) => handleEvent(JSON.parse(ev.data));
    }

    connect();
    return () => {
      closed = true;
      wsRef.current?.close();
    };
  }, []);

  function send(task) {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    setMessages((m) => [...m, { role: "user", kind: "user", text: task }]);
    ws.send(JSON.stringify({ task }));
  }

  return { messages, screenshot, connected, running, send };
}
