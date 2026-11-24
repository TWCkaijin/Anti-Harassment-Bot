import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import MaterialIcon from "./MaterialIcon";

describe("MaterialIcon", () => {
  it("renders a fixed-size local SVG without changing the accessible button name", () => {
    const { container } = render(
      <button><MaterialIcon icon="open_in_new" size={17} className="text-primary" />開啟資源</button>,
    );
    const svg = container.querySelector("svg")!;

    expect(svg).toHaveAttribute("width", "17");
    expect(svg).toHaveAttribute("height", "17");
    expect(svg).toHaveStyle({ width: "17px", height: "17px" });
    expect(svg).toHaveClass("text-primary", "shrink-0");
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(svg).toHaveAttribute("focusable", "false");
    expect(svg).toHaveAttribute("stroke", "currentColor");
    expect(svg.querySelector("path")).not.toBeNull();
    expect(svg.querySelector("text, image, use")).toBeNull();
    expect(container.querySelector(".material-symbols-outlined")).toBeNull();
    expect(container).not.toHaveTextContent("open_in_new");
    expect(screen.getByRole("button", { name: "開啟資源" })).toBeInTheDocument();
  });

  it.each(["unknown_icon", "__proto__", "constructor", "<script>alert(1)</script>"])(
    "renders a visible fallback for an unsupported name without exposing it: %s",
    (icon) => {
      const { container } = render(<MaterialIcon icon={icon} />);
      const svg = container.querySelector("svg")!;

      expect(svg).toHaveAttribute("width", "24");
      expect(svg.querySelector("path, circle")).not.toBeNull();
      expect(container.textContent).toBe("");
      expect(container.innerHTML).not.toContain(icon);
    },
  );

  it("keeps detailed filled icons legible and fills simple silhouettes", () => {
    const { container, rerender } = render(<MaterialIcon icon="favorite" filled />);
    expect(container.querySelector("svg")).toHaveAttribute("fill", "currentColor");

    rerender(<MaterialIcon icon="verified_user" filled />);
    expect(container.querySelector("svg")).toHaveAttribute("fill", "none");
    expect(container.querySelector("svg")).toHaveAttribute("stroke-width", "2.25");
  });

  it.each([0, -1, Number.NaN, Number.POSITIVE_INFINITY])(
    "keeps the icon visible for an invalid size: %s",
    (size) => {
      const { container } = render(<MaterialIcon icon="menu" size={size} />);
      expect(container.querySelector("svg")).toHaveAttribute("width", "24");
      expect(container.querySelector("svg")).toHaveAttribute("height", "24");
    },
  );
});
