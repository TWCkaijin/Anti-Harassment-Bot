import { useLayoutEffect, type RefObject } from "react";

/** Grow with text and wrapping, then scroll inside the viewport-bounded field. */
export function useAutosizeTextarea(ref: RefObject<HTMLTextAreaElement | null>, value: string, visible = true): void {
  useLayoutEffect(() => {
    const field = ref.current;
    if (!field || !visible) return;
    const resize = () => {
      const style = getComputedStyle(field);
      const pixels = (property: string) => Number.parseFloat(style.getPropertyValue(property)) || 0;
      const border = pixels("border-top-width") + pixels("border-bottom-width");
      const minimum = (pixels("line-height") || 24) + pixels("padding-top") + pixels("padding-bottom") + border;
      const viewportHeight = window.visualViewport?.height ?? window.innerHeight;
      const maximum = Math.max(minimum, Math.min(240, viewportHeight * 0.4));
      field.style.height = "auto";
      const measured = Math.max(minimum, field.scrollHeight + border);
      field.style.height = `${Math.ceil(Math.min(measured, maximum))}px`;
      field.style.overflowY = measured > maximum ? "auto" : "hidden";
    };
    resize();
    let width = field.getBoundingClientRect().width;
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(entries => {
      const nextWidth = entries[0]?.contentRect.width;
      if (nextWidth !== undefined && nextWidth !== width) { width = nextWidth; resize(); }
    });
    observer?.observe(field);
    window.addEventListener("resize", resize);
    window.visualViewport?.addEventListener("resize", resize);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", resize);
      window.visualViewport?.removeEventListener("resize", resize);
    };
  }, [ref, value, visible]);
}
