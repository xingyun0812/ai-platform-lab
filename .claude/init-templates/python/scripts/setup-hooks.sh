#!/usr/bin/env bash
# 安装 pre-commit hooks
set -euo pipefail

echo "==> 安装 pre-commit..."
if ! command -v pre-commit &>/dev/null; then
    pip install pre-commit
fi
pre-commit install
echo "==> pre-commit hooks 已安装"
