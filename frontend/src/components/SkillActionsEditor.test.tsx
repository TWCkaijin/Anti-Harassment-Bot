import { useState } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ActionButton } from "../services/api";
import SkillActionsEditor from "./SkillActionsEditor";

function Editor() {
  const [actions, setActions] = useState<ActionButton[]>([]);
  return <SkillActionsEditor actions={actions} onChange={setActions} />;
}

describe("SkillActionsEditor", () => {
  it("keeps distinct IDs and enforces option count bounds while allowing removal", () => {
    render(<Editor />);
    fireEvent.change(screen.getByLabelText("新增按鈕類型"), { target: { value: "options" } });
    fireEvent.click(screen.getByRole("button", { name: "新增按鈕" }));
    fireEvent.click(screen.getByRole("button", { name: "新增按鈕" }));
    const ids = screen.getAllByLabelText(/^選項 ID/);
    expect((ids[0] as HTMLInputElement).value).toMatch(/^choice_[a-z0-9-]+$/);
    expect((ids[1] as HTMLInputElement).value).not.toEqual((ids[0] as HTMLInputElement).value);
    const first = within(screen.getByRole("group", { name: "按鈕 1 · 選項按鈕" }));
    expect(first.getByRole("button", { name: "移除按鈕 1 的選項 1" })).toBeDisabled();
    for (let i = 0; i < 6; i++) fireEvent.click(first.getByRole("button", { name: "新增選項" }));
    expect(first.getByRole("button", { name: "新增選項" })).toBeDisabled();
    expect(first.getAllByLabelText("選項文字")).toHaveLength(8);
    fireEvent.click(first.getByRole("button", { name: "移除按鈕 1 的選項 3" }));
    expect(first.getAllByLabelText("選項文字")).toHaveLength(7);
    expect(first.getByRole("button", { name: "新增選項" })).toBeEnabled();
  });

  it("generates distinct action IDs when editing separate Skills", () => {
    const { rerender } = render(<Editor key="first_skill" />);
    fireEvent.change(screen.getByLabelText("新增按鈕類型"), { target: { value: "options" } });
    fireEvent.click(screen.getByRole("button", { name: "新增按鈕" }));
    const firstId = (screen.getByLabelText(/^選項 ID/) as HTMLInputElement).value;

    rerender(<Editor key="second_skill" />);
    fireEvent.change(screen.getByLabelText("新增按鈕類型"), { target: { value: "options" } });
    fireEvent.click(screen.getByRole("button", { name: "新增按鈕" }));
    const secondId = (screen.getByLabelText(/^選項 ID/) as HTMLInputElement).value;
    expect(secondId).not.toBe(firstId);
    expect(firstId).toMatch(/^[a-z][a-z0-9_-]{1,63}$/);
    expect(secondId).toMatch(/^[a-z][a-z0-9_-]{1,63}$/);
  });
});
