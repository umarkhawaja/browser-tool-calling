import React from "react";

export default function PreviewWindow({ screenshot, running }) {
  return (
    <main className="preview">
      <header className="preview-header">
        <div className="dots">
          <span /><span /><span />
        </div>
        <span className="preview-title">Live browser preview</span>
        {running && <span className="live">● live</span>}
      </header>
      <div className="preview-body">
        {screenshot ? (
          <img src={`data:image/png;base64,${screenshot}`} alt="browser preview" />
        ) : (
          <div className="placeholder">
            The agent's browser will appear here once it starts navigating.
          </div>
        )}
      </div>
    </main>
  );
}
