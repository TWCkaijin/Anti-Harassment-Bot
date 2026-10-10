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

## 開始使用

使用 Python 3.13、Node.js 24、pnpm 10.27.0、uv。由根目錄執行：

```bash
uv sync --frozen --all-extras
pnpm install --frozen-lockfile
```

依 [本機啟動手冊](docs/OPERATIONS.md#本機安裝與啟動) 設定獨立的本機環境，再啟動 Client API `8000`、Admin API `8001`、Client 網頁 `5173`、Admin 網頁 `5174` 及 Admin worker。既有 `.env`、`.env.local`、`firebase_admin.json`、`data/`、`resource/` 原樣保留；部署包由白名單產生，不包含上述機密或原始資料。

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
