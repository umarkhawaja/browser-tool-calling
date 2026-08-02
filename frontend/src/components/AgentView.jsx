import React from "react";

// Below this many pixels from the top there is no room for a badge above the
// box, so it moves inside.
const BADGE_HEIGHT = 13;

/**
 * The page as the model reads it: a numbered box over every element it can
 * address.
 *
 * `elements` must be exactly what the model was given and no more — the backend
 * cuts both the listing and this payload at the same limit. Drawing extras
 * would show a page the agent cannot actually act on.
 */
export default function AgentView({ elements, scale, clickedIndex, frameHeight }) {
  return (
    <div className="overlay">
      <div className="overlay-veil" />
      {elements.map((element) => {
        const [x, y, width, height] = element.rect || [0, 0, 0, 0];
        // Elements scrolled out of view still have rects, outside the frame.
        if (!width || !height || y > frameHeight || y + height < 0) return null;

        return (
          <div
            key={element.index}
            className={`element-box ${element.index === clickedIndex ? "clicked" : ""}`}
            style={{
              left: x * scale,
              top: y * scale,
              width: width * scale,
              height: height * scale,
            }}
            title={`[${element.index}] <${element.tag}> ${element.label}`}
          >
            <span
              className={`element-index ${y * scale < BADGE_HEIGHT ? "inside" : ""}`}
            >
              {element.index}
            </span>
          </div>
        );
      })}
    </div>
  );
}
