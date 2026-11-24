import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { I18nProvider } from "@rag/shared/chat-ui";
import Sidebar from "./Sidebar";
import type { ConversationSession } from "../hooks/useConversation";
const session: ConversationSession = { id: "one", title: "合成測試", createdAt: 1, messages: [{ id: "u", role: "user", content: "測試", timestamp: 1 }] };
const props = { sessions: [session], currentSessionId: "one", isOpen: false, isDesktop: false, isCollapsed: true, onClose: vi.fn(), onCollapse: vi.fn(), onSelectSession: vi.fn(), onNewSession: vi.fn(), onDeleteSession: vi.fn(), onRenameSession: vi.fn(), onOpenSettings: vi.fn(), onOpenAdmin: vi.fn() };
beforeEach(() => { localStorage.clear(); vi.clearAllMocks(); });

it("keeps the closed preview inert and requests collapse with Escape from the pinned sidebar", () => {
  const view = (isCollapsed: boolean) => <I18nProvider><button data-sidebar-trigger>切換側邊欄</button><Sidebar {...props} isDesktop isCollapsed={isCollapsed} /></I18nProvider>;
  const { rerender } = render(view(true));
  const sidebar = document.querySelector("aside")!;
  const toggle = screen.getByRole("button", { name: "切換側邊欄" });
  fireEvent.pointerEnter(toggle);
  expect(sidebar).toHaveAttribute("inert");
  rerender(view(false));
  expect(sidebar).not.toHaveAttribute("inert");
  expect(sidebar).toHaveAttribute("data-sidebar-mode", "pinned");
  fireEvent.keyDown(sidebar, { key: "Escape" });
  expect(props.onCollapse).toHaveBeenCalledOnce();
  expect(toggle).toHaveFocus();
});

it("uses a click drawer without hover on touch and restores focus on close", () => {
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

it("lists only public emergency contacts", () => {
  render(<I18nProvider><Sidebar {...props} isOpen /></I18nProvider>);
  fireEvent.click(screen.getByRole("button", { name: "緊急協助" }));
  const links = Array.from(document.querySelectorAll('a[href^="tel:"]'));
  expect(links.map((link) => link.getAttribute("href"))).toEqual(["tel:113", "tel:110"]);
  expect(links.map((link) => link.textContent)).toEqual([
    expect.stringContaining("113 保護專線"),
    expect.stringContaining("110 報案專線"),
  ]);
});
