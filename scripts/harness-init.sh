#!/usr/bin/env bash
# harness-init.sh — 一键初始化 Harness 工程底座
#
# 用法:
#   bash <(curl -sfL https://raw.githubusercontent.com/xingyun0812/ai-platform-lab/main/scripts/harness-init.sh) python
#   bash <(curl -sfL https://raw.githubusercontent.com/xingyun0812/ai-platform-lab/main/scripts/harness-init.sh) typescript-react my-project
#   bash <(curl -sfL https://raw.githubusercontent.com/xingyun0812/ai-platform-lab/main/scripts/harness-init.sh) --list
#
# 可选参数:
#   第一个参数: 模板名 (python | python-fastapi | typescript-react | go | rust)
#   第二个参数: 项目名 (默认从当前目录名推断)
#   --list:   列出可用模板
#   --help:   显示帮助

set -euo pipefail

REPO_BASE="https://raw.githubusercontent.com/xingyun0812/ai-platform-lab/main/.claude/init-templates"

# ---------- 可用模板 ----------
TEMPLATES="python python-fastapi typescript-react go rust"

show_help() {
    cat <<EOF
用法: harness-init.sh <模板名> [项目名]

可用模板:
  python             Python CLI / 库项目
  python-fastapi     FastAPI 后端项目 (含 web, uvicorn 相关配置)
  typescript-react   TypeScript React 前端项目
  go                 Go 服务/CLI 项目
  rust               Rust 项目

示例:
  bash harness-init.sh python my-awesome-tool
  bash harness-init.sh typescript-react

项目名可选，默认从当前目录名推断。
EOF
    exit 0
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    show_help
fi

if [[ "${1:-}" == "--list" ]]; then
    echo "可用模板:"
    for t in $TEMPLATES; do
        echo "  - $t"
    done
    exit 0
fi

# ---------- 参数 ----------
TEMPLATE="${1:-}"
PROJECT_NAME="${2:-$(basename "$(pwd)")}"

if [[ -z "$TEMPLATE" ]]; then
    echo "请指定模板名。可用模板:"
    for t in $TEMPLATES; do
        echo "  - $t"
    done
    echo ""
    echo "示例: bash harness-init.sh python"
    exit 1
fi

# 验证模板
VALID=false
for t in $TEMPLATES; do
    if [[ "$t" == "$TEMPLATE" ]]; then
        VALID=true
        break
    fi
done

if ! $VALID; then
    echo "未知模板: $TEMPLATE"
    echo "可用模板: $TEMPLATES"
    exit 1
fi

# ---------- 开始安装 ----------
echo ""
echo "==> Harness 工程底座初始化"
echo "    模板:      $TEMPLATE"
echo "    项目:      $PROJECT_NAME"
echo "    目标目录:  $(pwd)"
echo ""

# 确认
read -rp "继续? [Y/n] " confirm
if [[ "$confirm" != "" && "$confirm" != "Y" && "$confirm" != "y" ]]; then
    echo "已取消"
    exit 0
fi

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

echo "==> 下载模板: $TEMPLATE ..."

# 下载文件列表
FILES_TO_DOWNLOAD=(
    "CLAUDE.md"
    ".pre-commit-config.yaml"
    "Justfile"
    "scripts/setup-hooks.sh"
)

mkdir -p scripts

for file in "${FILES_TO_DOWNLOAD[@]}"; do
    url="$REPO_BASE/$TEMPLATE/$file"
    echo "   下载 $file ..."
    if curl -sfL "$url" -o "$TMPDIR/$file" 2>/dev/null; then
        # 替换占位符
        sed_cmd="s/<PROJECT_NAME>/$PROJECT_NAME/g"
        if [[ "$(uname)" == "Darwin" ]]; then
            sed -i '' "$sed_cmd" "$TMPDIR/$file"
        else
            sed -i "$sed_cmd" "$TMPDIR/$file"
        fi
        # 复制到项目
        target_dir=$(dirname "$file")
        if [[ "$target_dir" != "." ]]; then
            mkdir -p "$target_dir"
        fi
        cp "$TMPDIR/$file" "$file"
    else
        echo "   ⚠️ 下载失败 (非致命): $file"
    fi
done

# 下载 pyproject.toml.append (仅 Python 项目)
if [[ "$TEMPLATE" == "python" || "$TEMPLATE" == "python-fastapi" ]]; then
    echo ""
    echo "==> 检测到 Python 项目，需要手动追加 pyproject.toml 配置:"
    echo ""
    if curl -sfL "$REPO_BASE/$TEMPLATE/pyproject.toml.append" -o /dev/null 2>/dev/null; then
        curl -sfL "$REPO_BASE/$TEMPLATE/pyproject.toml.append"
        echo ""
        echo "    请手动将上述内容追加到你的 pyproject.toml 中。"
    fi
fi

# ---------- 安装依赖 ----------
echo ""
echo "==> 配置环境..."

# pre-commit
if command -v pre-commit &>/dev/null; then
    echo "   安装 pre-commit hooks ..."
    pre-commit install 2>/dev/null || echo "   ⚠️ pre-commit install 失败（可能是仓库未初始化）"
else
    echo "   ⚠️ pre-commit 未安装。请手动安装:"
    echo "      pip install pre-commit  # 或 brew install pre-commit"
fi

# just
if command -v just &>/dev/null; then
    echo "   ✅ just 已安装"
else
    echo "   ⚠️ just 未安装。建议安装:"
    echo "      brew install just       # macOS"
    echo "      cargo install just      # 或通过 cargo"
fi

# ---------- 完成 ----------
echo ""
echo "========================================"
echo "  ✅ Harness 底座初始化完成！"
echo "========================================"
echo ""
echo "  已创建/修改的文件:"
for file in "${FILES_TO_DOWNLOAD[@]}"; do
    if [[ -f "$file" ]]; then
        echo "    • $file"
    fi
done
echo ""
echo "  建议下一步:"
echo "    1. 手动检查并修改 CLAUDE.md（补充启动命令、环境变量）"
echo "    2. 初始化 git 仓库: git init"
echo "    3. 补充项目依赖"
echo "    4. 设置 CI (GitHub Actions)"
echo ""
echo "  注意: 这些模板是起点，请根据实际项目调整。"
echo ""
