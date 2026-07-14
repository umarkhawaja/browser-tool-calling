import ChatPanel from "./components/ChatPanel.jsx";
import PreviewWindow from "./components/PreviewWindow.jsx";
import { useAgentSocket } from "./hooks/useAgentSocket.js";

export default function App() {
  const { messages, screenshot, connected, running, send, stop } = useAgentSocket();

  return (
    <div className="app">
      <ChatPanel
        messages={messages}
        onSend={send}
        onStop={stop}
        connected={connected}
        running={running}
      />
      <PreviewWindow screenshot={screenshot} running={running} />
    </div>
  );
}
