# 品質工具

此目錄保留仍在使用的品質工具。過去的提示／回答比較與評讀報告工具已移到 [scripts/archive/quality](../archive/quality/README.md)。

新測試以 admin/tests/reliability 與管理端行為測試為入口；不要將歷史評讀數字當成最新版本驗收。

`retest_workbook_cases.py` 對「AI測試紀錄與總覽-1019.xlsx」的 5 筆「待確認」與 10 筆「待修正」建立逐輪重測。預設只離線核對案例與輪次，輸出 ID、數量和工作簿雜湊；不輸出問題原文，也不呼叫模型：

```bash
uv run --frozen python scripts/quality/retest_workbook_cases.py --workbook '/path/to/AI測試紀錄與總覽-1019.xlsx'
uv run --frozen python scripts/quality/retest_workbook_cases.py --workbook '/path/to/AI測試紀錄與總覽-1019.xlsx' --prepare --run-dir /private/tmp/rag-workbook-retest
```

`--prepare` 使用瀏覽器同源規則產生權限 `0600` 的本機 `outbound_review.json`；**每一輪都須人工檢視、修改可能漏掉的姓名／機構／地址，然後逐筆設定 `approved: true`**。這份檔案可能仍含規則漏掉的個資，請只放在擁有者可讀的專案外目錄。可另外用 `--hidden-terms-file` 指定權限 `0600`、每行一個隱藏詞的本機檔案。準備不等於品質通過，也不等於授權傳輸未核對內容。

真實重測須在可用的 Admin 診斷與 Gate 設定下，指定 `--live --allow-live --password-file /path/to/preview.password --run-dir ...`。程式要求互動式終端，**每次 POST 前**把完整請求暫存至 `outbound_current.json`，等待人工開啟檢查並輸入指定確認字串；拒絕或離開時不送出。預設每案例重跑三次，可用 `--case ID` 篩選。結果只記錄案例／輪次、設定 revision、契約 hash、來源 ID、Gate 判定與延遲、回覆雜湊及字數，不存回答全文；每次嘗試留 `.started` 標記，避免意外重複付費。模型答案仍需另行人工評分。

需要人工評分時可加 `--store-masked-replies`，將經瀏覽器規則及額外人名、單位、精確地點遮罩後的回答寫入執行目錄內權限 `0600` 的 `masked_answers.jsonl`。先在本機檢視該檔是否仍含敏感內容，再評分；`results.jsonl` 仍只保存雜湊與診斷中繼資料。

這個 CLI 走 Admin 背景診斷 API，能檢查多輪內容，但不驗證 Client 的點擊、SSE、App Check、跨分頁儲存或雲端 IAM。這些須在四項 Preview 部署後以真實瀏覽器另行驗收。

- check_source_package.py --output /tmp/functions-source.json：離線列出 Functions 排除規則下的檔案並檢查必要模組與禁止項目；這不是實際 Firebase CLI 上傳封包或部署驗證。
- check_admin_bundle.mjs：在正常 Admin build 與 `vite build --mode preview --outDir dist-preview` 後執行，確認正式產物不含遮罩驗證頁／Worker，且 Preview 產物包含兩者；CI 也會執行此檢查。`--production-only` 只檢查正式產物，直接部署正式 Admin 前會執行。
