# Admin Rerank 測試

入口為「驗證及測試 → Rerank 測試」，亦可使用 Admin URL 的 `#rerank`。功能只進行檢索與 rerank，不產生聊天回答，不寫入正式 runtime 設定或檢索語料。

## 操作

1. 建立資料集，新增查詢及資料類型。原文及額外遮罩詞留在瀏覽器；按「本機去識別預覽」，核對文字後勾選確認，才可儲存。每題最多 4,000 Unicode 字元，每資料集最多 200 題；超限拒絕、不截斷。此頁使用既有本機遮罩器，仍須人工核對預覽。
2. 執行設定預填目前環境值，確認 N、K、模型、timeout、節錄長度、距離門檻及 provider 排序。N 為 1–50、K 為 1–20、N ≥ K；資料類型保障開啟時，K 必須至少等於選取類型數。
3. 確認外部呼叫後按「取得標註候選」。查詢會送至目前環境 embedding 服務。候選任務在後台繼續，關閉瀏覽器不會取消。載入候選後逐組標記相關／不相關；候選預設未判定，標註時不顯示 rerank 分數。
4. 展開「從檢索資料庫補選候選外來源」，使用分頁文件、來源詳情或文件 ID 補選。標準答案可以包含候選池沒有的來源，絕不自動補進候選。缺少來源資訊時，為片段設定明確人工來源 ID；同一來源的片段須使用同一 ID，不能靠相似標題猜測。
5. 人工確認所有標註，儲存，再發布版本。沒有相關來源時明確勾選「無相關來源」。未判定、待對應或相關／無相關相互矛盾時無法發布。修改查詢或範圍會回到待確認狀態，需要儲存後重新核對。
6. 先執行 5 題，再執行完整資料集。查詢與文件節錄會送到現有 OpenRouter rerank 端點及選用模型供應商；按既有服務設定計費。候選數不超過 K 時略過 rerank，模型指標不適用。
7. 檢視整體與分類 macro/micro、有效題數、失敗狀態、每題排名、分數、規則標記、漏檢索／掉出／新增命中。可下載 JSON 或 CSV，或重開歷史。
8. 「用相同候選重跑」使用該歷史執行的資料集版本及已完成候選題目；可調模型、K、timeout、節錄長度、provider。若改查詢、N、類型、距離、類型保障、embedding 或集合映射，須重新檢索。重跑按新設定形成新紀錄。

資料集封存可解除；結果封存仍保留證據。草稿題目可刪除，已發布版本不變。頁籤切換會保留本機未儲存輸入；重新整理前請儲存。409 衝突不自動覆寫，保留輸入供核對後重新載入草稿。

## 指標

`G` 為人工確認相關單位集合。法規單位為正規化法名＋條號（沿用法名別名及條號解析）；判決、救濟為類型＋source_id。缺少必要資訊時才允許 `manual:<kind>:<manual_unit_id>`。同一法條／來源的多個片段只計一次。

- candidate：全候選池命中單位數 / |G|。
- before：同一候選池原始前 K 個片段涵蓋的命中單位數 / |G|。
- model：模型排序前 K 個片段涵蓋的命中單位數 / |G|。
- final：套用精確法條優先、類型保障後的前 K 個片段命中單位數 / |G|。
- delta：final − before。

先取 K 個片段才去重，不能去重後再補滿 K 個來源。macro 是有效題目 recall 平均；micro 是有效題目命中數總和 / gold 數總和。空 gold 題目 recall 為 null（畫面「不適用」），另顯示返回片段數。檢索失敗為未完成量測，不作正常零命中。timeout／failed 使用既有 fallback 最終結果，model 排序及 recall 為 null。`ok` 才代表 rerank 成功；`skipped_small_pool` 為未呼叫。完成率表示題目處理已結束，須搭配有效量測率及狀態一起解讀。

指標只相對已標註的相關來源，不能證明標註涵蓋全庫。逐題記錄觀察時間及版本，不宣稱跨題為全庫原子快照。改變節錄長度是不同實驗條件。

## 保存格式與 API

所有資料依目前 Admin 環境隔離，供該環境已登入 Admin 使用，跨 session 可讀；沿用 session／CSRF。private Firestore 保存小型索引，object storage 保存完整草稿、候選、執行快照及逐題結果：

| 集合 | 內容 |
| --- | --- |
| `admin_rerank_datasets` | 名稱、封存、草稿 revision、published_revision、draft_ref |
| `admin_rerank_revisions` | 不可覆寫 revision、cases_ref、集合映射、sha256 |
| `admin_rerank_runs` | 輸入快照、模型設定、job_id、進度、summary、results_ref |
| `admin_rerank_preparations` | 題目查詢綁定、候選任務及持久化結果參照 |

API 前綴 `/api/v1/admin/rerank-tests`（亦有 `/v1/admin/rerank-tests` alias）：

| 方法／路徑 | 用途 |
| --- | --- |
| GET/POST `/datasets` | 分頁列出／建立 |
| GET/PATCH `/datasets/:id` | 讀取／改名、封存 |
| POST `/datasets/:id/cases` | 新增草稿題目 |
| PUT/DELETE `/datasets/:id/cases/:case` | 更新／刪除草稿 |
| POST `/datasets/:id/publish` | 發布目前草稿 revision |
| GET `/datasets/:id/revisions/:revision` | 讀取不可變版本 |
| GET `/documents?kind=law` | 依目前 runtime 集合映射分頁瀏覽 |
| POST `/documents/resolve` | 按類型及 doc_id 讀取完整證據 |
| POST `/datasets/:id/prepare` | 建立標註候選任務 |
| GET `/datasets/:id/cases/:case/preparation` | 取得候選狀態與結果 |
| GET/POST `/runs` | 分頁紀錄／建立執行 |
| GET `/runs/:id` | 設定、摘要與逐題證據 |
| POST `/runs/:id/archive` | 封存 |
| GET `/runs/:id/export?format=json\|csv` | 完整 JSON／摘要 CSV |

草稿及候選準備需 `If-Match` revision；衝突 409。啟動候選／run 需 `Idempotency-Key`，重複請求沿用相同 key，不同內容不得共用。每個 annotation 包含 `kind`, `doc_id`, `relevance`, `manual_unit_id`, `evidence_sha256`；確認標註必須提交實際核對的雜湊，文件已變更時拒絕，不能靜默更新。發布及執行亦檢查來源是否刪除、停用或改版，不移除 gold 來縮小分母。

JSON export 的 `results[]` 保存 `case`（查詢與人工 annotations）、`candidates`（完整內容、集合/doc ID、單位、版本、內容及證據雜湊）、`model_order`、`final_order`、`scores`、`selection_reasons`、`excerpts`（文字及 SHA256）、`rerank`、`metrics`。雜湊使用排序鍵、無多餘空白、UTF-8 的 canonical JSON；文字雜湊也包含 JSON 字串編碼。候選 `retrieval_binding` 綁定檢索條件；`dataset_sha256` 綁定已發布題目。一般聊天日誌不加入這些完整文件 trace。

CSV 只匯出逐題指標、狀態、查詢與耗時，具公式字首轉義；需要離線重算請下載 JSON。

## 任務、取消與恢復

使用現有 durable jobs，逐題序列派送，embedding 與 rerank 分別記錄外部呼叫開始與完成。已完成候選及模型結果先 checkpoint，再更新 run；worker 重送可重用結果，不再次呼叫 provider。若外部呼叫可能完成但無持久化結果，進入 `needs_review`，必須明確確認可能重複計費才重試。關閉瀏覽器不會自動取消；取消停止後續題目，已送出呼叫可能已計費。取消的執行保留部分證據，可建立新執行。

run、revision 與候選結果不依附 generic job 的 TTL；完成狀態另存 run。Generic job 到期後不提供原任務重試，但歷史與下載仍可用。新執行需要現行 worker、private DB、object storage 權限與既有 Cloud Tasks 設定；本變更沒有部署這些資源。

## 離線驗證

合成資料 `admin/tests/fixtures/rerank/hand-calculated.json` 的 gold 為 A/B/C；候選 A/A/B，K=2。candidate=2/3、before=1/3、model=final=2/3，完全不呼叫外部服務：

```sh
python3 scripts/replay_rerank.py admin/tests/fixtures/rerank/hand-calculated.json
python3 scripts/replay_rerank.py /path/to/downloaded-run.json
.venv/bin/python -m pytest admin/tests/test_rerank_evaluation.py admin/tests/test_rerank.py admin/tests/test_admin_jobs.py -q
pnpm --dir admin/frontend test
pnpm --dir admin/frontend lint
pnpm --dir admin/frontend build
```

`replay_rerank.py` 只用 Python 標準函式庫與純指標模組；逐題及 summary 不相符時失敗退出。合成 fixture 的內容雜湊是明確佔位值，只用於指標重算，不可拿來發布真實資料集。

瀏覽器測試先啟動本機 Vite，再執行 `node admin/tests/browser_rerank_smoke.mjs`；可用 `PLAYWRIGHT_MODULE` 指定已安裝的 Playwright，`BROWSER_BASE_URL` 指定 localhost。測試攔截所有 API、阻擋外站，以 1440 與 390 像素驗證完整流程；它不證明真實模型品質、雲端部署或法律相關性。真實試跑需管理員選定環境、核對查詢、人工標準答案與外部呼叫後執行。
