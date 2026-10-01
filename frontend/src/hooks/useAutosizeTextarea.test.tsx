import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useRef, useState } from "react";
import { useAutosizeTextarea } from "./useAutosizeTextarea";

function Composer() {
  const [value, setValue] = useState("");
  const field = useRef<HTMLTextAreaElement>(null);
  useAutosizeTextarea(field, value);
  return <textarea ref={field} value={value} onChange={event => setValue(event.target.value)} style={{ lineHeight: "24px", padding: "10px 0", border: 0 }} />;
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("autosize textarea", () => {
  it("grows beyond two lines, scrolls at the limit, and shrinks when text is cleared", () => {
    let scrollHeight = 44;
    vi.spyOn(HTMLTextAreaElement.prototype, "scrollHeight", "get").mockImplementation(() => scrollHeight);
    render(<Composer />);
    const field = screen.getByRole("textbox");
    expect(field).toHaveStyle({ height: "44px", overflowY: "hidden" });
    scrollHeight = 140;
    fireEvent.change(field, { target: { value: "第一行\n第二行\n第三行\n第四行\n第五行" } });
    expect(field).toHaveStyle({ height: "140px", overflowY: "hidden" });
    scrollHeight = 420;
    fireEvent.change(field, { target: { value: "很長的多行文字" } });
    expect(field).toHaveStyle({ height: "240px", overflowY: "auto" });
    scrollHeight = 44;
    fireEvent.change(field, { target: { value: "" } });
    expect(field).toHaveStyle({ height: "44px", overflowY: "hidden" });
  });

  it("recalculates wrapping after width changes and adapts to a smaller viewport", () => {
    let scrollHeight = 68;
    vi.spyOn(HTMLTextAreaElement.prototype, "scrollHeight", "get").mockImplementation(() => scrollHeight);
    let resized!: ResizeObserverCallback;
    const disconnect = vi.fn();
    vi.stubGlobal("ResizeObserver", class { constructor(callback: ResizeObserverCallback) { resized = callback; } observe() {} disconnect = disconnect; });
    const { unmount } = render(<Composer />);
    const field = screen.getByRole("textbox");
    expect(field).toHaveStyle({ height: "68px" });
    scrollHeight = 164;
    act(() => { resized([{ contentRect: { width: 300 } }] as ResizeObserverEntry[], {} as ResizeObserver); });
    expect(field).toHaveStyle({ height: "164px", overflowY: "hidden" });
    vi.stubGlobal("innerHeight", 300);
    fireEvent(window, new Event("resize"));
    expect(field).toHaveStyle({ height: "120px", overflowY: "auto" });
    unmount();
    expect(disconnect).toHaveBeenCalledOnce();
  });
});
