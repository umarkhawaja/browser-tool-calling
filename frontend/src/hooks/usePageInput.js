import { useCallback, useEffect, useRef } from "react";
import { toKeyEvent, toViewport } from "../lib/pageInput.js";

const MOVE_INTERVAL_MS = 50; // hover should feel live, not be pixel-exact

/**
 * Wires a DOM surface up as a remote browser: pointer and keyboard events on
 * the preview image become input events for the real page.
 *
 * Everything is inert unless `active`, so a stray click can never reach the
 * page while the agent is the one driving.
 */
export function usePageInput({ active, imageRef, frameSize, onInput }) {
  const surfaceRef = useRef(null);
  const lastMoveAt = useRef(0);

  // Wheel is bound by hand: React attaches it passively, which forbids
  // preventDefault, and without that the app scrolls instead of the page.
  useEffect(() => {
    const surface = surfaceRef.current;
    if (!surface || !active) return;

    function onWheel(event) {
      event.preventDefault();
      onInput({
        kind: "scroll",
        dx: Math.round(event.deltaX),
        dy: Math.round(event.deltaY),
      });
    }
    surface.addEventListener("wheel", onWheel, { passive: false });
    return () => surface.removeEventListener("wheel", onWheel);
  }, [active, onInput]);

  // Taking control should let you type immediately, without a stray click.
  useEffect(() => {
    if (active) surfaceRef.current?.focus();
  }, [active]);

  const sendPointer = useCallback(
    (event, extra) => {
      if (!active || !imageRef.current) return;
      onInput({ ...toViewport(event, imageRef.current, frameSize), ...extra });
    },
    [active, imageRef, frameSize, onInput]
  );

  const handlers = {
    onMouseMove(event) {
      const now = Date.now();
      if (now - lastMoveAt.current < MOVE_INTERVAL_MS) return;
      lastMoveAt.current = now;
      sendPointer(event, { kind: "move" });
    },
    onClick(event) {
      sendPointer(event, { kind: "click", button: "left", clicks: event.detail || 1 });
    },
    onContextMenu(event) {
      event.preventDefault();
      sendPointer(event, { kind: "click", button: "right", clicks: 1 });
    },
    onKeyDown(event) {
      if (!active) return;
      const input = toKeyEvent(event);
      if (!input) return;
      event.preventDefault(); // keep Tab/Space/arrows in the page, not our UI
      onInput(input);
    },
  };

  return { surfaceRef, handlers };
}
