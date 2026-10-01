import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  MAX_USER_MESSAGE_CHARACTERS,
  USER_MESSAGE_TOO_LONG_ERROR,
} from "../hooks/conversationHistory";
import { I18nProvider, useI18n } from "../i18n";
import { IMAGE_ACCEPT, IMAGE_ONLY_MESSAGE, MAX_IMAGE_BYTES } from "../services/imageUpload";
import type { SendOutcome } from "../services/sendOutcome";
import WelcomeHero from "./WelcomeHero";

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function LanguageToggle() {
  const { setLocale } = useI18n();
  return <button onClick={() => setLocale("en")}>English</button>;
}

const imageFixtures = [
  { mime: "image/png", filename: "photo.png", bytes: [137, 80, 78, 71, 13, 10, 26, 10] },
  { mime: "image/jpeg", filename: "photo.jpg", bytes: [255, 216, 255] },
  { mime: "image/gif", filename: "photo.gif", bytes: [71, 73, 70, 56, 57, 97] },
  { mime: "image/webp", filename: "photo.webp", bytes: [82, 73, 70, 70, 0, 0, 0, 0, 87, 69, 66, 80] },
];

function imageFile(fixture = imageFixtures[0]): File {
  return new File([new Uint8Array(fixture.bytes)], fixture.filename, { type: fixture.mime });
}

async function selectImage(file = imageFile()) {
  fireEvent.change(screen.getByLabelText("選擇圖片"), { target: { files: [file] } });
  return screen.findByRole("img", { name: "已選擇的圖片" });
}

describe("WelcomeHero", () => {
  it.each([undefined, true])("offers image selection when allowImageUpload is %s", (allowImageUpload) => {
    const { container } = render(<I18nProvider><WelcomeHero onSuggest={vi.fn()} allowImageUpload={allowImageUpload} /></I18nProvider>);
    expect(container.querySelector('input[type="file"]')).toHaveAttribute("accept", IMAGE_ACCEPT);
    expect(screen.getByLabelText("選擇圖片")).toBeEnabled();
    expect(screen.getByRole("button", { name: "加入圖片" })).toBeEnabled();
    expect(screen.queryByText(/目前不接受圖片/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "傳送訊息" })).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "想說的情況或問題" })).toBeVisible();
  });
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
    expect(textarea).toHaveValue("");
  });

  it("hides the picker and rejects pasted images when uploads are disabled", () => {
    const onSuggest = vi.fn();
    const readAsDataURL = vi.spyOn(FileReader.prototype, "readAsDataURL");
    const { container } = render(<I18nProvider><WelcomeHero onSuggest={onSuggest} allowImageUpload={false} /></I18nProvider>);
    expect(container.querySelector('input[type="file"]')).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "加入圖片" })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    const textarea = screen.getByRole("textbox", { name: "想說的情況或問題" });
    fireEvent.change(textarea, { target: { value: "尚未送出的文字" } });

    const defaultAllowed = fireEvent.paste(textarea, {
      clipboardData: {
        files: [imageFile()],
      },
    });

    expect(defaultAllowed).toBe(false);
    expect(readAsDataURL).not.toHaveBeenCalled();
    expect(screen.queryByRole("img", { name: "已選擇的圖片" })).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("圖片上傳目前已停用");
    expect(textarea).toHaveValue("尚未送出的文字");
    expect(onSuggest).not.toHaveBeenCalled();
  });

  it.each(imageFixtures)("previews a valid $mime selection and sends an image-only fallback without object URLs", async (fixture) => {
    const onSuggest = vi.fn();
    vi.stubGlobal("URL", class extends URL {
      static createObjectURL = vi.fn();
      static revokeObjectURL = vi.fn();
    });
    render(<I18nProvider><WelcomeHero onSuggest={onSuggest} /></I18nProvider>);
    const image = await selectImage(imageFile(fixture));
    const dataUrl = image.getAttribute("src");
    expect(dataUrl).toBe(`data:${fixture.mime};base64,${btoa(String.fromCharCode(...fixture.bytes))}`);
    expect(onSuggest).not.toHaveBeenCalled();
    expect(URL.createObjectURL).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "傳送訊息" }));

    expect(onSuggest).toHaveBeenCalledExactlyOnceWith(IMAGE_ONLY_MESSAGE, dataUrl, dataUrl);
    expect(screen.queryByRole("img", { name: "已選擇的圖片" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "傳送訊息" })).toBeDisabled();
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
  });

  it("accepts pasted images, keeps the text draft, and supports removing and reselecting the same file", async () => {
    const onSuggest = vi.fn();
    render(<I18nProvider><WelcomeHero onSuggest={onSuggest} /></I18nProvider>);
    const textarea = screen.getByRole("textbox", { name: "想說的情況或問題" });
    fireEvent.change(textarea, { target: { value: "圖片的文字說明" } });
    const file = imageFile();
    expect(fireEvent.paste(textarea, { clipboardData: { files: [file] } })).toBe(false);
    await screen.findByRole("img", { name: "已選擇的圖片" });
    expect(textarea).toHaveValue("圖片的文字說明");
    expect(onSuggest).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "移除圖片" }));
    expect(screen.queryByRole("img", { name: "已選擇的圖片" })).not.toBeInTheDocument();
    expect(textarea).toHaveValue("圖片的文字說明");
    const image = await selectImage(file);
    expect(screen.getByLabelText("選擇圖片")).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: "傳送訊息" }));
    expect(onSuggest).toHaveBeenCalledExactlyOnceWith("圖片的文字說明", image.getAttribute("src"), image.getAttribute("src"));
    expect(textarea).toHaveValue("");
  });

  it.each([
    { name: "SVG", file: () => new File(["<svg/>"], "image.svg", { type: "image/svg+xml" }), error: "PNG、JPEG、GIF 或 WEBP" },
    { name: "non-image file", file: () => new File(["pdf"], "image.pdf", { type: "application/pdf" }), error: "PNG、JPEG、GIF 或 WEBP" },
    { name: "empty image", file: () => new File([], "image.png", { type: "image/png" }), error: "空的" },
    { name: "oversized image", file: () => new File([new Uint8Array(MAX_IMAGE_BYTES + 1)], "image.png", { type: "image/png" }), error: "5 MB" },
  ])("rejects $name before reading its contents", ({ file, error }) => {
    const readAsDataURL = vi.spyOn(FileReader.prototype, "readAsDataURL");
    const onSuggest = vi.fn();
    render(<I18nProvider><WelcomeHero onSuggest={onSuggest} /></I18nProvider>);
    fireEvent.change(screen.getByLabelText("選擇圖片"), { target: { files: [file()] } });
    expect(readAsDataURL).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(error);
    expect(screen.queryByRole("img", { name: "已選擇的圖片" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "傳送訊息" })).toBeDisabled();
    expect(onSuggest).not.toHaveBeenCalled();
  });

  it("rejects an image whose contents do not match its MIME type", async () => {
    const onSuggest = vi.fn();
    render(<I18nProvider><WelcomeHero onSuggest={onSuggest} /></I18nProvider>);
    fireEvent.change(screen.getByLabelText("選擇圖片"), { target: { files: [new File(["not a PNG"], "image.png", { type: "image/png" })] } });
    expect(await screen.findByRole("alert")).toHaveTextContent("圖片內容與檔案格式不符");
    expect(screen.queryByRole("img", { name: "已選擇的圖片" })).not.toBeInTheDocument();
    expect(onSuggest).not.toHaveBeenCalled();
  });

  it("waits for image reading before sending even when a text draft already exists", async () => {
    const onSuggest = vi.fn();
    render(<I18nProvider><WelcomeHero onSuggest={onSuggest} /></I18nProvider>);
    const textarea = screen.getByRole("textbox", { name: "想說的情況或問題" });
    fireEvent.change(textarea, { target: { value: "附圖說明" } });
    fireEvent.change(screen.getByLabelText("選擇圖片"), { target: { files: [imageFile()] } });
    const sendButton = screen.getByRole("button", { name: "傳送訊息" });
    expect(sendButton).toBeDisabled();
    for (const suggestion of within(screen.getByRole("group", { name: "可以從這裡開始" })).getAllByRole("button")) expect(suggestion).toBeDisabled();
    fireEvent.click(sendButton);
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSuggest).not.toHaveBeenCalled();
    await screen.findByRole("img", { name: "已選擇的圖片" });
    expect(sendButton).toBeEnabled();
  });

  it("keeps a selected image removable when uploads are switched off and blocks sending until removal", async () => {
    const onSuggest = vi.fn();
    const { rerender } = render(<I18nProvider><WelcomeHero onSuggest={onSuggest} /></I18nProvider>);
    await selectImage();
    const textarea = screen.getByRole("textbox", { name: "想說的情況或問題" });
    fireEvent.change(textarea, { target: { value: "不要悄悄捨棄附件" } });
    rerender(<I18nProvider><WelcomeHero onSuggest={onSuggest} allowImageUpload={false} /></I18nProvider>);
    expect(screen.getByRole("img", { name: "已選擇的圖片" })).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent("請移除圖片後再傳送");
    expect(screen.queryByLabelText("選擇圖片")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "傳送訊息" })).toBeDisabled();
    for (const suggestion of within(screen.getByRole("group", { name: "可以從這裡開始" })).getAllByRole("button")) expect(suggestion).toBeDisabled();
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSuggest).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "移除圖片" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "傳送訊息" }));
    expect(onSuggest).toHaveBeenCalledExactlyOnceWith("不要悄悄捨棄附件", undefined, undefined);
  });

  it("blocks image selection, pasted image reading, and sending while disabled", async () => {
    const onSuggest = vi.fn();
    const readAsDataURL = vi.spyOn(FileReader.prototype, "readAsDataURL");
    const { rerender } = render(<I18nProvider><WelcomeHero onSuggest={onSuggest} /></I18nProvider>);
    await selectImage();
    readAsDataURL.mockClear();
    rerender(<I18nProvider><WelcomeHero onSuggest={onSuggest} disabled /></I18nProvider>);
    expect(screen.getByRole("button", { name: "加入圖片" })).toBeDisabled();
    expect(screen.getByLabelText("選擇圖片")).toBeDisabled();
    expect(screen.getByRole("button", { name: "移除圖片" })).toBeDisabled();
    const textarea = screen.getByRole("textbox", { name: "想說的情況或問題" });
    expect(textarea).toBeEnabled();
    fireEvent.change(textarea, { target: { value: "連線完成前先整理的草稿" } });
    expect(textarea).toHaveValue("連線完成前先整理的草稿");
    fireEvent.change(screen.getByLabelText("選擇圖片"), { target: { files: [imageFile()] } });
    fireEvent.paste(textarea, { clipboardData: { files: [imageFile()] } });
    fireEvent.keyDown(textarea, { key: "Enter" });
    fireEvent.click(screen.getByRole("button", { name: "傳送訊息" }));
    expect(readAsDataURL).not.toHaveBeenCalled();
    expect(onSuggest).not.toHaveBeenCalled();
    expect(screen.getByRole("img", { name: "已選擇的圖片" })).toBeVisible();
  });

  it.each([
    { name: "false", cancel: (): SendOutcome => false },
    { name: "an asynchronous false", cancel: (): SendOutcome => Promise.resolve(false) },
    { name: "a rejected promise", cancel: (): SendOutcome => Promise.reject(new Error("Review cancelled")) },
  ])("retains both text and image after $name and clears them only after acceptance", async ({ cancel }) => {
    const onSuggest = vi.fn<() => SendOutcome>().mockImplementationOnce(cancel).mockResolvedValueOnce(true);
    render(<I18nProvider><WelcomeHero onSuggest={onSuggest} /></I18nProvider>);
    const image = await selectImage();
    const dataUrl = image.getAttribute("src");
    const textarea = screen.getByRole("textbox", { name: "想說的情況或問題" });
    fireEvent.change(textarea, { target: { value: "  附件的原始說明  " } });
    const sendButton = screen.getByRole("button", { name: "傳送訊息" });
    await act(async () => { fireEvent.click(sendButton); });
    expect(onSuggest).toHaveBeenCalledExactlyOnceWith("附件的原始說明", dataUrl, dataUrl);
    expect(textarea).toHaveValue("  附件的原始說明  ");
    expect(screen.getByRole("img", { name: "已選擇的圖片" })).toHaveAttribute("src", dataUrl);
    expect(sendButton).toBeEnabled();
    fireEvent.change(textarea, { target: { value: "修正後的附件說明" } });
    await act(async () => { fireEvent.click(sendButton); });
    expect(onSuggest).toHaveBeenLastCalledWith("修正後的附件說明", dataUrl, dataUrl);
    expect(onSuggest).toHaveBeenCalledTimes(2);
    expect(textarea).toHaveValue("");
    expect(screen.queryByRole("img", { name: "已選擇的圖片" })).not.toBeInTheDocument();
    expect(sendButton).toBeDisabled();
  });

  it("keeps text and image while confirmation is pending and ignores repeated clicks and Enter", async () => {
    let finishConfirmation!: (approved: boolean) => void;
    const onSuggest = vi.fn(() => new Promise<boolean>(resolve => { finishConfirmation = resolve; }));
    render(<I18nProvider><WelcomeHero onSuggest={onSuggest} /></I18nProvider>);
    await selectImage();
    const textarea = screen.getByRole("textbox", { name: "想說的情況或問題" });
    fireEvent.change(textarea, { target: { value: "等待確認的附圖說明" } });
    const sendButton = screen.getByRole("button", { name: "傳送訊息" });
    fireEvent.click(sendButton);
    expect(textarea).toHaveValue("等待確認的附圖說明");
    expect(screen.getByRole("img", { name: "已選擇的圖片" })).toBeVisible();
    expect(sendButton).toBeDisabled();
    fireEvent.click(sendButton);
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSuggest).toHaveBeenCalledOnce();
    await act(async () => { finishConfirmation(false); });
    expect(sendButton).toBeEnabled();
    expect(screen.getByRole("img", { name: "已選擇的圖片" })).toBeVisible();
  });

  it("grows from one line to a viewport-bounded height and shrinks after sending without an inner focus outline", async () => {
    vi.spyOn(HTMLTextAreaElement.prototype, "scrollHeight", "get").mockImplementation(function (this: HTMLTextAreaElement) {
      return this.value.length > 100 ? 700 : this.value.includes("\n") ? 120 : 24;
    });
    vi.stubGlobal("innerHeight", 600);
    render(<I18nProvider><WelcomeHero onSuggest={vi.fn()} /></I18nProvider>);
    const textarea = screen.getByRole("textbox", { name: "想說的情況或問題" });
    const height = () => Number.parseFloat(textarea.style.height);
    const initialHeight = height();
    expect(textarea).toHaveAttribute("rows", "1");
    expect(textarea).toHaveStyle({ outline: "none", boxShadow: "none" });
    fireEvent.change(textarea, { target: { value: "第一行\n第二行" } });
    expect(height()).toBeGreaterThan(initialHeight);
    fireEvent.change(textarea, { target: { value: "長".repeat(200) } });
    expect(height()).toBeLessThanOrEqual(240);
    expect(textarea).toHaveStyle({ overflowY: "auto" });
    vi.stubGlobal("innerHeight", 300);
    fireEvent(window, new Event("resize"));
    expect(height()).toBeLessThanOrEqual(120);
    fireEvent.click(screen.getByRole("button", { name: "傳送訊息" }));
    await waitFor(() => expect(height()).toBe(initialHeight));
    expect(textarea).toHaveStyle({ overflowY: "hidden" });
  });

  it.each([
    { name: "false", cancel: () => false },
    { name: "an asynchronous false", cancel: () => Promise.resolve(false) },
    { name: "a rejected promise", cancel: () => Promise.reject(new Error("Review cancelled")) },
  ])("preserves an editable draft after $name and allows retry", async ({ cancel }) => {
    const onSuggest = vi.fn<() => SendOutcome>()
      .mockImplementationOnce(cancel)
      .mockReturnValueOnce(undefined);
    render(<I18nProvider><WelcomeHero onSuggest={onSuggest} /></I18nProvider>);
    const textarea = screen.getByRole("textbox", { name: "想說的情況或問題" });
    const sendButton = screen.getByRole("button", { name: "傳送訊息" });
    fireEvent.change(textarea, { target: { value: "  需要再檢查的草稿  " } });

    await act(async () => { fireEvent.click(sendButton); });

    expect(onSuggest).toHaveBeenCalledTimes(1);
    expect(onSuggest).toHaveBeenNthCalledWith(1, "需要再檢查的草稿", undefined, undefined);
    expect(textarea).toHaveValue("  需要再檢查的草稿  ");
    expect(textarea).toBeEnabled();
    expect(sendButton).toBeEnabled();

    fireEvent.change(textarea, { target: { value: "修改後的草稿" } });
    fireEvent.click(sendButton);

    expect(onSuggest).toHaveBeenCalledTimes(2);
    expect(onSuggest).toHaveBeenNthCalledWith(2, "修改後的草稿", undefined, undefined);
    expect(textarea).toHaveValue("");
    expect(sendButton).toBeDisabled();
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
