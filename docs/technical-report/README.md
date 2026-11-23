# 正式技術文件

閱讀版快照日期：2026-10-10。技術說明與三份手冊記錄當時的 Client／Admin 拆分版本；其中自動送出與圖片上傳等隱私敘述不適用於目前工作目錄。現行隱私流程見 [隱私邊界](PRIVACY.md)，完整操作步驟以 [操作手冊](../OPERATIONS.md) 為準，實際雲端完成狀態以 [遷移報告](../MIGRATION_REPORT.md) 為準。

| 文件 | 來源 |
| --- | --- |
| 技術報告 | [技術報告.md](技術報告.md) |
| 使用者手冊 | [manuals/使用者手冊.md](manuals/使用者手冊.md) |
| 管理者手冊 | [manuals/管理者手冊.md](manuals/管理者手冊.md) |
| 開發維運手冊 | [manuals/開發維運手冊.md](manuals/開發維運手冊.md) |

HTML 與 PDF 不納入 Git，由 [scripts/build.sh](scripts/README.md) 從上述 Markdown 產生，並附在 GitHub Release 發布。

詳細參考：[聊天契約](CHAT_CONTRACT.md)、[隱私邊界](PRIVACY.md)、[API 與設定](API_REFERENCE.md)、[批次預測](BATCH_PREDICTION.md)、[重排與統計](RERANKING.md)、[行為測試](BEHAVIOR_TESTS.md)。

密碼、資料庫、背景工作、部署及回復見 [操作手冊](../OPERATIONS.md)。本次驗收與未完成項目集中記錄於 [遷移報告](../MIGRATION_REPORT.md)，不再依賴舊版交付／留存報告。

HTML 內嵌樣式與圖形，可離線閱讀；外部參考連結需連網。圖稿來源見 [assets](assets/README.md)，重建方法見 [scripts](scripts/README.md)。文件建置不呼叫模型或雲端 API。

每次修改來源後重新建置，並檢查中文、表格、頁尾與連結。機器檢查報告可輸出至專案外，不能取代頁面目視檢查。
