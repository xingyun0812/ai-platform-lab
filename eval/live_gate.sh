#!/usr/bin/env bash
# 统一 Live 验收 — 需 Gateway + LLM_API_KEY
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

REQUIRE=""
for arg in "$@"; do
  case "$arg" in
    --require-live) REQUIRE="--require-live" ;;
    -h|--help)
      echo "Usage: $0 [--require-live]"
      echo "  默认：无 LLM_API_KEY 时 skip（exit 0）"
      echo "  --require-live：无 Key 或失败则 exit 1"
      exit 0
      ;;
  esac
done

exec python3 eval/live_gate.py run --base-url "${BASE_URL:-http://127.0.0.1:8000}" $REQUIRE
