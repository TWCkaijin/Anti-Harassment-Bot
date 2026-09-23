import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ActionButton, OptionsActionButton } from "../services/api";
import FollowUpPanel from "./FollowUpPanel";

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

const nextAction: OptionsActionButton = {
  action: "options", id: "choose_contact", label: "選擇聯絡方式", title: "您偏好哪一種聯絡方式？",
  options: [{ label: "電話", value: "電話聯絡" }, { label: "線上", value: "線上聯絡" }],
};

const clarificationProps = {
  interactionMode: "clarify" as const,
  clarifyingQuestions: [optionsAction.title],
};
const multipleQuestionProps = {
  ...clarificationProps,
  clarifyingQuestions: [optionsAction.title, nextAction.title],
};

describe("FollowUpPanel", () => {
  it.each(["answer", undefined] as const)("does not open in %s mode even when actions and questions are present", (interactionMode) => {
    const { container } = render(<FollowUpPanel
      interactionMode={interactionMode}
      clarifyingQuestions={[optionsAction.title]}
      actions={[optionsAction, { action: "url", label: "相關資源", url: "https://example.org" }]}
      suggestedReplies={["想先了解資源"]}
      onSend={vi.fn()}
      onHide={vi.fn()}
    />);
    expect(container).toBeEmptyDOMElement();
  });

  it.each([undefined, [], ["", "  "], ["x".repeat(2001)]])("does not open in clarify mode without a valid explicit question: %j", (clarifyingQuestions) => {
    const { container } = render(<FollowUpPanel
      interactionMode="clarify"
      clarifyingQuestions={clarifyingQuestions}
      actions={[optionsAction]}
      suggestedReplies={["繼續聊聊"]}
      onSend={vi.fn()}
    />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows inline choices with Other and only sends after explicit submission", () => {
    const onSend = vi.fn();
    const onSent = vi.fn(() => expect(onSend).toHaveBeenCalledTimes(1));
    render(<FollowUpPanel {...clarificationProps} actions={[optionsAction]} suggestedReplies={["不重複的建議"]} onSend={onSend} onSent={onSent} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByText("不重複的建議")).not.toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "其他" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "送出回覆" })).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: "看看資源" }));
    expect(onSend).not.toHaveBeenCalled();
    const send = screen.getByRole("button", { name: "送出回覆" });
    fireEvent.click(send);
    fireEvent.click(send);
    expect(onSend).toHaveBeenCalledExactlyOnceWith(optionsAction.options[0].value, {
      answers: [{ question: optionsAction.title, answer: optionsAction.options[0].value }],
    });
    expect(onSent).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
  });

  it("accepts Other text with question context and does not send the previous selection", () => {
    const onSend = vi.fn();
    render(<FollowUpPanel interactionMode="clarify" clarifyingQuestions={[optionsAction.title]} actions={[optionsAction]} onSend={onSend} />);
    expect(screen.getByRole("region", { name: "AI 需要更多您的資訊" })).toBeInTheDocument();
    expect(screen.getAllByText(optionsAction.title)).toHaveLength(1);
    fireEvent.click(screen.getByRole("radio", { name: "看看資源" }));
    const other = screen.getByRole("textbox", { name: "其他補充" });
    fireEvent.focus(other);
    expect(screen.getByRole("radio", { name: "其他" })).toBeChecked();
    expect(screen.getByRole("button", { name: "送出回覆" })).toBeDisabled();
    fireEvent.change(other, { target: { value: "  我想補充其他情況  " } });
    fireEvent.click(screen.getByRole("button", { name: "送出回覆" }));
    expect(onSend).toHaveBeenCalledExactlyOnceWith(`${optionsAction.title}\n我想補充其他情況`, {
      answers: [{ question: optionsAction.title, answer: "我想補充其他情況" }],
    });
  });

  it("keeps the selected answer and allows retry when the sender throws", () => {
    const onSend = vi.fn().mockImplementationOnce(() => { throw new Error("sender unavailable"); });
    const onSent = vi.fn();
    render(<FollowUpPanel {...clarificationProps} actions={[optionsAction]} onSend={onSend} onSent={onSent} />);
    fireEvent.click(screen.getByRole("radio", { name: "看看資源" }));
    fireEvent.click(screen.getByRole("button", { name: "送出回覆" }));
    expect(screen.getByRole("alert")).toHaveTextContent("回覆未能送出，請再試一次。");
    expect(screen.getByRole("radio", { name: "看看資源" })).toBeChecked();
    expect(onSent).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "送出回覆" }));
    expect(onSend).toHaveBeenCalledTimes(2);
    expect(onSend).toHaveBeenLastCalledWith(optionsAction.options[0].value, {
      answers: [{ question: optionsAction.title, answer: optionsAction.options[0].value }],
    });
    expect(onSent).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
  });

  it("keeps Other drafts when switching to a configured option", () => {
    const onSend = vi.fn();
    render(<FollowUpPanel {...clarificationProps} actions={[optionsAction]} onSend={onSend} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "草稿" } });
    fireEvent.click(screen.getByRole("radio", { name: "繼續說明" }));
    expect(screen.getByRole("textbox")).toHaveValue("草稿");
    fireEvent.click(screen.getByRole("button", { name: "送出回覆" }));
    expect(onSend).toHaveBeenCalledExactlyOnceWith(optionsAction.options[1].value, {
      answers: [{ question: optionsAction.title, answer: optionsAction.options[1].value }],
    });
  });

  it("does not steal input focus when the panel appears", () => {
    render(<><textarea aria-label="主輸入欄" autoFocus /><FollowUpPanel {...clarificationProps} actions={[optionsAction]} onSend={vi.fn()} /></>);
    expect(screen.getByRole("textbox", { name: "主輸入欄" })).toHaveFocus();
  });

  it("only renders a hide control when the parent supports hiding", () => {
    const { rerender } = render(<FollowUpPanel {...clarificationProps} actions={[optionsAction]} onSend={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "隱藏" })).not.toBeInTheDocument();
    rerender(<FollowUpPanel {...clarificationProps} actions={[optionsAction]} onSend={vi.fn()} onHide={vi.fn()} />);
    expect(screen.getByRole("button", { name: "隱藏" })).toBeInTheDocument();
  });

  it("requests hiding without sending or clearing draft answers", () => {
    const onHide = vi.fn();
    const onSend = vi.fn();
    const onSent = vi.fn();
    render(<FollowUpPanel {...multipleQuestionProps} actions={[optionsAction, nextAction]} onSend={onSend} onHide={onHide} onSent={onSent} />);
    fireEvent.click(screen.getByRole("radio", { name: "看看資源" }));
    fireEvent.click(screen.getByRole("button", { name: "下一題" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "稍後再完成的草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "隱藏" }));
    expect(onHide).toHaveBeenCalledTimes(1);
    expect(onSend).not.toHaveBeenCalled();
    expect(onSent).not.toHaveBeenCalled();
    expect(screen.getByText("2 / 2 題")).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue("稍後再完成的草稿");
    fireEvent.click(screen.getByRole("button", { name: "上一題" }));
    expect(screen.getByRole("radio", { name: "看看資源" })).toBeChecked();
  });

  it("hides on Escape from the Other field but preserves IME composition", () => {
    const onHide = vi.fn();
    const onSend = vi.fn();
    render(<FollowUpPanel {...clarificationProps} actions={[optionsAction]} onSend={onSend} onHide={onHide} />);
    const other = screen.getByRole("textbox");
    fireEvent.change(other, { target: { value: "尚未送出的回答" } });
    fireEvent.keyDown(other, { key: "Escape", isComposing: true });
    fireEvent.keyDown(other, { key: "Escape", keyCode: 229 });
    expect(onHide).not.toHaveBeenCalled();
    expect(fireEvent.keyDown(other, { key: "Escape" })).toBe(false);
    expect(onHide).toHaveBeenCalledTimes(1);
    expect(onSend).not.toHaveBeenCalled();
    expect(other).toHaveValue("尚未送出的回答");
  });

  it("allows hiding a clarification panel with resources while loading", () => {
    const onHide = vi.fn();
    render(<FollowUpPanel {...clarificationProps} actions={[{ action: "url", label: "相關資訊", url: "https://example.org" }]} onSend={vi.fn()} onHide={onHide} isLoading />);
    fireEvent.click(screen.getByRole("button", { name: "隱藏" }));
    expect(onHide).toHaveBeenCalledTimes(1);
  });

  it("only puts the Other field in the keyboard tab order when Other is selected", () => {
    render(<FollowUpPanel {...clarificationProps} actions={[optionsAction]} onSend={vi.fn()} />);
    fireEvent.click(screen.getByRole("radio", { name: "看看資源" }));
    expect(screen.getByRole("textbox")).toHaveAttribute("tabindex", "-1");
    fireEvent.click(screen.getByRole("radio", { name: "其他" }));
    expect(screen.getByRole("textbox")).toHaveAttribute("tabindex", "0");
    fireEvent.click(screen.getByRole("radio", { name: "看看資源" }));
    expect(screen.getByRole("textbox")).toHaveAttribute("tabindex", "-1");
  });

  it("preserves IME composition and Shift+Enter, and submits on plain Enter", () => {
    const onSend = vi.fn();
    render(<FollowUpPanel {...clarificationProps} suggestedReplies={["建議"]} onSend={onSend} />);
    const other = screen.getByRole("textbox");
    fireEvent.change(other, { target: { value: "我的說明" } });
    fireEvent.keyDown(other, { key: "Enter", isComposing: true });
    fireEvent.keyDown(other, { key: "Enter", keyCode: 229 });
    fireEvent.keyDown(other, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.keyDown(other, { key: "Enter" });
    expect(onSend).toHaveBeenCalledExactlyOnceWith(`${optionsAction.title}\n我的說明`, {
      answers: [{ question: optionsAction.title, answer: "我的說明" }],
    });
  });

  it("uses legacy suggested replies as choices, requiring confirmation", () => {
    const onSend = vi.fn();
    render(<FollowUpPanel {...clarificationProps} suggestedReplies={["可以先說明", "再想一下"]} onSend={onSend} />);
    expect(screen.getByRole("region", { name: "AI 需要更多您的資訊" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "再想一下" }));
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "送出回覆" }));
    expect(onSend).toHaveBeenCalledExactlyOnceWith(`${optionsAction.title}\n再想一下`, {
      answers: [{ question: optionsAction.title, answer: "再想一下" }],
    });
  });

  it("offers free text when a clarification has no predefined options", () => {
    const onSend = vi.fn();
    render(<FollowUpPanel interactionMode="clarify" clarifyingQuestions={["對方和您是什麼關係？"]} onSend={onSend} />);
    expect(screen.getAllByRole("radio")).toHaveLength(1);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "同事" } });
    fireEvent.click(screen.getByRole("button", { name: "送出回覆" }));
    expect(onSend).toHaveBeenCalledExactlyOnceWith("對方和您是什麼關係？\n同事", {
      answers: [{ question: "對方和您是什麼關係？", answer: "同事" }],
    });
  });

  it("preserves question context for a short legacy suggested answer", () => {
    const onSend = vi.fn();
    render(<FollowUpPanel interactionMode="clarify" clarifyingQuestions={["對方和您是什麼關係？"]} suggestedReplies={["同事", "不認識"]} onSend={onSend} />);
    fireEvent.click(screen.getByRole("radio", { name: "同事" }));
    fireEvent.click(screen.getByRole("button", { name: "送出回覆" }));
    expect(onSend).toHaveBeenCalledExactlyOnceWith("對方和您是什麼關係？\n同事", {
      answers: [{ question: "對方和您是什麼關係？", answer: "同事" }],
    });
  });

  it("includes supplemental clarification context with an Other answer", () => {
    const onSend = vi.fn();
    render(<FollowUpPanel interactionMode="clarify" actions={[optionsAction]} clarifyingQuestions={[optionsAction.title, "也請說明您方便聯絡的時段。"]} onSend={onSend} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "先了解資源，晚上方便聯絡。" } });
    fireEvent.click(screen.getByRole("button", { name: "送出回覆" }));
    expect(onSend).toHaveBeenCalledExactlyOnceWith(`${optionsAction.title}\n也請說明您方便聯絡的時段。\n先了解資源，晚上方便聯絡。`, {
      answers: [{ question: `${optionsAction.title}\n也請說明您方便聯絡的時段。`, answer: "先了解資源，晚上方便聯絡。" }],
    });
  });

  it("blocks whitespace-only Other and messages exceeding the shared total limit", () => {
    const onSend = vi.fn();
    render(<FollowUpPanel {...clarificationProps} actions={[optionsAction]} onSend={onSend} />);
    const other = screen.getByRole("textbox");
    fireEvent.change(other, { target: { value: " \n " } });
    expect(screen.getByRole("button", { name: "送出回覆" })).toBeDisabled();
    fireEvent.change(other, { target: { value: "字".repeat(2000) } });
    expect(screen.getByRole("alert")).toHaveTextContent("訊息不可超過 2,000 個字元");
    expect(screen.getByRole("button", { name: "送出回覆" })).toBeDisabled();
    fireEvent.keyDown(other, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("allows the exact user message boundary after including the question context", () => {
    const onSend = vi.fn();
    render(<FollowUpPanel {...clarificationProps} actions={[optionsAction]} onSend={onSend} />);
    const answer = "字".repeat(2000 - optionsAction.title.length - 1);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: answer } });
    fireEvent.click(screen.getByRole("button", { name: "送出回覆" }));
    expect(onSend).toHaveBeenCalledExactlyOnceWith(`${optionsAction.title}\n${answer}`, {
      answers: [{ question: optionsAction.title, answer }],
    });
  });

  it("disables editing and submission if a request begins with a selection present", () => {
    const onSend = vi.fn();
    const { rerender } = render(<FollowUpPanel {...clarificationProps} actions={[optionsAction]} onSend={onSend} />);
    fireEvent.click(screen.getByRole("radio", { name: "看看資源" }));
    rerender(<FollowUpPanel {...clarificationProps} actions={[optionsAction]} onSend={onSend} isLoading />);
    expect(screen.getByRole("radio", { name: "看看資源" })).toBeDisabled();
    expect(screen.getByRole("textbox")).toBeDisabled();
    expect(screen.getByRole("button", { name: "送出回覆" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "送出回覆" }));
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("associates answers with each question and preserves them while navigating", () => {
    const onSend = vi.fn();
    render(<FollowUpPanel {...multipleQuestionProps} actions={[optionsAction, nextAction]} onSend={onSend} />);
    expect(screen.getByText("1 / 2 題")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上一題" })).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: "看看資源" }));
    expect(screen.getByRole("button", { name: "送出回覆" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "下一題" }));
    expect(screen.getByRole("group", { name: nextAction.title })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下一題" })).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "  電子郵件  " } });
    fireEvent.click(screen.getByRole("button", { name: "上一題" }));
    expect(screen.getByRole("radio", { name: "看看資源" })).toBeChecked();
    expect(screen.getByRole("textbox")).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: "下一題" }));
    expect(screen.getByRole("textbox")).toHaveValue("  電子郵件  ");
    expect(screen.getByText("已填 2 / 2 題")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "送出回覆" }));
    expect(onSend).toHaveBeenCalledExactlyOnceWith(`${optionsAction.title}\n${optionsAction.options[0].value}\n\n${nextAction.title}\n電子郵件`, {
      answers: [
        { question: optionsAction.title, answer: optionsAction.options[0].value },
        { question: nextAction.title, answer: "電子郵件" },
      ],
    });
  });

  it("validates the combined multi-question payload instead of each answer alone", () => {
    const onSend = vi.fn();
    render(<FollowUpPanel {...multipleQuestionProps} actions={[optionsAction, nextAction]} onSend={onSend} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "甲".repeat(1000) } });
    fireEvent.click(screen.getByRole("button", { name: "下一題" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "乙".repeat(1000) } });
    expect(screen.getByRole("alert")).toHaveTextContent("2,000");
    expect(screen.getByRole("button", { name: "送出回覆" })).toBeDisabled();
    expect(onSend).not.toHaveBeenCalled();
  });

  it("renders resource links alongside questions but does not open for resources alone", () => {
    const actions: ActionButton[] = [{ action: "tel", label: "撥打專線", phone_number: "113" }, { action: "url", label: "開啟資源", url: "https://example.org" }];
    const { rerender } = render(<FollowUpPanel {...clarificationProps} actions={[optionsAction, ...actions]} onSend={vi.fn()} />);
    expect(screen.getByRole("link", { name: "撥打專線" })).toHaveAttribute("href", "tel:113");
    expect(screen.getByRole("link", { name: /開啟資源/ })).toHaveAttribute("rel", "noopener noreferrer");
    rerender(<FollowUpPanel actions={actions} onSend={vi.fn()} />);
    expect(screen.queryByRole("region")).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("ignores malformed choices and falls back to valid legacy suggestions", () => {
    const actions = [null, {}, { ...optionsAction, options: [null, {}] }, { ...optionsAction, options: [optionsAction.options[0]] }] as unknown as ActionButton[];
    render(<FollowUpPanel {...clarificationProps} actions={actions} suggestedReplies={["可用建議"]} onSend={vi.fn()} />);
    expect(screen.getByRole("radio", { name: "可用建議" })).toBeInTheDocument();
  });

  it("rejects choices outside server bounds or with duplicate values", () => {
    const malformed: OptionsActionButton[] = [
      { ...optionsAction, label: "x".repeat(81) },
      { ...optionsAction, title: "x".repeat(161) },
      { ...optionsAction, id: "not a valid id" },
      { ...optionsAction, options: [{ label: "x".repeat(81), value: "valid" }, optionsAction.options[1]] },
      { ...optionsAction, options: [{ label: "超長內容", value: "😀".repeat(501) }, optionsAction.options[1]] },
      { ...optionsAction, options: [optionsAction.options[0], { ...optionsAction.options[0], value: ` ${optionsAction.options[0].value} ` }] },
    ];
    render(<FollowUpPanel {...clarificationProps} actions={malformed} onSend={vi.fn()} />);
    expect(screen.getAllByRole("radio")).toHaveLength(1);
    expect(screen.getByRole("radio", { name: "其他" })).toBeInTheDocument();
  });

  it("accepts the 500-code-point option boundary and sends its exact configured value", () => {
    const onSend = vi.fn();
    const value = "😀".repeat(500);
    render(<FollowUpPanel {...clarificationProps} actions={[{ ...optionsAction, options: [{ label: "邊界", value }, optionsAction.options[1]] }]} onSend={onSend} />);
    fireEvent.click(screen.getByRole("radio", { name: "邊界" }));
    fireEvent.click(screen.getByRole("button", { name: "送出回覆" }));
    expect(onSend).toHaveBeenCalledExactlyOnceWith(value, {
      answers: [{ question: optionsAction.title, answer: value }],
    });
  });

  it("does not render for absent, empty, or entirely malformed data", () => {
    const { container, rerender } = render(<FollowUpPanel onSend={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<FollowUpPanel actions={null as unknown as ActionButton[]} suggestedReplies={null as unknown as string[]} clarifyingQuestions={null as unknown as string[]} onSend={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<FollowUpPanel actions={[{ action: "url", label: "壞網址", url: "javascript:alert(1)" }]} suggestedReplies={["", " ", "x".repeat(2001)]} onSend={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});
