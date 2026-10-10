# 可重用品質工具

此目錄保留提示／回答比較、評讀報告及人工分數整理工具。既有實驗輸入與結果已外部封存，交付不依賴該封存位置。

- main_alignment.py 需要明確的 --engine、--data、--env-file 與 --variant；實際模型執行還需 --allow-live，可能計費。
- report_main_alignment.py 與 report_adaptive_response.py 接收結果資料夾，離線生成報告。
- score_main_alignment.py 接收 answer key 及人工評分，不自行產生專家結論。
- render_adaptive_review.mjs 必須明確傳入結果資料夾；缺少參數會顯示用法，不再讀取已封存的歷史路徑。

新測試以 admin/tests/reliability 與管理端行為測試為入口；不要將歷史評讀數字當成最新版本驗收。

- check_source_package.py --output /tmp/functions-source.json：離線列出 Functions 排除規則下的檔案並檢查必要模組與禁止項目；這不是實際 Firebase CLI 上傳封包或部署驗證。
