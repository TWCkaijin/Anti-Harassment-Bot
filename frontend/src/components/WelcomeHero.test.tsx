import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  MAX_USER_MESSAGE_CHARACTERS,
  USER_MESSAGE_TOO_LONG_ERROR,
} from "../hooks/conversationHistory";
import { I18nProvider } from "../i18n";
import WelcomeHero from "./WelcomeHero";

beforeEach(() => {
  window.localStorage.clear();
});

describe("WelcomeHero", () => {
  it("shows overflow without truncating and accepts the exact boundary", () => {
    const onSuggest = vi.fn();
    render(
      <I18nProvider>
        <WelcomeHero onSuggest={onSuggest} />
      </I18nProvider>,
    );
    const textarea = screen.getByPlaceholderText("我有什麼可以幫您的？");
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
});
