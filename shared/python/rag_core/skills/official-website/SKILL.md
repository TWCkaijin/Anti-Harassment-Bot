---
{
  "name": "official-website",
  "description": "當使用者要前往已設定的機關網站時，以通用 url action 提供入口；目前包含屏東縣政府首頁。",
  "metadata": {
    "scenario": {
      "id": "official_website",
      "name": "官方網站入口",
      "enabled": true,
      "priority": 90,
      "trigger_keywords": ["屏東", "縣政府", "縣府", "官方網站", "官網", "政府網頁"],
      "actions": [
        {"action": "url", "url": "https://www.pthg.gov.tw/", "label": "前往屏東縣政府網頁"}
      ]
    }
  }
}
---

# 官方網站入口

當使用者希望開啟、查看或取得本 Skill 已設定的機關網站，或同意前一輪提供網站入口的建議時，說明入口用途，並在 `action_buttons` 提供對應的 `url` selector。使用者明確要求「前往屏東縣政府網頁」時，直接提供 `{"action":"url","url":"https://www.pthg.gov.tw/"}`。

只從本 Skill 可用動作複製 URL，不自行推測表單、申訴頁或其他子頁網址。此動作開啟機關首頁，不代表已提交申請或申訴。單純提到地名但沒有網站需求時，不必顯示此按鈕。

同一個通用 `url` action 可用於其他機關：由管理者新增已核對的 `url`、`label`、觸發詞及情境指示，前端與 API 不需要加入機關名稱判斷。模型只輸出 `action`、`url`，按鈕標籤由設定提供。
