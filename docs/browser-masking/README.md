# 本機遮罩驗證

管理後台的「本機遮罩驗證」使用規則 + Tiny NER。規則辨識地址、聯絡資訊及識別號，CKIP Tiny NER 補人名和單位。推論在瀏覽器的獨立 Worker 以 WASM CPU 執行。

頁面使用既有的 Admin 登入權限。模型推論只處理內建合成測試資料，不會連線至聊天 API 或傳送案例文字。正式聊天的遮罩另有自己的流程。

## 準備模型

在專案根目錄執行以下指令。模型準備程式下載固定版本的公開權重，檢查 SHA-256 後在本機轉成 ONNX。

```sh
pnpm install --frozen-lockfile
uv run --script admin/frontend/scripts/prepare_tiny_ner.py
```

模型和詞表放在 `admin/frontend/.masking-tiny/`，不納入 Git。開發伺服器從這裡提供資源。模型依 GPL-3.0 授權。 [CKIP 模型卡](https://huggingface.co/ckiplab/albert-tiny-chinese-ner)

Preview 建置預設**不打包**模型，因為放上 Hosting 就等於散布 GPL-3.0 權重。確實需要在 Preview 驗證時，才在建置前設定 `MASKING_TINY_BUNDLE=1`。設定後若缺少模型檔，建置會失敗。對外散布前，須附上 GPL-3.0 授權全文、CKIP 版權聲明與權重來源，詳見 [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md)。`scripts/quality/check_admin_bundle.mjs` 會在未設定旗標時，拒絕含有模型的 Preview 建置。

## 使用與檢查

1. 依[操作手冊](../OPERATIONS.md)啟動本機 Admin API。
2. 執行 `pnpm --filter @rag/admin dev`，登入後在管理端開啟「本機遮罩驗證」。
3. 按「載入 Tiny NER」，查看 3 則基本辨識案例。這些案例不計入品質分數。
4. 依序執行開發集 80 則與保留驗證集 40 則，然後下載 JSON 報告。

停止會終止 Worker。載入失敗時，頁面會顯示錯誤並停用批次驗證。報告記錄模型與規則版本、載入時間、整體與每類精確率、召回率及 200–500 字案例的 P95。

固定合成案例的門檻為兩組資料各自的整體與每類精確率、召回率至少 95%，全部案例有效輸出，暖機後 P95 不超過 5 秒。即使通過合成案例門檻，仍需另以人工核對的獨立資料驗證。

## 本機瀏覽器測試

先準備模型並啟動本機開發伺服器，從專案根目錄執行。測試使用模擬 Admin 登入，並攔截對外與 API 請求。可用 `PLAYWRIGHT_MODULE` 指定本機 Playwright 套件路徑。

```sh
BROWSER_BASE_URL=http://127.0.0.1:5174 BROWSER_HEADLESS=true node admin/tests/browser_masking_validation.mjs
BROWSER_BASE_URL=http://127.0.0.1:5174 node admin/tests/browser_masking_failure_smoke.mjs
BROWSER_BASE_URL=http://127.0.0.1:5174 node admin/tests/browser_pii_manual_holdout.mjs
```

## 歷史評測

[2026-11-10 舊版瀏覽器遮罩評測](archive/LOCAL_EVALUATION_2026-11-10.md)記錄已淘汰的 WebGPU 方案與當時的合成案例結果。
