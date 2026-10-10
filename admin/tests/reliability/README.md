# 「適用法規」API 可靠性評測

五類對話行為的後台套件、多輪執行、人工覆核及 JSON／Excel 匯出，請見 [對話行為測試](../../../docs/technical-report/BEHAVIOR_TESTS.md)。此功能使用一般對話 v4，不使用法名命中評分。

三法有序預測與 Excel 副本寫入使用獨立工具 `applicable_law_predict.py`，操作與驗證見 [三法適用預測](../../../docs/technical-report/BATCH_PREDICTION.md)。它不使用本頁舊工具的法名覆蓋率或 gold 排列來產生預測。

新增的離線四層評測、634題未覆核候選資料、合成反例與v2多輪harness，請見 [FOUR_LAYER.md](FOUR_LAYER.md)。本頁保留既有法名覆蓋率工具的操作方式；法名命中不等於法律內容正確。

首可讀文字／完整回覆P50與P95、階段耗時、呼叫次數及成本待測工具，請見 [BENCHMARK.md](BENCHMARK.md)；預設離線，報告不包含案件原文。

`applicable_law_api_eval.py` 是獨立的黑箱評測工具。它只讀取 XLSX，透過既有
chat HTTP API 提問，不會 import 或改動 backend/agent。問題只包含情境，不包含
「適用法規」或補充標籤。

## 資料設定

工作簿須由操作人以 `--xlsx` 明確指定，不再預設讀取 repo 內的資料。自動化回歸測試使用合成 fixtures，不使用這些外部工作簿；詳見 [測試資料](../../../shared/tests/fixtures/README.md)。

內建 profile 會避免重複案例：

- `chatgpt_scene.xlsx / Sheet1`：A=`適用法規`、B=`情境`。
- `gemini_scene.xlsx / 工作表1`：A=`適用法規`、B=`場景`、E=無標題的補充法規。
  `工作`、`校園`、`社會` 三張拆分表不會再測一次。

主標籤和補充標籤會先轉成法規集合。例如 `性工法` + `亦涉刑法` 會成為
`{性別平等工作法, 刑法}`；兩部法規都出現在 `reply` 才算通過。回答中
`不涉及刑法`、`不適用刑法` 不算命中。

## 執行

先檢查單筆資料與實際 prompt，不呼叫 API：

```bash
uv run python admin/tests/reliability/applicable_law_api_eval.py \
  --xlsx /path/to/gemini_scene.xlsx \
  --row 4 \
  --dry-run
```

對本機 API 執行單筆、重複三次：

```bash
uv run python admin/tests/reliability/applicable_law_api_eval.py \
  --xlsx /path/to/gemini_scene.xlsx \
  --row 4 \
  --runs 3 \
  --api-url http://127.0.0.1:8000/api/v1/chat/
```

執行 Gemini 彙總表全部資料必須明確加上 `--all`：

```bash
uv run python admin/tests/reliability/applicable_law_api_eval.py \
  --xlsx /path/to/gemini_scene.xlsx \
  --all \
  --api-url http://127.0.0.1:8000/api/v1/chat/
```

預設為循序執行，請求間隔 2.1 秒，以配合目前每分鐘 30 次的 process-local
rate limit。可用 `--row`（Excel 的 1-based row）、`--case-id`、`--where-law`、
`--limit` 縮小範圍。完整選項請執行 `--help`。

若 API 已啟用 Firebase App Check，請把 token 放入環境變數，不要寫在命令列：

```bash
RAG_EVAL_APP_CHECK_TOKEN='...' uv run python \
  admin/tests/reliability/applicable_law_api_eval.py \
  --xlsx /path/to/gemini_scene.xlsx --row 4
```

## 結果

預設逐筆寫入 `admin/tests/reliability/results/*.jsonl`，並另存 `*.summary.json`。摘要包含：

- API success rate 與 HTTP/API error 數量；
- 主法規命中率；
- 所有必要法規 AND 命中率；
- multi-law 與含 `亦涉` 案例的全數命中率；
- law-level micro recall/precision；
- API latency p50/p95。

額外提到 XLSX 未列出的法規預設只警告，不會讓 coverage 失敗，因 gold label 未必
窮盡所有適用法律；仍會另外記錄 `strict_exact_pass`。API 失敗會保留每次 retry
軌跡，並計入端到端可靠性分母。

Exit code：`0` 達門檻、`1` 回答未達門檻、`2` 輸入或 gold 解析錯誤、`3` 有 API
操作錯誤。

## 新架構中的端點分工

`applicable_law_predict.py` 驗證 Admin 三法診斷契約，使用獨立 Admin 的密碼登入與 `test-jobs` 輪詢；預設本機 port 8001。`applicable_law_api_eval.py`、`four_layer_eval.py` 與 SSE `benchmark.py` 則驗證公開 Client 聊天行為，保留 Client chat API／App Check 路徑；本機 port 8000。工具都位於 Admin 測試目錄，但不會把公開 SSE 驗收誤改成無串流的管理診斷。實際呼叫需要明確選擇資料與 live 模式。
