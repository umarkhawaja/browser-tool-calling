import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import ChatPanel from "./ChatPanel.jsx";

describe("ChatPanel", () => {
  it("shows the hint when there are no messages", () => {
    render(<ChatPanel messages={[]} onSend={() => {}} connected running={false} />);
    expect(screen.getByText(/Ask the agent to do something/i)).toBeInTheDocument();
  });

  it("renders user and agent messages", () => {
    const messages = [
      { role: "user", kind: "user", text: "hello there" },
      { role: "agent", kind: "answer", text: "all done" },
    ];
    render(<ChatPanel messages={messages} onSend={() => {}} connected running={false} />);
    expect(screen.getByText("hello there")).toBeInTheDocument();
    expect(screen.getByText("all done")).toBeInTheDocument();
  });

  it("calls onSend with the typed text and clears the input", () => {
    const onSend = vi.fn();
    render(<ChatPanel messages={[]} onSend={onSend} connected running={false} />);
    const input = screen.getByPlaceholderText(/Message the agent/i);
    fireEvent.change(input, { target: { value: "search HN" } });
    fireEvent.submit(input.closest("form"));
    expect(onSend).toHaveBeenCalledWith("search HN");
    expect(input.value).toBe("");
  });

  it("does not send while the agent is running", () => {
    const onSend = vi.fn();
    render(<ChatPanel messages={[]} onSend={onSend} connected running={true} />);
    const input = screen.getByPlaceholderText(/Agent is working/i);
    expect(input).toBeDisabled();
    fireEvent.submit(input.closest("form"));
    expect(onSend).not.toHaveBeenCalled();
  });
});
