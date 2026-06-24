#!/usr/bin/env bash
# Phase N N3 — 从 wheel 或 PyPI 安装 SDK 后跑 sdk_smoke
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SOURCE="${SDK_PIP_SOURCE:-local}"   # local | pypi
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
VENV_DIR="${SDK_PIP_VENV:-$ROOT/.venv-sdk-pypi-smoke}"
PYTHON="${PYTHON:-python3}"

usage() {
  echo "Usage: $0 [--local|--pypi] [--version VER] [--no-venv]"
  echo "  --local     build wheel from sdk/python and pip install (default)"
  echo "  --pypi      pip install ai-platform-lab from PyPI"
  echo "  --version   package version (default: read sdk/python/ai_platform_lab/__init__.py)"
  echo "  BASE_URL=$BASE_URL"
  exit 0
}

USE_VENV=true
VERSION=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --local) SOURCE=local ;;
    --pypi) SOURCE=pypi ;;
    --version) VERSION="$2"; shift ;;
    --no-venv) USE_VENV=false ;;
    -h|--help) usage ;;
    *) echo "Unknown arg: $1" >&2; usage ;;
  esac
  shift
done

if [[ -z "$VERSION" ]]; then
  VERSION="$("$PYTHON" -c "import importlib.util; p='$ROOT/sdk/python/ai_platform_lab/__init__.py'; s=importlib.util.spec_from_file_location('m', p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(m.__version__)")"
fi

echo "==> SDK pip smoke (source=$SOURCE version=$VERSION)"

if $USE_VENV; then
  if [[ ! -d "$VENV_DIR" ]]; then
    "$PYTHON" -m venv "$VENV_DIR"
  fi
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  pip install -q -U pip
fi

if [[ "$SOURCE" == "local" ]]; then
  echo "==> build wheel"
  pip install -q build
  (cd "$ROOT/sdk/python" && python -m build -q)
  WHEEL="$ROOT/sdk/python/dist/ai_platform_lab-${VERSION}-py3-none-any.whl"
  if [[ ! -f "$WHEEL" ]]; then
    WHEEL="$(ls -1 "$ROOT/sdk/python/dist/"*.whl | head -1)"
  fi
  echo "==> pip install $WHEEL"
  pip install -q --force-reinstall "$WHEEL"
elif [[ "$SOURCE" == "pypi" ]]; then
  echo "==> pip install ai-platform-lab==$VERSION"
  pip install -q "ai-platform-lab==$VERSION"
else
  echo "Invalid SOURCE: $SOURCE" >&2
  exit 1
fi

echo "==> verify import"
python -c "import ai_platform_lab; print('    version', ai_platform_lab.__version__)"

echo "==> sdk_smoke (installed package, not editable path)"
# 确保不插入 sdk/python 到 PYTHONPATH
export PYTHONPATH=""
python "$ROOT/eval/sdk_smoke.py" --base-url "$BASE_URL"

echo "OK sdk_pypi_smoke"
