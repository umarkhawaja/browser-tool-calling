import ChatPanel from "./components/ChatPanel.jsx";
import PreviewWindow from "./components/PreviewWindow.jsx";
import { useAgentSocket } from "./hooks/useAgentSocket.js";

export default function App() {
  const { messages, screenshot, connected, running, send } = useAgentSocket();

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
