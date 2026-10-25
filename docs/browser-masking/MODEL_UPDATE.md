# 下載新版 PII 模型

在 Admin「驗證及測試 → 本機遮罩驗證」按 **下載新版模型**。

1. 瀏覽器向固定 Hugging Face repository `TWCKaijin/Qwen3-0.6B-PII-v1-MLC` 查詢 main 最新 commit。
2. 將結果驗證為 40 位 SHA，後續只下載該固定 commit 的允許模型檔案；WASM 仍使用網站指定的固定版本。
3. 只清除目標模型版本及對應 runtime 的 WebLLM 快取，並要求 HTTP 快取重新下載。其他網站資料、登入狀態不會被清除。
4. 重設本頁舊驗證結果、載入量測，重新執行基本辨識檢查。通過後記住版本，下次一般載入使用該版本的快取。

模型版本號會顯示在頁面上，下載的驗證報告也記錄實際載入的 SHA。「重新載入並量測快取」沿用選定版本，不檢查遠端新版本。若遠端 commit 沒變，下載新版按鈕仍會重新下載該版本，可用於修復損壞的快取。

下載可能需要數百 MB；更新前先下載想保留的驗證報告。下載途中可按「停止」。版本查詢、快取刪除、模型載入或基本辨識失敗時不會啟用完整驗證，也不會把聊天原文送往遠端補做推論。版本查詢僅傳送固定 repository 的 GET 請求，不傳個資、不帶登入憑證。

本功能供 Admin 本機模型驗證使用，沒有替換正式聊天遮罩流程。若新模型的架構、檔名或編譯 runtime 已改變，需先更新網站相容設定；按鈕不會自動放寬下載白名單或更換 WASM。新增按鈕需發布新版前端後，線上使用者才看得到。

版本解析使用 Hugging Face 的 [model_info / ModelInfo.sha](https://huggingface.co/docs/huggingface_hub/en/package_reference/hf_api#huggingface_hub.HfApi.model_info)；本地依安裝的 WebLLM `deleteModelAllInfoInCache` API 清除指定 appConfig 的模型快取。

## 設定群組固定版本

在該環境的 Admin → 系統設定 → PII 模型版本，填入完整 40 位小寫 commit SHA，再按「儲存變更」。欄位儲存於目前環境對應的 `runtime_config/<group>`（例如 `app_dev`）的 `pii_model_revision`，沿用現有 Admin 權限、CSRF 與 revision 衝突保護。留空使用網站內建版本。不接受 `main` 或 `latest` 作為固定版本。

Admin 驗證頁預設採用群組設定；「下載新版模型」只是本頁試驗，不會寫回群組。需要正式指定剛測試的版本時，將頁面顯示的完整 SHA 填回系統設定並儲存。「載入群組指定版本」可從試驗版本切回。修改已儲存的群組版本會重設驗證頁，避免不同版本結果混合。群組設定優先於瀏覽器以前記住的試驗版本。

本版沿用現有環境隔離：要設定 `app_dev`，須登入對應此群組的 Admin 環境；不提供從單一環境跨寫任意群組的入口。這是本機遮罩驗證的模型選擇設定，正式聊天尚未接入此本機模型。
