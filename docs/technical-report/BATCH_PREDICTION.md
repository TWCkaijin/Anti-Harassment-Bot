# 三法適用預測與 Excel 批次測試

版本更新：2026-10-10。以下為測試契約與評分說明；新版以背景 `test-jobs` 回 202，輪詢至完成後取得原診斷結果。登入、session 歸屬、版本漂移與保留期限見 [操作手冊](../OPERATIONS.md)。

本機提供管理員單筆「適用法規預測」、後台 Excel 選檔批次預測及獨立命令列批次工具，沿用 v4 理解、案件證據驗證與 RAG 流程。測試不追問，輸出依模型判斷排列的三法清單；第一項是主要申訴、調查處理法規，後續是同案另有適用事項的法規。這是預測紀錄，不代表案件成立，也不是法律正確率評分。

## 命令列工具的固定資料與輸出

| 輸入 | 工作表 | 情境欄 | 筆數 | 新增欄 |
| --- | --- | --- | ---: | --- |
| `/path/to/chatgpt_scene.xlsx` | `Sheet1` | B | 335 | F |
| `/path/to/gemini_scene.xlsx` | `工作表1` | B | 299 | G |

只將 B 欄情境送入模型，不傳送原有法規標記、判定、補充註記或 `gemini_result`。Gemini 的「工作／校園／社會」分頁保留，不重複測試。案例以非空情境辨識，不以格式化列數或是否有法規標記篩選。

每次測試目錄產出兩份副本：`chatgpt_scene.predicted.xlsx` 與 `gemini_scene.predicted.xlsx`。原始檔保持不變。預測欄標題固定為「預測的適用法條」，例如 `性工法、性騷法`；排列由左到右，不使用固定三法排序，也不依原始標記重排。

資訊不足、不適用三法或操作失敗時，Excel 預測欄留空。原因保存在逐筆紀錄及後台，不寫入法規欄。瀏覽器批次下載另有「無法預測原因」欄。原資料含條件式與相互衝突的註記，也沒有經覆核的標準順位，CLI 不提供分類或順位正確率；瀏覽器選用參考答案時另提供包含命中率，見 [統計定義](RERANKING.md)。

## 管理員 Excel 批次預測

1. 開啟「管理」並驗證 Admin Token，選擇「批次預測」。
2. 選擇本機 `.xlsx`，選定工作表、標題列與情境欄位。預設會尋找名稱含「情境／場景」的欄位；只讀取標題列之後的非空情境。
3. 如有姓名或單位需額外遮罩，可展開「自訂隱藏詞」，每行輸入一項。按「準備預測」後，完整遮罩情境可逐頁檢視，亦可下載全部送出預覽。
4. 確認畫面上的 OpenRouter 模型與情境，按「確認送出並開始預測」。整批循序執行，列與列之間間隔 2.1 秒，不需要為每筆重新確認。
5. 可選「完成本筆後暫停」，等待目前請求完成，再用「繼續未完成預測」續跑。失敗列不自動重試，需另按「僅重試失敗」。
6. 隨時可下載結果 Excel；原有資料保留，在所選工作表最右側新增「預測的適用法條」「預測狀態」「耗時（秒）」「比對結果」「命中法規」「無法預測原因」。有效空預測、操作失敗及未執行列在狀態欄區分。

檔案與完整工作簿不會上傳。前端使用本機 ZIP/XML 讀寫，重用 `clientPrivacy` 遮罩與送出授權，再呼叫原有管理員 `applicable_laws_v1` 端點。每次請求只含所選列的情境、空歷史、v4 與固定設定，不附上原法規標籤、其他欄位或其他工作表。批次畫面一次顯示整批預覽並以開始按鈕確認，即使一般對話的送前確認設定關閉亦保留此步驟。

準備時讀取當前完整設定並固定模型／prompt／RAG 參數，每筆傳送相同的 `expected_applicable_laws_contract_sha256`。契約變更、驗證失效、限流或連線中斷時停止後續請求；契約變更或驗證失效需先下載結果，再重新準備／登入。其他單筆錯誤記錄後繼續，不保存完整診斷或供應商錯誤正文。RAG 資料庫內容未被凍結。

支援 `.xlsx`（10 MB 以內、解壓縮後 50 MB 以內），最多 5,000 筆、每筆 2,000 字；超長情境會要求修正，絕不截斷。加密檔、受保護的選定工作表、舊版 `.xls` 不支援。空欄位略過且保留 Excel 原列號。選取不同工作表不會重複預測其他分頁。

執行與結果僅存於目前頁面記憶體，切換後台頁籤仍保留。關閉後台或重新整理會清除；介面會提醒先下載結果。暫停按鈕會等當筆回覆，避免續跑時重複計費；直接關閉瀏覽器只能停止前端等待，無法保證已送出的模型請求取消。Excel 副本保留原始情境，應視為原資料的本機副本。

前端測試：`pnpm --filter @rag/admin test`、`pnpm --filter @rag/admin lint`、`pnpm --filter @rag/admin build`。瀏覽器回歸：啟動 `pnpm --filter @rag/admin dev`，再於根目錄執行 `node admin/tests/browser_batch_prediction_smoke.mjs`；可設定 `PLAYWRIGHT_MODULE` 使用既有安裝。所有 API 由腳本攔截成合成回應，禁止非本機網路，不需啟動真實後端。產物預設在 `/tmp/harass-batch-smoke/`。


## 管理員單筆測試

進入管理員的「本次診斷測試」，選擇「適用法規預測」、輸入情境並執行。頁面顯示「預測的適用法條」及空預測原因，不生成一般聊天正文、追問或建議回覆。一般對話是預設模式。

瀏覽器持續使用既有本機遮罩及送前確認設定。最近兩次結果只在頁面記憶體內保存；「只切換後端 PII 再測一次」會重用同一份已確認請求、原測試契約與其他設定。

## API 契約

`POST /v1/admin/test-jobs`（也支援 `/api/v1/admin/test-jobs`）新增外層 `output_contract: "applicable_laws_v1"`。請求仍使用既有 `request` 與單次 `overrides`，需要管理員驗證，僅接受 v4、非串流。未指定契約時維持原有一般對話測試。

```json
{
  "output_contract": "applicable_laws_v1",
  "prediction": {
    "predicted_laws": ["性騷法", "性工法"],
    "decision_status": "predicted",
    "decision_reason": null
  },
  "diagnostics": {}
}
```

`predicted_laws` 最多三項，必須唯一，且只接受三個指定名稱。`predicted` 必須有預測且 `decision_reason` 為 `null`；`insufficient_facts` 和 `no_target_law` 必須為空陣列，並提供非空、最多 300 字的 `decision_reason`。資訊不足時具體指出缺少的關鍵事實；不適用時簡述情境依據，不生成逐步推理。回傳其他法名、額外聊天欄位、重複項目或狀態不一致都視為契約錯誤，不從自然回覆抽取或補救。

測試模式共用 v4 的理解證據驗證、query 遮罩、RAG 與來源整理，固定進入最終判斷，第一階段資訊不足不直接終止。檢索故障屬操作失敗；檢索零結果另記狀態，不冒充「不適用三法」。一般聊天的 schema、串流、追問及回覆核對維持原有行為。測試契約只套用單次請求，不持久化 runtime 設定。

## 批次紀錄與資料界線

批次採一次情境一次執行、循序請求，預設間隔 2.1 秒、HTTP timeout 190 秒，不自動重試。一次測試通常含理解與最終判斷兩次生成呼叫；RAG embedding 請求另計。

CLI 透過本機 Node 助手直接重用瀏覽器的 `clientPrivacy`，先形成遮罩後的完整送出預覽及內容雜湊。載入或遮罩失敗時停止，不能改送原文。自動遮罩仍不能保證完整匿名；副本保留原始情境，是本機資料保存，不是去識別化的資料集。

整批固定可覆寫的模型、生成參數、prompt 與 RAG 設定，每次請求帶入同一份 snapshot。另以唯讀的 `applicable_laws_contract_sha256` 核對實際服務指令、專屬理解／預測指令、schema 與啟用的情境腳本 catalog；此 fingerprint 不可透過 runtime overrides 修改。每次請求帶入預期契約雜湊，服務以同一份 catalog snapshot 核對契約及匹配腳本；契約變動時在模型呼叫前拒絕請求，批次停止。來源檔、工作表／列號、送出內容雜湊、設定或契約不符時拒絕混用紀錄。RAG 資料庫仍為實際服務讀取，不能宣稱已凍結其內容。

逐筆 JSONL 與摘要只保存列識別、雜湊、預測／固定狀態、必要耗時、生成呼叫數及檢索狀態，不保存原始情境、provider reasoning 或完整管理員診斷。遮罩後送出預覽僅供本機檢視及重現。操作 token 不保存於結果。

OOXML writer 只修改選定工作表的預測欄及必要顯示設定，其餘 ZIP 成員保持原樣；匯出前再次核對兩份來源檔雜湊，暫存檔通過讀回驗證後才原子替換副本。中斷或部分完成仍可輸出副本，未完成列留空，完成數以摘要為準；來源檔變動時保留既有紀錄與副本，拒絕匯出錯置的結果。

## CLI 操作

需要專案 Python 環境及支援原生 TypeScript、`node:module.registerHooks` 的 Node（本機以 Node 26 驗證）。後端需有可用模型、Firebase 與背景 worker；CLI 以隱藏密碼提示或 `--password-file` 登入，只在記憶體保留 Cookie／CSRF。新版使用 test-jobs 提交及輪詢，不接受固定 Admin token。

先依 [操作手冊](../OPERATIONS.md) 設定本機環境並啟動 Admin API 與 worker：

```bash
PYTHONPATH=shared/python:admin/backend uv run flask --app admin_app.main:create_app run --host 127.0.0.1 --port 8001
```

離線檢查不需要登入、不呼叫 API，也不建立結果檔：

```bash
uv run python admin/tests/reliability/applicable_law_predict.py \
  --xlsx /path/to/chatgpt_scene.xlsx --xlsx /path/to/gemini_scene.xlsx --dry-run
```

以下操作使用新的空測試目錄。每次均須用 `--xlsx` 明確指定外部工作簿，repo 不再要求隨附資料；完整送出預覽保存在 `outbound_preview.jsonl`，只包含本機遮罩後內容及固定覆寫設定。

```bash
uv run python admin/tests/reliability/applicable_law_predict.py \
  --xlsx /path/to/chatgpt_scene.xlsx --xlsx /path/to/gemini_scene.xlsx \
  --prepare-only --run-dir /tmp/applicable-laws-new

uv run python admin/tests/reliability/applicable_law_predict.py \
  --xlsx /path/to/chatgpt_scene.xlsx --xlsx /path/to/gemini_scene.xlsx \
  --smoke --resume --allow-live --run-dir /tmp/applicable-laws-new

uv run python admin/tests/reliability/applicable_law_predict.py \
  --xlsx /path/to/chatgpt_scene.xlsx --xlsx /path/to/gemini_scene.xlsx \
  --all --resume --allow-live --run-dir /tmp/applicable-laws-new
```

先檢查預覽，再執行六筆 smoke（ChatGPT rows 2、169、336；Gemini rows 2、151、300）。六筆通過後，全量續跑略過已完成六筆，共完成 634 筆。若省略獨立準備步驟，`--smoke --allow-live` 會先準備預覽再測試；已有紀錄需加 `--resume`。

有效空預測也是已完成結果，不重跑。操作失敗不自動重試；明確補測時才使用：

```bash
uv run python admin/tests/reliability/applicable_law_predict.py \
  --xlsx /path/to/chatgpt_scene.xlsx --xlsx /path/to/gemini_scene.xlsx \
  --all --resume --retry-failed --allow-live --run-dir /tmp/applicable-laws-new
```

`Ctrl-C` 會保存已完成紀錄並匯出部分副本。已啟動卻未取得回覆的請求視為 `interrupted_request`，避免續跑時默默重複可能已計費的請求。`--retry-failed` 只處理先前失敗列，不順便測試未開始的列。

可用 `--api-url` 指定另一個管理員測試 URL、`--node` 指定 Node 執行檔，或 `--hidden-terms-file` 提供本機 UTF-8 隱藏詞（每行一項）。隱藏詞及 token 不寫入結果；隱藏詞的雜湊納入續跑核對。

Exit code：`0` 選定執行無操作失敗；`1` 有操作失敗；`2` 輸入、準備或續跑不符；`130` 中斷。`0` 不代表已完成全部 634 筆，完整進度以 `summary.json` 的總數、完成數及未測數為準。
