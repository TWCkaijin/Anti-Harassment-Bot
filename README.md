# 性騷擾防治智能 AI 助手

[![Deploy Preview (dev)](https://github.com/TWCkaijin/Anti-Harassment-Bot/actions/workflows/deploy-dev.yml/badge.svg?branch=dev)](https://github.com/TWCkaijin/Anti-Harassment-Bot/actions/workflows/deploy-dev.yml)
[![Deploy Production (main)](https://github.com/TWCkaijin/Anti-Harassment-Bot/actions/workflows/deploy-main.yml/badge.svg?branch=main)](https://github.com/TWCkaijin/Anti-Harassment-Bot/actions/workflows/deploy-main.yml)

性騷擾防治智能 AI 助手是一個面向台灣使用情境的諮詢與法規引導服務。系統以創傷知情的對話方式回應使用者，並透過 RAG 檢索台灣性騷擾防治相關法規、通報管道、救濟資源與判決資料，協助使用者取得更清楚、可行且具備隱私保護的初步資訊。

> 本專案僅提供資訊整理、同理支持與流程引導，不能取代律師、心理師、醫師、社工或正式申訴與報案程序。

## 部署狀態與網址

| Branch | CD Workflow | 部署環境 | 前端網址 | API URL |
| --- | --- | --- | --- | --- |
| `dev` | [Deploy - Preview](https://github.com/TWCkaijin/Anti-Harassment-Bot/actions/workflows/deploy-dev.yml) | Firebase Hosting Preview Channel | <https://anti-harassment-bot--dev-preview.web.app> | <https://asia-east1-anti-harassment-bot.cloudfunctions.net/api_preview> |
| `main` | [Deploy - Production](https://github.com/TWCkaijin/Anti-Harassment-Bot/actions/workflows/deploy-main.yml) | Firebase Hosting Production | <https://anti-harassment-bot.web.app> | <https://asia-east1-anti-harassment-bot.cloudfunctions.net/api> |

`dev` 分支部署至 Firebase Hosting preview channel，workflow 設定有效期限為 7 天；`main` 分支部署至正式 Firebase Hosting 網址。

## 核心功能

- 創傷知情對話：以溫和、不批判、避免責怪受害者的語氣提供支持與資訊。
- 隱私去識別化：後端在送出模型請求前，會先處理手機、身分證字號、Email、信用卡、IP 等常見個資格式。
- RAG 法規檢索：整合 Firestore Vector Search，檢索法規、救濟資源與性騷擾相關判決資料。
- 本地優先紀錄：對話紀錄保存在使用者瀏覽器 LocalStorage，後端 API 採無狀態設計。
- 前後端分離：前端使用 React + Vite + TypeScript；後端使用 Flask，並透過 Firebase Functions 對外提供 API。

## 系統架構

```mermaid
graph TD
    A[React/Vite 前端] -->|送出訊息| B[Firebase Hosting]
    B -->|/api rewrite 或直接呼叫| C[Firebase Functions]
    C --> D[Flask API]
    D --> E[PII 去識別化]
    E --> F[OpenRouter Agent]
    F -->|需要法規或案例時| G[Firestore Vector Search]
    G --> F
    F --> D
    D --> A
    A --> H[瀏覽器 LocalStorage]
```

## 專案結構

```text
.
├── .github/workflows/       # CI 與 Firebase CD workflow
├── backend/                 # Flask API、Agent、RAG 與核心模組
│   ├── app/
│   │   ├── agents/          # OpenRouter agent
│   │   ├── api/             # chat / health API routes
│   │   ├── core/            # config、logger、anonymizer
│   │   └── rag/             # Firestore/default RAG implementation
│   └── scripts/             # Firestore ingestion scripts
├── data/                    # 法規、資源與判決資料
├── frontend/                # React + Vite + TypeScript 前端
├── scripts/                 # 資料轉換工具
├── tests/                   # pytest 測試
├── firebase.json            # Hosting 與 Functions 設定
├── main.py                  # Firebase Functions entrypoint
├── pyproject.toml           # Python dependency / pytest / ruff 設定
└── uv.lock                  # Python lockfile
```

## 本地開發

### 需求

- Python 3.13
- uv
- Node.js 24
- pnpm 9
- Firebase CLI

### 後端

```bash
cp .env.example .env
uv sync
uv run flask --app backend.app.main run --debug
```

後端預設會掛載：

- `GET /api/v1/health`
- `POST /api/v1/chat`
- `GET /v1/health`
- `POST /v1/chat`

### 前端

```bash
cd frontend
pnpm install
pnpm run dev
```

如需指定 API 位置，可在 `frontend/.env` 設定：

```bash
VITE_API_BASE_URL=http://127.0.0.1:5000
```

## 測試與檢查

```bash
uv run ruff check backend/ tests/
uv run ruff format --check backend/ tests/
uv run pytest tests/ -v
```

```bash
cd frontend
pnpm run lint
pnpm run build
```

## CI/CD

- `CI - Lint & Test`：所有 branch push 與 PR 都會執行後端 ruff、pytest、前端 ESLint 與 build。
- `Deploy - Preview (dev)`：push 到 `dev` 時，依變更範圍建置前端與/或部署 preview function。
- `Deploy - Production (main)`：push 到 `main` 時，依變更範圍部署正式 Hosting 與/或 production function；PR targeting `main` 也會執行同一 workflow 的檢查與部署流程設定。

## 環境變數

請從 `.env.example` 複製 `.env`，並設定必要金鑰與模型參數：

- `OPENROUTER_API_KEY`
- `OPENROUTER_BASE_URL`
- `OPENROUTER_MODEL`
- `EMBEDDING_PROVIDER`
- `EMBEDDING_MODEL`
- `RAG_COLLECTION_NAME`
- `RAG_JUDGMENT_COLLECTION_NAME`
- `RAG_REMEDY_COLLECTION_NAME`
- `RAG_RETRIEVAL_TOP_K`
- `FIREBASE_ADMIN_CREDENTIAL_PATH`
- `CORS_ORIGINS`
- `ENABLE_ANONYMIZATION`
- `ENVIRONMENT`

GitHub Actions 部署需要在 repository secrets / variables 中設定 Firebase 與 OpenRouter 相關值，例如 `FIREBASE_TOKEN`、`FIREBASE_SERVICE_ACCOUNT_JSON`、`OPENROUTER_API_KEY` 與模型設定。

## 資料匯入

Firestore RAG 資料可透過 `backend/scripts/ingest/` 內的腳本匯入：

```bash
uv run python -m backend.scripts.ingest.documents_to_firestore
uv run python -m backend.scripts.ingest.judgments_to_firestore
uv run python -m backend.scripts.ingest.remedies_to_firestore
```

也可使用整合腳本：

```bash
uv run python -m backend.scripts.ingest.all_to_firestore
```

## 安全聲明

- 請勿將 `.env`、Firebase service account、OpenRouter API key 或任何真實個資提交到 Git。
- 本服務不應被視為法律意見、醫療建議或心理諮商。
- 若使用者處於立即危險，請優先聯絡 `110`、`113`、`1955` 或所在地正式求助管道。
