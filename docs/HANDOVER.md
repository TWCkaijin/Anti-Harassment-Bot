# 過渡期交接與客戶環境遷移

版本日期：2026-10-08。內容依交付時本地程式、設定檔與 workflow 盤點；本次沒有登入雲端確認服務可用性、金鑰有效性、帳務或 IAM 權限。

## 1. 過渡期服務與設定

| 項目 | 本地設定所指向的環境 |
| --- | --- |
| Firebase 預設 project | anti-harassment-bot（.firebaserc） |
| Functions region | asia-east1 |
| 正式前端 | https://anti-harassment-bot.web.app |
| 正式 API base | https://asia-east1-anti-harassment-bot.cloudfunctions.net/api |
| Preview API base | https://asia-east1-anti-harassment-bot.cloudfunctions.net/api_preview |
| Preview Hosting | dev-preview channel，完整網址以該次部署輸出為準 |
| Runtime Config | runtime_config/app_main、runtime_config/app_dev；實際可由環境設定覆寫 |
| 共用情境設定 | scenario_scripts；現行 preview / production 並非完整專案隔離 |
| 模型入口 | OpenRouter；實際模型及生成設定可能由 Firestore Runtime Config 覆寫本地預設 |
| RAG | Firestore 的法規、判決與救濟 collections；索引及 embedding 模型需一併交接 |

網址是設定紀錄，不代表本次已確認線上部署版本。Preview workflow 的有效期限參數為 30 天，舊留言文字可能仍顯示 7 天；應以實際部署結果為準。

## 2. 隨附的私人設定

| 位置 | 用途 | 遷移時處理 |
| --- | --- | --- |
| 根目錄 .env | 模型 API key、Admin key、模型與 RAG fallback、CORS、runtime document 等 | 在客戶環境填入新服務設定 |
| 根目錄 .env.local | 原機器網路／DNS 輔助設定 | 依客戶網路需求確認是否仍需使用；不視為通用啟動設定 |
| frontend/.env | API base 與 Firebase Web Analytics 公開設定 | 修改客戶 API／Web app 設定並重新 build |
| firebase_admin.json | Firebase Admin service-account 憑證 | 過渡期沿用；客戶環境改用客戶身分或其 runtime ADC |
| .firebaserc | Firebase project alias | 客戶遷移時切換 project |
| .git/ | 完整原開發歷史、remote 與設定 | 本次原樣保留；推送前先確認目標遠端與資料範圍 |

機密值只存在隨附原檔，不在本文件複製。這些檔案留在私人交付目錄，不納入新 Git 提交或前端產物。Firebase Web API key 是瀏覽器公開設定，不能據此推論 Admin 或資料庫權限已交接。

## 3. 啟動與管理

安裝／本機啟動見 [專案 README](../README.md)。過渡期不要先複製範本覆蓋既有 `.env`。本機運作仍會呼叫設定中的外部服務；Functions emulator 也不代表 Firestore 已使用 emulator。

管理員由側欄 Admin 入口輸入 `.env` 對應的管理 key。每次操作須先核對 API 位置及 runtime document。管理者手冊說明批次、行為測試與設定快取。啟動應用、管理診斷及批次預測可能使用現有帳戶額度；本次交付驗收均採假值及合成回應。

## 4. 客戶環境遷移順序

1. 確認接收人、過渡期限、現有帳戶費用負責人，以及新 Firebase / Google Cloud / OpenRouter 的帳戶擁有人。
2. 建立客戶專案、服務身分、帳務及必要權限；分開規劃 preview / production 的 project、runtime document、Skills 與 secrets。
3. 在客戶控制的位置保存新金鑰，設定根目錄環境變數及 Admin 驗證；不要將後端秘密放入 VITE_*。
4. 依 [法源維護說明](../resource/legal/README.md) 準備資料與索引。先 dry-run，核對來源版本、embedding 維度、collections 和回復紀錄，再另行安排匯入。
5. 修改 .firebaserc、部署 workflow 的 project/API URL、前端環境變數及後端 CORS 正式／preview allowlist；目前程式的預設 CORS 也包含舊專案，須一併檢查。保持完整 origin 白名單。
6. 在客戶 CI 設定部署身分、模型／Admin secrets 及選用的 Analytics Variables。現有 workflow 仍使用 FIREBASE_TOKEN、FIREBASE_SERVICE_ACCOUNT_JSON、OPENROUTER_API_KEY、ADMIN_API_KEY，未於本次更換認證機制。
7. 核對 Functions source 排除規則、runtime 環境變數注入及 ADC。service-account JSON 已不應隨 source 上傳，程式在檔案不存在時有 ADC fallback；客戶 runtime 身分仍需相應 Firestore 權限。
8. 先驗證客戶 preview 的健康檢查、SSE、摘要、圖片開關、管理權限、RAG 及錯誤處理，再安排正式切換。測試失敗先回復 URL／設定；資料寫入依匯入 journal 回復，不能用本地程式備份取代雲端資料備份。
9. 確認客戶流量及費用正常後，依約定移除過渡身分權限、更換或停用舊模型／Admin key 與 service-account key，並通知相關維運人員。本次未執行上述雲端步驟。

## 5. 權責與待確認項目

| 項目 | 交接狀態 |
| --- | --- |
| 客戶接收人與維運窗口 | 待確認 |
| 過渡起訖日期及終止條件 | 待確認 |
| Firebase / Google Cloud / OpenRouter 費用負責人、預算與額度 | 待確認 |
| 隨附 key 是否仍有效、權限範圍與到期時間 | 待確認；未以金鑰呼叫服務驗證 |
| 雲端資料量、索引、備份與還原演練 | 待確認 |
| 正式部署版本、來源更新日與服務品質 | 待確認；本地原始碼不構成線上驗收證據 |
| 舊金鑰停用執行人及日期 | 待確認，應在客戶切換驗收後安排 |

## 6. 私人交付與公開部署

本次原樣保留 resource、Git 歷史、兩套虛擬環境及現有機密；因此整個交付資料夾不能當作 Firebase Hosting 公開目錄。Hosting 仍只發布 frontend/dist。Functions 依 firebase.json 的 ignore 清單排除機密檔、前端、測試、資料、文件及本機環境；打包清單檢查見 [交付驗收](DELIVERY.md)。

Functions source 排除 .env 檔不等於取消 runtime 所需環境變數。既有 workflow 的 dotenv 注入、雲端帳號及 runtime 啟動仍須在下一次部署前驗證；本次只檢查本地打包清單，未實際部署。
