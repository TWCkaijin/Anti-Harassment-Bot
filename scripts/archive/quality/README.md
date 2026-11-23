# 歷史評讀工具（已封存）

這些工具用於早期的提示／回答比較與人工評讀，不在 CI 或日常流程中執行。既有實驗輸入與結果已外部封存，交付不依賴該封存位置，也不要把它們產生的歷史數字當成目前版本的驗收結果。

- `main_alignment.py` 需要明確的 `--engine`、`--data`、`--env-file` 與 `--variant`；實際模型執行還需 `--allow-live`，可能計費。
- `report_main_alignment.py` 與 `report_adaptive_response.py` 接收結果資料夾，離線生成報告。兩者須留在同一資料夾（後者匯入前者）。
- `score_main_alignment.py` 接收 answer key 及人工評分，不自行產生專家結論。
- `render_adaptive_review.mjs` 必須明確傳入結果資料夾；缺少參數會顯示用法。
