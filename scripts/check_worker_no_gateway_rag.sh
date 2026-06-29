#!/usr/bin/env bash
# CI: apps/worker must not import apps.gateway.rag.* (Issue #152 PR-3).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PATTERN='apps\.gateway\.rag'

if rg -n "$PATTERN" apps/worker/; then
  echo "ERROR: apps/worker/ must not import apps.gateway.rag.* — use packages.rag.*"
  exit 1
fi

echo "OK: apps/worker has no apps.gateway.rag imports"
