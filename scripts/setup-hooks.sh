#!/usr/bin/env bash
# ai-platform-lab pre-commit 安装脚本
# 用法: bash scripts/setup-hooks.sh

set -euo pipefail

echo "==> 安装 pre-commit..."

# 检查 pre-commit 是否已安装
if ! command -v pre-commit &>/dev/null; then
    echo "  pre-commit 未找到，正在通过 pip 安装..."
    pip install pre-commit
fi

echo "==> 安装 pre-commit hooks..."
pre-commit install

echo "==> 运行一次全量检查..."
pre-commit run --all-files || {
    echo ""
    echo "⚠️  部分 hook 失败。请修复上述问题后重试。"
    echo "   你也可以跳过 hook 提交: git commit --no-verify"
    exit 1
}

echo "==> 全部通过！pre-commit hook 已生效。"
