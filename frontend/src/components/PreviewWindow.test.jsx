import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import PreviewWindow from "./PreviewWindow.jsx";

describe("PreviewWindow", () => {
  it("shows a placeholder when there is no screenshot", () => {
    render(<PreviewWindow screenshot={null} running={false} />);
    expect(screen.getByText(/agent's browser will appear here/i)).toBeInTheDocument();
  });

  it("renders the screenshot as a data-URI image when provided", () => {
    render(<PreviewWindow screenshot="ABC123" running={true} />);
    const img = screen.getByAltText("browser preview");
    expect(img).toHaveAttribute("src", "data:image/png;base64,ABC123");
  });

  it("shows the live indicator only while running", () => {
    const { rerender } = render(<PreviewWindow screenshot="ABC" running={false} />);
    expect(screen.queryByText(/●\s*live/i)).toBeNull();
    rerender(<PreviewWindow screenshot="ABC" running={true} />);
    expect(screen.getByText(/●\s*live/i)).toBeInTheDocument();
  });
});
