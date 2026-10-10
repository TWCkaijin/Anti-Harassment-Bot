import { trackAnalytics } from "../services/analytics";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ActionButton } from "../services/api";
import ActionButtons from "./ActionButtons";

vi.mock("../services/analytics", () => ({ trackAnalytics: vi.fn() }));

describe("ActionButtons", () => {
  it("retains legacy telephone links and opens valid external links in a new tab", () => {
    render(<ActionButtons actions={[
      { action: "tel", label: "撥打專線", phone_number: "113" },
      { action: "tel", label: "撥打市話", phone_number: "(08)732-0415" },
      { action: "url", label: "開啟資源", url: "https://example.org/resources" },
    ]} />);

    expect(screen.getByRole("link", { name: "撥打專線" })).toHaveAttribute("href", "tel:113");
    expect(screen.getByRole("link", { name: "撥打市話" })).toHaveAttribute("href", "tel:(08)732-0415");
    const external = screen.getByRole("link", { name: "開啟資源（另開新分頁）" });
    expect(external).toHaveAttribute("href", "https://example.org/resources");
    expect(external).toHaveAttribute("target", "_blank");
    expect(external).toHaveAttribute("rel", "noopener noreferrer");
  });

  it.each([
    "javascript:alert(1)", "data:text/html,hello", "file:///tmp/test", "//example.org",
    "/local", "mailto:help@example.org", "https://user:password@example.org",
    "https:\\example.org", "https:example.org", "https:///example.org", "https://exam\nple.org", "https://example.org/has space",
    "https://example.org/\u0000", `https://example.org/${"a".repeat(2048)}`,
  ])("does not render unsafe or malformed URL %s", (url) => {
    const { container } = render(<ActionButtons actions={[{ action: "url", label: "不應顯示", url }]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("ignores invalid phone links, invalid labels, unknown actions, and choice actions", () => {
    const malformed = [
      { action: "tel", label: "無效電話", phone_number: "113;ext=javascript:alert(1)" },
      { action: "url", label: "x".repeat(81), url: "https://example.org" },
      { action: "unknown", label: "不支援" },
      { action: "options", id: "choose", label: "選項", title: "選一個", options: [{ label: "一", value: "1" }, { label: "二", value: "2" }] },
      null,
    ] as unknown as ActionButton[];
    const { container } = render(<ActionButtons actions={malformed} />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("does not crash on malformed stored action lists", () => {
    const { container } = render(<ActionButtons actions={null as unknown as ActionButton[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});


it("records only the resource action type on click", () => {
  render(<ActionButtons actions={[{ action: "url", label: "private label", url: "https://example.org/private-resource" }]} />);
  const link = screen.getByRole("link");
  link.addEventListener("click", event => event.preventDefault());
  fireEvent.click(link);
  expect(trackAnalytics).toHaveBeenCalledExactlyOnceWith("resource_action_clicked", { action_type: "url" });
});
