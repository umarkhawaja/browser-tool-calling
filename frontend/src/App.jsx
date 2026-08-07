import { useState } from "react";
import ChatPanel from "./components/ChatPanel.jsx";
import PreviewWindow from "./components/PreviewWindow.jsx";
import TopBar from "./components/TopBar.jsx";
import { useAgentSocket } from "./hooks/useAgentSocket.js";

export default function App() {
  const agent = useAgentSocket();
  const [view, setView] = useState("you");

  return (
    <div className="app">
      <TopBar
        connected={agent.connected}
        running={agent.running}
        paused={agent.paused}
        controlling={agent.controlling}
        stepsTaken={agent.stepsTaken}
        maxSteps={agent.maxSteps}
        model={agent.model}
      />
      <div className="body">
        <ChatPanel
          entries={agent.entries}
          streaming={agent.streaming}
          running={agent.running}
          paused={agent.paused}
          onSend={agent.ask}
          onStop={agent.stop}
        />
        <PreviewWindow
          frame={agent.frame}
          page={agent.page}
          running={agent.running}
          paused={agent.paused}
          controlling={agent.controlling}
          view={view}
          onView={setView}
          onTakeControl={agent.takeControl}
          onReleaseControl={agent.releaseControl}
          onInput={agent.sendInput}
          onNavigate={agent.navigate}
        />
      </div>
    </div>
  );
}
