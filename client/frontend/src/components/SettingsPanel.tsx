import { hasAnalyticsConsent, isAnalyticsConfigured, setAnalyticsConsent } from "../services/analytics";
/**
 * SettingsPanel — 設定面板（側拉抽屜）
 * 語言切換、色彩組合（暫時佔位）、本地紀錄管理、匯出對話。
 */
import { useState } from "react";
import { MaterialIcon } from "@rag/shared/chat-ui";
import { useI18n, type Locale } from "@rag/shared/chat-ui";
import type { ConversationSession } from "../hooks/useConversation";
import { formatConversationText } from "../services/conversationExport";
import { TEXT_SIZES, type TextSize } from "../hooks/useTextSizePreference";
import { downloadBlob } from "@rag/shared/browser";

interface SettingsPanelProps {
  isOpen: boolean;
  onClose: () => void;
  sessions: ConversationSession[];
  onClearAll: () => void;
  storageChanged?: boolean;
  textSize?: TextSize;
  onTextSizeChange?: (size: TextSize) => void;
  textSizeSaveFailed?: boolean;
  showEmotions?: boolean;
  onShowEmotionsChange?: (value: boolean) => void;
  preferenceSaveFailed?: boolean;
  confirmPrivacyBeforeSending?: boolean;
  onConfirmPrivacyChange?: (enabled: boolean) => void;
  privacyReviewSaveFailed?: boolean;
}

export default function SettingsPanel({
  isOpen,
  onClose,
  sessions,
  onClearAll,
  storageChanged = false,
  textSize = "medium", onTextSizeChange, textSizeSaveFailed = false,
  showEmotions = false, onShowEmotionsChange, preferenceSaveFailed = false,
  confirmPrivacyBeforeSending = false, onConfirmPrivacyChange, privacyReviewSaveFailed = false,
}: SettingsPanelProps) {
  const { t, locale, setLocale } = useI18n();
  const [analyticsEnabled, setAnalyticsEnabled] = useState(hasAnalyticsConsent);
  const [showConfirm, setShowConfirm] = useState(false);
  const textSizeLabels = { small: t.textSizeSmall, medium: t.textSizeMedium, large: t.textSizeLarge };

  // ── 匯出為 JSON ──
  const exportAsJson = () => {
    const data = JSON.stringify(sessions, null, 2);
    downloadBlob(data, "conversations.json", "application/json");
  };

  // ── 匯出為純文字 ──
  const exportAsTxt = () => {
    let text = "";
    for (const session of sessions) {
      if (session.messages.length === 0 && Object.keys(session.caseFacts?.facts ?? {}).length === 0) continue;
      text += `=== 對話 ${new Date(session.createdAt).toLocaleString()} ===\n\n`;
      text += formatConversationText(session, t);
      text += "\n---\n\n";
    }
    downloadBlob(text, "conversations.txt", "text/plain");
  };

  const handleClearAll = () => {
    if (storageChanged) return;
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
            aria-label={t.close}
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

          {onTextSizeChange && <fieldset className="min-w-0">
            <legend className="mb-3 flex items-center gap-2 text-sm font-semibold text-on-surface">
              <MaterialIcon icon="text_size" size={18} />
              {t.settingTextSize}
            </legend>
            <div className="grid grid-cols-3 gap-1 rounded-xl border border-outline/30 bg-surface-container-low p-1">
              {TEXT_SIZES.map(size => (
                <label key={size} className="relative min-w-0">
                  <input
                    className="peer sr-only"
                    type="radio"
                    name="chat-text-size"
                    checked={textSize === size}
                    onChange={() => onTextSizeChange(size)}
                  />
                  <span className="flex min-h-[44px] cursor-pointer items-center justify-center rounded-lg border border-transparent px-1 text-center text-sm text-on-surface hover:bg-surface-container peer-checked:border-primary/30 peer-checked:bg-primary-container peer-checked:font-semibold peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-primary">
                    {textSizeLabels[size]}
                  </span>
                </label>
              ))}
            </div>
            {textSizeSaveFailed && <p role="alert" className="mt-2 text-xs text-error">{t.preferenceSaveFailed}</p>}
          </fieldset>}

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

          {onShowEmotionsChange && <section>
            <label className="flex items-center gap-3 text-sm font-semibold text-on-surface">
              <input type="checkbox" checked={showEmotions} onChange={event => onShowEmotionsChange(event.target.checked)} />{t.showEmotionLabels}
            </label>
            <p className="mt-2 text-xs leading-relaxed text-on-surface-variant">{t.emotionLabelsNote}</p>
            {preferenceSaveFailed && <p role="alert" className="mt-2 text-xs text-error">{t.preferenceSaveFailed}</p>}
          </section>}

          {onConfirmPrivacyChange && <section>
            <label className="flex min-h-[44px] cursor-pointer items-center gap-3 text-sm font-semibold text-on-surface">
              <input
                type="checkbox"
                checked={confirmPrivacyBeforeSending}
                onChange={event => onConfirmPrivacyChange(event.target.checked)}
                aria-describedby="privacy-confirmation-note"
                className="size-4 shrink-0 accent-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
              />
              {t.confirmPrivacyBeforeSending}
            </label>
            <p id="privacy-confirmation-note" className="text-xs leading-relaxed text-on-surface-variant">{t.privacyConfirmationNote}</p>
            {privacyReviewSaveFailed && <p role="alert" className="mt-2 text-xs text-error">{t.preferenceSaveFailed}</p>}
          </section>}

          {isAnalyticsConfigured() && <section>
            <label className="flex items-center gap-3 text-sm font-semibold text-on-surface">
              <input type="checkbox" checked={analyticsEnabled} onChange={event => {
                setAnalyticsConsent(event.target.checked);
                setAnalyticsEnabled(hasAnalyticsConsent());
              }} />
              分享使用統計（選用）
            </label>
            <p className="mt-2 text-xs text-on-surface-variant leading-relaxed">
              開啟後使用 Google Analytics（含分析 Cookie）記錄回覆耗時、錯誤次數及按鈕使用類型；不傳送對話、圖片、情緒或聯絡資訊。可隨時關閉，關閉不會刪除已送出的統計。
            </p>
          </section>}

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
                disabled={storageChanged}
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
                    disabled={storageChanged}
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
