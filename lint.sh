#!/usr/bin/env bash

# 當發生錯誤時立即退出
set -e

# 確保腳本在專案根目錄執行
cd "$(dirname "$0")"

echo "=============================================="
echo "      🚀 Local Linting & Checking Script       "
echo "=============================================="

if [ "$1" == "--fix" ]; then
    echo "1. [Backend] 執行 Ruff Check (自動修復)..."
    uv run ruff check --fix backend/ tests/ || true

    echo "2. [Backend] 執行 Ruff Format (自動格式化)..."
    uv run ruff format backend/ tests/

    echo "3. [Frontend] 執行 ESLint (自動修復)..."
    cd frontend
    npm run lint -- --fix || true
    cd ..

    echo "=============================================="
    echo "✅ 自動修復與格式化完成！"
    echo "=============================================="
else
    echo "1. [Backend] 執行 Ruff Check (僅檢查)..."
    uv run ruff check backend/ tests/

    echo "2. [Backend] 執行 Ruff Format (僅檢查)..."
    uv run ruff format --check backend/ tests/

    echo "3. [Frontend] 執行 ESLint (僅檢查)..."
    cd frontend
    npm run lint
    cd ..

    echo "=============================================="
    echo "✅ 所有檢查皆通過！"
    echo "=============================================="
    echo "💡 提示：若要自動修復錯誤，請執行: ./lint.sh --fix"
fi

echo "4. [Frontend] 執行 TypeScript 型別檢查..."
cd frontend
# 使用 npx 執行 tsc 進行型別檢查 (不編譯出檔案，僅檢查)
npx tsc -b
cd ..

echo "🎉 一切正常，您可以安心推送程式碼了！"
