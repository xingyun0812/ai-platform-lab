#!/usr/bin/env bash
# Phase O #93 — 数据分析 Vertical 冒烟
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

MODE=(--mock)
for arg in "$@"; do
  case "$arg" in
    --mock) MODE=(--mock) ;;
    --live) MODE=(--live) ;;
    -h|--help)
      echo "Usage: $0 [--mock|--live]"
      echo "  --mock  离线 tool 链 + YAML 校验（默认，无需 Gateway）"
      echo "  --live  需 Gateway 运行 + LLM_API_KEY（Orchestrator HTTP execute）"
      exit 0
      ;;
  esac
done

exec python3 eval/data_analysis_vertical.py "${MODE[@]}"
