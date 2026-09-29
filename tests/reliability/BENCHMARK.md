# 延遲、呼叫次數與待測成本

`benchmark.py` 預設只讀本地 recording，不會建立HTTP client。報告不保存請求原文、案件facts、回覆文字、session ID或錯誤正文；只保留樣本序號、數值、允許的route、來源／程式／執行設定hash。設定檔須另行保存，供對照hash與重現使用。

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tests/reliability/benchmark.py \
  --output /tmp/benchmark.offline.json

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_benchmark.py
```

`--recording` 可指定每行一個JSON recording的檔案；預設為 `fixtures/benchmark.synthetic.jsonl`。範例含3個合成樣本，其中2個完成、1個錯誤，用於檢查計算與失敗分母。

| 欄位 | 定義 |
|---|---|
| `first_readable_delta_ms` | 從請求開始到第一個非空白 `delta.text`／`reply_delta.text`；不計progress、guidance、連線事件或心跳 |
| `complete_ms` | 收到完整 `done` 事件的時間；失敗或未完成為unknown |
| `elapsed_ms` | 該次觀測結束時間，包括失敗；獨立於完成時間 |
| `model_ms`／`embedding_ms`／`retrieval_ms`／`vector_search_ms`／`validation_ms` | API `done.execution`提供的各階段時間；缺欄位為unknown |
| `model_calls` | API明確回傳的生成呼叫數，不自行從事件數推算 |
| `retry_ms`／`attempt_count` | API execution或recording明確提供的重試時間／嘗試次數；缺失為unknown，不填0 |
| `model_cost_usd` | 有完整usage、價格、USD與all_model_calls範圍聲明時才估算；否則unknown |

每個欄位回報known／unknown樣本數、P50、P95及已知值總和。`complete_ms` 的P50／P95僅由完成樣本計算，必須同時看failure_count；不可把失敗排除後的數字描述成完整端到端可靠性。`retrieval_ms`包含embedding時間，不應把重疊階段再次加總。

目前合成fixture計算結果：首可讀文字P50=600ms、P95=780ms；完成P50=1500ms、P95=1950ms；已知model_calls總和3、另1例未知。重試時間只有1例明確為0，另外2例未知。**這些是假資料驗算結果，不是真實服務效能或已節省的時間。**

recording需有 `events: [{at_ms,event,data}]`，時間從請求開始計算並依序排列；可加 `elapsed_ms`。來源完整格式參見合成fixture。重試欄位可放execution或recording頂層。`done.execution`優先於頂層execution。

成本資料範例：

```json
{
  "usage_scope": "all_model_calls",
  "usage": {"input_tokens": 1000, "output_tokens": 500},
  "prices": {"currency": "USD", "input_per_million": 1, "output_per_million": 2}
}
```

這組**合成價格**算出USD0.002，不是任何供應商的現行報價。未提供usage／價格、未確認涵蓋所有生成呼叫、或幣別不是USD，一律未評估。快取／reasoning等不同計價若不能對應上述完整token範圍，也應保留未評估，不套用錯誤單價。模型成本估算不包含embedding、Firestore、主機等；`total_service_cost`固定為`not_evaluated`。目前實際API沒有完整usage／價格資料，因此實際費用仍待測。

未來明確選擇live測試時，`requests.json`是完整chat request物件的JSON陣列，可含v2 contract；工具會強制`stream:true`。同時提供兩個旗標才可連線：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tests/reliability/benchmark.py \
  --live --api-url https://YOUR-STAGING-HOST/api/v1/chat/ \
  --requests /path/to/deidentified-requests.json --limit 3 \
  --metadata /path/to/run-metadata.json --output /tmp/benchmark.live.json
```

live預設最多3次請求，可能產生模型費用；沒有自動重試。App Check token只讀取 `RAG_EVAL_APP_CHECK_TOKEN`。live模式不把請求檔內容寫入報告；宜只使用合成或已去識別化情境。既有輸出須明確加`--overwrite`。

本輪只跑離線與httpx MockTransport測試，沒有連線正式或staging端點。退出碼0代表工具完成（離線fixture可含故意錯誤），2代表輸入／輸出錯誤，3代表live中至少一例未完成。
