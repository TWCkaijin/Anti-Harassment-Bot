/**
 * EmergencyFab — 浮動緊急聯絡按鈕 (Stitch 新版對話內頁 v0.1)
 * 固定在右側底部。純圓形圖示，無文字標籤（保留 hover tooltip 增加易用性）。
 * 113 為紅色愛心盾牌，110 為深灰安全盾牌。
 */
import MaterialIcon from "./MaterialIcon";
import { useI18n } from "../i18n";

export default function EmergencyFab() {
  const { t } = useI18n();

  return (
    <div className="fixed right-6 lg:right-8 bottom-32 flex flex-col gap-4 z-50">
      {/* 113 保護專線 */}
      <a className="group relative flex items-center justify-end" href="tel:113">
        <span className="mr-4 px-3 py-1.5 bg-error text-on-error rounded-lg font-bold shadow-lg opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap text-xs">
          {t.emergency113}
        </span>
        <div className="w-12 h-12 lg:w-14 lg:h-14 rounded-full bg-[#ba1a1a] flex items-center justify-center shadow-xl hover:scale-110 transition-transform active:scale-95 cursor-pointer">
          <MaterialIcon icon="favorite" size={24} filled className="text-white" />
        </div>
      </a>

      {/* 110 報案專線 */}
      <a className="group relative flex items-center justify-end" href="tel:110">
        <span className="mr-4 px-3 py-1.5 bg-inverse-surface text-inverse-on-surface rounded-lg font-bold shadow-lg opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap text-xs">
          {t.emergency110}
        </span>
        <div className="w-12 h-12 lg:w-14 lg:h-14 rounded-full bg-[#2b2b2b] flex items-center justify-center shadow-xl hover:scale-110 transition-transform active:scale-95 cursor-pointer">
          <MaterialIcon icon="verified_user" size={24} filled className="text-white" />
        </div>
      </a>
    </div>
  );
}
