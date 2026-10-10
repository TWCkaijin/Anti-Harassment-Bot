# Admin 前端

獨立管理介面包含系統設定、Prompt、Skills、診斷測試、批次預測、對話行為測試及檢索資料庫。使用 Node.js 24、pnpm 10.27.0；從專案根目錄執行：

```bash
pnpm install --frozen-lockfile
pnpm --filter @rag/admin dev
pnpm --filter @rag/admin test
pnpm --filter @rag/admin lint
pnpm --filter @rag/admin build
```

本機前端為 `http://127.0.0.1:5174`，`/api` 由 Vite 代理至 `http://127.0.0.1:8001`。管理 API 固定使用同源 `/api/v1/admin`；HttpOnly cookie 保存 session，修改請求加上記憶體中的 CSRF token，管理密碼及 token 不寫入 localStorage。

測試、來源檔案解析、確認後的資料匯入及刪除均建立背景工作，前端輪詢工作狀態。JSON／JSONL／CSV／XLSX 先在背景掃描欄位，管理者明確對應內容與來源資訊欄位後，再排隊產生完整預覽。檔案解析與欄位對應不執行 embedding；確認預覽後才建立匯入工作。

伺服器的匯入草稿與工作紀錄支援重新載入、重新登入後恢復；切換管理分頁也保留目前欄位選擇。工作紀錄提供分頁、進度、取消與明確重試；未知外部計費結果的工作需先勾選重複費用確認。既有資料若無可驗證完整來源，僅列出完整片段，不推測來源分組。

正式 build 只包含管理入口。Preview 使用 `pnpm --filter @rag/admin exec vite build --mode preview`，另包含 `/masking-validation.html` 本機遮罩驗證頁；該頁同樣需要管理登入。

合成瀏覽器檢查位於 `admin/tests/browser_*_smoke.mjs`。啟動 Vite 後，設定 `PLAYWRIGHT_MODULE` 指向已有 Playwright 安裝並以 Node.js 執行；所有 API 由合成回應取代，並封鎖非本機請求。這些檢查不代表已部署、真實模型品質或法律正確性。

目前已完成 Admin Preview 的桌面／手機真實登入與瀏覽驗證，包含七個分頁、`preview`／`app_dev` 環境識別、HttpOnly Cookie 與登出後 401；未由此驗證資料寫入、模型品質或正式切換。完整證據邊界與待辦見 [遷移報告](../../docs/MIGRATION_REPORT.md)，部署與日常維運見 [維運說明](../../docs/OPERATIONS.md)。
