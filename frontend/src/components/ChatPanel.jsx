import React, { useEffect, useRef } from "react";
import Composer from "./Composer.jsx";
import Trace from "./Trace.jsx";
import { Prose, groupIntoBlocks } from "../lib/transcript.jsx";

const SUGGESTIONS = [
  "Go to Hacker News and tell me the top story",
  "What's the weather in Karachi right now?",
  "Find the Playwright Python docs for locators",
];

const SHORTCUTS = [
  ["⌘K", "focus the message box"],
  ["V", "switch to the agent's view"],
  ["Esc", "hand the browser back"],
];

function Opening({ onPick }) {
  return (
    <div className="opening">
      <p>
        Ask for something on the web and the agent opens a browser to do it.
        Ask anything else and it just answers.
      </p>
      <div className="suggestions">
        {SUGGESTIONS.map((suggestion) => (
          <button key={suggestion} type="button" onClick={() => onPick(suggestion)}>
            {suggestion}
          </button>
        ))}
      </div>
      <div className="shortcuts">
        {SHORTCUTS.map(([key, meaning]) => (
          <div className="shortcut" key={key}>
            <kbd>{key}</kbd> {meaning}
          </div>
        ))}
      </div>
    </div>
  );
}

function Block({ block }) {
  switch (block.kind) {
    case "trace":
      return <Trace items={block.items} />;
    case "you":
      return <div className="you-said">{block.text}</div>;
    case "note":
      return <p className="note">{block.text}</p>;
    case "error":
      return <div className="error"><Prose text={block.text} /></div>;
    default:
      return <div className="answer"><Prose text={block.text} /></div>;
  }
}

export default function ChatPanel({ entries, streaming, onSend, onStop, running, paused }) {
  const bottom = useRef(null);
  const composer = useRef(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [entries, streaming, running]);

  const blocks = groupIntoBlocks(entries);
  const isEmpty = blocks.length === 0 && !streaming;

  return (
    <aside className="rail">
      <div className="rail-head">
        <span className="label">Transcript</span>
        {running && (
          <span className={`label ${paused ? "label-paused" : "label-working"}`}>
            {paused ? "Paused" : "Working"}
          </span>
        )}
      </div>

      <div className="transcript">
        {isEmpty && <Opening onPick={onSend} />}
        {blocks.map((block, i) => <Block block={block} key={i} />)}

        {/* The answer as it arrives, replaced by the settled message when the
            backend sends the authoritative version. */}
        {streaming && (
          <div className="answer streaming">
            <Prose text={streaming} />
            <span className="caret" />
          </div>
        )}
        {running && !streaming && (
          <p className="working">
            <span className="spinner" />
            {paused ? "paused — the browser is yours" : "thinking"}
          </p>
        )}
        <div ref={bottom} />
      </div>

      <Composer ref={composer} onSend={onSend} onStop={onStop} running={running} />
    </aside>
  );
}
