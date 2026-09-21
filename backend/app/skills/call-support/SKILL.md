---
{
  "name": "call-support",
  "description": "在使用者希望打電話求助或索取聯絡電話時，選用已設定的通用 tel action。",
  "metadata": {
    "scenario": {
      "id": "call_support",
      "name": "電話求助",
      "enabled": true,
      "priority": 100,
      "trigger_keywords": ["撥打", "打電話", "電話", "113", "保護專線", "基金會", "專線"],
      "actions": [
        {"action": "tel", "phone_number": "113", "label": "撥打 113 保護專線"},
        {"action": "tel", "phone_number": "02-2391-7133", "label": "撥打現代婦女基金會"},
        {"action": "tel", "phone_number": "02-8911-8595", "label": "撥打勵馨基金會"}
      ]
    }
  }
}
---

# 電話求助

使用者索取求助電話、希望打電話或已同意前一輪的聯絡建議時，於回覆說明可聯絡的對象，並直接提供對應的 `tel` action。不要因為尚未重複確認而省略已明確要求的按鈕。

從本 Skill 可用動作複製 selector，例如 `{"action":"tel","phone_number":"113"}`，放入 `action_buttons`。只選與這次需求相關的號碼，最多三個；不要編造號碼、按鈕標籤或額外欄位。

按鈕會交由使用者的裝置開啟撥號介面，不表示已完成通話。僅討論事件、沒有聯絡需求時可以不提供電話按鈕。保留使用者自行決定是否撥打的空間。
