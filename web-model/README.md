# Qwen3-0.6B 個資片段辨識 LoRA：獨立訓練專案

這個資料夾可整份複製到其他位置執行。所有 Python 程式只讀取本資料夾的程式、提示詞、資料與模型；不需要原專案的後端、前端、環境變數、Firebase 或 API 金鑰。

目標是改善繁體中文個資辨識：先逐字擷取片段，再判斷類型。不是訓練法律問答模型。使用原始 `Qwen/Qwen3-0.6B`，不是瀏覽器已量化的 `*-MLC` 模型。

本版提供可訓練的程式與 **360 筆合成標注案例**，尚未進行正式 0.6B 微調、品質驗收或瀏覽器部署。資料是種子集，沒有宣稱人工逐筆審核或足以保證隱私安全。

2026-10-13 已驗證：6 項契約測試通過；整份資料夾複製至獨立位置後仍可做資料檢查與 dry-run；官方 Qwen3 tokenizer 檢查全部 1,360 筆範例（train／valid／test 最長 455／418／413 tokens）；極小隨機 Qwen3 的實際梯度更新、原權重凍結、adapter 保存／重載／合併與 loss mask 通過。這個 tiny 測試使用 CPU，不是原始 0.6B 訓練或 Metal 效能測試。

## 1. 先理解會訓練什麼

LoRA 會凍結原始模型的大部分參數，只訓練加在特定線性層的小型矩陣。你可以把它理解成替原模型加一份可學習的修正。訓練產物 `adapters.safetensors` 不能單獨使用，需要原模型，或先把兩者合併。

同一個 adapter 學習兩個任務：

1. **Locate**：輸入一段文字，輸出 `{"spans":["林予澄","train.user0@example.invalid"]}`。必須逐字複製原文，沒有個資則為空陣列。
2. **Classify**：輸入原文與其中一個候選片段，輸出 `{"type":"person"}`；也可能是 `organization`、`address`、`contact`、`identifier`、`none`。

這裡的任務政策將公司／學校名稱也列為需要遮罩的資訊，並不是法律上的個資定義判定。日期、年齡、城市、職稱、法規、110／113／119、方括號代稱，依本任務規格不標注。

`prompts.json` 已獨立保存兩階段提示詞與三個固定示例。它是 `zh-span-extraction-v4-staged` 的快照，執行時不讀取任何外部專案檔案。訓練與 Python 評估共用同一份提示詞。

## 2. 電腦與環境

目前訓練後端是 **macOS Apple Silicon（M 系列）+ MLX**。固定的 MLX 0.30.4 wheel 在本次環境使用 macOS 26 版本；建議 macOS 26+、Python 3.13（套件亦以 Python 3.11–3.13 為目標）。Windows、Linux、Intel Mac 可做純 Python 資料檢查，但不能直接執行本版 Metal 訓練後端。跨平台 CUDA 訓練需要另接 Transformers/PEFT backend。

16 GB 統一記憶體可先嘗試本版 batch=1、長度 2048、gradient checkpointing 設定；實際峰值仍以 smoke run 為準。建議預留至少 8 GB 磁碟供套件、原模型、合併模型及匯出使用。第一次下載／安裝需要網路；模型下載完成後，訓練與推論在本機執行，沒有把訓練資料傳給外部推論 API。

以下命令都在本資料夾執行：

```bash
cd web-model
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip check
```

若已複製成獨立資料夾，直接 `cd` 到該位置即可。不要複製 `.venv`，換電腦後重建。固定直接依賴在 `requirements.txt`，本次完整安裝版本另存 `requirements-lock-macos-py313.txt`。

## 3. 先看資料，再開始訓練

| 分組 | 原文案例 | 展開後訓練格式 | 無個資案例 | 用途 |
|---|---:|---:|---:|---|
| train | 240 | 880 | 50 | 更新 LoRA 參數 |
| valid | 60 | 240 | 20 | 選設定與 checkpoint |
| test | 60 | 240 | 20 | 設定確定後的最終檢查 |

一筆原文會展開成一筆 locate，以及每個不同個資片段／指定負例各一筆 classify，所以 360 筆原文合計是 **1,360 筆 SFT 範例**，不是 1,360 段互相獨立的原文。

- `data/annotations.jsonl`：完整原文、類型、起訖位置、分組、句型家族、負例候選、合成來源與審核狀態。
- `data/train.jsonl`、`valid.jsonl`、`test.jsonl`：已展開的 messages 格式。
- `data/manifest.json`：數量與 SHA-256，便於追蹤版本。
- `DATASET.md`：標注規則與擴充方法。

24 個訓練句型家族、6 個驗證家族、6 個測試家族彼此分開；每個家族產生 10 個版本。具體姓名、單位、聯絡資訊、地址、識別碼也不跨組。這能減少直接洩漏，但仍是同一套合成方法，不能當成真實使用者分布。三個固定 prompt 示範不屬於任何評估案例；同一份示範會出現在所有 locate 輸入。

```bash
python prepare_data.py --check
python -m unittest discover -s tests -v
python train.py --dry-run
```

以上不下載模型、不訓練。`--check` 檢查標注、重複文字、跨組洩漏與展開檔案一致性。`--dry-run` 顯示訓練計算與資料指紋。

`python prepare_data.py` 會重新產生種子資料，**覆寫 data 下的標注與展開檔案**；人工修改後應改用 `python prepare_data.py --from-annotations` 保留修改內容。

## 4. 下載原始模型

```bash
python download_model.py --output models/base
```

程式先取得 Hugging Face commit SHA，再下載固定版本；`models/base/download.json` 記錄版本。模型權重約為十億位元組量級，下載時間取決於網路。若要重現實驗：

```bash
python download_model.py --revision 你的模型commitSHA --output models/base-repeat
```

`--tokenizer-only --output models/tokenizer` 只下載 tokenizer／設定，供開發驗證，不可拿這個目錄當正式訓練權重。下載器遇到非空目錄會停止，避免混合兩版資產。

下載後可執行 `python check_tokens.py --model models/base`；若只下載 tokenizer，則指定 `models/tokenizer`。它不載入模型權重，會檢查全部 1,360 筆範例是否使用正確的非思考前綴、第一個答案 token 與 EOS，並列出各組最長長度。

## 5. 建立微調前基準

```bash
python evaluate.py --model models/base --split valid --output runs/base-valid
```

這會真正用原模型推論，輸出逐筆 `predictions.jsonl`（包括每階段原始回覆）及 `metrics.json`。只想確認推論能跑，可加 `--limit 3` 並使用不同輸出目錄；少數案例結果不代表整組品質。

主要指標：

- **Recall**：標注的個資中，成功抓到且類型正確的比例。個資漏抓是此任務的重要風險。
- **Precision**：預測片段中，文字與類型都完全正確的比例；可看是否過度遮罩。
- **F1**：precision 與 recall 的綜合值。
- **valid_output_rate**：整段 locate 與所有 classify 都通過嚴格格式檢查的比例。
- **case_exact_rate**：整筆案例的片段及類型集合完全吻合的比例。
- **negative_case_accuracy**：沒有個資的案例能合法輸出空結果的比例。

採「不同的 `(文字, 類型)` 配對」計分，重複姓名只算一個；不做模糊比對。任何一階段回覆無效，該案全部視為無有效輸出，原本有個資的部分會計入 FN，不能藉格式錯誤逃過漏抓計分。

Python 評估使用 greedy decoding，沒有瀏覽器的 JSON grammar 約束、800 字分塊／重疊／timeout。因此它適合比較同一條 Python 流程下微調前後變化，**不是與舊 WebLLM 分數直接等價的測試**。量化後還要在真正瀏覽器重測。

## 6. 先跑 8 次迭代的 smoke run

```bash
python train.py --model models/base --steps 8 --output runs/smoke
python evaluate.py --model models/base --adapter runs/smoke --split valid --limit 3 --output runs/smoke-valid
```

smoke run 只確認模型可載入、算出 loss、反向傳播、儲存及重載 adapter，不期待 8 次迭代帶來效果。遇到非空 output 目錄會停止；下次改用 `runs/smoke-2`。

本程式會先對完整 train／valid 分詞，確認每筆連同提示詞與答案不超長。超過 `max_seq_length` 會明確報錯，不會默默截掉答案。Qwen3 使用 `enable_thinking=False`，並只對**最後一段 assistant 答案及 EOS**計算 loss；system、few-shot 示例、使用者原文、空 thinking 前綴與 padding 都不計 loss。

若只想確認 MLX 訓練 API 可用，可先執行 `python tests/smoke_backend.py`。這個測試在 CPU 上建立極小的隨機 Qwen3，測試反向傳播、凍結參數、adapter 儲存／重載／合併，不下載原始 0.6B 權重，也不能用來衡量任務品質。

## 7. 第一輪正式訓練

```bash
python train.py --model models/base --output runs/lora-v1
python evaluate.py --model models/base --adapter runs/lora-v1 --split valid --output runs/lora-v1-valid
```

初始設定在 `configs/lora.json`：

| 參數 | 初值 | 意義 |
|---|---:|---|
| epochs | 2 | 約看完 train 兩遍 |
| batch_size | 1 | 一次處理一筆，減少記憶體 |
| grad_accumulation_steps | 4 | 累積四筆才更新一次，effective batch=4 |
| learning_rate | 0.00005 | 每次更新幅度，先小幅微調 |
| rank | 8 | LoRA 修正矩陣的容量 |
| scale | 16 | **MLX 的直接倍率**，不要直接當成 PEFT 的 lora_alpha |
| dropout | 0.05 | 訓練時的隨機丟棄比例 |
| layers / targets | 全部層的 q_proj、v_proj | 只調注意力的兩組投影 |
| max_seq_length | 2048 | 含提示詞與答案的最大 token 數 |
| seed | 42 | 固定隨機種子；不保證不同硬體位元級一致 |

MLX 的 `iters` 是 microbatch 次數。880 筆、batch=1、2 epochs，共 1,760 次 microbatch、440 次 optimizer update。`--steps` 必須是 4 的正倍數；程式會防止最後留下未更新的梯度。每 200 次驗證及保存，最後也保存 adapter；沒有自動 early stopping 或自動挑 best checkpoint。

看到 train loss 下降，只表示模型更容易產生訓練答案；還需看 valid loss、召回率、錯誤類型。若 valid 變差，先縮短為 1 epoch 或降低 learning rate。若姓名、地址持續漏抓，優先補充不同寫法並人工審核，不要只將相同模板重複更多次。

最後的 `adapters.safetensors` 不是自動選出的最佳模型。若想比較第 800 次迭代的 checkpoint，可以複製成獨立 adapter 目錄後評估：

```bash
mkdir -p runs/checkpoint-800
cp runs/lora-v1/adapter_config.json runs/checkpoint-800/adapter_config.json
cp runs/lora-v1/0000800_adapters.safetensors runs/checkpoint-800/adapters.safetensors
python evaluate.py --model models/base --adapter runs/checkpoint-800 --split valid --output runs/checkpoint-800-valid
```

訓練紀錄中的週期性 valid loss 在該次迭代更新前計算，checkpoint 在更新後保存；選定候選後應用上面的完整任務評估確認。不要用 test 組挑 checkpoint。

訓練時間尚未量測，不提供虛構分鐘數。用 smoke run 的 tokens/sec／記憶體峰值估算；完整每 200 次驗證也會耗時。執行時讓電腦接電，並關閉占用大量記憶體的其他模型工作。

## 8. 建議的三階段訓練計畫

1. **流程驗證**：使用現有種子集，完成 baseline → 8-step smoke → 2-epoch 初訓 → valid 比較。這一輪的目的是確認任務能被學到，並找最常見錯誤。
2. **資料改善**：人工審核全部種子標注；再加入至少數百筆不同來源／句型的合成或已獲授權案例。優先增加暱稱、英文姓名、不同電話形式、真正複雜地址、身分證形式、混合中英文、錯字、長文、文字中的指令、難以判別的泛稱。初步可規劃 1,000–3,000 筆原文，但數量不是達標保證。家族及衍生版本整組放同一 split。
3. **凍結與驗收**：只用 valid 選設定；選定後才比較 base 和 LoRA 的完整 test。再以獨立人工案例及瀏覽器端量化模型驗收。若已根據 test 錯誤修改訓練資料，該 test 已成開發資料，需建立新的最終測試集。

```bash
python evaluate.py --model models/base --split test --output runs/base-test
python evaluate.py --model models/base --adapter runs/lora-v1 --split test --output runs/lora-v1-test
```

建議驗收至少要求：所有五類召回率都改善或沒有明顯退步、格式成功率接近完整、負例沒有明顯惡化，並逐筆檢視漏抓。不要僅憑綜合 F1 導出「可以完全保護個資」。實際上線門檻需依使用情境與更大測試集決定。

## 9. 合併與 WebLLM 匯出

```bash
python export_model.py --adapter runs/lora-v1 --output models/merged-v1
python evaluate.py --model models/merged-v1 --split valid --output runs/merged-v1-valid
```

合併後應再比較結果，確認 adapter 沒有遺失。合併產物是完整 safetensors 權重，**還不能直接放進 WebLLM**。MLX adapter 也不是可直接交給 PEFT 的 adapter 格式。

瀏覽器路徑是：

```text
原始 Qwen3 + LoRA adapter
  → 合併的 safetensors
  → MLC convert_weight（q4f16_1）與 gen_config
  → MLC 權重分片、tokenizer、mlc-chat-config.json
  → 相容的 WebGPU WASM model library
  → 自訂 WebLLM model_list + 瀏覽器驗收
```

MLC 編譯工具不屬於訓練依賴，建議另外建立環境，依 [MLC 安裝文件](https://llm.mlc.ai/docs/install/mlc_llm.html) 安裝相容的 `mlc_llm` 與 TVM。以下為後續轉換範本，須以安裝版本的 `--help` 及 Qwen3 conversation template 確認；本次沒有執行 MLC 轉換：

```bash
python -m mlc_llm convert_weight models/merged-v1 --quantization q4f16_1 -o dist/qwen3-pii-v1
python -m mlc_llm gen_config models/merged-v1 --quantization q4f16_1 --conv-template qwen3 -o dist/qwen3-pii-v1
```

保持模型架構／詞表不變，可能重用相容的 Qwen3-0.6B WASM，但仍要核對量化模式、context/prefill、WebLLM 與 MLC 版本。另須核對非思考模式模板；不要只換權重 URL 就宣稱完成部署。原模型的 safetensors、adapter 或 GGUF 都不能代替 MLC 權重。

程式碼與小型合成資料可放 GitHub；模型權重建議放 Hugging Face 的 model repository。發布時同時保留 base commit、提示詞版本、資料 hash、LoRA 設定、評估方式、Apache-2.0 原模型授權資訊。不要把私人訓練原文放入公開模型 repository。此專案沒有自動登入、建立 repository 或上傳功能。

## 10. 常見問題與檔案

- **安裝失敗**：先確認 Apple Silicon、macOS 與 Python 版本；不要將 MLX 安裝到父專案的 `.venv`。本次已修正 MLX-LM 0.31.1 要求 Transformers 5 的依賴衝突。
- **提示 PyTorch was not found**：本版由 MLX 執行模型，Transformers 僅用於 tokenizer；這則提示本身不代表 MLX 不能訓練，不必因此安裝 PyTorch。
- **CPU smoke 找不到 g++**：確認已安裝 Xcode Command Line Tools，且 `/usr/bin` 位於 PATH。受限沙箱若連 Metal 初始化都失敗，請在一般 macOS 終端機執行。
- **記憶體不足**：維持 batch=1、gradient checkpointing；可將層數降成 16。若要降低長度，先確認資料真的能容納；程式不會靜默截斷。
- **loss 是 NaN**：停止該輪，檢查資料及套件版本、降低 learning rate；使用新的 output 保留故障證據。
- **輸出總是空陣列**：檢查各類 recall、正負案例平衡、prompt 是否一致；不要只看 JSON 合法率。
- **想接著訓練**：本版刻意不提供精確 resume；單純載入 adapter 不會恢復 optimizer／隨機狀態。本版每次從指定 base 建立新實驗。
- **時間太久**：兩階段每個候選都要一次推論，eval 可能比想像中慢；先用 `--limit` 確認流程，再做完整評估。

`train.py` 是訓練入口；`backend.py` 實作 LoRA 更新及排除 padding 的 loss；`evaluate.py` 負責完整兩階段評估；`export_model.py` 合併；`download_model.py` 固定下載版本；`common.py` 實作 prompt、嚴格解析及答案分詞邊界；`tests/` 驗證資料與計分契約。`runs/`、`models/`、`.venv/`、`dist/` 已加入本資料夾 `.gitignore`。每次實驗另存 `run.json` 設定／版本紀錄與 `loss.jsonl` 訓練／驗證 loss。

官方參考：[Qwen3-0.6B 模型卡](https://huggingface.co/Qwen/Qwen3-0.6B)、[Qwen 微調指南](https://qwen.readthedocs.io/en/latest/training/ms_swift.html)、[MLX LoRA](https://github.com/ml-explore/mlx-lm/blob/v0.31.1/mlx_lm/LORA.md)、[MLC 權重轉換](https://llm.mlc.ai/docs/compilation/convert_weights.html)、[WebLLM 自訂模型](https://llm.mlc.ai/docs/deploy/webllm.html)。
