import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  MAX_USER_MESSAGE_CHARACTERS,
  USER_MESSAGE_TOO_LONG_ERROR,
} from "../hooks/conversationHistory";
import { I18nProvider, useI18n } from "../i18n";
import WelcomeHero from "./WelcomeHero";

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function LanguageToggle() {
  const { setLocale } = useI18n();
  return <button onClick={() => setLocale("en")}>English</button>;
}

describe("WelcomeHero", () => {
  it("offers a concise introduction and three working suggestions without the readiness badge", () => {
    const onSuggest = vi.fn();
    render(<I18nProvider><WelcomeHero onSuggest={onSuggest} /></I18nProvider>);
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("先從您想說的開始一起釐清下一步");
    expect(screen.getByText("整理處境、了解權益，找到可用的協助。")).toBeVisible();
    expect(screen.queryByText(/AI 助理已就緒|即時、專業|匿名通報的管道/)).not.toBeInTheDocument();
    const suggestions = within(screen.getByRole("group", { name: "可以從這裡開始" })).getAllByRole("button");
    expect(suggestions).toHaveLength(3);
    for (const suggestion of suggestions) fireEvent.click(suggestion);
    expect(onSuggest.mock.calls).toEqual([
      ["我想了解自己的權益"], ["我可以向哪裡求助？"], ["先幫我整理下一步"],
    ]);
    expect(screen.getByText("回覆由 AI 產生，僅供參考；內容會送至服務處理，請避免提供個資。")).toBeVisible();
  });

  it("shows overflow without truncating and accepts the exact boundary", () => {
    const onSuggest = vi.fn();
    render(
      <I18nProvider>
        <WelcomeHero onSuggest={onSuggest} />
      </I18nProvider>,
    );
    const textarea = screen.getByRole("textbox", { name: "想說的情況或問題" });
    expect(textarea).toHaveAttribute("placeholder", "描述情況或提問…");
    const sendButton = screen.getByRole("button", { name: "傳送訊息" });
    const boundary = "x".repeat(MAX_USER_MESSAGE_CHARACTERS);
    const overflow = `${boundary}x`;

    fireEvent.change(textarea, { target: { value: overflow } });

    expect(textarea).toHaveValue(overflow);
    expect(screen.getByRole("alert")).toHaveTextContent(USER_MESSAGE_TOO_LONG_ERROR);
    expect(sendButton).toBeDisabled();
    expect(onSuggest).not.toHaveBeenCalled();

    fireEvent.change(textarea, { target: { value: boundary } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(sendButton).toBeEnabled();
    fireEvent.click(sendButton);

    expect(onSuggest).toHaveBeenCalledWith(boundary, undefined, undefined);
  });

  it("names image actions and preview so an attachment can be selected and removed accessibly", () => {
    vi.stubGlobal("URL", class extends URL {
      static createObjectURL = vi.fn(() => "blob:test-image");
      static revokeObjectURL = vi.fn();
    });
    render(<I18nProvider><WelcomeHero onSuggest={vi.fn()} /></I18nProvider>);
    const fileInput = screen.getByLabelText("選擇圖片");
    const chooseFile = vi.spyOn(fileInput, "click");
    fireEvent.click(screen.getByRole("button", { name: "加入圖片" }));
    expect(chooseFile).toHaveBeenCalledOnce();
    fireEvent.change(fileInput, { target: { files: [new File(["image"], "photo.png", { type: "image/png" })] } });
    expect(screen.getByRole("img", { name: "已選擇的圖片" })).toHaveAttribute("src", "blob:test-image");
    fireEvent.click(screen.getByRole("button", { name: "移除圖片" }));
    expect(screen.queryByRole("img", { name: "已選擇的圖片" })).not.toBeInTheDocument();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:test-image");
  });

  it("switches the introduction, accessible labels and browser identity together", () => {
    render(<I18nProvider><LanguageToggle /><WelcomeHero onSuggest={vi.fn()} /></I18nProvider>);
    expect(document.title).toBe("溫暖守護｜性騷擾協助與資訊");
    expect(document.documentElement).toHaveAttribute("lang", "zh-TW");
    fireEvent.click(screen.getByRole("button", { name: "English" }));
    expect(document.title).toBe("Warm Support｜Sexual harassment support and information");
    expect(document.documentElement).toHaveAttribute("lang", "en");
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("Start with what you want to shareExplore the next step together");
    expect(screen.getByRole("textbox", { name: "Your situation or question" })).toHaveAttribute("placeholder", "Share or ask…");
    expect(screen.getByRole("button", { name: "Add an image" })).toBeVisible();
    expect(within(screen.getByRole("group", { name: "Start here" })).getAllByRole("button")).toHaveLength(3);
    expect(screen.queryByText(/AI Assistant Ready|Anonymous reporting channels/)).not.toBeInTheDocument();
    expect(screen.getByText("AI responses are for reference. Content is sent to the service; avoid personal details.")).toBeVisible();
  });
});
