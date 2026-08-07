import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeAll } from "vitest";
import PreviewWindow from "./PreviewWindow.jsx";

const noop = () => {};
const FRAME = { data: "ABC123", meta: { width: 1280, height: 800 } };
const PAGE = {
  url: "https://news.ycombinator.com/",
  total: 2,
  elements: [
    { index: 0, tag: "a", type: "", label: "Home", rect: [10, 20, 100, 30] },
    { index: 8, tag: "a", type: "", label: "News", rect: [10, 60, 100, 30] },
  ],
};

beforeAll(() => {
  // jsdom has no ResizeObserver; the overlay measures the image with one.
  global.ResizeObserver = class {
    observe() {}
    disconnect() {}
  };
});

function setup(props = {}) {
  const onInput = vi.fn();
  const onNavigate = vi.fn();
  const onView = vi.fn();
  const utils = render(
    <PreviewWindow
      frame={FRAME} page={PAGE} running={false} paused={false}
      controlling={false} view="you"
      onView={onView} onTakeControl={noop} onReleaseControl={noop}
      onInput={onInput} onNavigate={onNavigate}
      {...props}
    />
  );
  return { onInput, onNavigate, onView, ...utils };
}

function stubImageBox({ left = 0, top = 0, width = 640, height = 400 } = {}) {
  const img = screen.getByAltText("Live browser page");
  vi.spyOn(img, "getBoundingClientRect").mockReturnValue({
    left, top, width, height, right: left + width, bottom: top + height, x: left, y: top,
  });
  return img;
}

const surface = () => document.querySelector(".stage-body");

describe("PreviewWindow", () => {
  it("shows a placeholder when no page is open", () => {
    setup({ frame: null });
    expect(screen.getByText(/No page open yet/i)).toBeInTheDocument();
  });

  it("renders the frame as a data-URI image", () => {
    setup();
    expect(screen.getByAltText("Live browser page")).toHaveAttribute(
      "src", "data:image/jpeg;base64,ABC123"
    );
  });

  describe("address bar", () => {
    it("shows the current page, tidied", () => {
      setup();
      expect(screen.getByLabelText("Address")).toHaveValue("news.ycombinator.com");
    });

    it("is read-only until you take control", () => {
      setup({ controlling: false });
      expect(screen.getByLabelText("Address")).toBeDisabled();
    });

    it("navigates on submit once you hold control", () => {
      const { onNavigate } = setup({ controlling: true });
      const input = screen.getByLabelText("Address");
      fireEvent.focus(input);
      fireEvent.change(input, { target: { value: "example.com" } });
      fireEvent.submit(input.closest("form"));
      expect(onNavigate).toHaveBeenCalledWith("example.com");
    });
  });

  describe("agent view", () => {
    it("draws no element boxes in your view", () => {
      setup({ view: "you" });
      expect(document.querySelectorAll(".element-box")).toHaveLength(0);
    });

    it("draws one numbered box per interactive element", () => {
      setup({ view: "agent" });
      expect(document.querySelectorAll(".element-box")).toHaveLength(2);
      expect(screen.getByText("8")).toBeInTheDocument();
    });

    it("labels each box with what the model sees", () => {
      setup({ view: "agent" });
      expect(document.querySelector('[title="[8] <a> News"]')).toBeTruthy();
    });

    it("marks the element the agent just clicked", () => {
      // The listing says which one it was, not the number in the click result:
      // a click that moves the page renumbers what is on it.
      const [home, news] = PAGE.elements;
      setup({
        view: "agent",
        page: { ...PAGE, elements: [home, { ...news, clicked: true }] },
      });
      const hits = document.querySelectorAll(".element-box.clicked");
      expect(hits).toHaveLength(1);
      expect(hits[0].textContent).toBe("8");
    });

    it("marks nothing when the click took the page somewhere else", () => {
      setup({ view: "agent" });
      expect(document.querySelectorAll(".element-box.clicked")).toHaveLength(0);
    });

    it("reports how many elements the model can address", () => {
      setup({ view: "agent" });
      expect(screen.getByText(/2 addressable/)).toBeInTheDocument();
    });

    it("warns when the page has more elements than the model was shown", () => {
      // The overlay must not imply the agent can click things it never saw.
      setup({ view: "agent", page: { ...PAGE, total: 228 } });
      expect(screen.getByText(/226 beyond the model's reach/)).toBeInTheDocument();
    });

    it("says nothing about hidden elements when none are hidden", () => {
      setup({ view: "agent" });
      expect(screen.queryByText(/beyond the model/)).toBeNull();
    });

    it("skips elements scrolled out of the viewport", () => {
      setup({ view: "agent", page: { ...PAGE, elements: [
        ...PAGE.elements,
        { index: 9, tag: "a", label: "Below the fold", rect: [10, 1400, 100, 30] },
        { index: 10, tag: "a", label: "Above it", rect: [10, -80, 100, 30] },
      ]}});
      expect(document.querySelectorAll(".element-box")).toHaveLength(2);
    });

    it("toggles on V", () => {
      const { onView } = setup({ view: "you" });
      fireEvent.keyDown(window, { key: "v" });
      expect(onView).toHaveBeenCalledWith("agent");
    });
  });

  describe("input forwarding", () => {
    it("sends nothing while the agent holds the browser", () => {
      const { onInput } = setup({ controlling: false });
      stubImageBox();
      fireEvent.click(surface(), { clientX: 100, clientY: 50 });
      expect(onInput).not.toHaveBeenCalled();
    });

    it("maps a click on the scaled image back to viewport coordinates", () => {
      const { onInput } = setup({ controlling: true });
      stubImageBox({ width: 640, height: 400 }); // rendered at half size
      fireEvent.click(surface(), { clientX: 320, clientY: 200, detail: 1 });
      expect(onInput).toHaveBeenCalledWith({
        kind: "click", x: 640, y: 400, button: "left", clicks: 1,
      });
    });

    it("accounts for the image's offset on screen", () => {
      const { onInput } = setup({ controlling: true });
      stubImageBox({ left: 100, top: 40, width: 1280, height: 800 });
      fireEvent.click(surface(), { clientX: 150, clientY: 90, detail: 1 });
      expect(onInput).toHaveBeenCalledWith(expect.objectContaining({ x: 50, y: 50 }));
    });

    it("passes the double-click count through", () => {
      const { onInput } = setup({ controlling: true });
      stubImageBox({ width: 1280, height: 800 });
      fireEvent.click(surface(), { clientX: 10, clientY: 10, detail: 2 });
      expect(onInput).toHaveBeenCalledWith(expect.objectContaining({ clicks: 2 }));
    });

    it("sends a right-click on context menu", () => {
      const { onInput } = setup({ controlling: true });
      stubImageBox({ width: 1280, height: 800 });
      fireEvent.contextMenu(surface(), { clientX: 10, clientY: 20 });
      expect(onInput).toHaveBeenCalledWith(expect.objectContaining({ button: "right" }));
    });

    it("sends printable characters as text", () => {
      const { onInput } = setup({ controlling: true });
      fireEvent.keyDown(surface(), { key: "a" });
      expect(onInput).toHaveBeenCalledWith({ kind: "type", text: "a" });
    });

    it("sends named keys as a key press", () => {
      const { onInput } = setup({ controlling: true });
      fireEvent.keyDown(surface(), { key: "Enter" });
      expect(onInput).toHaveBeenCalledWith({ kind: "key", key: "Enter" });
    });

    it("combines modifiers into a key press", () => {
      const { onInput } = setup({ controlling: true });
      fireEvent.keyDown(surface(), { key: "a", ctrlKey: true });
      expect(onInput).toHaveBeenCalledWith({ kind: "key", key: "Control+a" });
    });

    it("ignores bare modifier presses", () => {
      const { onInput } = setup({ controlling: true });
      fireEvent.keyDown(surface(), { key: "Shift", shiftKey: true });
      fireEvent.keyDown(surface(), { key: "Control", ctrlKey: true });
      expect(onInput).not.toHaveBeenCalled();
    });

    it("does not re-apply Shift to an already-capitalised character", () => {
      const { onInput } = setup({ controlling: true });
      fireEvent.keyDown(surface(), { key: "A", shiftKey: true });
      expect(onInput).toHaveBeenCalledWith({ kind: "type", text: "A" });
    });

    it("forwards wheel events as scrolls", () => {
      const { onInput } = setup({ controlling: true });
      fireEvent.wheel(surface(), { deltaX: 0, deltaY: 120 });
      expect(onInput).toHaveBeenCalledWith({ kind: "scroll", dx: 0, dy: 120 });
    });
  });

  describe("control handoff", () => {
    it("offers to take control when the agent has it", () => {
      const onTakeControl = vi.fn();
      setup({ onTakeControl });
      fireEvent.click(screen.getByRole("button", { name: /take control/i }));
      expect(onTakeControl).toHaveBeenCalled();
    });

    it("offers to give it back once held, and says who is driving", () => {
      const onReleaseControl = vi.fn();
      setup({ controlling: true, onReleaseControl });
      expect(screen.getByText(/you are driving/i)).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /give back/i }));
      expect(onReleaseControl).toHaveBeenCalled();
    });

    it("releases on Escape", () => {
      const onReleaseControl = vi.fn();
      setup({ controlling: true, onReleaseControl });
      fireEvent.keyDown(window, { key: "Escape" });
      expect(onReleaseControl).toHaveBeenCalled();
    });
  });
});
