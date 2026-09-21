---
{
  "name": "choose-next-step",
  "description": "在使用者想了解下一步、希望挑選方向或要求彈出選項時，用通用 options action 讓使用者選擇要討論的內容。",
  "metadata": {
    "scenario": {
      "id": "choose_next_step",
      "name": "選擇下一步",
      "enabled": true,
      "priority": 80,
      "trigger_keywords": ["下一步", "怎麼辦", "怎麼做", "如何處理", "不知道", "選項", "選擇", "可以做什麼", "處理方式", "彈出", "彈窗"],
      "actions": [
        {
          "action": "options",
          "id": "choose_next_step",
          "label": "選擇接下來想了解的事",
          "title": "你想先了解哪一個方向？",
          "options": [
            {"label": "了解處理流程", "value": "我想先了解這個情況可以採取的處理流程。"},
            {"label": "整理事件與資料", "value": "請協助我整理事件經過與目前已有的資料。"},
            {"label": "尋找支援資源", "value": "我想了解有哪些可以聯絡的支援資源。"}
          ]
        }
      ]
    }
  }
}
---

# 選擇下一步

使用者不確定下一步、希望自行挑選討論方向或明確要求「彈出選項讓我選擇」時，先簡短說明可選擇想了解的內容，再將 `{"action":"options","id":"choose_next_step"}` 放進 `action_buttons`。使用者已在上一輪同意挑選方向時也可提供。

`options` 是通用的選單按鈕：點擊只開啟選擇視窗；使用者選定後，設定中的 `value` 才會以使用者訊息送出。依該訊息接續回覆，資訊不足時再詢問具體問題。不要宣稱選擇已完成申訴、聯絡或資料提交。

只輸出本 Skill 列出的 `action` 與 `id`，不要把 `label`、`title`、`options` 重新編造在模型輸出中。它們由伺服器從 Skill 設定補齊；選項不需再逐項放入 `suggested_replies`。`suggested_replies` 仍提供 2 至 4 個貼近當下對話的簡短回覆。

其他情境使用相同方法：管理者為選單設定唯一 `id`、按鈕 `label`、視窗 `title`，以及 2 至 8 個 `{label, value}` 選項，再以觸發詞和本段指示說明何時提供。若現有選單與需求無關，就不提供，不使用未設定的選單 ID。
