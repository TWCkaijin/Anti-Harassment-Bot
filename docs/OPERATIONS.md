# Client／Admin 操作、執行與部署手冊

本手冊對應拆分後程式。實際完成狀態與驗收範圍見 [遷移報告](MIGRATION_REPORT.md)；Preview 網址以部署 JSON 的輸出為準。不要以舊版 PDF 的路徑、固定 Admin token 或部署指令操作新版。

截至 2026-11-10，Preview 已驗證 11 篇有官方來源的法條摘要；Production 尚未發布新版。下列部署命令是操作步驟，不能視為已完成正式切換。

## 環境對照

| 項目 | production | preview／本機開發 |
| --- | --- | --- |
| Firebase project | `anti-harassment-bot` | `anti-harassment-bot` |
| 業務 database | `(default)` | `app-dev` |
| 設定文件 | `runtime_config/app_main` | `runtime_config/app_dev` |
| 私有 database | `admin-private` | `admin-private-dev` |
| Client Function | `api` | `api_preview` |
| Admin Function | `admin_api` | `admin_api_preview` |
| 私有原檔 bucket | `anti-harassment-bot-admin-prod` | `anti-harassment-bot-admin-dev` |
| Cloud Tasks queue | `rag-admin-prod` | `rag-admin-dev` |
| 執行／佇列區域 | `asia-east1` | `asia-east1` |

Firestore 四個 databases 位於 `nam5`。`app-dev` 是 dev 業務庫的實際名稱，因 Firestore database ID 至少需要四個字元。所有資料庫禁止瀏覽器直連；網站僅呼叫 API。四個 Functions 分別使用 `rag-client-prod/dev`、`rag-admin-prod/dev` 服務帳號。

## 本機安裝與啟動

工具：Python 3.13、Node.js 24、pnpm 10.27.0、uv；部署需 Google Cloud CLI 與 Firebase CLI 15.33.0；Rules Emulator 需 Java 21。舊虛擬環境不作為可攜式交付，請依鎖定檔安裝。

部署前用 `command -v firebase` 與 `firebase --version` 核對 PATH 實際選用的 CLI；電腦可能同時存在舊獨立執行檔與 npm 版本。`deploy.py` 會要求版本 15.33.0，避免不同 CLI 的 Python 函式分析行為造成部署失敗。函式分析使用的臨時環境旗標不寫入部署環境；線上服務仍必須取得各環境的模型與澄清簽章 secrets 才能啟動。

```bash
uv sync --frozen --all-extras
pnpm install --frozen-lockfile
```

保留現有 `.env`／`.env.local`。建立 `.admin-local/development.env`，參考 `.env.example` 填入本機模型 key；該目錄已排除 Git。每個後端終端先設定以下變數（路徑由根目錄計算）：

```bash
export PYTHONPATH="$PWD/shared/python:$PWD/client/backend:$PWD/admin/backend"
export RAG_ENV_FILE="$PWD/.admin-local/development.env"
export FLASK_SKIP_DOTENV=1
export ENVIRONMENT=development
export GOOGLE_CLOUD_PROJECT=anti-harassment-bot
export FIRESTORE_DATABASE_ID=app-dev
export RUNTIME_CONFIG_DOCUMENT_ID=app_dev
export ADMIN_PRIVATE_DATABASE_ID=admin-private-dev
export ADMIN_COOKIE_SECURE=false
export ADMIN_ORIGINS='["http://localhost:5174","http://127.0.0.1:5174"]'
export ADMIN_STORAGE_BUCKET=anti-harassment-bot-admin-dev
export ADMIN_TASKS_QUEUE=
export ADMIN_TASKS_TARGET_URL=
export ADMIN_TASKS_SERVICE_ACCOUNT=
export ADMIN_TASKS_AUDIENCE=
```

`FLASK_SKIP_DOTENV=1` 停用 Flask CLI 自動讀取根目錄 `.env`，由共用設定模組依 `RAG_ENV_FILE` 載入本機設定，讓兩個 API 與 Python worker 使用相同來源。終端已匯出的同名變數仍優先，切換設定前請核對。

本機程式可使用 ADC（`gcloud auth application-default login`），或共用 Firestore 工廠支援的本機憑證檔。Storage SDK 使用 ADC；上傳／下載的操作者需有 dev bucket 權限。連線 dev Firestore、Storage 及 OpenRouter 會實際使用雲端資源。本機 HTTP 才可 `ADMIN_COOKIE_SECURE=false`。

五個程序分別在終端執行：

| 程序 | 命令 | 位址／目的 |
| --- | --- | --- |
| Client API | `uv run --frozen flask --app client_app.main:create_app run --host 127.0.0.1 --port 8000` | `/api/v1/health/`、`/api/v1/chat/` |
| Admin API | `uv run --frozen flask --app admin_app.main:create_app run --host 127.0.0.1 --port 8001` | `/api/v1/admin/*` |
| Client 網頁 | `pnpm --filter @rag/client dev` | `http://127.0.0.1:5173` |
| Admin 網頁 | `pnpm --filter @rag/admin dev` | `http://127.0.0.1:5174` |
| Admin worker | `uv run --frozen python -m admin_app.worker_cli --poll-seconds 2` | 執行 development 的診斷／匯入／刪除 jobs |

前端使用 Vite 的同源 `/api` proxy。Client 可透過 `VITE_API_BASE_URL` 指定 API；Admin 必須保持同源 cookie 流程。worker 也可用 `--once --limit 20` 執行一批，Ctrl-C 停止輪詢；它拒絕 production／preview、已部署 runtime 與非空 Cloud Tasks 設定，且不提供免驗證 HTTP 入口。API 與 worker 的環境、資料庫及 bucket 必須相同。

## 管理密碼與登入

目標設定是正式與 Preview 使用同一組登入密碼，但兩個私有庫各自保存 Argon2id hash，Session 仍按環境隔離。首次初始化由具有對應私有庫寫入權限的操作者執行：

```bash
ADMIN_COOKIE_SECURE=true PYTHONPATH=shared/python:admin/backend uv run --frozen python -m admin_app.password_cli --environment preview --project anti-harassment-bot
```

CLI 隱藏輸入且要求重複確認。改密碼加 `--reset`，停用登入加 `--disable`；已存在的密碼不會被首次初始化命令覆蓋。可加 `--use-gcloud-login` 使用目前 gcloud 登入（短效 token 只留記憶體）；或加 `--generate-password-file .admin-local/preview.password` 產生新的 mode `0600` 隨機密碼檔。已有檔案可用 `--password-file` 讀取，權限必須僅限擁有者。不要把密碼放命令列參數或提交 Git。

舊版曾分別初始化 Preview 與 Production 密碼，並留有本機 `.admin-local/preview.password`、`.admin-local/production.password`（均為 0600）。在更新正式憑證前，先以現有 Preview 密碼檔驗證 Preview 登入，完成 Preview 四個單元的瀏覽器／API 驗收及必要備份；全部通過後，才用同一檔案對 `--environment production` 執行 `--reset --password-file .admin-local/preview.password`，再分別驗證兩站登入。若 Preview 驗證失敗，不可把可能過期的檔案寫入正式庫。重設會使正式舊 Session 失效；舊 production 密碼檔其後不再作為登入依據。正式管理入口為 [Admin Production](https://anti-harassment-bot-admin.web.app)，發布狀態以遷移報告為準。Preview 網址每次部署後以 deployment JSON 實際輸出為準；舊 channel 網址可能已過期。

本機 development 與 preview 共用 `admin-private-dev` 的密碼 hash，但 sessions 有環境綁定。密碼輪替會使該私有庫所有舊 sessions 失效。

在 Admin 網頁輸入密碼後，後端設定 `__session` Cookie（雲端為 Secure／HttpOnly／SameSite=Strict、host-only）。預設閒置 30 分鐘、最長 8 小時；每次請求查驗期限、撤銷及 credential version。登出撤銷 session。瀏覽器不保存管理 token；CLI 也只在記憶體保留 Cookie 與 CSRF。登入限流預設每來源 15 分鐘 5 次、全域 100 次。

## 設定、Prompt、Skills 與測試

先確認頁面環境。系統設定涵蓋模型、provider 排序、rerank、流程開關及 Prompt；Skills 提供新增、編輯與啟停。儲存採 revision 比對，收到衝突時重新讀取再整合修改，不會直接覆蓋另一個分頁的新版本。Client 一般設定快取約 30 秒、Skills 約 60 秒。

### 聊天隱私與內容審查

Client 瀏覽器先以規則遮罩當次訊息及選入的歷史、摘要、個案欄位與追問資料，預設自動送出。可在聊天設定開啟「送出前遮罩確認」，逐次檢視與編輯遮罩內容。取消確認時不呼叫聊天 API。規則可能漏掉自由文字中的人名、單位或事件細節。Admin 診斷及批次工具另有本機遮罩與批次預覽流程，不會逐筆顯示 Client 確認視窗。後端遮罩開關獨立於瀏覽器確認。

Admin Preview／開發環境的瀏覽器 PII 小模型仍僅供手動驗證；固定 120 則與另寫的 16 則合成診斷集均未通過品質門檻，尚未接入 Client 聊天。16 則標註仍待獨立人工審定；實測數字見[遷移報告](MIGRATION_REPORT.md)。

Gate 預設關閉。在「系統設定 → Gate 審查設定」選擇模型、逾時並啟用。前段與後段提示詞在「Prompt 設定 → 聊天內容審查（Gate）」編輯，亦可從 Gate 審查設定按「編輯內容審查提示詞」前往。按「儲存變更」後可用「本次診斷測試」查驗。預設每段逾時為 10 秒。`gate_prompt` 用於輸入審查，`gate_post_prompt` 用於回覆審查；既有設定未存後段欄位時，後端使用版本化預設值，不覆寫前段提示詞。啟用時，輸入審查與主模型並行，但輸入審查允許前不顯示主模型輸出；允許後可先顯示暫定串流，回覆審查若拒絕就撤回該次內容，再由主模型產生簡短拒答。審查或拒答服務失敗時回傳可重試的 `review_unavailable`，不保留暫定回覆。Admin 單筆診斷使用同一流程。合成測試只驗證流程；實際 Gate 模型需另以代表性案例驗證 JSON schema 支援與判斷品質，不能因設定存在就視為已驗收。

在「Gate 審查設定」可選用 Jev 1.14。Jev 使用 OpenRouter Decisions API，沿用輸入與回覆提示詞作為判斷準則，僅送出 Gate 原有的規則遮罩資料。兩段審查共用「Jev 判定門檻」（0.50–1.00，預設 0.70）。最高選項的機率低於門檻時回傳可重試的審查錯誤，已有暫定回覆會撤回。其他 Gate 模型沿用原有審查方式。先在 Preview 用合成案例測量誤擋、漏放與延遲，再決定是否啟用。


圖片附件目前在 Client 及 Admin 診斷端點一律停用，後端收到圖片回 `422 image_upload_disabled`。即使既有 Firestore 的 `enable_image_upload=true`，也不會放行；Admin 頁的該欄位只顯示保留的設定值。

### Client App Check

App Check 預設關閉。先在 Firebase 對 Client Web App 註冊 [reCAPTCHA Enterprise 分數型網站金鑰](https://firebase.google.com/docs/app-check/web/recaptcha-enterprise-provider)，再給前端完整的 `VITE_FIREBASE_API_KEY`、`VITE_FIREBASE_PROJECT_ID`、`VITE_FIREBASE_APP_ID`、`VITE_APP_CHECK_SITE_KEY`，並設 `VITE_APP_CHECK_ENABLED=true`；後端設 `CHAT_APP_CHECK_ENABLED=true`。前端在啟用模式取得不到 token 時不送出聊天請求，後端在啟用模式拒絕缺失或無效的 `X-Firebase-AppCheck` token。兩端開關及 Web App 註冊必須配套核對。部署 workflow 對 dev／production 分別使用 `CLIENT_FIREBASE_APP_ID_DEV/PROD`、`CLIENT_APP_CHECK_ENABLED_DEV/PROD`、`CLIENT_APP_CHECK_SITE_KEY_DEV/PROD` 及 `CLIENT_CHAT_APP_CHECK_ENABLED_DEV/PROD` GitHub Variables；`CLIENT_FIREBASE_APP_ID` 可明確選擇建置使用的 Client Web App。正式環境若有多個未標為 RAG 的 Web App，未指定 ID 時建置會停止，避免挑錯 App。這些變數與 Firebase 註冊狀態尚須由操作者核實。Preview 與 `main-preview` 的實際 Hosting 網域也須在 [reCAPTCHA key 允許網域](https://docs.cloud.google.com/recaptcha/docs/create-key-website)中核對，先用瀏覽器確認能取得有效 token，再啟用對應後端的強制驗證。

單筆診斷提交背景工作並輪詢。Excel 批次與行為測試由瀏覽器逐筆安排，提供暫停、繼續、失敗重試與本機匯出。工作簿、批次紀錄及報表留在本機；後端單次診斷的去識別請求與結果預設 24 小時後不可讀取並清理。job 綁定建立它的 session；重新登入後不能讀取前一個 session 的單次工作。Runtime／Skills／契約版本改變會停止原測試。模型結果不確定時需人工選擇重試，避免自動重複消耗。

可靠性 CLI 已移到 `admin/tests/reliability`，目前只支援兩個固定工作簿格式：`chatgpt_scene.xlsx` 的 `Sheet1`、335 筆連續情境列，預測欄為 F；以及 `gemini_scene.xlsx` 的 `工作表1`、299 筆連續情境列，預測欄為 G。兩者情境欄均為 B，來源預測欄必須空白。不能只將任意工作簿改名使用；自選工作表與欄位請使用 Admin 網頁批次工具。

以下以專案內的原始 `resource/chatgpt_scene.xlsx` 示範。`--dry-run` 完全離線；`--prepare-only` 需要先啟動本機 Admin API，並以隱藏提示輸入管理密碼，登入取得當前設定後才在本機產生遮罩預覽與結果副本。兩者都不提交模型工作、不覆寫來源工作簿：

```bash
PYTHONPATH=shared/python:admin/backend uv run --frozen python -m admin.tests.reliability.applicable_law_predict --xlsx resource/chatgpt_scene.xlsx --dry-run
PYTHONPATH=shared/python:admin/backend uv run --frozen python -m admin.tests.reliability.applicable_law_predict --xlsx resource/chatgpt_scene.xlsx --prepare-only --run-dir /tmp/rag-evaluation
```

實際呼叫再指定 `--smoke` 或 `--all`、`--allow-live`、`--password-file` 及 `--api-url https://ADMIN_HOST/api/v1/admin/test-jobs`；本機預設 `http://127.0.0.1:8001/api/v1/admin/test-jobs`。有需要可指定 `--origin`。CLI 使用 native TypeScript 遮罩 bridge，此工具以 Node 26 驗證；可用 `--node` 指定該執行檔。只準備預覽不等於授權送出真實情境，請先核對來源及目的服務。其他選項見 `--help` 與 [可靠性工具說明](../admin/tests/reliability/README.md)。

截至 2026-11-10，完整工作簿重播尚未完成：後續對外傳送步驟遭自動核准審查拒絕。請勿將本機準備、合成測試或部分執行結果表述為完整真實案例評分。

## 檢索來源的新增、下載與刪除

1. 在「檢索資料」選擇法規、判決或求助資源類別，上傳 JSON／JSONL、CSV／XLSX、TXT／Markdown 或可抽取文字的 PDF／DOCX。PDF 不含 OCR。
2. 檢查欄位對應、完整文字、metadata、分段次序與數量。預設每檔上限 5 MiB、PDF 100 頁、一次 100 chunks；超限或解析不完整會拒絕，不截斷發布。
3. 確認預覽後才建立 embedding／發布工作。確認前可有私有暫存原檔，但不呼叫 embedding、不寫入檢索 collection。確認綁定環境、內容 hash、分段計畫及 embedding 設定。
4. 輪詢至完成，查看來源詳情與所有 chunks，並透過已登入 API 下載原檔。相同內容去重、重複確認回傳原 job；同來源不同內容回報衝突，本版不支援就地替換。
5. 刪除前核對環境、來源、分段數及原檔，確認刪除整份來源。資料刪除後繼續清理私有原檔及暫存副本；「清理中」表示尚未完成，依頁面狀態恢復或重試。

新來源的 chunks、來源及 receipt 在同一業務庫 transaction 發布；私有庫 job 透過 receipt 對帳。大型舊來源依核對過的 manifest 分批刪除。舊資料沒有可驗證 manifest 時僅可瀏覽；缺少原檔時明示無原檔，不推測還原。repository 的 `data/`、`resource/` 不會被網頁刪除。舊資料搬移及來源 catalog 的計畫／套用／核對命令見 [資料工具說明](../admin/backend/admin_app/scripts/README.md)。

## 建置與檢查

```bash
uv run --frozen pytest client/tests admin/tests shared/tests -q
pnpm -r lint
pnpm --filter @rag/shared test
pnpm --filter @rag/client test
pnpm --filter @rag/admin test
pnpm -r build
pnpm test:rules
uv run --frozen python scripts/build_functions.py
uv run --frozen python scripts/quality/check_source_package.py
```

建置產生 `build/functions/client` 與 `build/functions/admin`，各含自己的入口、application package、`rag_core` 與鎖定 requirements。Client 不含 Admin 程式；兩包排除環境檔、憑證、原始資料與測試。`build/` 可重建，不是來源。CI 另執行 Ruff；Rules 在 Emulator 中驗證四個 databases 的匿名及已登入讀寫拒絕，不能取代真實服務帳號 IAM 驗收。

## 雲端初始設定

以下命令均由根目錄執行，明確指定 project。`provision_*` 與 `configure_admin_workers.py` 預設只顯示計畫；加 `--apply` 才修改雲端。先檢視各命令計畫與必要權限。

```bash
uv run --frozen python scripts/provision_firebase.py --phase all
uv run --frozen python scripts/provision_delivery.py --phase hosting
```

基礎資源包含 databases、各環境服務帳號與資料庫 IAM、私有 Storage、Cloud Tasks、Secret Manager。`secrets` 階段是既有服務的初次遷移：建立獨立模型／新澄清簽章 secret，舊澄清相容只保留衍生簽章 key，不複製舊管理登入 key。既有 secret 不會被每次部署覆寫。全新 project 需另由操作者建立這些 secrets；此 repository 的初始移轉工具以既有 project 為目標。

```bash
firebase deploy --only firestore --project anti-harassment-bot --config firebase.json
```

四個 databases 都套用拒絕瀏覽器直連的 Rules。業務庫三個向量索引需等待 READY；embedding 模型 `intfloat/multilingual-e5-large`、維度 `1024`，不得只改模型而保留不相容索引。

## 發布與背景工作

```bash
uv run --frozen python scripts/deploy.py --environment preview --service admin --backend-only --output /tmp/deploy-preview-admin-backend.json
uv run --frozen python scripts/deploy.py --environment preview --service admin --frontend-only --output /tmp/deploy-preview-admin-frontend.json
uv run --frozen python scripts/deploy.py --environment preview --service client --backend-only --output /tmp/deploy-preview-client-backend.json
uv run --frozen python scripts/deploy.py --environment preview --service client --frontend-only --output /tmp/deploy-preview-client-frontend.json
uv run --frozen python scripts/configure_admin_workers.py --environment preview --apply
```

四個命令分別部署 Admin 後端、Admin 前端、Client 後端、Client 前端；任一項可獨立重跑。Preview 從各 JSON 取得實際隨機網址，兩個 Hosting site 的 `dev-preview` 均為 30 天。正式使用相同四個命令並將 `--environment` 換成 `production`；Client site 為 `anti-harassment-bot`，Admin site 為 `anti-harassment-bot-admin`。先完成 Preview 的四項驗收再切換正式環境。

每次發布前，先在受保護的操作紀錄中記下該環境兩個 Functions 的現行版本與非機密設定、兩個 Hosting site 的現行 release、Runtime／Skills revision、佇列未完成工作及必要的 Firestore／Storage 備份位置。每一步完成後再驗收下一步；任一步失敗只回復該單元，且只能使用保留新架構登入及資料邊界的已知可用版本。

| 單元與順序 | 發布後核對 | 回復依據 |
| --- | --- | --- |
| 1. Admin 後端 | 健康檢查、登入／登出、設定 revision、診斷工作與 worker OIDC；若 Function URI 改變，重新核對 worker target | 先前已驗證的 `admin_api` 或 `admin_api_preview` 程式版本與設定快照；確認佇列及 receipt 後再恢復工作 |
| 2. Admin 前端 | 對應 Admin site 的登入、設定讀寫與診斷頁 | 該 site／channel 的上一個已知可用 Hosting release，不更動 Client site |
| 3. Client 後端 | 健康檢查、App Check 開關與 token、v1–v4 聊天及 SSE 撤回、資料庫隔離 | 先前已驗證的 `api` 或 `api_preview` 程式版本與設定快照；資料修復依備份及來源 receipt |
| 4. Client 前端 | 對應 Client site 的本機遮罩、確認開關、文字串流撤回、圖片封鎖及 App Check | 該 site／channel 的上一個已知可用 Hosting release，不更動 Admin site |

正式環境只在 Preview 的四步瀏覽器與 API 驗收、Runtime IAM 檢查、資料備份及回復演練完成後切換。`main-preview` 仍是獨立的 Client Hosting-only 預覽，不屬於這四步，也不建立新後端。

首次在 Production 強制 Client App Check 時，仍依上表發布四個單元，但第三步的 Client 後端先以 `CHAT_APP_CHECK_ENABLED=false` 部署新程式，避免仍在使用舊前端的正式站與 `main-preview` 立即收到 401。第四步以正式 Client Web App 與已註冊的 Enterprise key 建置並發布正式 Client 前端；再依 [main-preview 手冊](MAIN_PREVIEW.md)單獨更新該 Hosting channel。兩站都先從真實瀏覽器確認能交換 token 並完成聊天；最後以 `CHAT_APP_CHECK_ENABLED=true` 單獨重跑 Client 後端部署，並確認缺失／偽造 token 回 401、有效 token 成功。這是首次啟用的額外設定切換；往後已啟用的發布維持四單元順序。若中途停止，保持後端驗證關閉，直到兩個 Client Hosting channel 均相容。

首次 Admin Function 建立後，腳本以實際 `run.app` URI 配置 worker target／audience。接著 worker 設定工具建立每 5 分鐘的 OIDC Scheduler 維護、私有庫 TTL 與 Storage 暫存生命週期。Cloud Tasks 每環境一個 queue；worker 驗證 OIDC audience 及指定 `rag-tasks-prod/dev` 身分。HTTP service 可公開接收傳輸，但 `/internal` 不接受一般 session 或偽造 header。

澄清 token 的舊簽章相容截止時間固定為 `2026-10-11T20:00:00Z`，重新部署不延長。截止後可另外安排解除 legacy secrets 綁定與清除衍生舊 key；新 token 仍使用獨立的新 secret。

### GitHub Actions

`.github/workflows` 的正式四項部署只允許在 main 手動觸發，逐項或完整選擇；dev preview 在 dev push 時依受影響服務部署，也可在 Actions 明確選擇 dev branch 後手動選一項。正式切換前應先完成 Preview 四項驗收。首次啟用 App Check 時，正式 workflow 的 `client_app_check` 選 `stage` 部署第三步，兩個 Client 前端完成後，再選 `client-backend` 與 `enforce` 進行最後切換；`inherit` 會沿用 Production 變數，且從未啟用狀態切為強制驗證時必須明確選 `enforce`。WIF 綁定固定 repository ID、允許的事件與 workflow refs 及 deploy service account，不使用長效 service-account JSON。

建立 WIF 前先讀 [CI 權限審查](../infrastructure/iam/CI_DEPLOYMENT_REVIEW.md)。`scripts/provision_delivery.py --phase federation` 顯示所需計畫；Functions 的更新權限只綁定四個既有 Functions，其餘資源採逐項限制。Hosting Admin 目前需 project 層級授權。若需實測四個 runtime 服務帳號的跨庫與寫入拒絕矩陣，可執行 `uv run --frozen python scripts/verify_runtime_iam_operator.py` 檢視與執行限時探針。

Functions 必須分別配置 **build service account** 與 **runtime service account**。在既有 Functions 完成 operator bootstrap 後，先執行 `uv run python scripts/provision_build.py --environment all` 審閱計畫，再以 `--apply` 建立兩個專用 Builder、綁定來源／artifact／日誌權限並設定建置帳號。這是 operator 工作，不應給 CI IAM 管理權限。`provision_firebase.py --phase identities` 也會預建兩個 Builder 帳號；federation provisioning 會將 deployer 的 actAs 限制在四個 runtime 與兩個 build 帳號上。一般部署會在打包前檢查建置帳號，禁止意外沿用預設 Compute 身分。

## 驗收與故障排查

| 現象 | 檢查方式 |
| --- | --- |
| Admin 回 401 | 檢查 session 是否到期／密碼已輪替，重新登入；不要改用固定 token |
| 寫入回 403 | 確認同源網址、Origin、CSRF；Cookie 不跨 Client／Admin site |
| 設定回 409 | 重新載入最新 revision，再整合修改 |
| 登入／輪詢回 429 | 依 `Retry-After` 等待，不重複提交模型工作 |
| Job 持續 queued | 雲端檢查 queue、target／audience、task service account；本機檢查 worker 是否啟動 |
| Job 設定漂移或結果不確定 | 重新檢視當前設定；由操作者明確決定是否建立新工作 |
| 刪除顯示清理中 | 檢查同環境 bucket 權限、generation 與 maintenance；不要直接標成完成 |
| Firestore PermissionDenied | 核對 Function runtime SA、database ID 與 DB 條件式 IAM；Rules 不約束 Admin SDK |
| 匯入維度不符 | 檢查模型實際向量長度、1024 維索引及預覽設定版本 |
| 本機登入 gcloud 成功但 SDK 失敗 | CLI login 與 ADC 是不同憑證；更新 ADC 或密碼 CLI 明確選用 `--use-gcloud-login` |
| 私有庫故障 | Admin 拒絕服務；先修復連線／權限，不能略過授權 |

正式切換前需完成 preview 的登入／拒絕案例、診斷佇列、合成匯入與整份刪除、同環境設定生效、Client SSE、Rules 及真實 SA 跨庫阻擋。模型品質與法律正確性不由這些工程驗收推定。

## 回復與資料保護

先停止新增匯入／刪除。以已知可用的新架構版本重新執行 deploy；不要回復會重新開放舊固定 Admin token 的版本。設定透過最新 revision 寫回核對過的舊值；資料依事前備份與來源 receipts 修復。跨庫 job 狀態以業務庫 receipt 為準，不假設兩庫可原子回復。

密碼洩漏時用 `password_cli --reset` 或 `--disable` 使既有 sessions 失效；模型 secret 與澄清簽章 key 分開輪替。原始 `.env`、`.env.local`、`firebase_admin.json`、`resource/`、`data/` 不納入部署。不要將含 Cookie、密碼、provider key 或個案全文的日誌及診斷輸出加入版本控制。
