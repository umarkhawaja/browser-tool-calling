import React from "react";

/**
 * The step budget is drawn as discrete ticks because it *is* discrete — the
 * agent gets a fixed number of moves and then gives up. A continuous bar would
 * imply a smoothness the thing does not have.
 */
function Budget({ stepsTaken, maxSteps }) {
  const nearlySpent = stepsTaken > maxSteps - 4;

  return (
    <div className="budget" title={`${stepsTaken} of ${maxSteps} steps used this run`}>
      <span className="label">Budget</span>
      <span className="ticks">
        {Array.from({ length: maxSteps }, (_, i) => (
          <span
            key={i}
            className={`tick ${i < stepsTaken ? (nearlySpent ? "spent" : "used") : ""}`}
          />
        ))}
      </span>
      <span className="budget-count">{stepsTaken}/{maxSteps}</span>
    </div>
  );
}

export default function TopBar({
  connected, running, paused, controlling, stepsTaken, maxSteps, model,
}) {
  // The mark doubles as the run indicator: who, if anyone, is driving.
  const driver = controlling ? "human" : running && !paused ? "agent" : "";

  return (
    <header className="topbar">
      <div className="brand">
        <span className={`mark ${driver}`} />
        Browser Agent
      </div>
      {model && <span className="model">{model}</span>}

      <div className="topbar-right">
        <Budget stepsTaken={stepsTaken} maxSteps={maxSteps} />
        <span className="link-state">
          <span className={`led ${connected ? "on" : "off"}`} />
          {connected ? "connected" : "reconnecting"}
        </span>
      </div>
    </header>
  );
}
