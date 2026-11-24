# 交接文件

給接手團隊的第一份文件：系統現在處於什麼狀態、要移交哪些權限與資料、有哪些已知限制，以及哪些事情還在等決定。安裝與日常操作見 [README](../README.md) 與 [操作手冊](OPERATIONS.md)，架構細節見 [架構說明](MIGRATION_REPORT.md)。

撰寫日期：2026-11-24。文中「目前」皆指此日期；雲端狀態以實際部署紀錄為準。

## 1. 目前狀態

| 項目 | 狀態 |
| --- | --- |
| Production | **尚未發布拆分後的新版。** 正式站仍是舊版；切換步驟見操作手冊〈發布與背景工作〉。 |
| Preview | 兩個後端與 Admin 前端已部署過拆分後的版本（2026-11-10 驗證）。本次重構（`refactor/handover` 分支）**尚未部署**。 |
| 聊天契約 | 後端與前端都只接受 v4（`contract_version: 4`）；v1–v3 回 422。 |
| 圖片上傳 | 停用。需同時打開程式常數 `IMAGE_UPLOAD_ROLLOUT_ENABLED`（`shared/python/rag_core/core/runtime_config.py`）與 runtime 設定才會開放；前端驗證程式保留。 |
| Gate 內容審查 | 程式完成，預設關閉。實際 Gate 模型的誤擋／漏放／延遲尚未以代表性案例驗證。 |
| 瀏覽器 PII 模型（Tiny NER） | 只在 Admin Preview／開發環境的驗證頁，未接入 Client 聊天。未達品質門檻（見第 4 節）。 |

### 程式碼與分支

- 本次交接整理位於 `refactor/handover`（自 `dev` 分出）。完成後應合併回 `dev`，再由 `dev` 開 PR 到 `main`。
- 整理內容：移除 v1–v3 契約與死碼、拆分過大的後端與前端模組、共用聊天 UI（`@rag/shared/chat-ui`）、統一錯誤格式、固定工具版本、補測試與文件。每個 commit 訊息都說明了動機與行為差異。
- 驗證基準（2026-11-24，本機）：pytest 1399、shared 273、client 166、admin 265 全部通過；ruff、eslint、前端建置、Functions 封裝檢查與 Admin bundle 邊界檢查通過。

## 2. 權限與帳號移交清單

接手前由現任負責人逐項移交或重新授權。**不要以複製本機檔案的方式移交機密。**

| 項目 | 說明 |
| --- | --- |
| GitHub repository | `TWCkaijin/Anti-Harassment-Bot`（**公開**）。移交 admin 權限或轉移 repository；確認 branch protection。 |
| Firebase／GCP project | `anti-harassment-bot`（設定於根目錄 `project.env`；換專案步驟見操作手冊〈專案 ID〉）。授予接手者 Owner 或必要的最小角色；核對四個 runtime 服務帳號（`rag-client-prod/dev`、`rag-admin-prod/dev`）、兩個 Builder 帳號與 `rag-tasks-prod/dev`。 |
| CI 身分（WIF） | GitHub Variables `WORKLOAD_IDENTITY_PROVIDER`、`DEPLOY_SERVICE_ACCOUNT`。WIF 條件綁定 repository ID；**轉移 repository 後 ID 不變，但更換 repository 必須重建 WIF**。見 [CI 權限審查](../infrastructure/iam/CI_DEPLOYMENT_REVIEW.md)。 |
| CI 長效 token | 無。三個部署 workflow 都改用 WIF（main-preview 於 2026-11-24 切換，GCP 條件已更新）。GitHub 上殘留的 `FIREBASE_TOKEN`、`ADMIN_API_KEY`、`AGENT_SYSTEM_PROMPT`、`FIREBASE_SERVICE_ACCOUNT_JSON`、`OPENROUTER_API_KEY` 已無 workflow 使用，待 main-preview 在 main 上執行成功後刪除（見第 3 節）。 |
| OpenRouter | 帳號與帳單移交；金鑰存在 Secret Manager。 |
| 管理密碼 | Production 與 Preview 各一組（Argon2id hash 在私有庫）。交接時用 `password_cli --reset` 重設，舊 sessions 會失效。見操作手冊〈管理密碼與登入〉。 |
| reCAPTCHA Enterprise／App Check | 若啟用，移交 site key 與 Firebase App 註冊；GitHub Variables 見下表。 |

### Secret Manager（只列名稱）

| Secret | 用途 |
| --- | --- |
| `RAG_OPENROUTER_PROD`／`RAG_OPENROUTER_DEV` | 模型與 embedding 的 OpenRouter 金鑰 |
| `RAG_CLARIFICATION_PROD`／`RAG_CLARIFICATION_DEV` | 澄清問題簽章金鑰 |
| `RAG_CLARIFICATION_LEGACY_PROD`／`RAG_CLARIFICATION_LEGACY_DEV` | 舊簽章相容金鑰，**已於 2026-10-11 到期**（見第 5 節） |

### GitHub Variables

`FIREBASE_API_KEY`、`WORKLOAD_IDENTITY_PROVIDER`、`DEPLOY_SERVICE_ACCOUNT`、`CLIENT_FIREBASE_APP_ID_DEV/PROD`、`CLIENT_APP_CHECK_ENABLED_DEV/PROD`、`CLIENT_APP_CHECK_SITE_KEY_DEV/PROD`、`CLIENT_CHAT_APP_CHECK_ENABLED_DEV/PROD`。

`FIREBASE_PROJECT_ID` variable 已不被 workflow 使用（改讀 `project.env`），可一併刪除。

### 需要另外移交的資料

`data/`（知識庫來源）與 `resource/`（研究工作簿、官方法源快照）**不在 Git 中**，只存在原開發機。請以加密方式另行移交，並核對 `data/README.md` 與 `resource/legal/README.md`。雲端 collections 的內容不等於這兩個目錄的快照。

## 3. 現任負責人在交接前要做的事

這些動作需要本人的帳號或判斷，程式無法代勞：

1. **撤銷本機服務帳號金鑰** `firebase_admin.json`（未提交 Git，但存在原開發機）。在 GCP IAM 刪除該金鑰後刪除檔案；CI 已改用 WIF，不需要它。
2. **重設 Preview 與 Production 管理密碼**，以安全管道交給接手者。
3. **清理原開發機的本機檔案**：根目錄舊 `.env`（含已廢除的 `ADMIN_API_KEY`）、`.code-review-graph/`、`.admin-local/dev-runs/` 舊紀錄。
4. **評估 Git 歷史中的工作簿。** repository 是公開的，而歷史中有 `resource/chatgpt_scene.xlsx` 與 `resource/gemini_scene.xlsx`（2026-09-21 加入，2026-10-10 移除）。若內容不宜公開，需以 `git filter-repo` 改寫歷史並強制推送，且所有 clone 都要重建；這是不可逆操作，需負責人決定。
5. 撤銷 `FIREBASE_TOKEN` 或確認由接手者重新產生。

## 4. 已知限制

| 範圍 | 限制 |
| --- | --- |
| PII 遮罩 | Client 只用規則遮罩，可能漏掉自由文字中的人名、單位或事件細節。Tiny NER 在 120 則合成集召回率 31.5%（有效輸出 41/120），16 則診斷集 10.3%，P95 延遲 11–22 秒，未達門檻；16 則標註仍待獨立人工審定。 |
| Tiny NER 授權 | GPL-3.0。權重不在 repository，Preview 建置預設不打包（需 `MASKING_TINY_BUNDLE=1`）。若要用於使用者功能，需先確認 GPL-3.0 可接受或改用寬鬆授權模型。見 [第三方授權](../THIRD_PARTY_NOTICES.md)。 |
| 法規分類評估 | 工作簿只有一輪部分重播（15 案完成 7 案、43 輪完成 17 輪），早於最新後端修正。不能據此宣稱真實案例準確率。 |
| 檢索資料 | Preview `app-dev` 有 13 篇已核對官方來源的法條摘要；`data/documents` 的舊 seed 部分缺少來源與版本 metadata，匯入前需核對。PDF 匯入不含 OCR。 |
| 模型品質 | 回歸測試使用替身，不證明模型回答品質或法律正確性。 |
| 前端 | `useConversation`、FollowUpPanel、PrivacyReviewDialog、useImageAttachment 仍有寫死的中文（英文介面下顯示中文）；設定面板中「分享使用統計」段落未翻譯；設定面板按 Escape 不會關閉。 |
| 程式結構 | `AdminBatchPrediction.tsx`（約 1000 行）、`useConversation.ts`（約 780 行）、`guided_v4.py` 的 `run_guided_v4`、`corpus/service.py` 仍偏大。它們是共用大量狀態的單一單元，硬拆會增加間接層；建議在需要修改該邏輯時一併重新設計，並先補畫面測試。 |
| 本機開發 | 本機服務連雲端 dev 資料，會影響線上 Preview 並產生模型費用；沒有完整離線模式。 |
| 技術報告 | `docs/technical-report/` 的手冊是 2026-10-10 的閱讀版快照，部分隱私敘述已過時；操作以 OPERATIONS.md 為準。 |

## 5. 待決事項

| 事項 | 現況 | 建議 |
| --- | --- | --- |
| 移除已到期的舊澄清簽章金鑰 | `CLARIFICATION_LEGACY_VALID_UNTIL` = 2026-10-11（`scripts/deploy.py`）；Functions 仍宣告 `RAG_CLARIFICATION_LEGACY_*` secrets | 從 `client/backend/main.py`、`admin/backend/main.py` 移除 legacy secrets 與 deploy 值，部署後刪除 Secret Manager 中的兩個 secret。屬部署變更，需安排在一次正式發布中。 |
| `clarification_tokens` 的 v3 驗證路徑 | v1–v3 退役後已無呼叫者，但與金鑰輪替測試相連 | 與上一項一起移除，單獨一個小 PR。 |
| `output_format` Prompt 段落 | 只用於已退役的 v1；Admin Prompt 編輯器仍顯示但不生效 | 移除欄位與編輯器項目；需處理 Firestore 中已存的覆寫值。 |
| Production 切換 | 新版只在 Preview | 依操作手冊先完成 Preview 四單元驗收、IAM 檢查、備份與回復演練。 |
| Gate 與 App Check 啟用 | 都預設關閉 | 先在 Preview 以代表性案例量測，再決定。 |
| Tiny NER 去留 | 未達門檻、GPL-3.0 | 決定繼續調整、換模型或移除驗證頁。 |
| 區域與資料庫名稱集中化 | 專案 ID 已集中在 `project.env`；區域 `asia-east1` 與資料庫名稱仍散在程式、workflow 與設定中 | 若要搬到其他區域或專案再比照 `project.env` 處理。 |

## 6. 接手驗收建議

由未參與開發的人，從乾淨 clone 依 README 啟動 Client 與 Admin，並確認：

1. `uv run --frozen python scripts/dev.py --check` 通過，四個服務能啟動。
2. Client 顯示「已連線」，`/api/v1/health/` 回報 `chat_contract_versions: [4]`。
3. 以 Preview 管理密碼登入 Admin，能讀取設定、Prompt 與 Skills。
4. README〈檢查〉的指令全部通過。
5. 讀過本文件第 3–5 節，並在交接會議中逐項確認權限移交與待決事項的負責人。
