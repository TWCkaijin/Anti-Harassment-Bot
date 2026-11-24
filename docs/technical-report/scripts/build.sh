#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/bin:/bin:${PATH:-}"
DOC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOC_PYTHON="${DOC_PYTHON:-python3}"
export PYTHONDONTWRITEBYTECODE=1
"$DOC_PYTHON" -B "$DOC_DIR/scripts/build_documents.py"
"$DOC_PYTHON" -B "$DOC_DIR/scripts/verify_artifacts.py"
