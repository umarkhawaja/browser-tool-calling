import { describe, it, expect } from "vitest";
import { toKeyEvent, toViewport, tidyUrl } from "./pageInput.js";

/** A stand-in for the preview image at a given size and position. */
function image({ left = 0, top = 0, width = 1280, height = 800 } = {}) {
  return { getBoundingClientRect: () => ({ left, top, width, height }) };
}

describe("toViewport", () => {
  it("passes coordinates through at full size", () => {
    const point = toViewport({ clientX: 100, clientY: 50 }, image(), { width: 1280, height: 800 });
    expect(point).toEqual({ x: 100, y: 50 });
  });

  it("undoes CSS scaling", () => {
    const half = image({ width: 640, height: 400 });
    const point = toViewport({ clientX: 320, clientY: 200 }, half, { width: 1280, height: 800 });
    expect(point).toEqual({ x: 640, y: 400 });
  });

  it("subtracts the image's offset on screen", () => {
    const offset = image({ left: 100, top: 40 });
    const point = toViewport({ clientX: 150, clientY: 90 }, offset, { width: 1280, height: 800 });
    expect(point).toEqual({ x: 50, y: 50 });
  });

  it("falls back to the rendered box when the frame size is unknown", () => {
    const point = toViewport({ clientX: 10, clientY: 10 }, image(), null);
    expect(point).toEqual({ x: 10, y: 10 });
  });

  it("rounds to whole pixels", () => {
    const odd = image({ width: 641, height: 400 });
    const point = toViewport({ clientX: 100, clientY: 0 }, odd, { width: 1280, height: 800 });
    expect(Number.isInteger(point.x)).toBe(true);
  });
});

describe("toKeyEvent", () => {
  const key = (k, mods = {}) => toKeyEvent({ key: k, ...mods });

  it("treats a printable character as typed text", () => {
    expect(key("a")).toEqual({ kind: "type", text: "a" });
  });

  it("does not re-apply Shift to an already-capitalised character", () => {
    // The OS has shifted it already; "Shift+A" would double up.
    expect(key("A", { shiftKey: true })).toEqual({ kind: "type", text: "A" });
  });

  it("sends a named key by name", () => {
    expect(key("Enter")).toEqual({ kind: "key", key: "Enter" });
    expect(key("ArrowLeft")).toEqual({ kind: "key", key: "ArrowLeft" });
  });

  it("prefixes modifiers", () => {
    expect(key("a", { ctrlKey: true })).toEqual({ kind: "key", key: "Control+a" });
    expect(key("a", { metaKey: true })).toEqual({ kind: "key", key: "Meta+a" });
  });

  it("keeps Shift only for named keys", () => {
    expect(key("Tab", { shiftKey: true })).toEqual({ kind: "key", key: "Shift+Tab" });
  });

  it("orders multiple modifiers consistently", () => {
    expect(key("a", { ctrlKey: true, altKey: true })).toEqual({
      kind: "key", key: "Control+Alt+a",
    });
  });

  it("ignores a bare modifier press", () => {
    for (const modifier of ["Shift", "Control", "Alt", "Meta"]) {
      expect(key(modifier)).toBeNull();
    }
  });
});

describe("tidyUrl", () => {
  it("drops the scheme and any trailing slash", () => {
    expect(tidyUrl("https://news.ycombinator.com/")).toBe("news.ycombinator.com");
    expect(tidyUrl("http://example.com")).toBe("example.com");
  });

  it("keeps the path", () => {
    expect(tidyUrl("https://example.com/a/b")).toBe("example.com/a/b");
  });

  it("shows nothing for a blank page", () => {
    expect(tidyUrl("about:blank")).toBe("");
    expect(tidyUrl("")).toBe("");
    expect(tidyUrl(undefined)).toBe("");
  });
});
