# 交付整理與驗收

交付日期：2026-10-08。此次直接整理原專案，沒有提交 Git、推送、部署、輪替金鑰或變更雲端資料。工作開始前既有的未提交功能與修改已保留，不能把整份 git diff 當成本次整理變更。

## 保留與清理

- resource/、.git/、venv/、.venv/、根目錄 .env / .env.local、frontend/.env、firebase_admin.json 原位置及內容保留；保護範圍共 36,443 項，前後雜湊與目錄清單比對結果見 delivery-manifest.json。
- 原應用程式、原測試程式、main.py、套件宣告及鎖定檔與整理前備份逐檔一致；新增了離線 Functions source 清單檢查工具。
- 710 個內部紀錄／簡報／結果檔外部封存，約 83.3 MiB；檔案以原相對路徑及 SHA-256 保存。
- 清除 23,831 個可重建檔案，約 320.3 MiB，包含前端依賴、build、coverage、套件／Python 快取。未清理受保護目錄內的任何快取。
- data 保留既有資料，新增用途及來源說明；resource 內既有結果全部保留。
- README、正式文件、範本、忽略與 source 排除規則已整理。部署觸發方式、project alias 及現有應用設定未改動。

## 程式與資料驗證

驗證在專案外、不含真實環境檔及憑證的副本執行；Python 3.13.6、Node.js 26.0.0。依賴從既有環境複製，沒有修改原 venv。CI 仍指定 Node.js 24，本次未另行驗證該 Node 版本或全新網路下載安裝。

| 檢查 | 本次結果 |
| --- | --- |
| 後端 pytest | 922 項通過；移除外部封存輸出後再次通過 |
| Ruff check / format check | 通過；最終 92 個 Python 檔案格式符合 |
| 前端 ESLint | 通過 |
| Vitest | 42 個測試檔、612 項通過 |
| Coverage | statements 85.59%、branches 83.84%、functions 84.82%、lines 90.43%；門檻通過 |
| TypeScript / Vite production build | 通過；仍有主 chunk 大於 500 kB 的既有提示 |
| 候選資料重建 | 634 筆；專家 gold 0，不視為模型品質驗收 |
| 四層離線評估 | 8 個合成案例完成；故意錯誤案例保留，不以退出碼代表品質通過 |
| 離線 benchmark | 3 個合成記錄完成，不代表真實延遲與成本 |

## 瀏覽器與匯出

本機 Chrome，桌面 1440 px 與手機 390 px；API 攔截為合成回應，封鎖非本機請求。

- 一般 v4 聊天：確認前不外送、取消不送出、email 遮罩、SSE、文字摘要更正、重新回答與重載後摘要／訊息保留；兩種寬度均無 page error。
- Excel 批次：整批預覽、固定設定、暫停／續跑、頁籤保留、無法預測原因與下載。
- 行為測試：每種寬度 7 輪請求、前輪資料承接、逐輪確認、人工覆核、離線匯入及證據變更使舊覆核失效；設定寫入 0 次。
- 4 份瀏覽器下載 Excel 以 openpyxl 獨立讀回；批次含原工作表與統計表，行為測試含 5 案、7 輪及設定表，公式儲存格均為 0。

現有 browser_smoke / browser_processing_smoke 使用較早 v2 fixture；本次一般聊天採另外保存的 v4 合成驗收脚本，不把旧 fixture 的結果套用到新版。實際測試腳本及證據存於私人封存的 verification-evidence。

## 文件與部署邊界

| 檢查 | 本次結果 |
| --- | --- |
| 正式文件 | 技術報告 4 頁，使用者／管理者／開發維運手冊各 3 頁，共 13 頁 |
| PDF | 中文抽取、頁數與雜湊檢查通過；已逐頁渲染並檢視中文、表格與圖稿 |
| HTML | 4 份文件 × 桌面／手機，共 8 組通過；未水平溢出、無 page error、禁止 HTTP 外送 |
| 本機文件連結 | 正式文件與維護說明的相對路徑檢查通過 |
| 機密掃描 | 正式程式／文件及合成設定的前端產物未找到本次保留 key 的實際值；未將 resource 或 Git 歷史改寫或宣稱已完整去敏 |
| Functions source | 本地排除規則清單檢查通過，必要 runtime 模組與內建 Skills 保留；禁止項目排除 |

[source-package-manifest.json](source-package-manifest.json) 是本地設定規則下的檔案清單，不是 Firebase CLI 實際上傳 ZIP。下次部署仍需核對 CLI 打包、dotenv runtime 注入、ADC 權限與雲端啟動；本次沒有執行這些雲端操作。

## 封存與回復

私人封存位於本專案同層的 RAG_HARASS_BOT-private-archive-20261008，不屬於客戶交付目錄。originals 保存整理前非受保護內容；originals-manifest.json、archived-items.json、generated-cleanup-manifest.json 保存追溯清單。保護範圍前後完整清單也在封存目錄。原始 node_modules 與一般快取未完整備份，屬於可重建產物；簡報內依賴連結另保存於 supplemental。

此備份不能取代雲端資料备份。回復時依 manifest 選擇原路徑，先核對目前檔案是否已有新修改，避免整包覆蓋。

交付目錄清單見 [delivery-manifest.json](delivery-manifest.json)；為避免自我雜湊循環，manifest 本身不列自己的 SHA-256。大型受保護目錄以項目數與清單摘要呈現，完整逐檔紀錄保存在私人封存。過渡與客戶遷移見 [HANDOVER](HANDOVER.md)，未完成事项見 [已知限制](KNOWN_LIMITATIONS.md)。
