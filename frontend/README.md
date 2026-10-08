# 前端維護

React、TypeScript、Vite、Tailwind CSS；使用 Node.js 24 / pnpm 9。從專案根目錄執行：

```bash
pnpm --dir frontend install --frozen-lockfile
VITE_API_BASE_URL=http://127.0.0.1:5000/api pnpm --dir frontend dev --host 127.0.0.1
pnpm --dir frontend lint
pnpm --dir frontend test:coverage
pnpm --dir frontend build
```

Flask 預設本機 port 為 5000。既有 `.env` 保留作過渡用途；新環境參考 `.env.example`，不覆寫既有設定。沒有明確設定 API URL 時，程式仍有舊 8000 fallback，因此應始終指定 `VITE_API_BASE_URL`。

`dist/` 是可重建的公開產物。`VITE_*` 會進入瀏覽器，僅能放公開設定，不能放 Admin token、服務帳戶私鑰或模型 API key。Firebase Web app 設定與 Admin service-account 私鑰不同。

應用維護與驗收方式見 [開發維運手冊](../docs/technical-report/manuals/開發維運手冊.md)，過渡與部署見 [交接文件](../docs/HANDOVER.md)。
