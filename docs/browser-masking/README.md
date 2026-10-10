# 瀏覽器遮罩模型驗證

這是正式改造前的獨立模型驗證工具。模型抽取在瀏覽器執行，不呼叫聊天 API 或 OpenRouter；手動開啟頁面仍須通過 Admin 登入，驗證 session 會使用管理後端及其 Firestore。下方自動化模式以合成 session 攔截登入驗證，才不需要管理後端或 Firestore。正式聊天仍使用既有流程，只有驗收通過後才開始整合與移除舊 PII 程式。

2026-10-09 的歷史實測未通過：Qwen3-0.6B／zh-span-extraction-v2 在 120 則合成案例中回傳有效 JSON，但全部預測皆為空集合，165 個預期敏感片段命中 0 個；召回率 0%，精確率不適用。105 則含敏感片段的案例失敗，15 則無敏感片段案例符合預期。此結論來自當時的 Apple M3／Chrome WebGPU 實測，本次架構重構未重跑模型，沒有宣稱其他裝置相同。

例如「我是林沐青」應辨識合成姓名，「masking.alpha@example.com」應辨識電子郵件，該輪均未抽出。冷載入約 107 秒、下載約 336 MiB，快取載入約 0.857 秒；暖機 P95 約 0.786 秒只反映空輸出速度，不能推估正確抽取效能。保留可重跑的程式、固定版本、合成資料與這份失敗摘要，不保留重複的逐筆留存報告。

正式聊天持續使用既有規則遮罩；小模型、持久化對照表及舊 PII 替換尚未進入正式流程。未來如果依該輪結果調整模型或提示，原 40 則保留驗證集應視為已使用資料，另設新保留集。

## 執行驗證頁

```sh
pnpm install --frozen-lockfile
pnpm --filter @rag/admin build --mode preview
pnpm --filter @rag/admin exec vite preview --host 127.0.0.1 --port 5198 --strictPort
```

手動使用前，先依 [操作手冊](../OPERATIONS.md) 啟動本機 Admin API（`127.0.0.1:8001`），使用 development 環境、`ADMIN_COOKIE_SECURE=false` 與已初始化的 dev 管理密碼。啟動 API 的終端須將 `ADMIN_ORIGINS` 設為包含 `http://127.0.0.1:5198` 的 JSON 陣列；Vite preview 會沿用 `/api` proxy。此本機後端仍會連線 dev Firestore，並不是純離線模式。

開啟 `http://127.0.0.1:5198/masking-validation.html` 並登入，依序載入模型、重新載入量測快取、執行開發集及保留驗證集，最後下載 JSON 報告。首次載入需要下載公開模型資源；此頁只提供內建合成案例，不讀取使用者對話、Excel 或本機對照表。該頁只包含在 preview 建置，production 建置不提供。

採用固定建置頁量測，避免開發伺服器熱更新中斷 GPU 工作。停止會終止 Worker；再執行前需重新載入模型。不支援 WebGPU、載入失敗或檢查失败時不產生可送出的內容，也沒有雲端原文 fallback。

## 固定版本與資料

- 執行套件：`@mlc-ai/web-llm@0.2.85`。
- 模型：`Qwen3-0.6B-q4f16_1-MLC`，revision `8c14ce481d4c692769976ad52afea453a102df19`。
- 模型執行檔：`binary-mlc-llm-libs` revision `025bcaf3780fa8254f5e5efd3bfea0a5397248f4`，`v0_2_84/base/Qwen3-0.6B-q4f16_1_cs1k-webgpu.wasm`。
- 提示版本：`zh-span-extraction-v2`；temperature 0、seed 0、關閉思考、JSON schema、context 4096。四則固定示例不屬於兩組評估資料。
- 合成資料：120 則繁體中文案例，80 則開發集、40 則保留驗證集。姓名、單位、地址、聯絡方式、識別號、多人物、無個資、提示注入各 15 則；每類最後 5 則為保留驗證集。
- 名稱與數字均為人工合成，沒有讀取專案 `resource/`、正式對話或使用者資料。案例包含模板變化；保留驗證集不等同獨立真實世界資料集。

模型只接收案例文字與固定任務說明，不接收預期答案。模型輸出需逐字存在於來源；虛構片段、截斷或無效 JSON 都算檢查失敗。只移除 WebLLM 關閉 Qwen3 思考時注入的固定空白思考標記，不從任意文字中擷取看似有效的 JSON。

## 評分與切換門檻

- 以每案例不重複的完整敏感片段作精確匹配；部分匹配同時計入誤遮與漏遮，類型差異保留於報告供檢查。
- 若完全沒有預測片段，精確率記為 `null`（不適用），不能當成 100% 或通過門檻。
- 每一組都需精確率、召回率至少 95%，有效輸出 100%。
- 暖機後 200–500 字案例的第 95 百分位處理時間不超過 5 秒；失敗案例也納入其實際延遲，不排除慢的失敗結果。
- 必須完成全部 80 + 40 則才能通過。中斷、逾時或未執行案例不會被當成成功。
- 使用 IndexedDB 快取，以便所有模型下載都經過 Worker 的固定資源白名單。下載量是 `fetch` 回應解壓後內容位元組，不含 HTTP/TLS 開銷；快取命中不會發出 `fetch`。載入時間與下載內容量分開記錄。
- 任一門檻不符，交付結果及失敗案例，暫不替換正式聊天；不以放寬分數或雲端 fallback 補足。

## 自動化重現

先啟動上方 preview server，再執行。此模式在瀏覽器測試內攔截 session 查詢並回傳合成登入狀態，不必啟動 Admin API、不使用真實密碼，也不修改正式驗證機制：

```sh
BROWSER_BASE_URL=http://127.0.0.1:5198 \
BROWSER_ARTIFACTS=/tmp/rag-browser-masking-validation \
PLAYWRIGHT_MODULE=/absolute/path/to/playwright \
node admin/tests/browser_masking_validation.mjs
```

這是本機合成驗證用的自動化測試，不是對話或批次預測命令列入口。它使用獨立 Chrome 設定檔，攔截網路只允許讀取本機頁面與公開模型資源，保存裝置資訊、載入量測、逐案例結果、網路紀錄及畫面。設定檔若已包含快取，第一次載入不能當成冷啟動；使用新的輸出目錄重新量測。

測試結果只代表記錄的裝置與瀏覽器。Chrome User-Agent 可能固定顯示 Intel/macOS 10.15，實際主機架構、CPU 與系統版本需以 `capabilities.json` 的 host 欄位及執行環境紀錄為準。Edge 與手機需另外實測，不由 Chrome 結果推定。
