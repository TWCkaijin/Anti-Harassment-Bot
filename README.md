# 性騷擾防治智能 AI 助手

以 React / Vite 前端、Flask API、Firebase Functions / Firestore 與 OpenRouter 組成的諮詢與法規引導系統。提供串流回覆、可編輯情境摘要、瀏覽器文字遮罩、RAG 檢索及管理端驗證工具。

本服務提供初步資訊與支持，不取代專業諮詢或正式申訴程序。來源引用與自動評分均不代表個案法律結論已經人工確認。

## 交付入口

| 文件 | 內容 |
| --- | --- |
| [交接與遷移](docs/HANDOVER.md) | 過渡期環境、憑證用途、客戶環境遷移與權責待確認項目 |
| [正式文件索引](docs/technical-report/README.md) | 技術報告及使用者、管理者、開發維運手冊；Markdown / HTML / PDF |
| [API 與設定參考](docs/technical-report/API_REFERENCE.md) | SSE、管理設定、環境變數及記錄方式 |
| [已知限制](docs/KNOWN_LIMITATIONS.md) | 部署、隱私、權限、資料與驗證界線 |
| [交付驗收](docs/DELIVERY.md) | 本次檢查結果、保留範圍與交付檔案清單 |
| [資料說明](data/README.md) | 知識庫、原始來源與匯入前注意事項 |

`resource/`、`.git/`、`venv/`、`.venv/`、現有 `.env` 設定及 Firebase 憑證均按交付要求原樣保留。這是含過渡期機密的私人交付目錄；Git 歷史亦保留。機密仍不納入新的 Git 提交，也不屬於公開前端產物。

## 安裝與啟動

工具版本：Python 3.13、Node.js 24、pnpm 9、uv；部署另需 Firebase CLI。新機器請依鎖定檔重建環境，隨附虛擬環境可能含原機器路徑，不保證可直接搬用。不要覆寫過渡期既有環境檔。

```bash
uv sync --frozen
pnpm --dir frontend install --frozen-lockfile
```

在第一個終端由專案根目錄啟動後端：

```bash
uv run --frozen flask --app backend.app.main run --host 127.0.0.1 --port 5000
```

在第二個終端啟動前端；以下環境變數只覆寫這次啟動，不改動隨附 `.env`：

```bash
VITE_API_BASE_URL=http://127.0.0.1:5000/api pnpm --dir frontend dev --host 127.0.0.1
```

瀏覽器連到 Vite 顯示的位址。API 健康檢查為 `GET /api/v1/health/`，聊天為 `POST /api/v1/chat/`。本機應用仍可能透過隨附憑證連到正式模型與 Firestore；本機啟動不等於離線模式。

## 核心功能

- v4 自然段落串流與單一文字摘要；保留 v1-v3 相容契約。
- 送出前文字遮罩與可選人工預覽；圖片原圖不會自動去識別。
- 法規、判決與求助資源檢索，支援 provider 排序及可選 reranking。
- 管理端 Runtime Config、Prompt、Skills、單筆診斷、Excel 三法批次預測與多輪行為測試。
- 瀏覽器本地對話、匯出、刪除與選擇性 Analytics。

## 專案結構

| 目錄 | 用途 |
| --- | --- |
| backend/ | API、Agent、RAG、應用情境指令與資料匯入工具 |
| frontend/ | 網頁應用、素材及前端測試 |
| tests/ | 後端測試、瀏覽器合成流程及可靠性工具 |
| scripts/ | 資料轉換與可重用評估工具 |
| data/ | 知識庫與原始資料，完整保留 |
| resource/ | 情境 Excel、既有結果及法源快照，完整保留 |
| docs/ | 正式技術文件、交接與驗收 |
| .github/workflows/ | 既有 CI 與 main/dev 部署流程 |

## 測試與部署

測試應在不含真實環境檔及憑證的獨立副本中執行，使用假 key 與測試替身。一般安裝後的檢查命令如下：

```bash
uv run --frozen ruff check main.py backend/ scripts/ tests/
uv run --frozen ruff format --check main.py backend/ scripts/ tests/
OPENROUTER_API_KEY=ci-placeholder-not-a-real-key ENVIRONMENT=ci uv run --frozen pytest tests/ -q
pnpm --dir frontend lint
pnpm --dir frontend test:coverage
pnpm --dir frontend build
```

CI 及 main/dev 推送部署觸發方式保持原樣。現有專案識別與過渡期網址見交接文件；本次整理不部署、不變更雲端設定。正式環境的可用性、模型品質、帳務與權限須另行驗證。

## 授權

保留原始 [LICENSE](LICENSE)（Apache License 2.0）。資料集、外部服務及引用来源的條件須依各自來源確認。
