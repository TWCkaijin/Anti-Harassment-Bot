# main 的獨立 Firebase Hosting 預覽

- 專案／Hosting site：`anti-harassment-bot`
- channel：`main-preview`；與 dev 的 `dev-preview` 分開。
- 後端：既有正式 `api`（`asia-east1`），共用正式資料與費用。
- 有效期限：每次部署起 30 天；以 Firebase 回傳 URL 為準，不猜測隨機後綴。
- 此部署只上傳前端與此 channel 的 Hosting 設定，不部署 Functions、資料庫或正式 live channel。

## GitHub Actions

手動執行 **Deploy — Main Hosting Preview**，branch 選 `main`。CI gate 通過後才建置及部署，網址寫入 workflow summary。以 Workload Identity Federation 取得短期憑證（與其他部署 workflow 相同的 `rag-deploy` 身分，不使用長效 token）；正式 Client Web App ID 由 `CLIENT_FIREBASE_APP_ID_PROD` 指定，必須與其 App Check 註冊一致。此 workflow 沒有 push trigger，也不會部署正式 live 或 dev 預覽。

## 本機部署

確認 checkout 在 main，並以具有 Hosting 部署權限的帳號完成 Firebase CLI 登入（`firebase login`）或 ADC（`gcloud auth application-default login`）。不要建立或使用服務帳戶金鑰檔。若正式 Client API 將強制 App Check，先匯出正式 Client Web App 的 `VITE_FIREBASE_API_KEY`、`VITE_FIREBASE_PROJECT_ID`、`VITE_FIREBASE_APP_ID` 與 `VITE_APP_CHECK_SITE_KEY`；該 Enterprise key 須允許本 channel 的實際網域。

```sh
VITE_API_BASE_URL=/api VITE_ENV=preview VITE_ANALYTICS_ENABLED=false \
  VITE_APP_CHECK_ENABLED=true pnpm --filter @rag/client build
firebase hosting:channel:deploy main-preview \
  --project "$(grep ^FIREBASE_PROJECT_ID= project.env | cut -d= -f2)" \
  --config firebase.main-preview.json \
  --expires 30d --non-interactive
```

成功後檢查回傳網址與 `<preview-url>/api/v1/health/`。健康檢查不會執行付費模型推論；實際聊天會使用正式 API。

首次啟用正式 API 的 App Check 時，先在後端驗證保持關閉的情況下更新正式 Client 前端和此 channel，使用瀏覽器確認兩站都能取得有效 token，最後才開啟正式 API 的強制驗證。舊 `main-preview` bundle 沒有 token 支援，不能在更新前開啟後端驗證。這個 channel 共用正式 API，但不提供新 Preview 後端的驗收證據。

## 轉送與限制

前端以相對 `/api` 呼叫，`firebase.main-preview.json` 將其轉送至正式 `api`。這樣不必修改正式 API 的 CORS allowlist；目前程式預設只允許 `dev-preview` 形狀的預覽 origin。設定明確寫入 Functions region，並且沒有 `pinTag`，不建立或部署新的後端版本。

Firebase Hosting 轉送有 60 秒 request timeout，不能視為與直接呼叫 Cloud Functions 完全相同的長時間 SSE 路徑。若要測試超過 60 秒的串流，需要另行授權為正式 API 加入此 preview origin 的 CORS 設定，然後改用直接 API URL。參考 [Firebase Hosting / Cloud Functions](https://firebase.google.com/docs/hosting/functions)。

預覽預設關閉 Analytics，避免混入正式使用統計。它是公開且暫時的網址，不是獨立資料環境。channel 管理方式參考 [Firebase preview channels](https://firebase.google.com/docs/hosting/manage-hosting-resources)。
