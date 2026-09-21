# Project TODO

本文件整理 2026-08-11 程式碼圖譜與前後端、RAG、安全、CI/CD 審視所發現的問題。

狀態標記：

- `待處理`：尚未開始。
- `本輪處理`：本次工作範圍，完成後改為 `已完成` 或拆出外部待辦。
- `外部設定`：需要 Firebase、Google Cloud 或 GitHub 管理權限，不能只靠此 checkout 完成。

本輪已驗證：後端 107 項 pytest、前端 23 項 Vitest 與 coverage gate、Ruff、ESLint 及 production build。

## P0 — 上線前必須處理

### 1. 部署秘密與 Functions source 邊界（待處理）

- [ ] 停止在部署工作目錄產生含秘密的 `.env` 與 `firebase_admin.json`。
- [ ] Functions runtime 改用 ADC；OpenRouter/Admin secrets 改用 Firebase/Google Secret Manager。
- [ ] GitHub 部署身分改用 OIDC/Workload Identity Federation，移除長效 `FIREBASE_TOKEN` 與 service-account JSON。
- [ ] 將 Functions source 收斂到專用目錄，明確排除 `.env*`、credential JSON、`venv/`、cache、graph 與測試產物。
- [ ] 產生並檢查實際 Firebase source manifest；若舊 workflow 曾部署，輪替 service account、OpenRouter key 與 Admin token。

### 2. 公開 API 邊界（本輪已處理；仍有外部設定）

- [x] 移除任意 Origin 與 `Access-Control-Allow-Credentials` 反射，統一使用 exact allowlist 與嚴格錨定的 Firebase preview pattern。
- [x] 公開 500 回應只回 opaque error ID；完整 traceback 僅保留在伺服器端日誌。
- [x] 設定全域 request-body 上限，以及伺服器端圖片 MIME、magic bytes、base64 與 decoded-size 驗證。
- [x] 為昂貴的 chat endpoint 加入可設定 App Check 驗證與基礎 process-local rate-limit defense。
- [ ] `外部設定` 正式環境啟用 App Check enforcement，並配置跨 instance/global rate limit、預算告警及 circuit breaker。

### 3. Admin 身分與環境隔離（待處理）

- [ ] 以短效 Firebase Auth/IAM claims 取代永久靜態 Admin root token。
- [ ] 停止將 Admin token 存入 `localStorage`；加入登出、閒置逾時與不可偽造的 audit identity。
- [ ] Preview/production 使用不同 Firebase project、service identity、Admin secret、runtime collection 與 scenario collection。
- [ ] 建立受保護的 GitHub preview/production environments 與 production approval gate。

## P1 — 正確性、隱私與 RAG 可信度

### 4. 前後端對話契約（本輪已完成）

- [x] 統一 image-only request：有合法圖片時允許空文字，禁用圖片時回明確契約錯誤。
- [x] 送 history 前以完整 turn 排除 cancelled、error、image-only orphan 與空白訊息，避免停止回覆後永久 422。
- [x] 統一 current user、history user 與 assistant reply/history 的單則長度上限。
- [x] 前後端都為 history 設定總字元 budget，前端不拆 turn 也不靜默截斷。
- [x] 統一 API problem detail 型別，UI 可處理 `detail` 與結構化 `errors`，不再顯示 `[object Object]`。

### 5. 隱私宣稱與真實資料流（待處理）

- [ ] UI 不再固定宣稱「匿名安全」；公開目前 anonymization/image capability 狀態。
- [ ] 圖片預設關閉；加入明確外送同意、EXIF 移除，以及 OCR/影像 PII 遮罩策略。
- [ ] 補強姓名、地址、帳號等 PII 偵測，或明確降低產品匿名化宣稱。
- [ ] production 日誌不得保存完整 RAG query、對話或漏網 PII。
- [ ] 為敏感 localStorage 對話提供私密模式、保留期限、可靠 quota fallback 與清除說明。

### 6. RAG 失敗語意、grounding 與來源（本輪核心修正已完成）

- [x] Embedding、Firestore、vector index 故障改為 typed retrieval failure，不得偽裝成「查無資料」。
- [x] 只有真正成功且零筆結果時才能產生 no-data 訊息；基礎設施錯誤回 503/retryable。
- [x] 多 tool-call 的 `rag_used` 會累積，並保留每次查詢的結果計數。
- [x] 保存 vector distance，支援可校準的 relevance threshold 與跨 collection 全域排名。
- [x] 法律、期限、程序等高風險回答由伺服器要求 retrieval/grounding，即使 client 關閉 RAG 也不可繞過。
- [x] RAG context 加入不可信資料邊界與 prompt-injection 防護。
- [ ] 擴充 citation contract：法條/判決段落、版本、來源 URL、更新日期可由使用者核對。
- [ ] 建立 corpus manifest、checksum、embedding model/dimension/version 與 stale-document reconciliation。
- [ ] 建立離線 Recall@k、MRR/nDCG、groundedness、危機情境與 prompt-injection 評測集。

### 7. Runtime config 與營運控制（本輪核心修正已完成）

- [x] Firestore 讀取端與 Admin 寫入端共用同一型別與範圍驗證。
- [x] 壞欄位逐欄回退到安全預設並告警；不得因單一錯值讓服務崩潰或把字串 `"false"` 當成 `true`。
- [x] Firestore 暫時失敗時保留 last-known-good；隱私／診斷／圖片開關採 fail-closed 策略。
- [x] 實作真正的 maintenance 503；不要讓 `maintenance_message` 成為無作用設定。
- [x] `ENVIRONMENT=production` 時強制關閉 development diagnostics。
- [ ] 加入 runtime revision、history、rollback 與 optimistic concurrency。
- [x] 將 provider timeout、rate limit、schema、config 與 retrieval 錯誤分類；只有可重試錯誤會進入前端自動重試，429 尊重等待視窗不立即重送。

## P2 — 測試、CI/CD 與可維護性

### 8. 自動測試、CI gate 與架構熱點（本輪核心修正已完成）

- [x] 加入 frontend test runner、Testing Library 與 coverage thresholds；覆蓋 history、取消、image-only、API error、retry 與 Admin 重要流程。
- [x] 補 Firebase HTTP wrapper、CORS、payload、maintenance、runtime config 與 RAG failure backend tests。
- [x] CI 預設 `contents: read`，移除 PR 測試中的真實 OpenRouter secret 與自動寫入權限。
- [x] Ruff/format 範圍納入 `main.py` 與 `scripts/`，並加入 frontend test/coverage job。
- [x] Deploy 必須等待 CI 成功；加入 branch scoped concurrency，preview 留言拆成最小權限 job。
- [x] `firebase.json`、`.firebaserc` 與 workflow-only 變更也要觸發必要部署。
- [x] 從 `useConversation` 拆出 turn-aware history/request builder 與邊界測試。
- [ ] 繼續拆分 `useConversation` 的 persistence、transport、retry 與 image lifecycle。
- [ ] 拆分 `OpenRouterAgent.run` 的 prompt、tool execution、retrieval 與 response validation。
- [ ] 逐步拆分 `Sidebar`、`MessageItem`、`AdminPanel`，降低 graph hotspot blast radius。

## 其他已發現問題

### 前端與 UX（待處理）

- [ ] 修正圖片 blob URL 在送出後立即 revoke，且重新整理後失效的生命週期問題。
- [ ] 對話資料改用適合大型 Blob/結構資料的儲存方式，並避免無期限明文保存 debug 資訊。
- [ ] Settings/Admin panel 加入 dialog semantics、focus trap、Escape 與焦點復原。
- [ ] Sidebar 對話列改成可鍵盤操作；訊息與 loading 狀態加入 live region。
- [ ] 移除固定五秒顯示延遲，改用真實進度或串流；修正 `h-screen`/`h-dvh` 行動 viewport 衝突。
- [ ] Admin reset 加二次確認、環境提示與 unsaved-change guard。
- [ ] 語言切換時同步 `<html lang>`，讓 113 等操作呈現可存取的連結/按鈕語意。

### RAG、資料與營運（待處理）

- [ ] 為 249 份未追蹤 corpus 建立可重現的外部資料版本與 provenance manifest。
- [ ] 對硬編碼法律／求助電話加入 authoritative source 與 `verified_at`，建立定期複核流程。
- [ ] 將 liveness 與 readiness 分離；readiness 應檢查必要設定與依賴但不得洩漏秘密。
- [ ] 評估同步 Flask + `asyncio.run()` + 全域 AsyncOpenAI 的跨 event-loop/高併發風險。

### 依賴、文件與供應鏈（待處理）

- [ ] 將 Ruff、pytest 等工具移到 dev dependency group，移除重複 `dotenv` 套件。
- [ ] 固定 uv、Firebase CLI 與 GitHub Actions 的版本／commit SHA，加入 dependency/CVE 掃描。
- [ ] 統一 pnpm 工具鏈；修正 `lint.sh` 中 npm/npx 與檢查範圍漂移。
- [ ] 修正 preview 30 天與 README/PR comment 7 天的矛盾。
- [ ] 更新 `frontend/.env.example` 的 Flask/5000 API URL，替換 Vite 範本 README，將 `implementation.md` 改成現況 ADR/完成記錄。
