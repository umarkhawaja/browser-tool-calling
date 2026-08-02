import React from "react";

function seconds(ms) {
  return ms ? `${(ms / 1000).toFixed(1)}s` : "";
}

/**
 * The agent's own log for one turn. Steps are numbered because they really are
 * a sequence against a fixed budget, and timed because a local model can spend
 * anywhere from one second to thirty on a step.
 */
export default function Trace({ items }) {
  let stepNumber = 0;

  return (
    <div className="trace">
      {items.map((item, i) =>
        item.kind === "thought" ? (
          <p className="thought" key={i}>{item.text}</p>
        ) : (
          <div className="step" key={i}>
            <span className="step-number">
              {String(++stepNumber).padStart(2, "0")}
            </span>
            <span className="step-body">
              <span className="step-tool">{item.text}</span>
              {item.detail && <span className="step-detail">{item.detail}</span>}
            </span>
            <span className="step-elapsed">{seconds(item.elapsedMs)}</span>
          </div>
        )
      )}
    </div>
  );
}
