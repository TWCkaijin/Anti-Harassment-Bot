# 溫暖守護：首頁識別

這次調整保留既有橘色 `#d15b00`、暖白背景 `#fff9f6`、側邊欄、頁首與中央對話輸入版面。

## 標記

`public/brand-mark.svg` 是網站與頁籤共用的向量原稿。圓角對話框代表願意傾聽，下方環抱弧線代表陪伴；保留清楚的留白，避免機器人、閃光或官方徽章的意象。標記只使用既有橘色與暖白色。

- 頁籤直接引用原稿，不再使用 Vite 圖示。
- `BrandMark` 在桌面側邊欄使用 48 px，在手機頁首使用 32 px。
- 相鄰文字已提供品牌名稱，裝飾圖示使用空白替代文字，避免螢幕閱讀器重複朗讀。
- 功能圖示透過 `MaterialIcon` 相容元件映射到本地 Lucide SVG，不需下載圖示字型。

## 文案

統一使用「溫暖守護」，功能說明為「性騷擾協助與資訊」。移除「AI 已就緒」、Agent 與保證即時專業服務的宣傳字樣；開場改為邀請使用者說明情況。保留低調但可見的 AI 生成及資訊外送說明，避免讓簡潔文案造成真人服務或完全匿名的誤解。

中英文同步維護於 `src/i18n/locales`，切換語系會同步頁面標題與文件語言。電話求助、輸入、上傳、設定與對話管理的原有功能維持可用。

## 本機檢查

使用 `tests/browser_fixture_server.py` 與本機 Vite，設定 `VITE_API_BASE_URL=http://127.0.0.1:5055/api`、`VITE_ANALYTICS_ENABLED=false`。執行 `node tests/browser_homepage_smoke.mjs --stage after`；可用 `PLAYWRIGHT_MODULE` 指定既有安裝。瀏覽器檢查封鎖非本機請求，確認圖示不依賴外部字型，並輸出桌面、手機與窄螢幕的中英文畫面。測試不代表正式部署、真人諮詢或法律正確性驗證。



歷史設計預覽與瀏覽器截圖已外部封存；正式品牌素材仍位於 frontend/public 與 frontend/src/assets。驗收以 docs/DELIVERY.md 為準。
