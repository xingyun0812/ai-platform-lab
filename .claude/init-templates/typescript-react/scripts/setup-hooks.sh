#!/usr/bin/env bash
# 安装 pre-commit hooks (Node 项目)
set -euo pipefail

echo "==> 安装 pre-commit..."
if ! command -v pre-commit &>/dev/null; then
    if command -v brew &>/dev/null; then
        brew install pre-commit
    elif command -v pip &>/dev/null; then
        pip install pre-commit
    else
        echo "请先安装 pre-commit: https://pre-commit.com/"
        exit 1
    fi
fi
pre-commit install
echo "==> pre-commit hooks 已安装"
