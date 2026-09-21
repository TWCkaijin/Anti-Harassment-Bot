import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../i18n";
import type { ActionButton, OptionsActionButton } from "../services/api";
import ActionButtons from "./ActionButtons";
import MessageItem from "./MessageItem";

const optionsAction: OptionsActionButton = {
  action: "options",
  id: "choose_next_step",
  label: "選擇下一步",
  title: "您想先了解哪一項？",
  options: [
    { label: "看看資源", value: "我想先了解可使用的資源" },
    { label: "繼續說明", value: "我想先繼續描述目前的情況" },
  ],
};

const dialogPrototype = HTMLDialogElement.prototype;
const originalShowModal = Object.getOwnPropertyDescriptor(dialogPrototype, "showModal");
const originalClose = Object.getOwnPropertyDescriptor(dialogPrototype, "close");

beforeAll(() => {
  // jsdom has no modal methods. Shim visibility while testing our event handlers;
  // actual focus trapping and background inertness belong to the browser dialog.
  Object.defineProperties(dialogPrototype, {
    showModal: {
      configurable: true,
      value() { this.setAttribute("open", ""); },
    },
    close: {
      configurable: true,
      value() { this.removeAttribute("open"); },
    },
  });
});

afterAll(() => {
  if (originalShowModal) Object.defineProperty(dialogPrototype, "showModal", originalShowModal);
  else Reflect.deleteProperty(dialogPrototype, "showModal");
  if (originalClose) Object.defineProperty(dialogPrototype, "close", originalClose);
  else Reflect.deleteProperty(dialogPrototype, "close");
});

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
    render(<ActionButtons actions={[{ action: "url", label: "不應顯示", url }]} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("ignores invalid phone links, unsupported actions and malformed stored options", () => {
    const malformed = [
      { action: "tel", label: "無效電話", phone_number: "113;ext=javascript:alert(1)" },
      { action: "unknown", label: "不支援" },
      { ...optionsAction, options: [{ label: "缺少內容" }] },
      { ...optionsAction, options: [optionsAction.options[0]] },
      { ...optionsAction, id: "not a valid id" },
      null,
    ] as unknown as ActionButton[];
    render(<ActionButtons actions={malformed} onSend={vi.fn()} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("only sends the selected value after explicit choice, then closes and restores focus", () => {
    const onSend = vi.fn();
    render(<ActionButtons actions={[optionsAction]} onSend={onSend} />);
    const trigger = screen.getByRole("button", { name: optionsAction.label });
    trigger.focus();
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: optionsAction.title });
    const option = within(dialog).getByRole("button", { name: "看看資源" });

    expect(onSend).not.toHaveBeenCalled();
    expect(option).toHaveFocus();
    expect(dialog).toHaveAttribute("aria-modal", "true");
    fireEvent.click(option);
    fireEvent.click(option);

    expect(onSend).toHaveBeenCalledExactlyOnceWith(optionsAction.options[0].value);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("rejects stored options outside the server's display and value limits", () => {
    const malformed: OptionsActionButton[] = [
      { ...optionsAction, label: "x".repeat(81) },
      { ...optionsAction, title: "x".repeat(161) },
      { ...optionsAction, options: [{ label: "x".repeat(81), value: "valid" }, optionsAction.options[1]] },
      { ...optionsAction, options: [{ label: "超長內容", value: "😀".repeat(501) }, optionsAction.options[1]] },
      { ...optionsAction, options: [optionsAction.options[0], optionsAction.options[0]] },
    ];
    render(<ActionButtons actions={malformed} onSend={vi.fn()} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("allows the server's 500-character Unicode value boundary", () => {
    const onSend = vi.fn();
    const value = "😀".repeat(500);
    render(<ActionButtons actions={[{ ...optionsAction, options: [{ label: "選擇內容", value }, optionsAction.options[1]] }]} onSend={onSend} />);
    fireEvent.click(screen.getByRole("button", { name: optionsAction.label }));
    fireEvent.click(screen.getByRole("button", { name: "選擇內容" }));
    expect(onSend).toHaveBeenCalledExactlyOnceWith(value);
  });

  it.each(["cancel", "escape", "backdrop"])("dismisses with %s without sending a message", (method) => {
    const onSend = vi.fn();
    render(<ActionButtons actions={[optionsAction]} onSend={onSend} />);
    const trigger = screen.getByRole("button", { name: optionsAction.label });
    trigger.focus();
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: optionsAction.title });
    if (method === "cancel") fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    if (method === "escape") fireEvent(dialog, new Event("cancel", { cancelable: true }));
    if (method === "backdrop") fireEvent.click(dialog);

    expect(onSend).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("prevents submission when a request starts with an options dialog already open", () => {
    const onSend = vi.fn();
    const { rerender } = render(<ActionButtons actions={[optionsAction]} onSend={onSend} />);
    fireEvent.click(screen.getByRole("button", { name: optionsAction.label }));
    rerender(<ActionButtons actions={[optionsAction]} onSend={onSend} isLoading />);
    const option = within(screen.getByRole("dialog")).getByRole("button", { name: "看看資源" });

    expect(option).toBeDisabled();
    expect(screen.getByRole("button", { name: optionsAction.label })).toBeDisabled();
    fireEvent.click(option);
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("disables option launchers without a sender or while loading", () => {
    const { rerender } = render(<ActionButtons actions={[optionsAction]} />);
    expect(screen.getByRole("button", { name: optionsAction.label })).toBeDisabled();
    rerender(<ActionButtons actions={[optionsAction]} onSend={vi.fn()} isLoading />);
    expect(screen.getByRole("button", { name: optionsAction.label })).toBeDisabled();
  });

  it("renders generic actions in an assistant message and forwards selection through onSend", () => {
    const onSend = vi.fn();
    render(
      <I18nProvider>
        <MessageItem message={{ id: "assistant-1", role: "assistant", content: "您可以選擇下一步。", timestamp: Date.now(), actionButtons: [optionsAction] }} onSend={onSend} />
      </I18nProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: optionsAction.label }));
    fireEvent.click(screen.getByRole("button", { name: "繼續說明" }));
    expect(onSend).toHaveBeenCalledExactlyOnceWith(optionsAction.options[1].value);
  });
});
