# 合成測試資料

自動化測試不讀取 `resource/`，也不複製其中的工作簿、案件或法規快照。這個目錄及以下測試 helper 可隨 repo 保存。

- `workbooks.py`：以固定合成文字建立 OOXML，包含情境、標籤、備註、額外工作表、樣式和 ZIP 附屬檔案。根目錄 `conftest.py` 在 `tmp_path` 建立 335／299 列，保留既有 profile 的大批次、首／中／末抽樣及匯出不變性檢查；筆數不代表使用原始案例。
- `admin/tests/test_corpus_legal_import.py::synthetic_legal_snapshot`：用虛構條文產生六法資料、現行與待生效版本及 manifest，驗證完整性與版本隔離，不驗證真實法律內容。
- `admin/frontend/src/test/scenarioFixture.ts`：以記憶體內的合成 OOXML 測試工作表、欄位、空白列、列號及匯出保留。
- `admin/frontend/src/test/behaviorFixture.ts`：獨立撰寫的五筆匯入案例，含簡寫腳本及不可信歷史結果；不從正式內建案例反向產生期望值。
- `admin/tests/reliability/fixtures/` 與 `admin/frontend/src/masking-lab/fixtures.json`：既有合成評測及遮罩案例，維持原用途。

Python 使用 `pytest client/tests admin/tests shared/tests`；前端使用 `pnpm -r test`。外部工作簿評測工具必須明確傳入 `--xlsx`，四層離線評測仍預設使用內建合成 JSONL。
