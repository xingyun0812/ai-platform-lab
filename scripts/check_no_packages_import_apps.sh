#!/usr/bin/env bash
# CI: packages/** must not import apps.gateway platform facades (Issue #145).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PATTERN='from apps\.gateway\.(settings import get_settings|model_router|rag\.paths|rag\.pipeline import resolve_retrieve_version)'

if rg -n "$PATTERN" packages/; then
  echo "ERROR: packages/ must use packages.platform instead of apps.gateway facades above."
  exit 1
fi

echo "OK: no forbidden packages -> apps.gateway platform imports"
