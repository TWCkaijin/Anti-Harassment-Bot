import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../i18n";
import {
  MAX_USER_MESSAGE_CHARACTERS,
  USER_MESSAGE_TOO_LONG_ERROR,
} from "../hooks/conversationHistory";
import ChatInput from "./ChatInput";
import { IMAGE_ACCEPT, IMAGE_ONLY_MESSAGE, MAX_IMAGE_BYTES } from "../services/imageUpload";
import type { ConversationMessage } from "../hooks/useConversation";

const replyPrompt: ConversationMessage = {
  id: "assistant-1",
  role: "assistant",
  content: "我會陪您一起了解接下來的選擇。",
  timestamp: 1,
  interactionMode: "clarify",
  clarifyingQuestions: ["您想先了解哪一項？"],
  actionButtons: [{
    action: "options", id: "choose_next", label: "選擇下一步", title: "您想先了解哪一項？",
    options: [{ label: "了解資源", value: "我想先了解可使用的資源" }, { label: "繼續說明", value: "我想繼續說明" }],
  }],
};

const cancelledSends = [
  ["synchronous cancellation", () => false],
  ["asynchronous cancellation", () => Promise.resolve(false)],
  ["rejected confirmation", () => Promise.reject(new Error("Confirmation unavailable"))],
] as const;
const pngFile = () => new File([new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10, 0])], "example.png", { type: "image/png" });

beforeEach(() => localStorage.clear());
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("ChatInput", () => {
  const v4Prompt: ConversationMessage = { ...replyPrompt, contractVersion: 4, factsRevision: 2,
    clarification: { question_id: "open.2", context_revision: 2, question: "事情發生在哪種場合？", reason: "釐清場合", options: [{ label: "校外", value: "校外" }], selection_mode: "single", max_selections: 1, validation_token: "signed" } };
  it.each([
    ["capabilities are pending", undefined, v4Prompt],
    ["the backend is older", 3, v4Prompt],
    ["the question revision is stale", 4, { ...v4Prompt, clarification: { ...v4Prompt.clarification!, context_revision: 1 } }],
    ["the message revision is stale", 4, { ...v4Prompt, factsRevision: 1 }],
    ["the question is superseded", 4, { ...v4Prompt, superseded: true }],
    ["the signing token is missing", 4, { ...v4Prompt, clarification: { ...v4Prompt.clarification!, validation_token: undefined } }],
    ["a saved v2 question remains after upgrading", 4, { ...replyPrompt, contractVersion: 2, factsRevision: 2, clarification: { question_id: "case.city.2", fact_key: "city", question: "在哪個城市？", reason: "釐清地點", options: [{ label: "高雄", value: "高雄" }] } }],
  ] as const)("keeps the composer available without actionable old choices when %s", (_name, version, prompt) => {
    const onSend = vi.fn();
    const snapshot = structuredClone(prompt);
    render(<I18nProvider><ChatInput onSend={onSend} replyPrompt={prompt as ConversationMessage} caseRevision={2} activeContractVersion={version} /></I18nProvider>);
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "送出回覆" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "了解資源" })).not.toBeInTheDocument();
    const composer = screen.getByPlaceholderText("請描述您的狀況或提出問題…");
    expect(composer).toBeVisible();
    fireEvent.change(composer, { target: { value: "我想直接補充" } });
    fireEvent.click(screen.getByRole("button", { name: "傳送訊息" }));
    expect(onSend).toHaveBeenCalledWith("我想直接補充", undefined, undefined);
    expect(prompt).toEqual(snapshot);
  });
  it("enables a current v4 question after health and preserves hide/show drafts", () => {
    const onSend = vi.fn();
    render(<I18nProvider><ChatInput onSend={onSend} replyPrompt={v4Prompt} caseRevision={2} activeContractVersion={4} /></I18nProvider>);
    fireEvent.click(screen.getByRole("radio", { name: "校外" }));
    fireEvent.click(screen.getByRole("button", { name: "隱藏選項" }));
    expect(screen.getByPlaceholderText("請描述您的狀況或提出問題…")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "顯示問題" }));
    expect(screen.getByRole("radio", { name: "校外" })).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "送出回覆" }));
    expect(onSend).toHaveBeenCalledWith(expect.any(String), undefined, undefined, expect.objectContaining({ clarificationAnswer: expect.objectContaining({ question_id: "open.2", context_revision: 2, validation_token: "signed" }) }));
  });
  it("ignores stale clarification metadata when v3 explicitly answers", () => {
    const prompt: ConversationMessage = { ...replyPrompt, contractVersion: 3, factsRevision: 2, interactionMode: "answer", clarification: { question_id: "case.other_role.2", fact_key: "other_role", question: "對方與您是什麼關係？", reason: "釐清情境", options: [{ label: "主管", value: "主管" }], selection_mode: "single", max_selections: 1 } };
    render(<I18nProvider><ChatInput onSend={vi.fn()} replyPrompt={prompt} caseRevision={2} /></I18nProvider>);
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText("請描述您的狀況或提出問題…")).toBeVisible();
  });
  it("hides the picker and blocks pasted images when uploads are disabled", () => {
    const onSend = vi.fn();
    const read = vi.spyOn(FileReader.prototype, "readAsDataURL");
    const { container } = render(<I18nProvider><ChatInput onSend={onSend} allowImageUpload={false} /></I18nProvider>);
    expect(screen.queryByRole("button", { name: "加入圖片" })).not.toBeInTheDocument();
    expect(container.querySelector('input[type="file"]')).toBeNull();
    fireEvent.paste(screen.getByPlaceholderText("請描述您的狀況或提出問題…"), { clipboardData: { files: [new File(["image"], "test.png", { type: "image/png" })] } });
    expect(screen.getByRole("alert")).toHaveTextContent("圖片上傳目前已停用");
    expect(read).not.toHaveBeenCalled();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(onSend).not.toHaveBeenCalled();
  });
  it.each([undefined, true])("offers the image picker when allowImageUpload=%s", allowImageUpload => {
    render(<I18nProvider><ChatInput onSend={vi.fn()} allowImageUpload={allowImageUpload} /></I18nProvider>);
    expect(screen.getByRole("button", { name: "加入圖片" })).toBeEnabled();
    expect(screen.getByLabelText("選擇圖片")).toHaveAttribute("accept", IMAGE_ACCEPT);
    expect(screen.queryByText(/尚未提供本機去識別/)).not.toBeInTheDocument();
  });
  it("disables typed clarification sending while keeping hide and draft recovery available", () => {
    const onSend = vi.fn();
    const typedPrompt: ConversationMessage = { ...replyPrompt, contractVersion: 2, factsRevision: 2, clarification: { question_id: "case.other_role.2", fact_key: "other_role", question: "對方與您是什麼關係？", reason: "協助了解處理管道", options: [{ label: "主管", value: "主管" }] } };
    const { rerender } = render(<I18nProvider><ChatInput onSend={onSend} replyPrompt={typedPrompt} caseRevision={2} activeContractVersion={2} /></I18nProvider>);
    fireEvent.click(screen.getByRole("radio", { name: "自行補充" }));
    fireEvent.change(screen.getByRole("textbox", { name: "自行補充" }), { target: { value: "客戶" } });
    rerender(<I18nProvider><ChatInput onSend={onSend} replyPrompt={typedPrompt} caseRevision={2} activeContractVersion={2} disabled /></I18nProvider>);
    expect(screen.getByRole("radio", { name: "主管" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "送出回覆" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "送出回覆" }));
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "隱藏選項" }));
    expect(screen.getByPlaceholderText("請描述您的狀況或提出問題…")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "顯示問題" }));
    expect(screen.getByRole("button", { name: "隱藏選項" })).toHaveFocus();
    expect(screen.getByRole("textbox", { name: "自行補充" })).toHaveValue("客戶");
  });

  it("limits next-step suggestions across combined actions and keeps the composer available", () => {
    const action = replyPrompt.actionButtons![0];
    if (action.action !== "options") throw new Error("Expected choice fixture");
    const prompt: ConversationMessage = { ...replyPrompt, interactionMode: "answer", clarifyingQuestions: [], actionButtons: [
      { ...action, options: [{ label: "一", value: "回覆一" }, { label: "二", value: "回覆二" }] },
      { ...action, id: "choose_more", options: [{ label: " 一 ", value: "回覆一" }, { label: "三", value: "回覆三" }, { label: "四", value: "回覆四" }, { label: "五", value: "回覆五" }] },
    ] };
    const onSend = vi.fn();
    render(<I18nProvider><ChatInput onSend={onSend} replyPrompt={prompt} /></I18nProvider>);
    const suggestions = within(screen.getByRole("region", { name: "下一步建議" }));
    expect(suggestions.getAllByRole("button")).toHaveLength(4);
    expect(suggestions.getByRole("button", { name: "四" })).toBeVisible();
    expect(suggestions.queryByRole("button", { name: "五" })).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText("請描述您的狀況或提出問題…")).toBeVisible();
    fireEvent.click(suggestions.getByRole("button", { name: "四" }));
    expect(onSend).toHaveBeenCalledExactlyOnceWith("回覆四");
  });

  it("submits trimmed text through the accessible send control", () => {
    const onSend = vi.fn();
    render(
      <I18nProvider>
        <ChatInput onSend={onSend} />
      </I18nProvider>,
    );

    fireEvent.change(screen.getByPlaceholderText("請描述您的狀況或提出問題…"), {
      target: { value: "  我需要協助  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "傳送訊息" }));

    expect(onSend).toHaveBeenCalledWith("我需要協助", undefined, undefined);
  });

  it.each(cancelledSends)("retains the editable text draft after %s and permits another send", async (_name, cancel) => {
    const onSend = vi.fn<() => boolean | Promise<boolean>>().mockImplementationOnce(cancel).mockReturnValue(true);
    render(<I18nProvider><ChatInput onSend={onSend} /></I18nProvider>);
    const composer = screen.getByPlaceholderText("請描述您的狀況或提出問題…");
    const sendButton = screen.getByRole("button", { name: "傳送訊息" });
    fireEvent.change(composer, { target: { value: "  還在整理的草稿  " } });

    await act(async () => { fireEvent.click(sendButton); });

    expect(onSend).toHaveBeenCalledExactlyOnceWith("還在整理的草稿", undefined, undefined);
    expect(composer).toHaveValue("  還在整理的草稿  ");
    await waitFor(() => expect(sendButton).toBeEnabled());
    fireEvent.change(composer, { target: { value: "修正後的草稿" } });
    await act(async () => { fireEvent.click(sendButton); });
    expect(onSend).toHaveBeenLastCalledWith("修正後的草稿", undefined, undefined);
    expect(onSend).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(composer).toHaveValue(""));
  });

  it("keeps the draft while confirmation is pending and prevents duplicate submissions", async () => {
    let finishConfirmation!: (approved: boolean) => void;
    const onSend = vi.fn(() => new Promise<boolean>(resolve => { finishConfirmation = resolve; }));
    render(<I18nProvider><ChatInput onSend={onSend} /></I18nProvider>);
    const composer = screen.getByPlaceholderText("請描述您的狀況或提出問題…");
    const sendButton = screen.getByRole("button", { name: "傳送訊息" });
    fireEvent.change(composer, { target: { value: "等待確認的草稿" } });
    fireEvent.click(sendButton);
    expect(composer).toHaveValue("等待確認的草稿");
    fireEvent.click(sendButton);
    fireEvent.keyDown(composer, { key: "Enter" });
    expect(onSend).toHaveBeenCalledOnce();

    await act(async () => { finishConfirmation(false); });
    expect(composer).toHaveValue("等待確認的草稿");
    expect(sendButton).toBeEnabled();
  });

  it("preserves a selected image and draft on cancellation, then clears both after acceptance", async () => {
    const onSend = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    render(<I18nProvider><ChatInput onSend={onSend} /></I18nProvider>);
    const composer = screen.getByPlaceholderText("請描述您的狀況或提出問題…");
    fireEvent.change(composer, { target: { value: "  請協助看這張圖  " } });
    fireEvent.change(screen.getByLabelText("選擇圖片"), { target: { files: [pngFile()] } });
    const preview = await screen.findByRole("img", { name: "已選擇的圖片" });
    const dataUrl = preview.getAttribute("src");
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "傳送訊息" })); });
    expect(onSend).toHaveBeenLastCalledWith("請協助看這張圖", dataUrl, dataUrl);
    expect(composer).toHaveValue("  請協助看這張圖  ");
    expect(preview).toBeVisible();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "傳送訊息" })); });
    expect(onSend).toHaveBeenCalledTimes(2);
    expect(composer).toHaveValue("");
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("pastes, removes and sends an image-only request with a neutral message", async () => {
    const onSend = vi.fn();
    render(<I18nProvider><ChatInput onSend={onSend} /></I18nProvider>);
    const composer = screen.getByPlaceholderText("請描述您的狀況或提出問題…");
    fireEvent.paste(composer, { clipboardData: { files: [pngFile()] } });
    await screen.findByRole("img", { name: "已選擇的圖片" });
    fireEvent.click(screen.getByRole("button", { name: "移除圖片" }));
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "傳送訊息" })).toBeDisabled();
    fireEvent.paste(composer, { clipboardData: { files: [pngFile()] } });
    const preview = await screen.findByRole("img", { name: "已選擇的圖片" });
    const dataUrl = preview.getAttribute("src");
    fireEvent.click(screen.getByRole("button", { name: "傳送訊息" }));
    expect(onSend).toHaveBeenCalledExactlyOnceWith(IMAGE_ONLY_MESSAGE, dataUrl, dataUrl);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("keeps a selected image across menu hiding and blocks it if runtime uploads become disabled", async () => {
    const onSend = vi.fn();
    const { rerender } = render(<I18nProvider><ChatInput onSend={onSend} /></I18nProvider>);
    fireEvent.change(screen.getByLabelText("選擇圖片"), { target: { files: [pngFile()] } });
    const preview = await screen.findByRole("img", { name: "已選擇的圖片" });
    rerender(<I18nProvider><ChatInput onSend={onSend} replyPrompt={replyPrompt} /></I18nProvider>);
    expect(preview).not.toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "隱藏選項" }));
    expect(preview).toBeVisible();
    rerender(<I18nProvider><ChatInput onSend={onSend} allowImageUpload={false} /></I18nProvider>);
    expect(preview).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent("請移除圖片後再傳送");
    expect(screen.getByRole("button", { name: "傳送訊息" })).toBeDisabled();
    fireEvent.keyDown(screen.getByPlaceholderText("請描述您的狀況或提出問題…"), { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "移除圖片" }));
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("rejects unsupported, oversize and spoofed images without sending them", async () => {
    const read = vi.spyOn(FileReader.prototype, "readAsDataURL");
    const onSend = vi.fn();
    render(<I18nProvider><ChatInput onSend={onSend} /></I18nProvider>);
    const picker = screen.getByLabelText("選擇圖片");
    fireEvent.change(picker, { target: { files: [new File(["<svg/>"], "image.svg", { type: "image/svg+xml" })] } });
    expect(screen.getByRole("alert")).toHaveTextContent("PNG、JPEG、GIF 或 WEBP");
    const oversize = pngFile();
    Object.defineProperty(oversize, "size", { value: MAX_IMAGE_BYTES + 1 });
    fireEvent.change(picker, { target: { files: [oversize] } });
    expect(screen.getByRole("alert")).toHaveTextContent("5 MB");
    expect(read).not.toHaveBeenCalled();
    fireEvent.change(picker, { target: { files: [new File(["not-an-image"], "fake.png", { type: "image/png" })] } });
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("圖片內容與檔案格式不符"));
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(onSend).not.toHaveBeenCalled();
  });

  it.each([{ disabled: true }, { isLoading: true }])("does not read pasted images while unavailable: %j", state => {
    const read = vi.spyOn(FileReader.prototype, "readAsDataURL");
    render(<I18nProvider><ChatInput onSend={vi.fn()} {...state} /></I18nProvider>);
    expect(screen.getByRole("button", { name: "加入圖片" })).toBeDisabled();
    fireEvent.paste(screen.getByPlaceholderText("請描述您的狀況或提出問題…"), { clipboardData: { files: [pngFile()] } });
    expect(read).not.toHaveBeenCalled();
  });

  it("grows and shrinks with content without a blue inner outline, retaining orange container focus", () => {
    let scrollHeight = 140;
    vi.spyOn(HTMLTextAreaElement.prototype, "scrollHeight", "get").mockImplementation(() => scrollHeight);
    render(<I18nProvider><ChatInput onSend={vi.fn()} /></I18nProvider>);
    const composer = screen.getByPlaceholderText("請描述您的狀況或提出問題…");
    fireEvent.change(composer, { target: { value: "第一行\n第二行\n第三行\n第四行" } });
    expect(composer).toHaveStyle({ height: "140px", outline: "none", boxShadow: "none" });
    fireEvent.focus(composer);
    expect(composer.parentElement).toHaveClass("ring-primary/20");
    scrollHeight = 44;
    fireEvent.change(composer, { target: { value: "" } });
    expect(composer).toHaveStyle({ height: "44px", overflowY: "hidden" });
  });

  describe.each([
    { name: "typed", prompt: v4Prompt, label: "校外", activeContractVersion: 4 },
    { name: "legacy", prompt: replyPrompt, label: "了解資源", activeContractVersion: undefined },
  ] as const)("$name clarification confirmation", ({ prompt, label, activeContractVersion }) => {
    it.each(cancelledSends)("keeps the selected answer visible after %s and permits resubmission", async (_name, cancel) => {
      const onSend = vi.fn<() => boolean | Promise<boolean>>().mockImplementationOnce(cancel).mockReturnValue(true);
      render(<I18nProvider><ChatInput onSend={onSend} replyPrompt={prompt} caseRevision={2} activeContractVersion={activeContractVersion} /></I18nProvider>);
      fireEvent.click(screen.getByRole("radio", { name: label }));
      await act(async () => { fireEvent.click(screen.getByRole("button", { name: "送出回覆" })); });

      expect(onSend).toHaveBeenCalledOnce();
      expect(screen.getByRole("radio", { name: label })).toBeChecked();
      expect(screen.getByRole("radio", { name: label })).toBeVisible();
      await waitFor(() => expect(screen.getByRole("button", { name: "送出回覆" })).toBeEnabled());
      expect(screen.getByPlaceholderText("請描述您的狀況或提出問題…")).not.toBeVisible();

      await act(async () => { fireEvent.click(screen.getByRole("button", { name: "送出回覆" })); });
      expect(onSend).toHaveBeenCalledTimes(2);
      expect(onSend.mock.calls[1]).toEqual(onSend.mock.calls[0]);
      await waitFor(() => expect(screen.getByPlaceholderText("請描述您的狀況或提出問題…")).toBeVisible());
      expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    });
  });

  it.each(cancelledSends)("keeps next-step suggestions usable after %s", async (_name, cancel) => {
    const onSend = vi.fn<() => boolean | Promise<boolean>>().mockImplementationOnce(cancel).mockReturnValue(true);
    render(<I18nProvider><ChatInput onSend={onSend} suggestedReplies={["查看資源"]} /></I18nProvider>);
    const suggestion = screen.getByRole("button", { name: "查看資源" });
    await act(async () => { fireEvent.click(suggestion); });
    expect(onSend).toHaveBeenCalledExactlyOnceWith("查看資源");
    await waitFor(() => expect(suggestion).toBeEnabled());
    await act(async () => { fireEvent.click(suggestion); });
    expect(onSend).toHaveBeenCalledTimes(2);
    expect(suggestion).toBeDisabled();
  });

  it("keeps overflow visible and only submits at the 2,000-character boundary", () => {
    const onSend = vi.fn();
    render(
      <I18nProvider>
        <ChatInput onSend={onSend} />
      </I18nProvider>,
    );
    const textarea = screen.getByPlaceholderText("請描述您的狀況或提出問題…");
    const sendButton = screen.getByRole("button", { name: "傳送訊息" });
    const boundary = "x".repeat(MAX_USER_MESSAGE_CHARACTERS);
    const overflow = `${boundary}x`;

    fireEvent.change(textarea, { target: { value: overflow } });

    expect(textarea).toHaveValue(overflow);
    expect(screen.getByRole("alert")).toHaveTextContent(USER_MESSAGE_TOO_LONG_ERROR);
    expect(sendButton).toBeDisabled();
    expect(onSend).not.toHaveBeenCalled();

    fireEvent.change(textarea, { target: { value: boundary } });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(sendButton).toBeEnabled();
    fireEvent.click(sendButton);

    expect(onSend).toHaveBeenCalledWith(boundary, undefined, undefined);
  });

  it("covers the ordinary composer and restores its text draft after hiding the menu", () => {
    const onSend = vi.fn();
    const { rerender } = render(<I18nProvider><ChatInput onSend={onSend} /></I18nProvider>);
    const composer = screen.getByPlaceholderText("請描述您的狀況或提出問題…");
    fireEvent.change(composer, { target: { value: "一般輸入框尚未送出的草稿" } });
    rerender(<I18nProvider><ChatInput onSend={onSend} replyPrompt={replyPrompt} /></I18nProvider>);

    expect(composer).not.toBeVisible();
    expect(screen.queryByRole("button", { name: "加入圖片" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "傳送訊息" })).not.toBeInTheDocument();
    expect(screen.getByText(/\/ 2,000/)).not.toBeVisible();
    fireEvent.keyDown(composer, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();

    fireEvent.change(screen.getByRole("textbox", { name: "其他補充" }), { target: { value: "選單裡的另一份草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "隱藏選項" }));
    expect(composer).toBeVisible();
    expect(composer).toHaveFocus();
    expect(composer).toHaveValue("一般輸入框尚未送出的草稿");
    expect(screen.getByRole("button", { name: "加入圖片" })).toBeVisible();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "下一步建議" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "顯示選單" }));

    expect(screen.getByRole("button", { name: "隱藏選項" })).toHaveFocus();
    expect(screen.getByRole("radio", { name: "其他" })).toBeChecked();
    expect(screen.getByRole("textbox", { name: "其他補充" })).toHaveValue("選單裡的另一份草稿");
    expect(composer).not.toBeVisible();
    fireEvent.click(screen.getByRole("radio", { name: "了解資源" }));
    fireEvent.click(screen.getByRole("button", { name: "隱藏選項" }));
    fireEvent.click(screen.getByRole("button", { name: "顯示選單" }));
    expect(screen.getByRole("radio", { name: "了解資源" })).toBeChecked();
    expect(screen.getByRole("textbox", { name: "其他補充" })).toHaveValue("選單裡的另一份草稿");
    expect(onSend).not.toHaveBeenCalled();
  });

  it("restores the composer after submission and forwards structured answers alongside model context", () => {
    const onSend = vi.fn();
    render(<I18nProvider><ChatInput onSend={onSend} replyPrompt={replyPrompt} /></I18nProvider>);
    fireEvent.change(screen.getByRole("textbox", { name: "其他補充" }), { target: { value: "  我想先休息  " } });
    fireEvent.click(screen.getByRole("button", { name: "送出回覆" }));

    expect(onSend).toHaveBeenCalledExactlyOnceWith("您想先了解哪一項？\n我想先休息", undefined, undefined, {
      answers: [{ question: "您想先了解哪一項？", answer: "我想先休息" }],
    });
    expect(screen.getByPlaceholderText("請描述您的狀況或提出問題…")).toBeVisible();
    expect(screen.queryByRole("button", { name: "顯示選單" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  });

  it("keeps stopping available with the loading menu both expanded and hidden", () => {
    const onSend = vi.fn();
    const onStop = vi.fn();
    render(<I18nProvider><ChatInput onSend={onSend} onStop={onStop} replyPrompt={replyPrompt} isLoading /></I18nProvider>);
    expect(screen.getAllByRole("button", { name: "停止回覆" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "停止回覆" }));
    expect(onStop).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "送出回覆" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "隱藏選項" }));
    expect(screen.getAllByRole("button", { name: "停止回覆" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "停止回覆" }));
    expect(onStop).toHaveBeenCalledTimes(2);
    expect(onSend).not.toHaveBeenCalled();
  });

  it("lets Escape hide the panel without sending or losing the selected answer", () => {
    const onSend = vi.fn();
    render(<I18nProvider><ChatInput onSend={onSend} replyPrompt={replyPrompt} /></I18nProvider>);
    const option = screen.getByRole("radio", { name: "了解資源" });
    fireEvent.click(option);
    fireEvent.keyDown(option, { key: "Escape" });
    expect(screen.getByPlaceholderText("請描述您的狀況或提出問題…")).toBeVisible();
    expect(screen.getByPlaceholderText("請描述您的狀況或提出問題…")).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: "顯示選單" }));
    expect(screen.getByRole("radio", { name: "了解資源" })).toBeChecked();
    expect(onSend).not.toHaveBeenCalled();
  });

  it.each([
    { actionButtons: [], suggestedReplies: [], clarifyingQuestions: [] },
    { actionButtons: null, suggestedReplies: null, clarifyingQuestions: null },
    { actionButtons: [{ action: "url", label: "無效連結", url: "javascript:alert(1)" }], suggestedReplies: [" ", "x".repeat(2001)], clarifyingQuestions: [null] },
  ])("keeps the composer available when the latest reply has no usable menu content: %j", (data) => {
    render(<I18nProvider><ChatInput onSend={vi.fn()} replyPrompt={{ ...replyPrompt, ...data } as unknown as ConversationMessage} /></I18nProvider>);
    expect(screen.getByPlaceholderText("請描述您的狀況或提出問題…")).toBeVisible();
    expect(screen.queryByRole("button", { name: "顯示選單" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "隱藏選項" })).not.toBeInTheDocument();
  });

  it.each([
    {
      name: "answer with suggestions",
      data: { interactionMode: "answer", actionButtons: [], clarifyingQuestions: [], suggestedReplies: ["了解申訴流程", "查看求助資源"] },
      label: "了解申訴流程",
    },
    {
      name: "answer with configured options",
      data: { interactionMode: "answer", actionButtons: replyPrompt.actionButtons, clarifyingQuestions: [], suggestedReplies: ["不應優先顯示的建議"] },
      label: "了解資源",
    },
    {
      name: "clarify without valid questions",
      data: { interactionMode: "clarify", actionButtons: [], clarifyingQuestions: [" "], suggestedReplies: ["了解申訴流程", "查看求助資源"] },
      label: "了解申訴流程",
    },
    {
      name: "answer with leftover question metadata",
      data: { interactionMode: "answer", actionButtons: [], clarifyingQuestions: ["不該顯示的追問？"], suggestedReplies: ["了解申訴流程", "查看求助資源"] },
      label: "了解申訴流程",
    },
  ] satisfies { name: string; data: Partial<ConversationMessage>; label: string }[])(
    "keeps the composer and horizontal next-step chips for $name",
    ({ data, label }) => {
      render(<I18nProvider><ChatInput onSend={vi.fn()} replyPrompt={{ ...replyPrompt, ...data }} /></I18nProvider>);
      const composer = screen.getByPlaceholderText("請描述您的狀況或提出問題…");
      const chips = screen.getByRole("region", { name: "下一步建議" });
      expect(composer).toBeVisible();
      expect(chips).toBeVisible();
      expect(chips.compareDocumentPosition(composer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(chips.firstElementChild).toHaveClass("flex-nowrap", "overflow-x-auto");
      expect(within(chips).getByRole("button", { name: label })).toHaveClass("whitespace-nowrap", "shrink-0");
      expect(screen.getByRole("button", { name: "加入圖片" })).toBeVisible();
      expect(screen.queryByRole("radio")).not.toBeInTheDocument();
      expect(screen.queryByRole("textbox", { name: "其他補充" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "隱藏選項" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "顯示選單" })).not.toBeInTheDocument();
      expect(screen.queryByText("不該顯示的追問？")).not.toBeInTheDocument();
    },
  );

  it("sends a configured next-step value directly without reply context and prevents repeated clicks", () => {
    const onSend = vi.fn();
    const prompt = { ...replyPrompt, interactionMode: "answer" as const, suggestedReplies: ["不應優先顯示的建議"] };
    const { rerender } = render(<I18nProvider><ChatInput onSend={onSend} replyPrompt={prompt} /></I18nProvider>);
    expect(screen.queryByRole("button", { name: "不應優先顯示的建議" })).not.toBeInTheDocument();
    const suggestion = screen.getByRole("button", { name: "了解資源" });
    fireEvent.click(suggestion);
    fireEvent.click(suggestion);
    fireEvent.click(screen.getByRole("button", { name: "繼續說明" }));
    expect(onSend).toHaveBeenCalledExactlyOnceWith("我想先了解可使用的資源");
    expect(suggestion).toBeDisabled();

    rerender(<I18nProvider><ChatInput onSend={onSend} replyPrompt={{ ...prompt, id: "assistant-2" }} /></I18nProvider>);
    expect(screen.getByRole("button", { name: "了解資源" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "繼續說明" }));
    expect(onSend).toHaveBeenLastCalledWith("我想繼續說明");
    expect(onSend).toHaveBeenCalledTimes(2);
  });

  it("disables next-step submission during loading and retains the ordinary stop control", () => {
    const onSend = vi.fn();
    const onStop = vi.fn();
    const { rerender } = render(<I18nProvider><ChatInput onSend={onSend} onStop={onStop} suggestedReplies={["查看資源"]} isLoading /></I18nProvider>);
    const suggestion = screen.getByRole("button", { name: "查看資源" });
    expect(suggestion).toBeDisabled();
    fireEvent.click(suggestion);
    fireEvent.click(screen.getByRole("button", { name: "停止回覆" }));
    expect(onSend).not.toHaveBeenCalled();
    expect(onStop).toHaveBeenCalledOnce();

    rerender(<I18nProvider><ChatInput onSend={onSend} onStop={onStop} suggestedReplies={["查看資源"]} /></I18nProvider>);
    fireEvent.click(screen.getByRole("button", { name: "查看資源" }));
    expect(onSend).toHaveBeenCalledExactlyOnceWith("查看資源");
  });

  it("leaves resource links to the message while preserving ordinary next-step suggestions", () => {
    const prompt: ConversationMessage = {
      ...replyPrompt, interactionMode: "answer", clarifyingQuestions: [], suggestedReplies: ["查看其他資源"],
      actionButtons: [{ action: "tel", label: "撥打諮詢專線", phone_number: "113" }, { action: "url", label: "官方網站", url: "https://example.org/" }],
    };
    render(<I18nProvider><ChatInput onSend={vi.fn()} replyPrompt={prompt} /></I18nProvider>);
    const composer = screen.getByPlaceholderText("請描述您的狀況或提出問題…");
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看其他資源" })).toBeVisible();
    expect(composer).toBeVisible();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  });
});
