#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

if [[ $# -gt 1 || ( ${1:-} != "" && ${1:-} != "--fix" ) ]]; then
    echo "Usage: ./lint.sh [--fix]" >&2
    exit 2
fi

python_targets=(
    client/backend admin/backend shared/python
    client/tests admin/tests shared/tests scripts conftest.py
)

if [[ ${1:-} == "--fix" ]]; then
    echo "修復 Python 與前端 lint 問題..."
    uv run --frozen --all-extras ruff check --fix "${python_targets[@]}"
    uv run --frozen --all-extras ruff format "${python_targets[@]}"
    pnpm -r lint --fix
fi

echo "檢查 Python lint 與格式..."
uv run --frozen --all-extras ruff check "${python_targets[@]}"
uv run --frozen --all-extras ruff format --check "${python_targets[@]}"

echo "檢查 shared、Client 與 Admin 前端 lint..."
pnpm -r lint

echo "檢查 shared、Client 與 Admin TypeScript 型別..."
pnpm --filter @rag/shared build
pnpm --filter @rag/client exec tsc -b
pnpm --filter @rag/admin exec tsc -b

echo "所有 lint、格式與型別檢查皆通過。"
