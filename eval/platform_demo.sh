#!/usr/bin/env bash
# Phase L #62 — 平台 Demo 冒烟（无 LLM / 有 LLM 两档）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# 自动加载根目录 .env（uvicorn 会读，但 bash 脚本默认不会）
if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
TENANT="${DEMO_TENANT:-admin}"
TOKEN="${DEMO_TOKEN:-sk-tenant-admin-change-me}"
WITH_LLM=false

for arg in "$@"; do
  case "$arg" in
    --with-llm) WITH_LLM=true ;;
    --no-llm) WITH_LLM=false ;;
    -h|--help)
      echo "Usage: $0 [--no-llm|--with-llm]"
      echo "  BASE_URL=$BASE_URL  TENANT=$TENANT"
      exit 0
      ;;
  esac
done

hdr=(-H "X-Tenant-Id: $TENANT" -H "Authorization: Bearer $TOKEN")

echo "==> healthz"
curl -sf "$BASE_URL/healthz" >/dev/null

echo "==> console static"
code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/console/")
test "$code" = "200"

echo "==> console metrics API"
curl -sf "${hdr[@]}" "$BASE_URL/internal/metrics" >/dev/null

echo "==> console tenants API"
curl -sf "${hdr[@]}" "$BASE_URL/internal/tenants" >/dev/null

echo "==> console rag knowledge-bases"
curl -sf "${hdr[@]}" "$BASE_URL/internal/rag/knowledge-bases" >/dev/null

echo "==> agents / orchestrator / audit"
curl -sf "${hdr[@]}" "$BASE_URL/internal/agents" >/dev/null || echo "    (skip agents: MULTI_AGENT_ENABLED?)"
curl -sf "${hdr[@]}" "$BASE_URL/internal/orchestrator/workflows" >/dev/null
curl -sf "${hdr[@]}" "$BASE_URL/internal/audit/recent?limit=5" >/dev/null

if $WITH_LLM; then
  if [[ -z "${LLM_API_KEY:-}" ]]; then
    echo "ERROR: --with-llm 需要 LLM_API_KEY" >&2
    echo "  写入 $ROOT/.env 或执行: export LLM_API_KEY=..." >&2
    exit 1
  fi
  echo "==> chat smoke (gateway 须已用同一 .env 启动)"
  chat_code=$(curl -s -o /tmp/platform_demo_chat.json -w "%{http_code}" -m 25 \
    "${hdr[@]}" -H "Content-Type: application/json" \
    -d '{"model":"chat-fast","messages":[{"role":"user","content":"ping"}],"max_tokens":8}' \
    "$BASE_URL/v1/chat/completions" || true)
  if [[ "$chat_code" != "200" ]]; then
    echo "ERROR: chat 返回 HTTP $chat_code（Gateway 可能未加载 .env，请重启 uvicorn）" >&2
    head -c 300 /tmp/platform_demo_chat.json 2>/dev/null; echo >&2
    exit 1
  fi
  echo "    chat ok"
  echo "==> index lab-demo v1"
  resp=$(curl -sf "${hdr[@]}" -H "Content-Type: application/json" \
    -d '{"kb_id":"lab-demo","version":1,"source_uri":"samples/hello.txt"}' \
    "$BASE_URL/internal/index")
  task_id=$(python3 -c "import json,sys; print(json.load(sys.stdin).get('task_id',''))" <<<"$resp")
  echo "    task_id=$task_id"
  for _ in $(seq 1 30); do
    st=$(curl -sf "${hdr[@]}" "$BASE_URL/internal/index/tasks/$task_id" \
      | python3 -c "import json,sys; print(json.load(sys.stdin).get('status',''))")
    if [[ "$st" == "success" || "$st" == "failed" ]]; then
      echo "    index status=$st"
      break
    fi
    sleep 1
  done
  echo "==> rag query"
  curl -sf "${hdr[@]}" -H "Content-Type: application/json" \
    -d "{\"tenant_id\":\"$TENANT\",\"kb_id\":\"lab-demo\",\"version\":1,\"query\":\"RAG 数据管道\"}" \
    "$BASE_URL/v1/rag/query" >/dev/null || echo "    (rag query skipped/failed)"
fi

echo "==> sdk smoke"
if $WITH_LLM; then
  python3 eval/sdk_smoke.py --base-url "$BASE_URL" --tenant "$TENANT" --api-key "$TOKEN"
else
  python3 eval/sdk_smoke.py --base-url "$BASE_URL" --tenant "$TENANT" --api-key "$TOKEN" \
    --skip-chat --skip-rag --skip-agent
fi

echo "==> feedback loop mock"
python3 eval/feedback_loop_demo.py --mock

echo "==> agent vertical smoke"
python3 eval/agent_vertical_smoke.py

echo "OK platform_demo ($($WITH_LLM && echo with-llm || echo no-llm))"
