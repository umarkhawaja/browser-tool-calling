import { useEffect, useRef, useState } from "react";

const WS_URL = import.meta.env.VITE_WS_URL || "ws://localhost:8008/ws";

/**
 * Manages the WebSocket connection to the backend agent.
 * Tracks chat messages, the latest browser screenshot, and connection state,
 * and exposes send(text) and stop(). Messages sent while disconnected are
 * buffered and flushed on reconnect.
 */
export function useAgentSocket() {
  const [messages, setMessages] = useState([]);
  const [screenshot, setScreenshot] = useState(null);
  const [connected, setConnected] = useState(false);
  const [running, setRunning] = useState(false);
  const wsRef = useRef(null);
  const queueRef = useRef([]); // outgoing messages waiting for a live socket

  useEffect(() => {
    let closed = false;

    function handleEvent(e) {
      if (e.type === "screenshot") return setScreenshot(e.data);
      if (e.type === "status") return setRunning(e.text === "running");
      // thought / action / answer / note / error all become chat entries.
      setMessages((m) => [
        ...m,
        { role: "agent", kind: e.type, text: e.text, detail: e.detail },
      ]);
    }

    function connect() {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;
      ws.onopen = () => {
        setConnected(true);
        queueRef.current.forEach((o) => ws.send(JSON.stringify(o)));
        queueRef.current = [];
      };
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

  function rawSend(obj) {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
    else queueRef.current.push(obj); // buffer until the socket is back
  }

  function send(text) {
    const t = text.trim();
    if (!t) return;
    setMessages((m) => [...m, { role: "user", kind: "user", text: t }]);
    rawSend({ message: t });
  }

  function stop() {
    rawSend({ stop: true });
  }

  return { messages, screenshot, connected, running, send, stop };
}
