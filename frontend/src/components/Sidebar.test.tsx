import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { I18nProvider } from "../i18n";
import Sidebar from "./Sidebar";
import type { ConversationSession } from "../hooks/useConversation";
const session: ConversationSession = { id: "one", title: "合成測試", createdAt: 1, messages: [{ id: "u", role: "user", content: "測試", timestamp: 1 }] };
const props = { sessions: [session], currentSessionId: "one", isOpen: false, isCollapsed: true, onClose: vi.fn(), onToggleCollapsed: vi.fn(), onSelectSession: vi.fn(), onNewSession: vi.fn(), onDeleteSession: vi.fn(), onRenameSession: vi.fn(), onOpenSettings: vi.fn(), onOpenAdmin: vi.fn() };
const media = (matches: boolean) => vi.fn(() => ({ matches, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
beforeEach(() => { localStorage.clear(); vi.clearAllMocks(); vi.useFakeTimers(); vi.stubGlobal("matchMedia", media(true)); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

it("previews as an overlay with a stationary trigger and waits 200ms after leaving", () => {
  render(<I18nProvider><Sidebar {...props} /></I18nProvider>);
  const aside = document.querySelector("aside")!;
  const trigger = screen.getByRole("button", { name: "固定開啟對話欄" });
  expect(aside).toHaveAttribute("inert");
  fireEvent.pointerEnter(trigger);
  expect(aside).not.toHaveAttribute("inert"); expect(aside).toHaveClass("fixed");
  expect(trigger).toHaveStyle({ left: "0px" });
  fireEvent.pointerLeave(trigger); fireEvent.pointerEnter(aside);
  act(() => vi.advanceTimersByTime(250)); expect(aside).not.toHaveAttribute("inert");
  fireEvent.pointerLeave(aside);
  act(() => vi.advanceTimersByTime(199)); expect(aside).not.toHaveAttribute("inert");
  act(() => vi.advanceTimersByTime(1)); expect(aside).toHaveAttribute("inert");
});

it("holds the preview for keyboard focus and an open portal menu", () => {
  render(<I18nProvider><Sidebar {...props} /></I18nProvider>);
  const aside = document.querySelector("aside")!;
  fireEvent.pointerEnter(screen.getByRole("button", { name: "固定開啟對話欄" }));
  const menu = screen.getByRole("button", { name: /對話選項.*合成測試/ });
  act(() => menu.focus()); fireEvent.pointerLeave(aside);
  act(() => vi.advanceTimersByTime(300)); expect(aside).not.toHaveAttribute("inert");
  fireEvent.click(menu); act(() => menu.blur()); fireEvent.pointerLeave(aside);
  act(() => vi.advanceTimersByTime(300)); expect(aside).not.toHaveAttribute("inert");
  fireEvent.click(screen.getByRole("button", { name: "關閉對話選單" }));
  act(() => vi.advanceTimersByTime(250)); expect(aside).not.toHaveAttribute("inert");
  act(() => menu.blur());
  act(() => vi.advanceTimersByTime(250)); expect(aside).toHaveAttribute("inert");
});

it("pins explicitly and keeps it pinned on message updates", () => {
  const view = (collapsed: boolean, messages = session.messages) => <I18nProvider><Sidebar {...props} isCollapsed={collapsed} sessions={[{ ...session, messages }]} /></I18nProvider>;
  const { rerender } = render(view(true));
  fireEvent.click(screen.getByRole("button", { name: "固定開啟對話欄" }));
  expect(props.onToggleCollapsed).toHaveBeenCalledOnce();
  rerender(view(false));
  const aside = document.querySelector("aside")!;
  fireEvent.pointerLeave(aside); act(() => vi.advanceTimersByTime(500));
  rerender(view(false, [...session.messages, { id: "a", role: "assistant", content: "新段落", timestamp: 2 }]));
  expect(aside).not.toHaveAttribute("inert"); expect(aside).toHaveClass("relative");
  expect(screen.getByRole("button", { name: "收起對話欄" })).toHaveStyle({ left: "320px" });
});

it("uses a click drawer without hover on touch and restores focus on close", () => {
  vi.stubGlobal("matchMedia", media(false));
  const view = (open: boolean) => <I18nProvider><button>開啟</button><Sidebar {...props} isOpen={open} /></I18nProvider>;
  const { rerender } = render(view(false));
  const open = screen.getByRole("button", { name: "開啟" }); act(() => open.focus());
  expect(screen.queryByRole("button", { name: "固定開啟對話欄" })).not.toBeInTheDocument();
  rerender(view(true));
  const drawer = screen.getByRole("dialog");
  expect(drawer).toContainElement(document.activeElement as HTMLElement);
  fireEvent.keyDown(drawer, { key: "Escape" }); expect(props.onClose).toHaveBeenCalled();
  rerender(view(false)); expect(open).toHaveFocus();
});

it("moves keyboard focus into the mobile portal menu and returns it on Escape", () => {
  vi.stubGlobal("matchMedia", media(false));
  render(<I18nProvider><Sidebar {...props} isOpen /></I18nProvider>);
  const trigger = screen.getByRole("button", { name: /對話選項.*合成測試/ });
  fireEvent.click(trigger);
  const rename = screen.getByRole("button", { name: "重新命名" });
  expect(rename).toHaveFocus();
  fireEvent.keyDown(rename, { key: "Tab", shiftKey: true });
  expect(screen.getByRole("button", { name: "刪除對話" })).toHaveFocus();
  fireEvent.keyDown(document.activeElement!, { key: "Tab" });
  expect(rename).toHaveFocus();
  fireEvent.keyDown(rename, { key: "Escape" });
  expect(screen.queryByRole("button", { name: "重新命名" })).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});
