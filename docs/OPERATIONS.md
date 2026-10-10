# Client／Admin 操作、執行與部署手冊

本手冊對應拆分後程式。實際完成狀態、網址與驗收範圍見 [遷移報告](MIGRATION_REPORT.md)；不要以舊版 PDF 的路徑、固定 Admin token 或部署指令操作新版。

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

正式及 preview 密碼分開，私有庫只存 Argon2id hash。首次初始化由具有對應私有庫寫入權限的操作者執行：

```bash
ADMIN_COOKIE_SECURE=true PYTHONPATH=shared/python:admin/backend uv run --frozen python -m admin_app.password_cli --environment preview --project anti-harassment-bot
```

CLI 隱藏輸入且要求重複確認。改密碼加 `--reset`，停用登入加 `--disable`；已存在的密碼不會被首次初始化命令覆蓋。可加 `--use-gcloud-login` 使用目前 gcloud 登入（短效 token 只留記憶體）；或加 `--generate-password-file .admin-local/preview.password` 產生新的 mode `0600` 隨機密碼檔。已有檔案可用 `--password-file` 讀取，權限必須僅限擁有者。production 將 `--environment` 換成 `production` 並使用獨立檔案。不要把密碼放命令列參數或提交 Git。

本次已分別初始化 Preview 與 Production 管理密碼，僅存於本機 `.admin-local/preview.password`、`.admin-local/production.password`（均為 0600），不在報告中列出。正式管理入口為 [Admin Production](https://anti-harassment-bot-admin.web.app)，目前發布狀態以遷移報告為準。Preview 管理入口為 [Admin Preview](https://anti-harassment-bot-admin--dev-preview-q53x908r.web.app)，使用者入口為 [Client Preview](https://anti-harassment-bot--dev-preview-w28ypvc3.web.app)。密碼初始化不代表正式切換已完成；發布狀態以遷移報告為準。

本機 development 與 preview 共用 `admin-private-dev` 的密碼 hash，但 sessions 有環境綁定。密碼輪替會使該私有庫所有舊 sessions 失效。

在 Admin 網頁輸入密碼後，後端設定 `__session` Cookie（雲端為 Secure／HttpOnly／SameSite=Strict、host-only）。預設閒置 30 分鐘、最長 8 小時；每次請求查驗期限、撤銷及 credential version。登出撤銷 session。瀏覽器不保存管理 token；CLI 也只在記憶體保留 Cookie 與 CSRF。登入限流預設每來源 15 分鐘 5 次、全域 100 次。

## 設定、Prompt、Skills 與測試

先確認頁面環境。系統設定涵蓋模型、provider 排序、rerank、流程開關及 Prompt；Skills 提供新增、編輯與啟停。儲存採 revision 比對，收到衝突時重新讀取再整合修改，不會直接覆蓋另一個分頁的新版本。Client 一般設定快取約 30 秒、Skills 約 60 秒。

單筆診斷提交背景工作並輪詢。Excel 批次與行為測試由瀏覽器逐筆安排，提供暫停、繼續、失敗重試與本機匯出。工作簿、批次紀錄及報表留在本機；後端單次診斷的去識別請求與結果預設 24 小時後不可讀取並清理。job 綁定建立它的 session；重新登入後不能讀取前一個 session 的單次工作。Runtime／Skills／契約版本改變會停止原測試。模型結果不確定時需人工選擇重試，避免自動重複消耗。

可靠性 CLI 已移到 `admin/tests/reliability`，目前只支援兩個固定工作簿格式：`chatgpt_scene.xlsx` 的 `Sheet1`、335 筆連續情境列，預測欄為 F；以及 `gemini_scene.xlsx` 的 `工作表1`、299 筆連續情境列，預測欄為 G。兩者情境欄均為 B，來源預測欄必須空白。不能只將任意工作簿改名使用；自選工作表與欄位請使用 Admin 網頁批次工具。

以下以專案內的原始 `resource/chatgpt_scene.xlsx` 示範。`--dry-run` 完全離線；`--prepare-only` 需要先啟動本機 Admin API，並以隱藏提示輸入管理密碼，登入取得當前設定後才在本機產生遮罩預覽與結果副本。兩者都不提交模型工作、不覆寫來源工作簿：

```bash
PYTHONPATH=shared/python:admin/backend uv run --frozen python -m admin.tests.reliability.applicable_law_predict --xlsx resource/chatgpt_scene.xlsx --dry-run
PYTHONPATH=shared/python:admin/backend uv run --frozen python -m admin.tests.reliability.applicable_law_predict --xlsx resource/chatgpt_scene.xlsx --prepare-only --run-dir /tmp/rag-evaluation
```

實際呼叫再指定 `--smoke` 或 `--all`、`--allow-live`、`--password-file` 及 `--api-url https://ADMIN_HOST/api/v1/admin/test-jobs`；本機預設 `http://127.0.0.1:8001/api/v1/admin/test-jobs`。有需要可指定 `--origin`。CLI 使用 native TypeScript 遮罩 bridge，此工具以 Node 26 驗證；可用 `--node` 指定該執行檔。只準備預覽不等於授權送出真實情境，請先核對來源及目的服務。其他選項見 `--help` 與 [可靠性工具說明](../admin/tests/reliability/README.md)。

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
pnpm -r test
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
uv run --frozen python scripts/deploy.py --environment preview --service all --output /tmp/deploy-preview.json
uv run --frozen python scripts/configure_admin_workers.py --environment preview --apply
```

deploy 先建置／發布 Admin、Client 後端，再建置／發布對應前端；preview 從 JSON 取得實際隨機網址，兩個 Hosting site 的 `dev-preview` 均為 30 天。正式使用 `--environment production`，Client site 為 `anti-harassment-bot`，Admin site 為 `anti-harassment-bot-admin`。可用 `--service client|admin` 與 `--backend-only|--frontend-only` 做明確的單一服務操作，通常使用完整發布。

首次 Admin Function 建立後，腳本以實際 `run.app` URI 配置 worker target／audience。接著 worker 設定工具建立每 5 分鐘的 OIDC Scheduler 維護、私有庫 TTL 與 Storage 暫存生命週期。Cloud Tasks 每環境一個 queue；worker 驗證 OIDC audience 及指定 `rag-tasks-prod/dev` 身分。HTTP service 可公開接收傳輸，但 `/internal` 不接受一般 session 或偽造 header。

澄清 token 的舊簽章相容截止時間固定為 `2026-10-11T20:00:00Z`，重新部署不延長。截止後可另外安排解除 legacy secrets 綁定與清除衍生舊 key；新 token 仍使用獨立的新 secret。

### GitHub Actions

`.github/workflows` 保留 main 正式與 dev preview 分支部署，shared 變更觸發兩邊驗證；部署不因新 push 取消。WIF 綁定固定 repository ID、允許的 refs 及 deploy service account，不使用長效 service-account JSON。

建立 WIF 前先讀 [CI 權限審查](../infrastructure/iam/CI_DEPLOYMENT_REVIEW.md)。`scripts/provision_delivery.py --phase federation` 顯示所需計畫；Functions 的更新權限只綁定四個既有 Functions，其餘資源採逐項限制。Hosting Admin 目前需 project 層級授權，未授權時不能宣稱自動 Hosting 部署可用。最新雲端配置與部署驗證請看 [2026-10-10 權限報告](permissions-2026-10-10/README.md)。

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
