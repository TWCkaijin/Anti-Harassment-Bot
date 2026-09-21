import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../i18n";
import {
  MAX_USER_MESSAGE_CHARACTERS,
  USER_MESSAGE_TOO_LONG_ERROR,
} from "../hooks/conversationHistory";
import ChatInput from "./ChatInput";

describe("ChatInput", () => {
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
});
