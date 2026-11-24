# 文件建置與驗證

只建置技術報告及三份手冊，不依賴簡報、PowerPoint、Codex 或雲端服務。

在獨立文件環境安裝 requirements-docs.txt 的依賴，避免修改隨附應用虛擬環境：

```bash
python3 -m venv /tmp/rag-docs-env
/tmp/rag-docs-env/bin/python -m pip install -r docs/technical-report/scripts/requirements-docs.txt
DOC_PYTHON=/tmp/rag-docs-env/bin/python bash docs/technical-report/scripts/build.sh
```

也可使用已安裝 ReportLab / pypdf 的 Python。DOC_FONT_REGULAR、DOC_FONT_BOLD 指向可用繁體中文 TrueType 字型；macOS 預設嘗試 STHeiti，其他平台應提供字型。

build.sh 產生 SVG、Mermaid、HTML 與 PDF。verify_artifacts.py 檢查本機連結、離線 HTML、可抽取中文字、頁數與來源／輸出雜湊；不宣稱自動完成目視檢查。預設只列摘要，需要詳細報告時設定 `DOC_VERIFICATION_OUTPUT=/tmp/rag-documents-check.json`，避免把一次性驗證中間檔留在交付目錄。

若已安裝 pdftoppm，可把 PNG 放在專案外逐頁檢視：

```bash
DOC_RENDER_DIR=/tmp/rag-delivery-doc-pages python3 docs/technical-report/scripts/render_pdf_pages.py
```

可用 RUNTIME_BIN_DIR 指定 pdftoppm 的目錄。若已安裝 Chrome 與 Playwright，可作桌面／手機離線 HTML 檢查：

```bash
DOC_EVIDENCE_DIR=/tmp/rag-delivery-html node docs/technical-report/scripts/check_html.mjs
```

PLAYWRIGHT_MODULE 可指向既有 Playwright 模組。此檢查封鎖 HTTP 外送，截圖與證據寫至指定外部目錄，不夾入交付文件。
