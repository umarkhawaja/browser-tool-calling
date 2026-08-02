import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import ChatPanel from "./ChatPanel.jsx";

const at = 1_700_000_000_000;

function setup(props = {}) {
  const onSend = vi.fn();
  const onStop = vi.fn();
  const utils = render(
    <ChatPanel
      entries={[]} streaming="" onSend={onSend} onStop={onStop}
      running={false} paused={false} {...props}
    />
  );
  return { onSend, onStop, ...utils };
}

describe("ChatPanel", () => {
  it("opens with guidance and runnable suggestions", () => {
    const { onSend } = setup();
    expect(screen.getByText(/agent opens a browser/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Hacker News/i }));
    expect(onSend).toHaveBeenCalledWith("Go to Hacker News and tell me the top story");
  });

  it("renders your turn and the agent's answer", () => {
    setup({ entries: [
      { kind: "you", text: "hello there", at },
      { kind: "answer", text: "all done", at },
    ]});
    expect(screen.getByText("hello there")).toBeInTheDocument();
    expect(screen.getByText("all done")).toBeInTheDocument();
  });

  it("sends on Enter and clears the box; Shift+Enter does not send", () => {
    const { onSend } = setup();
    const ta = screen.getByPlaceholderText(/Ask the agent/i);
    fireEvent.change(ta, { target: { value: "hello" } });
    fireEvent.keyDown(ta, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.keyDown(ta, { key: "Enter", shiftKey: false });
    expect(onSend).toHaveBeenCalledWith("hello");
    expect(ta.value).toBe("");
  });

  it("swaps Send for Stop while a turn is running", () => {
    const { onStop } = setup({ running: true });
    fireEvent.click(screen.getByRole("button", { name: /stop/i }));
    expect(onStop).toHaveBeenCalled();
  });

  describe("trace", () => {
    const entries = [
      { kind: "you", text: "top stories", at },
      { kind: "action", text: "go_to_url", detail: "Navigated to https://news.ycombinator.com/", at: at + 1400 },
      { kind: "action", text: "click", detail: "Clicked element [8]", at: at + 2300 },
    ];

    it("numbers the steps in order", () => {
      setup({ entries });
      expect(screen.getByText("01")).toBeInTheDocument();
      expect(screen.getByText("02")).toBeInTheDocument();
    });

    it("times each step from the one before it", () => {
      setup({ entries });
      expect(screen.getByText("1.4s")).toBeInTheDocument();
      expect(screen.getByText("0.9s")).toBeInTheDocument();
    });

    it("groups consecutive steps into a single trace block", () => {
      setup({ entries });
      expect(document.querySelectorAll(".trace")).toHaveLength(1);
      expect(document.querySelectorAll(".step")).toHaveLength(2);
    });

    it("keeps the model's reasoning as prose, not as a step", () => {
      setup({ entries: [{ kind: "thought", text: "I should search first", at }] });
      expect(document.querySelectorAll(".step")).toHaveLength(0);
      expect(screen.getByText(/I should search first/)).toBeInTheDocument();
    });
  });

  describe("answers", () => {
    it("renders the reply as it streams, with a caret", () => {
      setup({ streaming: "The top story", running: true });
      expect(screen.getByText(/The top story/)).toBeInTheDocument();
      expect(document.querySelector(".caret")).toBeTruthy();
      expect(screen.queryByText(/thinking/)).toBeNull();
    });

    it("renders a list the model wrote as an actual list", () => {
      setup({ entries: [{ kind: "answer", text: "Top stories:\n1. Rust 2.0\n2. Show HN", at }] });
      expect(document.querySelectorAll("li")).toHaveLength(2);
      expect(screen.getByText("Rust 2.0")).toBeInTheDocument();
    });

    it("renders inline code and bold without injecting markup", () => {
      setup({ entries: [{ kind: "answer", text: "Use `npm run dev` and **stop**", at }] });
      expect(screen.getByText("npm run dev").tagName).toBe("CODE");
      expect(screen.getByText("stop").tagName).toBe("STRONG");
    });
  });

  it("shows the spinner while running with nothing streamed yet", () => {
    setup({ running: true });
    expect(screen.getByText(/thinking/)).toBeInTheDocument();
  });

  it("says who has the browser when paused", () => {
    setup({ running: true, paused: true });
    expect(screen.getByText(/the browser is yours/i)).toBeInTheDocument();
  });

  it("surfaces errors distinctly from answers", () => {
    setup({ entries: [{ kind: "error", text: "Could not reach Ollama", at }] });
    expect(document.querySelector(".error")).toHaveTextContent("Could not reach Ollama");
  });
});
