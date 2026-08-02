import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import AddressBar from "./AddressBar.jsx";
import AgentView from "./AgentView.jsx";
import { usePageInput } from "../hooks/usePageInput.js";

function ViewToggle({ showingAgentView, onChange }) {
  return (
    <div className="view-toggle" role="group" aria-label="Preview mode">
      <button
        type="button"
        className={!showingAgentView ? "on" : ""}
        onClick={() => onChange("you")}
        title="The page as you see it"
      >
        your view
      </button>
      <button
        type="button"
        className={showingAgentView ? "on agent" : ""}
        onClick={() => onChange("agent")}
        title="The numbered elements the model reasons over  (V)"
      >
        agent view
      </button>
    </div>
  );
}

function StatusStrip({
  controlling, agentDriving, showingAgentView,
  addressable, hidden, frameSize, onToggleControl,
}) {
  return (
    <div className="strip">
      <button
        type="button"
        className={`control-toggle ${controlling ? "on" : ""}`}
        onClick={onToggleControl}
        title={controlling
          ? "Give the browser back; the agent re-reads the page and continues"
          : "Drive the page yourself; the agent pauses after its current step"}
      >
        {controlling ? "Give back to agent" : "Take control"}
      </button>

      {controlling && <span className="by-you">you are driving</span>}
      {agentDriving && <span className="by-agent">agent is driving</span>}

      <div className="strip-right">
        {showingAgentView && (
          <span>
            {addressable} addressable
            {/* Elements past the model's limit cannot be clicked, because it is
                never told they exist. Worth saying out loud. */}
            {hidden > 0 && (
              <span className="unreachable"> · {hidden} beyond the model&apos;s reach</span>
            )}
          </span>
        )}
        {frameSize && <span>{frameSize.width}×{frameSize.height}</span>}
      </div>
    </div>
  );
}

export default function PreviewWindow({
  frame, page, running, paused, controlling, clickedIndex,
  view, onView, onTakeControl, onReleaseControl, onInput, onNavigate,
}) {
  const imageRef = useRef(null);
  const [scale, setScale] = useState(1);

  const frameSize = frame?.meta || { width: 1280, height: 800 };
  const elements = page?.elements || [];
  const hidden = Math.max(0, (page?.total ?? elements.length) - elements.length);
  const showingAgentView = view === "agent";

  const { surfaceRef, handlers } = usePageInput({
    active: controlling,
    imageRef,
    frameSize,
    onInput,
  });

  // Keep the overlay locked to the image as CSS scales it.
  useLayoutEffect(() => {
    const image = imageRef.current;
    if (!image) return;
    const measure = () => setScale((image.clientWidth || frameSize.width) / frameSize.width);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(image);
    return () => observer.disconnect();
  }, [frame, frameSize.width]);

  // V toggles the agent's view; Esc hands the browser back.
  useEffect(() => {
    function onKey(event) {
      if (event.target.matches?.("input, textarea")) return;
      if (event.key === "v" || event.key === "V") {
        onView(showingAgentView ? "you" : "agent");
      }
      if (event.key === "Escape" && controlling) onReleaseControl();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [showingAgentView, onView, controlling, onReleaseControl]);

  return (
    <main className="stage">
      <AddressBar url={page?.url} editable={controlling} onNavigate={onNavigate}>
        <ViewToggle showingAgentView={showingAgentView} onChange={onView} />
      </AddressBar>

      <div
        className={`stage-body ${controlling ? "live" : ""}`}
        ref={surfaceRef}
        tabIndex={controlling ? 0 : -1}
        {...handlers}
      >
        {frame ? (
          <div className="page">
            <img
              ref={imageRef}
              src={`data:image/jpeg;base64,${frame.data}`}
              alt="Live browser page"
              draggable={false}
            />
            {showingAgentView && (
              <AgentView
                elements={elements}
                scale={scale}
                clickedIndex={clickedIndex}
                frameHeight={frameSize.height}
              />
            )}
          </div>
        ) : (
          <div className="empty">
            <span>
              No page open yet. Ask the agent for something on the web, or press{" "}
              <strong>Take control</strong> to open the browser and drive it yourself.
            </span>
          </div>
        )}
      </div>

      <StatusStrip
        controlling={controlling}
        agentDriving={running && !paused && !controlling}
        showingAgentView={showingAgentView}
        addressable={elements.length}
        hidden={hidden}
        frameSize={frame ? frameSize : null}
        onToggleControl={controlling ? onReleaseControl : onTakeControl}
      />
    </main>
  );
}
