import { useCallback, useEffect, useRef, useState } from "react";

const WS_URL = import.meta.env.VITE_WS_URL || "ws://localhost:8008/ws";
const RECONNECT_DELAY_MS = 1500;

/** Pull the element index out of a click result: "Clicked element [8] 'News'". */
function parseClickedIndex(detail) {
  const match = /element \[(\d+)\]/.exec(detail || "");
  return match ? Number(match[1]) : null;
}

/**
 * Owns the WebSocket to the agent and every piece of state the UI draws from.
 *
 * Chat messages sent while disconnected are buffered and flushed on reconnect.
 * Browser input is not: replaying a stale click would land it blind on whatever
 * page happens to be open by then.
 */
export function useAgentSocket() {
  const [entries, setEntries] = useState([]);
  const [frame, setFrame] = useState(null);
  const [page, setPage] = useState(null);
  const [connected, setConnected] = useState(false);
  const [running, setRunning] = useState(false);
  const [paused, setPaused] = useState(false);
  const [controlling, setControlling] = useState(false);
  const [streaming, setStreaming] = useState("");
  const [stepsTaken, setStepsTaken] = useState(0);
  const [clickedIndex, setClickedIndex] = useState(null);
  const [model, setModel] = useState("");
  const [maxSteps, setMaxSteps] = useState(15);

  const socket = useRef(null);
  const pending = useRef([]);

  useEffect(() => {
    let unmounted = false;

    function handleEvent(event) {
      switch (event.type) {
        case "hello":
          setModel(event.model);
          setMaxSteps(event.max_steps || 15);
          return;
        case "screenshot":
          return setFrame({ data: event.data, meta: event.meta });
        case "page":
          return setPage({
            url: event.url,
            elements: event.elements || [],
            total: event.total,
          });
        case "token":
          return setStreaming((text) => text + event.text);
        case "token_reset":
          return setStreaming("");
        case "status":
          setRunning(event.text !== "idle"); // a paused turn is interruptible
          setPaused(event.text === "paused");
          return;
        case "action":
          setStepsTaken((n) => n + 1);
          if (parseClickedIndex(event.detail) !== null) {
            setClickedIndex(parseClickedIndex(event.detail));
          }
          break;
        default:
          break;
      }
      // thought/action/answer/note/error are the settled form of whatever was
      // streaming, so the live buffer is dropped rather than duplicated.
      setStreaming("");
      setEntries((all) => [
        ...all,
        { kind: event.type, text: event.text, detail: event.detail, at: Date.now() },
      ]);
    }

    function connect() {
      const ws = new WebSocket(WS_URL);
      socket.current = ws;
      ws.onopen = () => {
        setConnected(true);
        pending.current.forEach((message) => ws.send(JSON.stringify(message)));
        pending.current = [];
      };
      ws.onclose = () => {
        setConnected(false);
        setControlling(false); // control is per-connection on the backend
        if (!unmounted) setTimeout(connect, RECONNECT_DELAY_MS);
      };
      ws.onmessage = (message) => handleEvent(JSON.parse(message.data));
    }

    connect();
    return () => {
      unmounted = true;
      socket.current?.close();
    };
  }, []);

  /** Queued while offline and flushed on reconnect. */
  function send(message) {
    const ws = socket.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(message));
    else pending.current.push(message);
  }

  /** Dropped while offline, never replayed. */
  const sendNow = useCallback((message) => {
    const ws = socket.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(message));
  }, []);

  function ask(text) {
    const trimmed = text.trim();
    if (!trimmed) return;
    setStepsTaken(0);
    setClickedIndex(null);
    setEntries((all) => [...all, { kind: "you", text: trimmed, at: Date.now() }]);
    send({ message: trimmed });
  }

  function takeControl() {
    setControlling(true);
    send({ control: "user" });
  }

  function releaseControl() {
    setControlling(false);
    send({ control: "agent" });
  }

  return {
    entries, frame, page, connected, running, paused, controlling, streaming,
    stepsTaken, clickedIndex, model, maxSteps,
    ask,
    stop: () => send({ stop: true }),
    takeControl,
    releaseControl,
    // Stable identities: PreviewWindow binds these inside effects.
    sendInput: useCallback((input) => sendNow({ input }), [sendNow]),
    navigate: useCallback((url) => sendNow({ navigate: url }), [sendNow]),
  };
}
