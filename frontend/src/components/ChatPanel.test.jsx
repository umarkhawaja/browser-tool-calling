import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import ChatPanel from "./ChatPanel.jsx";

const noop = () => {};

describe("ChatPanel", () => {
  it("shows the hint when there are no messages", () => {
    render(<ChatPanel messages={[]} onSend={noop} onStop={noop} connected running={false} />);
    expect(screen.getByText(/do something on the web/i)).toBeInTheDocument();
  });

  it("renders user and answer messages as bubbles", () => {
    const messages = [
      { role: "user", kind: "user", text: "hello there" },
      { role: "agent", kind: "answer", text: "all done" },
    ];
    render(<ChatPanel messages={messages} onSend={noop} onStop={noop} connected running={false} />);
    expect(screen.getByText("hello there")).toBeInTheDocument();
    expect(screen.getByText("all done")).toBeInTheDocument();
  });

  it("renders thoughts/actions as compact step lines", () => {
    const messages = [{ role: "agent", kind: "action", text: "click", detail: "Clicked [3]" }];
    render(<ChatPanel messages={messages} onSend={noop} onStop={noop} connected running={false} />);
    expect(screen.getByText("action")).toBeInTheDocument();
    expect(screen.getByText(/Clicked \[3\]/)).toBeInTheDocument();
  });

  it("sends on Enter and clears the input; Shift+Enter does not send", () => {
    const onSend = vi.fn();
    render(<ChatPanel messages={[]} onSend={onSend} onStop={noop} connected running={false} />);
    const ta = screen.getByPlaceholderText(/Message the agent/i);

    fireEvent.change(ta, { target: { value: "hello" } });
    fireEvent.keyDown(ta, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();

    fireEvent.keyDown(ta, { key: "Enter", shiftKey: false });
    expect(onSend).toHaveBeenCalledWith("hello");
    expect(ta.value).toBe("");
  });

  it("keeps the input enabled while running and shows a Stop button", () => {
    const onStop = vi.fn();
    render(<ChatPanel messages={[]} onSend={noop} onStop={onStop} connected running={true} />);
    expect(screen.getByPlaceholderText(/Message the agent/i)).not.toBeDisabled();
    const stop = screen.getByRole("button", { name: /stop/i });
    fireEvent.click(stop);
    expect(onStop).toHaveBeenCalled();
  });
});
