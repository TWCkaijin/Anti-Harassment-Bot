# Runtime IAM 實測

`scripts/verify_runtime_iam_operator.py` 只對四個 runtime service accounts 暫授目前 gcloud 操作者 `roles/iam.serviceAccountTokenCreator`，期限 30 分鐘；每個 impersonated token 最長 900 秒，且不超過計畫剩餘期限。完成或失敗後，工具都會移除本次唯一 condition 的 bindings，並比對其他 bindings 與執行前相同。

目標專案固定為 `anti-harassment-bot`，合成文件固定為 `migration_permission_probe/not-present`。工具不查詢應用資料 collections，也不呼叫模型。四個帳號對四個 databases 各測一次讀取與一次 create-only 寫入，共 32 項；成功建立的合成文件會以 `updateTime` precondition 刪除。既有文件不會覆蓋或刪除，任何權限異常或清理失敗都停止後續矩陣。

先產生並核對計畫，再以其中的完整 `plan_sha256` 執行：

```sh
uv run python scripts/verify_runtime_iam_operator.py --output /tmp/runtime-iam-plan.json
uv run python scripts/verify_runtime_iam_operator.py --apply --plan /tmp/runtime-iam-plan.json --expected-plan-sha256 PLAN_SHA256 --propagation-wait-seconds 120 --output /tmp/runtime-iam-result.json
```

輸出檔必須尚不存在。同一輪重試須保留原本到期時間與先前報告；不得藉重建計畫延長已核准的授權視窗。`--propagation-wait-seconds 120` 會在四項授權均確認存在後，分段等待 120 秒再取得測試憑證；視窗不足時會停止。IAM policy 更新通常需要約 2 分鐘傳播，也可能更久，因此等待本身不代表權限已驗證。[Google IAM 說明](https://docs.cloud.google.com/iam/docs/access-change-propagation)

先前使用的 `__migration_permission_probe__` collection 名稱非法，不能作為 IAM 驗收依據；Firestore 禁止 collection/document IDs 符合 `__.*__`。工具現在會在 authentication 或 mutation 前拒絕這類保留名稱。[Firestore ID 限制](https://docs.cloud.google.com/firestore/quotas#collections_documents_and_fields)

驗收須同時檢查 `matrix.passed`、16 個 probe rows（32 項讀寫）、四個 `binding_removed=true` 與四個 `other_bindings_unchanged=true`。HTTP 400、只取得 token、或本機測試通過，都不等於雲端 IAM 矩陣通過。
