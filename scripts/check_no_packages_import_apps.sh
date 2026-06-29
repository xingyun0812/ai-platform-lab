#!/usr/bin/env bash
# CI: packages/** must not import apps.* (Issue #145 PR-3).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PATTERN='(from apps\.|import apps\.)'

if rg -n "$PATTERN" packages/; then
  echo "ERROR: packages/ must not import apps.* — use packages.platform / packages.contracts / packages.tenant."
  exit 1
fi

echo "OK: no packages -> apps imports"
