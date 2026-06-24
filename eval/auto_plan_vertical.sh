#!/usr/bin/env bash
# O1 auto_plan + 数据分析 Vertical 端到端闭环
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
      echo "  --mock  Plan→web_search/sql/calc 离线闭环（默认）"
      echo "  --live  需 Gateway + LLM_API_KEY（auto_plan + orchestrator 对照）"
      exit 0
      ;;
  esac
done

exec python3 eval/auto_plan_vertical.py "${MODE[@]}"
