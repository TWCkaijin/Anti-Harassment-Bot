# Chat v3：回答品質、分析摘要、條件式複選與管理診斷

更新日期：2026-10-01。這份文件說明目前本機實作；未部署、未匯入雲端法源。先前 technical-report 目錄的報告與簡報是 v2 的歷史快照，不能視為 v3 驗收結果。

## 回答與情境

v3 由同一次結構化模型回應整理需求、情境範圍、逐字證據、資訊充分性與必要追問。模型只產生可核對的摘要，不要求或公開原始思考過程。第三人稱、例題及假設情境與個人摘要分開；追問附 `context_scope=scenario`，該次選取保留在問答脈絡，不寫入個人 facts。

已知、未知、拒答或本輪已擷取的項目不重複追問。無效追問只取消該題，移除混在回答中的相應問句，保留其他分析與引用。缺事件時間只限制期限／歷史版本，不能清空法律方向。v2 保留短追問相容路徑；v3 交由模型依本輪問題判斷是否必要。

法律檢查逐句處理法名、精確條號配對、來源 ID、版本註記及期限數字。中文與阿拉伯條號先正規化，避免第 12 條誤配第 112 條、或把民法第 12 條與性工法第 13 條拼成性工法第 12 條。修正模式只替換有問題的主張；標記模式保留原文並加上待核對標示。被刪除主張的引用不計入實際引用數。這些是工程約束，不能證明每項法律推論、證據涵攝或期限適用都正確。

`rag_used.status` 表示是否取得檢索資料；`rag_used.sources` 僅列回答實際引用的來源。分析摘要及管理診斷分開記錄檢索數與引用數。執行時只查資料庫，不以即時網頁搜尋補洞。

## 契約與本機資料

health 宣告 `[1, 2, 3]`，前端選最高支援版本。v3 使用案件 schema 2、本機儲存 schema 3；`value` 可為單一字串或 1–4 個不重複字串。舊字串原樣保留，不猜測逗號的意義。遷移驗證失敗或遇未知版本時禁止寫回，保留原資料。含多值資料的對話不能靜默降級至 v2。

模型追問包含 `selection_mode=single|multiple`、`max_selections`、問題文字及最多 3 個具體選項。單選上限 1，複選 2–4；前端另加不確定、暫不提供與自行補充，共最多 6 個入口。前兩者與其他答案互斥，自行補充可與複選共存。未送出草稿、隱藏／重開、重新整理及摘要編輯均保留陣列。

v3 題目附 HMAC `validation_token`，綁定問題 ID、欄位、選項、範圍及選取上限；後端在遮罩前驗證，遮罩後只傳已驗證的內部限制，避免個資遮罩使合法選項變成無效。一般 API 不能提交該內部限制。可設定獨立 `CLARIFICATION_SIGNING_KEY`；未設定時由現有伺服器密鑰衍生。金鑰輪替會使舊題目的 token 失效，使用者可保留草稿並以一般輸入繼續。

資訊足夠時 `interaction_mode=answer` 且 `clarification=null`。前端也以 mode 為準，不讓殘留題目資料遮住輸入框。

## 分析事件與診斷

v3 在完成模型 JSON 與來源檢查後，送出 SSE `analysis` 摘要，再依段發出答案；不是原始 token 思考串流。每則事件為：

```json
{"stage":"sources","summary":"資料庫檢索取得 4 筆資料；回答實際引用 2 筆。","facts":[],"source_labels":["來源標籤"],"limitations":[]}
```

四種 stage 為 `understanding`、`sufficiency`、`sources`、`answer`；`done.analysis` 保留完整陣列。摘要只使用逐字使用者證據、實際檢索／引用資料與最後答案摘錄，限制作有界摘要。一般介面不顯示各步或總秒數；舊訊息沒有摘要時不補造。技術時間仍可在管理診斷檢查。SDK 的 DEBUG 請求內容／標頭輸出被抑制，不新增一般聊天全文後端保存。

每個請求固定一份 RuntimeConfig snapshot。公開 health 的 `client_settings` 只投影 UI 所需設定；完整設定只對驗證後的管理員提供。

| pipeline 欄位 | 預設 | 作用 |
| --- | --- | --- |
| mask_message / mask_case_context / mask_retrieval_query | true | 分別遮罩訊息歷史、摘要／選取、檢索查詢，另受 enable_anonymization 總開關控制 |
| trim_history / history_max_messages / history_max_chars | true / 40 / 12000 | 依完整回合保留近期歷史；請求硬上限 200 則／120000 字元 |
| extract_facts / auto_clarify / model_selection_mode | true | 自動擷取、必要追問、模型選擇單複選 |
| enable_rag / enable_skills / enable_analysis | true | 資料庫檢索、情境 Skill、分析摘要 |
| content_policy | repair | repair 修正無依據主張；annotate 保留並標記 |

既有模型、圖片、Top K、相似度門檻與個別 Skill 設定保留。結構、網址安全、管理員驗證及請求上限不列為可停用內容功能。

管理頁分為環境設定與「本次診斷測試」。後者呼叫受驗證保護的 `POST /v1/admin/chat-test`：

```json
{
  "request": {
    "contract_version": 3,
    "message": "完全虛構的測試訊息",
    "history": [],
    "case_context": {"schema_version": 2,"revision": 0,"facts": {}},
    "stream": false
  },
  "overrides": {"pipeline": {"enable_rag": false}}
}
```

回應 `{response, diagnostics}`，包含生效設定、歷史保留／移除數、遮罩種類、事實採用原因、檢索分數／排除原因、引用數、修正原因及 draft/final 答案。覆寫不更新 Firestore，測試內容不加到一般對話或 localStorage；離開測試分頁會取消請求。一般聊天拒絕 overrides。Provider 明確回報輸出長度上限時，回應 `model_output_limit`、`retryable=false`，避免相同預算反覆生成；不自動改寫管理員的 max_tokens。

## 法源與驗收界線

六部法律離線快照共 2,017 筆完整條文，1,929 筆可供現行方向檢索；刪除、尚未施行條文隔離。公布與生效日期是來源提供的法規層級資訊，缺資料不捏造。維護、差異、dry-run、批次回復、奈秒 timestamp 與中斷 journal 的說明見 [法源維護手冊](../../resource/legal/README.md)。本次沒有執行正式匯入或部署索引，因此線上資料庫尚不具有整份新法源；本機更新不代表線上服務已更新。

工程回歸涵蓋第一／第三人稱、引述、疑問、拒答、更正、回答保留、精確引用、複選互斥、遷移、契約協商、RAG 關閉零檢索、單次設定隔離及 SDK 日誌隱私。原茶水間例題保留 development／unreviewed 邊界，沒有升級為法律專家 gold。

真實模型整合測試使用另寫的甲乙虛構案例，固定提供六段本機官方條文；沒有執行 embedding 或查詢 Firestore。已觀察到主要性工法方向、部屬對主管關係、試圖而非完成的行為階段、附條件的刑事評估、正常 answer 模式及個人摘要空白。首次模型紀錄曾誤稱法源日期在未來，後續加入伺服器日期並在瀏覽器重新測試，未再觀察到該說法；首次紀錄保持原樣，不能用它證明修正效果。首次紀錄另有來源未直接支持求助電話敘述的問題，引用支持與法律推論仍待專家覆核。這些測試不能當作 Firestore 檢索召回、雲端部署或法律正確性證明。

瀏覽器另驗證四階段摘要、一般輸入框，以及模型產生的複選題。選取兩項具體答案與「自行補充」後，隱藏、重開、重新整理仍保留三項選取及補充文字；這次草稿沒有送出，選取驗證另由 API 回歸測試覆蓋。管理員單次關閉 RAG 的真實模型測試完成，診斷為 embedding／檢索皆零，切回環境設定後 RAG 預設仍啟用。

## 最終工程檢查

2026-10-01 最後一輪結果：

| 檢查 | 結果 |
| --- | --- |
| 後端完整 pytest | 478 通過 |
| 前端完整 Vitest | 23 個測試檔、282 項通過 |
| Python Ruff（本輪所有變更 Python 檔） | 通過 |
| 前端 ESLint、TypeScript、Vite build | 通過 |
| Git 差異格式 | 通過 |

後端測試使用明確的測試 CORS origins，涵蓋 localhost 與既有 Hosting 網域，沒有改動環境預設。建置仍有主要 JS bundle 略超過 500 kB 的提示；未把它視為效能已驗證。Node 的 module.register 棄用提示不影響這次測試或建置。

本輪臨時 Flask（5108）與 Vite（5181）服務已停止。另偵測到 5001 的 Firebase 由既有互動終端啟動；它不是本輪驗證服務，已保留，故不能宣稱目前所有 Emulator 都已關閉。沒有提交 Git commit、部署或寫入雲端法源。
