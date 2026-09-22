# Runtime Skills 與通用 Actions

這些 `SKILL.md` 會由 `core/scenario_scripts.py` 實際載入 agent。Markdown 本文是情境指令；frontmatter 使用 JSON（YAML 的相容子集），提供觸發詞與按鈕設定。新增情境通常只需要新增 Skill 或在管理面板設定，不需要修改按鈕元件或 API。

| Skill | 情境 | 共用動作 |
| --- | --- | --- |
| [call-support](call-support/SKILL.md) | 索取電話、同意撥打求助電話 | `tel` |
| [official-website](official-website/SKILL.md) | 開啟或取得機關網站 | `url` |
| [choose-next-step](choose-next-step/SKILL.md) | 不確定下一步、希望選擇方向（也接受「彈出選項」的舊說法） | `options` |

編寫 Skill 時，說明「何時使用」、「何時不使用」、「應選哪個動作」。標籤、目的網址及選項屬於設定；模型僅複製下列 selector 至回覆的 `action_buttons`，最多三個，無適合動作時為空陣列：

```json
[
  {"action": "tel", "phone_number": "113"},
  {"action": "url", "url": "https://www.pthg.gov.tw/"},
  {"action": "options", "id": "choose_next_step"}
]
```

API 會依 selector 找到已核准設定，再傳給前端。`tel` 與 `url` 保留直接撥號或開啟網站的按鈕；`options` 與補充資訊、建議回覆共用詢問選單，顯示時取代一般輸入區。選單右上角可隱藏選單以恢復一般輸入，再透過「顯示選單」入口開啟；切換時保留選項、「其他」內容與一般輸入草稿。每組選單以 `title` 作為問題，只搭配自己的 `options`；使用者選擇選項或填寫「其他」後，按「送出回覆」才提交，選取本身不會發送訊息。「其他」文字欄位由前端自動提供，不要將它加進設定的 `options` 或模型的 `suggested_replies`。

`value` 應寫成有上下文的使用者回覆，例如「我想先了解這個情況可以採取的處理流程」，避免只有「第一個」而無法接續對話。多題答案需保留所屬問題的脈絡，不能混成一組無法判斷對應問題的短句。送出後，使用者訊息泡泡會將問題以淡色置於答案上方；這項呈現使用本機 UI 保存的 metadata，API 仍傳送包含完整問答脈絡的文字，不新增模型或 API 欄位。

需要補充事實時，agent 使用 `interaction_mode: "clarify"` 與 `clarifying_questions`。沒有適用的既有 Skill 選單時，用 `suggested_replies` 提供 2 至 4 個能直接回答問題的具體可能答案；例如「事件發生在哪裡？」可搭配「在工作場所」、「在學校」、「在公共場所」。目前 `suggested_replies` 沒有逐題綁定欄位，優先每輪只問一個主要問題。若必須同時追問多題，答案選項需能清楚回答整組問題；若難以列舉具體答案，仍須提供與問題相關的短句，例如「我目前不確定發生地點」、「我暫時不方便提供發生地點」，使用者也能在「其他」欄位自行填寫。追問事實時不應插入無關的「選擇下一步」選單，也不能為了產生選項而編造未核准的 action ID 或 payload。

若選用已設定且符合目前問題的 `options`，前端優先呈現設定選項。回覆契約仍要求 `suggested_replies` 提供 2 至 4 個不重複的非空短句，可使用相關的延伸回覆，不必複製選單選項。Skill 指示應教 agent 如何依答案繼續對話，不要宣稱點選已完成申訴、聯絡或任何外部提交。

新增檔案時，在此目錄下建立 `<skill-name>/SKILL.md`，沿用現有 frontmatter：

```markdown
---
{
  "name": "resource-menu",
  "description": "使用者想選擇資源類型時，提供資源選單。",
  "metadata": {
    "scenario": {
      "id": "resource_menu",
      "name": "資源選單",
      "enabled": true,
      "priority": 50,
      "trigger_keywords": ["選擇資源", "資源選單"],
      "actions": [{
        "action": "options",
        "id": "resource_menu_choices",
        "label": "選擇想了解的資源",
        "title": "希望先了解哪一種資源？",
        "options": [
          {"label": "電話", "value": "我想了解可以聯絡的求助電話。"},
          {"label": "網站", "value": "請提供屏東縣政府官方網站。"}
        ]
      }]
    }
  }
}
---

使用者要求選擇資源時，簡短說明後在 action_buttons 提供
{"action":"options","id":"resource_menu_choices"}。
若使用者已指定電話或網站，直接處理該需求，不再要求選擇。
```

Skill ID 與 options ID 使用 2–64 個小寫英文字母、數字、底線或連字號，且以字母開頭。選單 ID 應在所有 Skills 間保持唯一；同時匹配的 Skills 若提供相同 selector，優先使用較高 priority 的設定。`label` 最長 80 字、`title` 最長 160 字，每個選項的 `value` 最長 500 字且不得重複。URL 最長 2048 字，只接受具有效主機名稱、無帳密及控制字元的 HTTP(S) 網址。

Firestore 同 ID 設定優先於檔案，包含 `enabled: false`；管理面板修改可在執行時生效。新增或修改 packaged `SKILL.md` 則需重啟後端。`official-website` 的首頁網址可參考[數位發展部網站標章紀錄](https://accessibility.moda.gov.tw/Applications/Detail?category=20240516184854)。其他網址須先核對目的網站再設定。
