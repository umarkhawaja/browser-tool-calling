import React, { useState } from "react";
import { tidyUrl } from "../lib/pageInput.js";

/**
 * The current page, and — once you hold the browser — somewhere to type a new
 * address. The agent's `go_to_url` tool is not reachable by the human, so
 * without this there is no way to navigate yourself.
 */
export default function AddressBar({ url, editable, onNavigate, children }) {
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false);

  function submit(event) {
    event.preventDefault();
    if (!draft.trim()) return;
    onNavigate(draft.trim());
    setEditing(false);
  }

  return (
    <div className="address">
      <form className={`url ${editable ? "editable" : ""}`} onSubmit={submit}>
        <span className="url-marker">{editable ? "→" : "◇"}</span>
        <input
          value={editing ? draft : tidyUrl(url)}
          disabled={!editable}
          spellCheck={false}
          placeholder={editable ? "type an address and press Enter" : "no page open"}
          onFocus={() => {
            setDraft(tidyUrl(url));
            setEditing(true);
          }}
          onBlur={() => setEditing(false)}
          onChange={(event) => setDraft(event.target.value)}
          aria-label="Address"
        />
      </form>
      {children}
    </div>
  );
}
