# 性騷擾防治智能 AI 助手

React／Vite、Flask、Firebase Functions／Firestore 與 OpenRouter 組成的諮詢系統。Client 提供聊天、情境摘要、瀏覽器遮罩與 RAG；獨立的 Admin 提供設定、Prompt／Skills、診斷、批次測試與檢索資料管理。

Client 聊天請求先在瀏覽器依規則遮罩再送出；可在聊天設定開啟「送出前遮罩確認」。後端只接受聊天契約 v4。圖片附件在 Client 與 Admin 診斷端點均停用。兩階段 Gate 可由 Admin 設定，既有環境不會因程式更新而自動開啟。這些敘述描述程式行為，不代表雲端版本已更新；目前狀態見 [交接文件](docs/HANDOVER.md)。

## 文件

| 文件 | 用途 |
| --- | --- |
| [交接文件](docs/HANDOVER.md) | **接手先讀**：目前狀態、權限與機密清單、已知限制、待決事項 |
| [操作、執行與部署手冊](docs/OPERATIONS.md) | 手動啟動、登入、資料管理、部署、故障排查與回復 |
| [架構說明](docs/MIGRATION_REPORT.md) | Client／Admin 拆分、資料與權限邊界、工作佇列設計 |
| [main 的 Hosting 預覽](docs/MAIN_PREVIEW.md) | `main-preview` channel 的部署與限制 |
| [聊天契約](docs/technical-report/CHAT_CONTRACT.md)、[API 參考](docs/technical-report/API_REFERENCE.md) | v4 請求／回應與 HTTP 端點 |
| [資料工具](admin/backend/admin_app/scripts/README.md) | 舊資料移轉、來源 manifest 與匯入 CLI |
| [CI 權限審查](infrastructure/iam/CI_DEPLOYMENT_REVIEW.md) | WIF 信任條件及各項部署權限 |
| [技術文件與閱讀版手冊](docs/technical-report/README.md) | 技術報告與手冊原稿；HTML／PDF 由 GitHub Release 提供 |
| [本機遮罩驗證](docs/browser-masking/README.md) | Admin 規則與 Tiny NER 的模型準備、測試及評分 |
| [第三方授權](THIRD_PARTY_NOTICES.md) | 相依套件與 Tiny NER（GPL-3.0）的授權說明 |

## 目錄與環境

```text
client/          # Client 前端、後端與測試
admin/           # Admin 前端、後端、資料工具與測試
shared/          # 共用 Python（rag_core）與前端（型別、聊天 UI、遮罩）
infrastructure/  # Firestore Rules、索引、IAM 與 Emulator 測試
scripts/         # 建置、部署、資源設定與維運工具
docs/            # 交接、架構與操作文件
```

部署目標的 Firebase／GCP 專案 ID 只設定在根目錄 `project.env`，其他名稱都由它推導（見 [操作手冊〈專案 ID〉](docs/OPERATIONS.md#專案-id)）。

`data/` 與 `resource/` 是本機保留的知識庫來源與研究工作簿，不在 Git 中，需另行移交（見交接文件）。`build/`、`dist/`、`dist-preview/` 由建置產生。

| 環境 | 業務資料庫 | Admin 私有庫 | Functions |
| --- | --- | --- | --- |
| production | `(default)` | `admin-private` | `api`／`admin_api` |
| preview | `app-dev` | `admin-private-dev` | `api_preview`／`admin_api_preview` |

瀏覽器不直接存取 Firestore。Admin 使用 HttpOnly session cookie，每次請求重新驗證。

## 快速開始（本機）

所有指令都在專案根目錄執行。本機服務連到**雲端 dev 資料**（`app-dev`、`admin-private-dev`、dev bucket），不是離線 Emulator：在本機 Admin 儲存的設定、Prompt、Skills 與檢索資料會影響線上 Preview，聊天與 embedding 會實際呼叫模型並可能計費。

1. 準備 Python 3.13、Node.js 24 以上（見 `.nvmrc`）、pnpm 10.27.0、uv 與 Google Cloud CLI，然後安裝相依套件：

   ```bash
   uv sync --frozen --all-extras
   pnpm install --frozen-lockfile
   ```

   若 `.venv` 是在別的路徑建立後才搬到此處，裡面的 `pytest`、`flask` 等指令會指向舊路徑。刪除 `.venv` 後重新執行 `uv sync --frozen --all-extras`，或改用 `uv run --frozen python -m pytest` 這類寫法。

2. 以有 dev Firestore 與 dev bucket 權限的帳號建立 Application Default Credentials：

   ```bash
   gcloud auth application-default login
   gcloud auth application-default set-quota-project anti-harassment-bot
   ```

3. 建立本機設定檔，填入 `OPENROUTER_API_KEY`（不要寫進 Git 或任何 `VITE_*` 變數）：

   ```bash
   mkdir -p .admin-local
   [ -f .admin-local/development.env ] || (umask 077; cp .env.example .admin-local/development.env)
   ```

4. 一次啟動兩個 API 與兩個網頁：

   ```bash
   uv run --frozen python scripts/dev.py
   ```

   - Client：<http://127.0.0.1:5173>（API 8000）
   - Admin：<http://127.0.0.1:5174>（API 8001），密碼向管理者取得
   - `Ctrl-C` 停止全部服務；`--check` 只檢查相依與 port；`--env-file` 指定其他設定檔。
   - 腳本自動啟用本機工作執行器，只處理本次啟動後新提交的工作。

需要個別控制每個程序、初始化密碼或排查錯誤時，見 [操作手冊](docs/OPERATIONS.md#本機安裝與啟動)。

## 檢查

```bash
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen python -m pytest -q
pnpm -r lint
pnpm -r test
pnpm -r build
uv run --frozen python scripts/build_functions.py
uv run --frozen python scripts/quality/check_source_package.py
```

Rules 驗收另外執行 `pnpm test:rules`，需 Java 21 與 Firebase CLI 15.33.0。回歸測試使用替身，不證明模型品質、雲端部署或 IAM 實際權限。

## 授權

[Apache License 2.0](LICENSE)。相依套件與 Tiny NER 模型（GPL-3.0，僅 Admin 驗證頁使用、不在 repository 中）見 [第三方授權](THIRD_PARTY_NOTICES.md)。外部資料與服務另依其來源條件使用。本系統提供初步資訊與支持，個案法律結論仍需適當的專業判斷。
