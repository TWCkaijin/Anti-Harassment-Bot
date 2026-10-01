# 官方法源維護與資料庫匯入

這份離線快照取自法務部全國法規資料庫官方 API：

- [中文法律 ZIP／JSON](https://law.moj.gov.tw/api/ch/law/json)
- [法務部政府開放資料集說明](https://data.gov.tw/dataset/18289)
- 查核日：2026-10-01；官方資料整編更新日：2026-09-18。

`official/articles.jsonl` 收錄性別平等工作法、性別平等教育法、性騷擾防治法、跟蹤騷擾防制法、中華民國刑法及民法。每筆為完整條文，不按字數截斷例外或後續項款。`official/manifest.json` 記錄來源雜湊、各法條數、版本、沿革、來源生效註記及隔離資料。

共 2,017 筆：1,929 筆可供現行方向檢索；86 筆刪除條文、民法第 166-1 條施行日未定，以及新公布但尚未施行的第 1223 條，均標記 `corpus_active=false`。第 1223 條另保留官方連結的仍生效舊條文，因此該条有兩個明確分開的版本。此日期限定的補充來源及雜湊在 `current_supplements.json`；下一次更新必須重新查核，不能自動沿用舊查核日期。

`promulgated_date`、`law_modified_date` 是官方法規最新修正公布日，並不表示每一條均在該日修正。`effective_date` 是官方法規層級生效排程，並不代表每一條、項、款在同一天生效；空值保留空值，詳見 `effective_note` 與 manifest 的沿革。事件發生日期、修法過渡及精確適用版本仍需個別確認。這份快照不是歷史法規全集，也不是專家判案 gold。

## 重建及舊資料稽核

```bash
.venv/bin/python backend/scripts/ingest/official_laws.py fetch --output /tmp/moj-law.zip
.venv/bin/python backend/scripts/ingest/official_laws.py build \
  --source /tmp/moj-law.zip --output-dir /tmp/official-laws \
  --checked-at 2026-10-01 --current-supplements resource/legal/current_supplements.json
.venv/bin/python backend/scripts/ingest/official_laws.py audit \
  --corpus /tmp/official-laws/articles.jsonl --output /tmp/legacy-audit.json
```

更新時使用實際查核日，並先重新檢查補充條文來源。`build` 不初始化 Firebase、不呼叫 embedding。輸出已有檔案時須明確指定 `--overwrite`。

`legacy-audit.json` 稽核原有 `data/documents` 的 5 筆資料，全部欠缺官方網址、版本及日期等 metadata，並指出舊「性騷擾防治法第 13 條、一年申訴」與舊名職場義務資料；沒有改寫原始資料，也沒有宣稱已讀取或更新雲端。`import-preview.json` 是未指定真實專案、無雲端快照的建立／隔離預覽，不能拿來直接 apply。一般指引不因缺少 metadata 自動刪除。

## 雲端差異、更新及回復

本次沒有執行以下雲端操作。正式更新時先選定專案、取得唯讀快照、檢查差異與費用，再使用明確的 apply 命令。快照與 rollback journal 包含原始 metadata 及 embeddings，應放在受限制的位置，不提交版本庫。

```bash
# 唯讀；輸出完整快照供差異及並行變更比對。
.venv/bin/python backend/scripts/ingest/official_laws.py snapshot \
  --project YOUR_PROJECT_ID --collection rag_documents --output /tmp/legal-before.json
# 離線 dry-run；不呼叫 embedding、不寫 Firestore。
.venv/bin/python backend/scripts/ingest/official_laws.py plan \
  --corpus resource/legal/official/articles.jsonl --snapshot /tmp/legal-before.json \
  --project YOUR_PROJECT_ID --collection rag_documents --output /tmp/legal-plan.json
# 明確寫入；先確認 plan_sha256 與預計的費用及更新範圍。
.venv/bin/python backend/scripts/ingest/official_laws.py apply \
  --plan /tmp/legal-plan.json --project YOUR_PROJECT_ID \
  --expected-plan-sha256 REVIEWED_PLAN_SHA256 --backup /tmp/legal-rollback.jsonl
# 回復先 dry-run；另加 --apply 才寫入。
.venv/bin/python backend/scripts/ingest/official_laws.py rollback \
  --project YOUR_PROJECT_ID --backup /tmp/legal-rollback.jsonl
```

只改動差異文件；已確認衝突的舊 seed 只設定隔離 metadata，沒有破壞性刪除。每筆寫入前先持久保存舊值，再在 Firestore transaction 重新比較快照；資料已變動時拒絕覆寫。匯入中斷也能依 journal 回復。回復只處理內容仍與該批次寫入結果完全一致的文件，遇到後續改動回報 conflict，不強行蓋掉。新建文件的回復會刪除該批新增文件；更新文件恢復完整舊值。

若程式在追加 journal 時中斷，回復讀取器只容許忽略最末一行沒有換行且未完成的 JSON／UTF-8，並在結果標記 `ignored_incomplete_journal_tail`；先前完整記錄仍可回復，中間損壞或缺少完整 header 則拒絕。Firestore timestamp 保留奈秒精度，避免還原時截去時間精度。

## 執行時檢索與索引

執行時只讀 Firestore，不連線抓取法律網站。明示法名及條號用 `metadata.lookup_keys` 精確查詢，再合併向量結果；法名另做限定該法的向量查詢。完整 metadata、cosine distance、查找方法與篩選原因可供診斷，實際引用仍由回答驗證決定。

除現有 `embedding` cosine vector index 外，法名限定查詢需要 `metadata.law_name`（ASCENDING）＋`embedding`（vector）複合索引，向量維度須與實際 embedding 模型一致。精確查詢使用 `metadata.lookup_keys` 的 array-contains 索引。未部署新複合索引時，診斷顯示 `law_name_index_status=missing_index`，並保留精確查詢及既有向量路徑；其他資料庫錯誤仍回報失敗。這次沒有部署索引。

`top_k` 為 1–50；cosine distance threshold 為 0–2，越小越相近。一般合併嚴守 top_k；保留既有 `preserve_data_types=true` 介面，混合類型的最小回傳預算為要求的類型數，讓法條與求助資料各有至少一個位置。向量門檻不淘汰精確條號命中，但尚未施行／隔離條文仍不回傳。官方來源 metadata 缺漏不會被自動補成已查證。

## 驗證

```bash
.venv/bin/python -m pytest -q tests/test_legal_corpus.py tests/test_legal_retrieval.py tests/test_firestore_ingest.py tests/test_rag.py
```

測試覆蓋官方來源快照完整性、條文未截断、待施行版本隔離、民法舊版補充、完整 metadata、精確／向量合併、門檻與 Top K、缺索引過渡、舊申訴錯誤篩除、dry-run 差異，以及 journal 回復和並行更改保護。這些工程檢查不代表法律專家已覆核每個模型答案。

`tests/reliability/fixtures/workplace.engineering.jsonl` 將茶水間部屬對主管的範例列為工程回歸，沿用原候選 `gemini:工作表1:4` 的 family 與 development 分組。要求先答主要職場法方向、保留關係方向及「試圖」語意、使用實際來源；刑事法名列為附條件選項。保留 `synthetic_fixture` 身分，不改原候選標記，不進專家 gold 分母；`engineering_expectations` 的法律語意仍需人工覆核，既有 evaluator 的法名／regex／source ID 檢查不能替代該覆核。
