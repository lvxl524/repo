#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
echo "使用 build_repo.py 重建索引（纯 Python，无需 apt/dpkg）"
if command -v python3 >/dev/null 2>&1; then
  python3 build_repo.py
else
  echo "::error::未找到 python3"; exit 1
fi
echo "---- 生成的 Packages ----"; cat Packages
