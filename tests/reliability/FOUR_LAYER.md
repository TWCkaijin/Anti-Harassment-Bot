# 四層評測操作與證據界線

`four_layer_eval.py` 是獨立、預設離線的評測程式，不會 import backend、初始化 Firebase 或自行呼叫模型。既有 `applicable_law_api_eval.py` 保持原有行為，仍可量測法名覆蓋率；法名命中不等於法律內容正確。

## 已產出的資料

- `candidates/cases.candidate.jsonl`：原始兩份 XLSX 的 634 題候選情境，335 + 299；全部 `unreviewed`，正式 gold 為 0。
- `candidates/manifest.json`：原始檔 SHA256、工作表／列號追溯、題數、警告、family 分組及 split 方法。
- `fixtures/four_layer.synthetic.jsonl`：8 個合成案例、9 個回應輪次，驗證評分流程及已知盲點。
- `results/four-layer.offline.json`：合成回應的離線評測結果，含故意錯誤的反例。這不是模型品質基準。

family 使用正規化文字三字片段 Jaccard 相似度 0.72 分群，再按 family hash 分 development／validation／holdout。同一 family 不跨組；此次資料在此閾值下為 634 群。此方法未確認語意近重複，故所有分組標示 `heuristic_needs_review`，不能視為已完成盲測隔離。原始 workbook 檔名不能證明生成模型、prompt 或人工覆核來源。

## 安全的離線命令

在專案根目錄執行；不需啟動 API 或設定付費模型：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tests/reliability/four_layer_eval.py evaluate \
  --metadata tests/reliability/fixtures/metadata.synthetic.json \
  --output /tmp/four-layer.offline.json

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tests/reliability/four_layer_eval.py export-candidates \
  --output-dir /tmp/four-layer-candidates

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tests/reliability/four_layer_eval.py evaluate \
  --cases tests/reliability/candidates/cases.candidate.jsonl \
  --output /tmp/candidate-readiness.json

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_four_layer_eval.py tests/test_applicable_law_api_eval.py
```

已存在的輸出必須明確加 `--overwrite`。離線 candidate readiness 無已錄製回答時，輸出 `not_observed` 與 `not_evaluated`；不製造回答、不計為 API 失敗，也不算正確。

## 四層涵蓋與限制

| 層次 | 自動檢查 | 無法由此證明的事項 |
|---|---|---|
| 法律分流 | 經審查標籤的多法規 precision／recall／F1、exact match、逐法 TP／FP／FN／TN、否定法名 | 法條、期限、法律要件、受理機關的正確性；條件適用法規的語意判斷 |
| 檢索 | 經標註相關文件的 Recall@k、MRR、已知錯版本及未知版本 | 相似文件是否真的支持回答；歷史法與過渡條款如何適用 |
| 回答 | 每節 `source_ids` 是否存在於本次 `sources`、必要引用缺失、設定的已知錯誤模式 | 有效 source ID 不代表引用支持；未命中錯誤模式不代表正確 |
| 多輪引導 | 需要／不需要追問、目標 fact_key、拒答、重複已知問題、facts_revision、更正後的禁用舊事實模式 | 開放式追問是否適切、同理性與法律需求是否真的滿足 |

`sources` 不含完整 ranked retrieval trace 時，檢索結果標示 `sources_proxy`；可在離線紀錄補 `retrieved_documents`。來源無 `version` 或 `corpus_version`，或缺少可供核對的 `expected_versions` 時列 `unknown`，不猜測現行版本；有版本字串不等於版本已核准。發現已知錯版本時，即使其他來源版本未知仍判版本失敗。

兩份 XLSX 的警告原樣保留：9 筆補充標籤無對應法規、11 筆「性影像」含糊標籤、6 筆條件式補充，類別可能重疊。未審標籤不會進入法律分流／檢索／引導品質分母。合成案例與專家覆核案例分組統計，不能混成整體法律準確率。

## 從候選資料建立專家評測集

保留原始 XLSX，另建立經覆核的 JSONL。每筆須有 `review_status: "expert_reviewed"`，以及 `review.reviewer_id`、`review.reviewed_at`。`annotations` 只填專家已確認的層次：

```json
{
  "case_id": "reviewed:example",
  "scenario": "經覆核、去識別化的情境",
  "review_status": "expert_reviewed",
  "review": {"reviewer_id": "reviewer-1", "reviewed_at": "YYYY-MM-DD"},
  "annotations": {
    "law_routing": {"required_laws": ["法規全名"], "conditional_laws": []},
    "retrieval": {
      "relevant_ids": ["collection/document-id"],
      "expected_versions": {"collection/document-id": "verified-version"}
    },
    "answer": {"requires_citations": true, "forbidden_patterns": []},
    "guidance": {"turns": [{"mode": "clarify", "fact_key": "relationship"}]}
  },
  "turns": [{"message": "使用者情境，不附上答案標籤"}]
}
```

`conditional_laws` 是專家允許但非必需的法名，不計入誤報；程式不會自動判讀附帶條件是否說對。專家仍須確認事發日期、法律版本、修法過渡規則與案例分組；本程式沒有改寫任何 `data/` 法源。

人工回答覆核必須針對一個具體回應。離線 `responses` 紀錄可加 `expert_review`，含 `reviewer_id`、`reviewed_at`、`verdict: "pass" | "fail"`，以及報告提供的 `response_sha256`。這個hash綁定 reply、answer_sections、sources；改動回答或證據後，舊覆核失效。live API 回傳的 `expert_review` 會被移除，避免模型自評冒充專家。此處 pass/fail 是覆核者對回答正確性及引用支持的整體判定；細項議題可存於同一離線覆核紀錄供人工追溯。

## 評估既有回應與可重現性

`--responses recorded.jsonl` 格式為每行 `{"case_id":"...","responses":[回應1,回應2]}`。每個回應沿用API reply、rag_used.sources、answer_sections、clarification、facts_revision 等欄位，也接受fixtures的頂層sources。合成fixtures示範有效與故意錯誤的情況。缺少任何預期輪次時，不把最後收到的片段當成完整回答評分。

`--metadata run.json` 提供 `model`、`prompt_sha256`、`config_sha256`、`corpus_version`。它們為執行者提供的資料，應取自實際環境，不能猜測。缺欄位保留 `unknown` 並加警告。報告另自動記錄案例內容、來源JSONL、評測程式hash、contract版本與k。

## 明確選擇的 live 測試

本輪未執行任何 live 評測。未來操作人選擇staging端點後，可執行：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tests/reliability/four_layer_eval.py evaluate \
  --cases /path/to/expert-reviewed.jsonl \
  --metadata /path/to/actual-run-metadata.json \
  --live --api-url https://YOUR-STAGING-HOST/api/v1/chat/ \
  --contract-version 2 --limit 10 --output /tmp/four-layer.live.json
```

必須同時給 `--live` 與明確 `--api-url`；沒有預設端點。live預設最多10個案例，可用 `--limit` 調整；多輪案例各輪皆可能呼叫模型並產生費用。沒有自動重試，避免重複模型費用。App Check token只從 `RAG_EVAL_APP_CHECK_TOKEN` 讀取，不寫入報告。API失敗記錄類型與耗時，停止該案例後續輪次，不保留錯誤回應正文。

v2 harness傳入 `contract_version: 2` 與 `case_context`。只在revision相符時合併非衝突 `explicit` fact_updates；不合併 `confirmation`，不覆寫已拒絕或衝突事實。fixture的 `context_updates` 代表使用者明確確認的新資料或更正，會增加revision。拒答需明確 `abstained: true` 或 `execution.route` 為 `abstain`／`insufficient_evidence`；不以禮貌免責句推測拒答。

退出碼：0表示評測程序完成（包含故意失敗fixtures），不是品質通過；2是輸入／輸出錯誤；3是至少一個API請求失敗。品質判讀必須查看各層status、分母及待覆核數，不提供會誤導的單一「整體通過率」。
