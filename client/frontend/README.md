# Client 前端

React、TypeScript、Vite、Tailwind CSS。使用 Node.js 24、pnpm 10.27.0；從專案根目錄執行：

```bash
pnpm install --frozen-lockfile
pnpm --filter @rag/client dev
pnpm --filter @rag/client lint
pnpm --filter @rag/client test
pnpm --filter @rag/client build
```

本機前端為 `http://127.0.0.1:5173`，`/api` 由 Vite 代理至 `http://127.0.0.1:8000`。部署腳本將 `VITE_API_BASE_URL` 指向对应環境的 Client Function，保留原有直接 API／SSE 連線；Hosting 也保留同源 `/api` rewrite。

共用 TypeScript 套件位於 `shared/frontend`，以 `@rag/shared` workspace 依賴引用。Client 不包含管理入口；管理操作使用獨立的 `admin/frontend`。

`dist/` 是可重建的公開產物。`VITE_*` 會進入瀏覽器，只能放公開設定，不能放管理密碼、session 簽章金鑰、服務帳戶私鑰或模型 API key。Firebase Web app 設定屬公開設定。

合成瀏覽器驗證可先啟動 `client/tests/browser_fixture_server.py`（port 5055），以 `VITE_API_BASE_URL=http://127.0.0.1:5055/api pnpm --filter @rag/client dev` 啟動前端，再執行 `client/tests/browser_homepage_smoke.mjs --stage after`。`PLAYWRIGHT_MODULE` 可指定已有的 Playwright 安裝；驗證會封鎖非本機請求。

部署與維運見根目錄 [README](../../README.md) 與 [操作手冊](../../docs/OPERATIONS.md)。
