"""仓库根路径 — packages 层统一 REPO_ROOT（Issue #145 PR-3）。"""

from __future__ import annotations

from pathlib import Path

# packages/platform/paths.py → parents[2] == repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
