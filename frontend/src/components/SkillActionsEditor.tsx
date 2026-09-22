import { useState } from "react";

import type { ActionButton } from "../services/api";
import MaterialIcon from "./MaterialIcon";

interface SkillActionsEditorProps {
  actions: ActionButton[];
  onChange: (actions: ActionButton[]) => void;
}

function createAction(type: ActionButton["action"], actions: ActionButton[]): ActionButton {
  if (type === "tel") return { action: "tel", label: "", phone_number: "" };
  if (type === "url") return { action: "url", label: "", url: "" };
  const existingIds = new Set(actions.flatMap((action) => action.action === "options" ? [action.id] : []));
  // IDs identify actions across all Skills, not only within this editor.
  let id = `choice_${crypto.randomUUID()}`;
  while (existingIds.has(id)) id = `choice_${crypto.randomUUID()}`;
  return {
    action: "options",
    id,
    label: "",
    title: "",
    options: [{ label: "", value: "" }, { label: "", value: "" }],
  };
}

const actionNames: Record<ActionButton["action"], string> = {
  tel: "撥打電話",
  url: "開啟網頁",
  options: "選項問答",
};

export default function SkillActionsEditor({ actions, onChange }: SkillActionsEditorProps) {
  const [newActionType, setNewActionType] = useState<ActionButton["action"]>("tel");
  const replaceAction = (index: number, next: ActionButton) => {
    onChange(actions.map((action, actionIndex) => actionIndex === index ? next : action));
  };

  return (
    <section className="space-y-3 border-t border-outline/10 pt-5" aria-label="通用 Actions">
      <div>
        <h4 className="font-bold">通用 Actions</h4>
        <p className="mt-1 text-xs leading-5 text-on-surface/55">
          設定可重複使用的電話、網頁或選項按鈕；在情境指令中教 Agent 何時使用。使用者先選擇選項或填寫「其他」，再按「送出回覆」。
        </p>
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <label className="min-w-0 flex-1 text-xs font-semibold text-on-surface/60">
          新增按鈕類型
          <select value={newActionType} onChange={(event) => setNewActionType(event.target.value as ActionButton["action"])} className="input">
            {Object.entries(actionNames).map(([type, name]) => <option key={type} value={type}>{name}</option>)}
          </select>
        </label>
        <button type="button" onClick={() => onChange([...actions, createAction(newActionType, actions)])} className="rounded-lg border border-secondary/25 px-3 py-2 text-xs font-bold text-secondary">
          新增按鈕
        </button>
      </div>
      {actions.map((action, index) => (
        <fieldset key={index} className="min-w-0 space-y-3 rounded-lg border border-outline/15 p-3">
          <legend className="px-1 text-xs font-bold text-on-surface/65">按鈕 {index + 1} · {actionNames[action.action]}</legend>
          <div className="flex items-end gap-2">
            <label className="min-w-0 flex-1 text-xs font-semibold text-on-surface/60">
              按鈕文字
              <input value={action.label} maxLength={80} onChange={(event) => replaceAction(index, { ...action, label: event.target.value })} className="input" />
            </label>
            <button type="button" onClick={() => onChange(actions.filter((_, actionIndex) => actionIndex !== index))} className="rounded-lg p-2 text-error hover:bg-error-container/40" aria-label={`移除按鈕 ${index + 1}`}>
              <MaterialIcon icon="delete" size={18} />
            </button>
          </div>
          {action.action === "tel" && (
            <label className="block text-xs font-semibold text-on-surface/60">
              電話號碼
              <input type="tel" value={action.phone_number} onChange={(event) => replaceAction(index, { ...action, phone_number: event.target.value })} className="input" />
            </label>
          )}
          {action.action === "url" && (
            <label className="block text-xs font-semibold text-on-surface/60">
              網頁網址
              <input type="url" value={action.url} onChange={(event) => replaceAction(index, { ...action, url: event.target.value })} placeholder="https://…" className="input" />
            </label>
          )}
          {action.action === "options" && (
            <>
              <label className="block text-xs font-semibold text-on-surface/60">
                選項 ID
                <input value={action.id} maxLength={64} pattern="[a-z][a-z0-9_-]{1,63}" onChange={(event) => replaceAction(index, { ...action, id: event.target.value })} className="input" />
                <span className="mt-1 block font-normal">以小寫英文字母開頭，使用 2–64 個小寫字母、數字、底線或連字號。</span>
              </label>
              <label className="block text-xs font-semibold text-on-surface/60">
                問題標題
                <input value={action.title} maxLength={160} onChange={(event) => replaceAction(index, { ...action, title: event.target.value })} className="input" />
              </label>
              {action.options.map((option, optionIndex) => (
                <fieldset key={optionIndex} className="min-w-0 space-y-2 rounded-lg bg-surface-container-low p-3">
                  <legend className="text-xs font-semibold text-on-surface/60">選項 {optionIndex + 1}</legend>
                  <label className="block text-xs font-semibold text-on-surface/60">
                    選項文字
                    <input value={option.label} maxLength={80} onChange={(event) => replaceAction(index, { ...action, options: action.options.map((item, itemIndex) => itemIndex === optionIndex ? { ...item, label: event.target.value } : item) })} className="input" />
                  </label>
                  <label className="block text-xs font-semibold text-on-surface/60">
                    送出的回覆內容
                    <textarea value={option.value} maxLength={500} rows={2} onChange={(event) => replaceAction(index, { ...action, options: action.options.map((item, itemIndex) => itemIndex === optionIndex ? { ...item, value: event.target.value } : item) })} className="input resize-y" />
                  </label>
                  <button type="button" disabled={action.options.length <= 2} onClick={() => replaceAction(index, { ...action, options: action.options.filter((_, itemIndex) => itemIndex !== optionIndex) })} className="text-xs font-semibold text-error disabled:opacity-40" aria-label={`移除按鈕 ${index + 1} 的選項 ${optionIndex + 1}`}>
                    移除選項
                  </button>
                </fieldset>
              ))}
              <button type="button" disabled={action.options.length >= 8} onClick={() => replaceAction(index, { ...action, options: [...action.options, { label: "", value: "" }] })} className="rounded-lg border border-secondary/25 px-3 py-2 text-xs font-bold text-secondary disabled:opacity-40">
                新增選項
              </button>
              <p className="text-xs text-on-surface/55">每組問題包含 2–8 個選項；使用者也可填寫「其他」。</p>
            </>
          )}
        </fieldset>
      ))}
      {actions.length === 0 && <p className="text-xs text-on-surface/55">此 Skill 尚未設定按鈕。</p>}
    </section>
  );
}
