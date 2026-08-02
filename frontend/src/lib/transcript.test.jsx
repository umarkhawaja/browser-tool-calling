import { describe, it, expect } from "vitest";
import { groupIntoBlocks } from "./transcript.jsx";

const at = 1_700_000_000_000;
const entry = (kind, offset = 0, extra = {}) => ({ kind, at: at + offset, ...extra });

describe("groupIntoBlocks", () => {
  it("returns nothing for an empty transcript", () => {
    expect(groupIntoBlocks([])).toEqual([]);
  });

  it("leaves a lone message alone", () => {
    const blocks = groupIntoBlocks([entry("you", 0, { text: "hi" })]);
    expect(blocks).toHaveLength(1);
    expect(blocks[0].kind).toBe("you");
  });

  it("folds consecutive narration into one trace block", () => {
    const blocks = groupIntoBlocks([
      entry("you", 0),
      entry("action", 1000),
      entry("thought", 1500),
      entry("action", 2000),
    ]);
    expect(blocks.map((b) => b.kind)).toEqual(["you", "trace"]);
    expect(blocks[1].items).toHaveLength(3);
  });

  it("starts a new trace block after an answer breaks the run", () => {
    const blocks = groupIntoBlocks([
      entry("action", 0),
      entry("answer", 100),
      entry("action", 200),
    ]);
    expect(blocks.map((b) => b.kind)).toEqual(["trace", "answer", "trace"]);
  });

  it("times each entry from the one before it", () => {
    const blocks = groupIntoBlocks([
      entry("you", 0),
      entry("action", 1400),
      entry("action", 2300),
    ]);
    expect(blocks[1].items.map((i) => i.elapsedMs)).toEqual([1400, 900]);
  });

  it("gives the very first entry no elapsed time", () => {
    // There is nothing to measure from, and 0 renders as blank.
    const blocks = groupIntoBlocks([entry("action", 0)]);
    expect(blocks[0].items[0].elapsedMs).toBe(0);
  });

  it("keeps the original entry fields", () => {
    const blocks = groupIntoBlocks([entry("action", 0, { text: "click", detail: "[8]" })]);
    expect(blocks[0].items[0]).toMatchObject({ text: "click", detail: "[8]" });
  });
});
