/**
 * SettingsPanel — 設定面板（側拉抽屜）
 * 語言切換、色彩組合（暫時佔位）、本地紀錄管理、匯出對話。
 */
import { useState } from "react";
import MaterialIcon from "./MaterialIcon";
import { useI18n, type Locale } from "../i18n";
import type { ConversationSession } from "../hooks/useConversation";

interface SettingsPanelProps {
  isOpen: boolean;
  onClose: () => void;
  sessions: ConversationSession[];
  onClearAll: () => void;
}

export default function SettingsPanel({
  isOpen,
  onClose,
  sessions,
  onClearAll,
}: SettingsPanelProps) {
  const { t, locale, setLocale } = useI18n();
  const [showConfirm, setShowConfirm] = useState(false);

  // ── 匯出為 JSON ──
  const exportAsJson = () => {
    const data = JSON.stringify(sessions, null, 2);
    downloadFile(data, "conversations.json", "application/json");
  };

  // ── 匯出為純文字 ──
  const exportAsTxt = () => {
    let text = "";
    for (const session of sessions) {
      if (session.messages.length === 0) continue;
      text += `=== 對話 ${new Date(session.createdAt).toLocaleString()} ===\n\n`;
      for (const msg of session.messages) {
        const role = msg.role === "user" ? "使用者" : "AI";
        text += `[${role}] ${msg.content}\n\n`;
      }
      text += "\n---\n\n";
    }
    downloadFile(text, "conversations.txt", "text/plain");
  };

  const downloadFile = (content: string, filename: string, mimeType: string) => {
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const handleClearAll = () => {
    onClearAll();
    setShowConfirm(false);
  };

  if (!isOpen) return null;

  return (
    <>
      {/* 遮罩 */}
      <div
        className="fixed inset-0 bg-black/30 z-[60]"
        onClick={onClose}
      />

      {/* 設定面板 */}
      <div className="fixed inset-y-0 right-0 z-[70] w-80 max-w-full bg-white border-l border-outline/20 flex flex-col animate-fade-in-up">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-outline-variant/20">
          <h2 className="text-lg font-semibold text-on-surface flex items-center gap-2">
            <MaterialIcon icon="settings" size={20} />
            {t.settingsTitle}
          </h2>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-surface-container-high transition-colors cursor-pointer"
          >
            <MaterialIcon icon="close" size={20} className="text-on-surface-variant" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-6">
          {/* ── 語言 ── */}
          <section>
            <h3 className="text-sm font-semibold text-on-surface mb-3 flex items-center gap-2">
              <MaterialIcon icon="translate" size={18} />
              {t.settingLanguage}
            </h3>
            <div className="flex gap-2">
              {(["zh-TW", "en"] as Locale[]).map((loc) => (
                <button
                  key={loc}
                  onClick={() => setLocale(loc)}
                  className={`
                    flex-1 py-2.5 px-3 rounded-xl text-sm font-medium transition-all cursor-pointer
                    ${
                      locale === loc
                        ? "bg-primary text-on-primary"
                        : "bg-surface-container hover:bg-surface-container-high text-on-surface"
                    }
                  `}
                >
                  {loc === "zh-TW" ? t.langZhTW : t.langEn}
                </button>
              ))}
            </div>
          </section>

          {/* ── 色彩組合 ── */}
          <section>
            <h3 className="text-sm font-semibold text-on-surface mb-3 flex items-center gap-2">
              <MaterialIcon icon="palette" size={18} />
              {t.settingTheme}
            </h3>
            <div className="flex gap-2">
              <button className="flex-1 py-2.5 px-3 rounded-xl text-sm font-medium bg-primary text-on-primary cursor-pointer">
                {t.themeWarm}
              </button>
              <button className="flex-1 py-2.5 px-3 rounded-xl text-sm font-medium bg-surface-container hover:bg-surface-container-high text-on-surface-variant cursor-pointer opacity-50" disabled>
                {t.themeCool}
              </button>
            </div>
          </section>

          {/* ── 本地紀錄 ── */}
          <section>
            <h3 className="text-sm font-semibold text-on-surface mb-3 flex items-center gap-2">
              <MaterialIcon icon="storage" size={18} />
              {t.settingLocalStorage}
            </h3>
            <p className="text-xs text-on-surface-variant mb-3">
              {sessions.filter(s => s.messages.length > 0).length} 個對話 ·{" "}
              {sessions.reduce((acc, s) => acc + s.messages.length, 0)} 則訊息
            </p>
            {!showConfirm ? (
              <button
                onClick={() => setShowConfirm(true)}
                className="w-full py-2.5 px-3 rounded-xl text-sm font-medium bg-error-container/30 text-error hover:bg-error-container/50 transition-colors cursor-pointer flex items-center justify-center gap-2"
              >
                <MaterialIcon icon="delete_forever" size={16} />
                {t.clearAllData}
              </button>
            ) : (
              <div className="space-y-2">
                <p className="text-xs text-error font-medium">
                  {t.clearAllDataConfirm}
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={() => setShowConfirm(false)}
                    className="flex-1 py-2 px-3 rounded-xl text-sm bg-surface-container hover:bg-surface-container-high transition-colors cursor-pointer"
                  >
                    {t.cancel}
                  </button>
                  <button
                    onClick={handleClearAll}
                    className="flex-1 py-2 px-3 rounded-xl text-sm bg-error text-on-error font-medium cursor-pointer hover:opacity-90"
                  >
                    {t.confirm}
                  </button>
                </div>
              </div>
            )}
          </section>

          {/* ── 匯出對話 ── */}
          <section>
            <h3 className="text-sm font-semibold text-on-surface mb-3 flex items-center gap-2">
              <MaterialIcon icon="download" size={18} />
              {t.settingExport}
            </h3>
            <div className="flex gap-2">
              <button
                onClick={exportAsJson}
                className="flex-1 py-2.5 px-3 rounded-xl text-sm font-medium bg-surface-container hover:bg-surface-container-high transition-colors cursor-pointer flex items-center justify-center gap-1"
              >
                <MaterialIcon icon="data_object" size={14} />
                {t.exportAsJson}
              </button>
              <button
                onClick={exportAsTxt}
                className="flex-1 py-2.5 px-3 rounded-xl text-sm font-medium bg-surface-container hover:bg-surface-container-high transition-colors cursor-pointer flex items-center justify-center gap-1"
              >
                <MaterialIcon icon="description" size={14} />
                {t.exportAsTxt}
              </button>
            </div>
          </section>
        </div>
      </div>
    </>
  );
}
