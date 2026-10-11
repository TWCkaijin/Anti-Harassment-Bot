# 性騷擾防治智能 AI 助手

React／Vite、Flask、Firebase Functions／Firestore 與 OpenRouter 組成的諮詢系統。Client 提供聊天、摘要、瀏覽器遮罩與 RAG；獨立 Admin 提供設定、Prompt／Skills、診斷、批次測試與檢索資料管理。

| 文件 | 用途 |
| --- | --- |
| [遷移報告與新架構](docs/MIGRATION_REPORT.md) | 架構、資料／權限邊界、已驗證結果與待完成事項 |
| [操作、執行與部署手冊](docs/OPERATIONS.md) | 安裝、五個本機程序、登入、資料管理、部署、故障排查及回復 |
| [資料工具](admin/backend/admin_app/scripts/README.md) | 舊資料移轉、來源 manifest 與匯入 CLI |
| [CI 權限審查](infrastructure/iam/CI_DEPLOYMENT_REVIEW.md) | WIF 信任條件及各項部署權限 |
| [技術文件與閱讀版手冊](docs/technical-report/README.md) | 已同步新架構的 Markdown／HTML／PDF 與可編輯圖稿 |
| [資料說明](data/README.md) | 原始來源與知識庫資料 |

## 目錄與資料庫

```text
client/       # Client 前端、後端與測試
admin/        # Admin 前端、後端、資料工具與測試
shared/       # Agent、RAG、契約、設定與瀏覽器遮罩
infrastructure/ # Rules、索引、IAM 與 Emulator 測試
scripts/      # 建置、部署與可重用維運工具
docs/         # 遷移報告與操作手冊
data/         # 保留的知識庫來源
resource/     # 保留的原始工作簿與研究資料
```

| 環境 | Client／Admin 共用業務庫 | Admin 私有庫 | Functions |
| --- | --- | --- | --- |
| production | `(default)` | `admin-private` | `api`／`admin_api` |
| preview | `app-dev` | `admin-private-dev` | `api_preview`／`admin_api_preview` |

同環境共用設定與檢索內容，管理密碼、sessions、jobs 存在私有庫。瀏覽器不直接存取 Firestore；Admin 使用 HttpOnly session cookie，每次請求重新驗證。正式部署與 preview 的完成狀態以遷移報告為準。

## Local testing：啟動 Client／Admin

以下以 macOS／Linux 的 bash 或 zsh 為例，**所有指令都在專案根目錄執行**。本機網頁與 API 連到雲端 dev 資料，不是離線 Emulator；不需要啟動 Cloud SQL。

| 項目 | 本機設定 |
| --- | --- |
| Client 網頁 | <http://127.0.0.1:5173> |
| Admin 網頁 | <http://127.0.0.1:5174> |
| Client API | `http://127.0.0.1:8000` |
| Admin API | `http://127.0.0.1:8001` |
| Firebase project | `anti-harassment-bot` |
| 業務資料庫／設定 | `app-dev`／`runtime_config/app_dev` |
| 管理私有資料庫 | `admin-private-dev` |
| 檔案 bucket | `anti-harassment-bot-admin-dev` |

本機 Admin 修改設定、Prompt、Skills 或檢索資料，**會影響共用這些 dev 資料的線上 Preview**。聊天、診斷及需要 embedding 的匯入會實際呼叫設定的模型服務並可能計費；請使用適合測試的資料。

### 一次啟動四個服務

完成下方的相依安裝、ADC 與模型金鑰設定後，在專案根目錄執行：

```bash
uv run --frozen python scripts/dev.py
```

腳本會啟動兩個 Flask API（8000／8001）與兩個 Vite 網頁（5173／5174），待四個 HTTP 入口回應後顯示網址。**不需建立或 source `development-shell.sh`**；腳本會自動固定 development、dev 資料庫、同源 proxy 與本機 Cookie 設定。

- 優先讀取 `.admin-local/development.env`；不存在時讀取既有根目錄 `.env`，啟動時會顯示實際路徑，不輸出金鑰。
- 按 `Ctrl-C` 會停止本次啟動的四個服務及其子程序；任一服務提早退出也會一併清理。
- Port 已被占用時會拒絕啟動，不會終止原本的程序。
- 各服務 log 位於啟動時顯示的 `.admin-local/dev-runs/run-*/`，可另開終端用 `tail -f <log 路徑>` 查看。
- 不自動啟動 Admin worker，避免接續待處理 jobs。需要背景工作時依步驟 6 另外啟動。

只檢查本機相依與 port，或指定另一份 env 檔：

```bash
uv run --frozen python scripts/dev.py --check
uv run --frozen python scripts/dev.py --env-file .admin-local/development.env
```

`--check` 不驗證雲端憑證或模型金鑰是否有效。啟動成功也不代表完整聊天／登入驗收通過。以下步驟 3–4 是需要個別控制程序時的手動啟動方式。

### 1. 安裝相依套件

準備 Python **3.13**、Node.js **24**、pnpm **10.27.0**、uv 與 Google Cloud CLI（`gcloud`）。一般本機啟動不需要 Firebase CLI 或 Java；只有部署／Rules Emulator 測試才需要。

```bash
uv python install 3.13
uv sync --frozen --all-extras
pnpm install --frozen-lockfile
```

### 2. 設定 Google Cloud 憑證與模型金鑰

使用有 dev Firestore 與 dev bucket 權限的 Google 帳號建立 Application Default Credentials（ADC）：

```bash
gcloud auth application-default login
gcloud auth application-default set-quota-project anti-harassment-bot
```

`gcloud auth login` 與 ADC 是不同的登入設定；只有 CLI 已登入，不代表 Python SDK 可以讀取資料庫。若根目錄已有 `firebase_admin.json`，目前 Firestore 初始化會優先使用該本機憑證；Storage SDK 使用 ADC。請確認兩者都有所需 dev 資源權限，並保留既有憑證檔。

建立本機設定檔，已有檔案時不覆蓋：

```bash
mkdir -p .admin-local
if [ ! -f .admin-local/development.env ]; then
  (umask 077; cp .env.example .admin-local/development.env)
fi
chmod 600 .admin-local/development.env
```

用編輯器開啟 `.admin-local/development.env`，至少將 `OPENROUTER_API_KEY` 的範例值改成有效金鑰。不要把真正金鑰寫入 README、Git 或前端 `VITE_*` 變數。檔案中的模型參數是 fallback；現有 `runtime_config/app_dev` 可覆寫模型選擇等設定。

### 3. 建立三個 Python 程序共用的環境設定

將下列內容存為 **`.admin-local/development-shell.sh`**。之後 Client API、Admin API 與 worker 都先 `source` 這個檔案；必須從專案根目錄載入，讓 `$PWD` 指向正確位置。

```bash
export PYTHONPATH="$PWD/shared/python:$PWD/client/backend:$PWD/admin/backend"
export RAG_ENV_FILE="$PWD/.admin-local/development.env"
export FLASK_SKIP_DOTENV=1
export ENVIRONMENT=development
export FIREBASE_PROJECT_ID=anti-harassment-bot
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

`FLASK_SKIP_DOTENV=1` 防止 Flask 自動讀取舊根目錄 `.env`，由 `RAG_ENV_FILE` 指定本機設定。終端已匯出的同名變數會優先於 env 檔；若曾設定其他環境，請使用乾淨終端，避免殘留 `FIRESTORE_EMULATOR_HOST`、`K_SERVICE` 或 `PYTHON_DOTENV_DISABLED` 等變數。

`ADMIN_COOKIE_SECURE=false` 僅供本機 HTTP 使用；雲端 Preview／Production 必須維持 `true`。本機不透過 Cloud Tasks 派送工作，所以上述四個 Tasks 設定需保持空白。

### 4. 分別開啟四個終端，啟動前後端

**終端 A：Client API（8000）**

```bash
source .admin-local/development-shell.sh
uv run --frozen flask --app client_app.main:create_app run --host 127.0.0.1 --port 8000
```

**終端 B：Admin API（8001）**

```bash
source .admin-local/development-shell.sh
uv run --frozen flask --app admin_app.main:create_app run --host 127.0.0.1 --port 8001
```

**終端 C：Client 網頁（5173）**

```bash
VITE_API_BASE_URL=/api pnpm --filter @rag/client dev
```

**終端 D：Admin 網頁（5174）**

```bash
pnpm --filter @rag/admin dev
```

開啟 <http://127.0.0.1:5173> 使用 Client，開啟 <http://127.0.0.1:5174> 使用 Admin。兩個 Vite 開發伺服器都會把 `/api` 代理到對應本機 API；Admin 登入必須透過 `5174` 的網頁走同源 Cookie 流程。一般啟動不用複製前端 Firebase 設定，也不用先執行 `pnpm build`。

兩個 Vite port 都設定 `strictPort`，被占用會直接報錯，不會自動改用其他 port。請勿混用 `localhost` 與 `127.0.0.1` 登入，以免 Cookie 綁在不同主機。

### 5. 登入本機 Admin

本機 development 與 Preview 共用 `admin-private-dev` 的管理密碼，但登入 sessions 依環境區分。這台開發機先前產生的 Preview 密碼位於 **`.admin-local/preview.password`**；用編輯器查看，再貼入 Admin 登入頁。請不要使用 `.admin-local/production.password`，也不要使用舊版 `ADMIN_API_KEY`。

密碼檔不在 Git 中，新 checkout 不一定存在，請向管理者取得目前 dev 密碼。**已有帳號時不需重新初始化或重設。** 若是新建的 dev 私有庫、尚未設定密碼，可在額外終端執行：

```bash
source .admin-local/development-shell.sh
ADMIN_COOKIE_SECURE=true uv run --frozen python -m admin_app.password_cli \
  --environment preview --project anti-harassment-bot --use-gcloud-login
```

CLI 會隱藏輸入並要求確認，密碼至少 12 個字元；`--use-gcloud-login` 使用目前 `gcloud auth login` 的帳號。此處指定 `preview` 是為了初始化共用的 dev 私有庫，不會把本機 API 切成 Preview 模式。已有密碼時 CLI 會拒絕覆蓋；不要為了啟動本機任意加 `--reset`，因為重設也會改變線上 Preview 密碼並使舊 sessions 失效。

### 6. 需要背景工作時，再啟動第五個終端

只瀏覽 Client 或管理設定，不一定需要 worker；**單筆診斷、依賴診斷工作的批次測試、資料匯入／刪除等 jobs 需要 worker**，否則工作會停在 `queued`。

**終端 E：Admin worker**

```bash
source .admin-local/development-shell.sh
uv run --frozen python -m admin_app.worker_cli --poll-seconds 2
```

worker 只接受 development、dev 資料庫及空白 Tasks 設定，並處理該環境中的待執行工作。啟動輪詢可能接續先前留下的 development jobs；若只想啟動網站，先不要開 worker。每個程序都可用 `Ctrl-C` 停止；不使用時也要停止 worker。

### 7. 確認啟動成功

在另一個終端執行以下唯讀探測：

```bash
# 直接檢查兩個後端
curl --fail http://127.0.0.1:8000/api/v1/health/
curl --fail http://127.0.0.1:8001/api/v1/admin/health

# 檢查兩個 Vite proxy
curl --fail http://127.0.0.1:5173/api/v1/health/
curl --fail http://127.0.0.1:5174/api/v1/admin/health

# 未攜帶登入 Cookie，預期 HTTP 401
curl -i http://127.0.0.1:5174/api/v1/admin/auth/session
```

健康端點成功只代表 HTTP 程序與路由可用，不代表 Firestore、Storage 或模型請求已通過完整測試。接著登入 Admin 確認可讀取設定；需要測試聊天時，再以測試文字送出模型請求。

| 現象 | 優先檢查 |
| --- | --- |
| `ModuleNotFoundError`／找不到 `client_app` 或 `admin_app` | 是否在根目錄執行、該 Python 終端是否已 `source` 共用設定、是否安裝 `--all-extras` |
| 頁面出現 proxy／connection refused | 對應 API 的 8000／8001 是否已啟動 |
| ADC／Firestore／Storage permission denied | ADC 是否已登入；既有 `firebase_admin.json` 的身分是否具有 dev 權限 |
| 登入後仍未驗證／Cookie 未送出 | `ADMIN_COOKIE_SECURE=false`、從 5174 開啟、固定使用同一主機名稱 |
| `Untrusted origin` | `ADMIN_ORIGINS` 是否包含瀏覽器實際使用的 origin |
| `Authentication service unavailable`（503） | 查看 Admin API 終端的 error_id／Google status；檢查 dev 私有庫存取權 |
| jobs 一直 `queued` | worker 是否啟動；API／worker 的環境、資料庫與 bucket 是否一致 |
| 模型回傳 401／404 | OpenRouter key、dev runtime 設定中的模型 ID／provider；不需重啟 Cloud SQL |

本機啟動器預設直接讀取 dev 的 `app-dev/runtime_config/app_dev` 與同庫的 Skills；本機儲存會影響線上 Preview。正式版使用另一個 Firestore database，並不是同一個資料庫的不同 collection。設定載入失敗會分別顯示 Runtime／Prompt 或 Skills 錯誤，保留其他已載入內容；尚未成功載入的區塊禁止儲存、補齊或重置，請先重新載入。

Admin 只在登入時比對密碼；後續沿用 Cookie session，每次仍從 Firestore 檢查撤銷、到期與密碼版本。活動時間最多約每 30 秒更新一次，因此閒置到期可能比最後一次請求提早不到 30 秒。讀取限流每次向 Firestore 預留最多 8 次額度，程序內使用；程序重啟／多實例分散可能浪費少量額度，但不會放寬全域上限。登入與寫入限流仍逐次計數。`429` 表示限流；`503` 應依日誌 error_id 與 phase 查明後端失敗原因。

Firestore 與 Storage 現在共用已初始化的 Firebase 憑證，避免本機 `firebase_admin.json` 與其他專案的 ADC 混用。來源清單只讀取列表欄位，不下載完整 manifest。後端程式修改後，請停止並重新執行 `uv run --frozen python scripts/dev.py`，再重新載入 Admin 頁面。

部署與進階維運請看 [操作手冊](docs/OPERATIONS.md)。上述程序只啟動本機服務，不會部署 Firebase Functions 或 Hosting。

## 檢查

```bash
uv run --frozen ruff check client/backend admin/backend shared/python client/tests admin/tests shared/tests scripts conftest.py
uv run --frozen ruff format --check client/backend admin/backend shared/python client/tests admin/tests shared/tests scripts conftest.py
uv run --frozen pytest client/tests admin/tests shared/tests -q
pnpm -r lint
pnpm -r test
pnpm -r build
uv run --frozen python scripts/build_functions.py
uv run --frozen python scripts/quality/check_source_package.py
```

Rules 驗收另外執行 `pnpm test:rules`，需 Java 21 與 Firebase CLI 15.33.0。一般回歸測試使用替身，不證明模型品質、雲端部署或 IAM 實際權限；雲端驗收結果分開記錄於遷移報告。

## 授權

保留 [Apache License 2.0](LICENSE)。外部資料與服務另依其來源條件使用。本系統提供初步資訊與支持，個案法律結論仍需適當的專業判斷。
