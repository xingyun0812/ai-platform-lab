#!/usr/bin/env python3
"""
文档-代码同步验证脚本
检查代码中新增的 public 函数/类是否已在对应文档中记录。

用法:
  python scripts/check_doc_sync.py                    # 检查全部
  python scripts/check_doc_sync.py --min-coverage 80   # 设定最少同步率
  python scripts/check_doc_sync.py --ci                # CI 模式 (exit 1 如果同步率低于 50%)
  python scripts/check_doc_sync.py --list-missing      # 只输出未同步项清单

退出码:
  0 — 同步率达标
  1 — 同步率低于阈值
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_DIR = REPO_ROOT / "docs"
CODE_DIRS = [
    REPO_ROOT / "packages",
    REPO_ROOT / "apps",
]

# 白名单：以下路径的模式或模块不用检查
SKIP_MODULES: set[str] = {
    "packages.platform",  # PlatformPort protocol — 实现类在别处
}

# 白名单：不要求文档化的 API 名字模式
SKIP_NAMES: set[str] = {
    "reset_*_for_tests",  # 测试辅助函数
}


def _name_matches_skip(name: str) -> bool:
    for pattern in SKIP_NAMES:
        if pattern.endswith("*"):
            if name.startswith(pattern[:-1]):
                return True
        elif pattern == name:
            return True
    return False


def _module_matches_skip(relpath: str) -> bool:
    for mod in SKIP_MODULES:
        if relpath.startswith(mod.replace(".", "/")):
            return True
    return False


def extract_public_apis(paths: list[Path]) -> dict[str, list[str]]:
    """提取代码中所有 public 函数/类定义。"""
    apis: dict[str, list[str]] = {}
    for base in paths:
        for pyfile in sorted(base.rglob("*.py")):
            if "/test_" in str(pyfile) or pyfile.name.startswith("test_"):
                continue
            try:
                tree = ast.parse(pyfile.read_text())
            except SyntaxError:
                continue
            rel = str(pyfile.relative_to(REPO_ROOT))
            if _module_matches_skip(rel):
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if node.name.startswith("_"):
                        continue
                    if _name_matches_skip(node.name):
                        continue
                    apis.setdefault(rel, []).append(node.name)
    return apis


def check_sync(apis: dict[str, list[str]]) -> list[str]:
    """检查 API 是否在文档中被引用。简单名字匹配。"""
    issues: list[str] = []
    all_doc_text = ""
    for doc_file in DOC_DIR.rglob("*.md"):
        all_doc_text += doc_file.read_text() + "\n"

    for filepath, names in apis.items():
        for name in names:
            if name not in all_doc_text:
                issues.append(f"{filepath} :: {name}")
    return issues


def main():
    args = sys.argv[1:]
    ci_mode = "--ci" in args
    list_only = "--list-missing" in args

    # 解析 --min-coverage 参数
    min_coverage = 0.0
    for arg in args:
        if arg.startswith("--min-coverage="):
            try:
                min_coverage = float(arg.split("=", 1)[1]) / 100.0
            except ValueError:
                pass
    if ci_mode and min_coverage == 0.0:
        min_coverage = 0.50  # CI 默认 50%

    print("Scanning code for public APIs...")
    apis = extract_public_apis(CODE_DIRS)
    total = sum(len(v) for v in apis.values())
    if total == 0:
        print("   No public APIs found (all filtered by skip rules).")
        return 0
    print(f"   Found {total} public definitions across {len(apis)} files")

    print("\nVerifying documentation sync...")
    issues = check_sync(apis)
    missing = len(issues)
    synced = total - missing
    sync_rate = synced / total

    print(f"   Synced: {synced}/{total} ({sync_rate:.1%})")
    print(f"   Missing: {missing}/{total} ({1 - sync_rate:.1%})")

    if missing > 0:
        if list_only or ci_mode:
            for issue in issues:
                print(issue)
        else:
            print(f"\n   First 20 missing items (use --list-missing to see all):")
            for issue in issues[:20]:
                print(f"      - {issue}")
            if len(issues) > 20:
                print(f"      ... and {len(issues) - 20} more")

    if min_coverage > 0 and sync_rate < min_coverage:
        print(f"\nFAIL: sync rate {sync_rate:.1%} < threshold {min_coverage:.1%}")
        return 1

    if missing == 0:
        print("All synced — every public API is referenced in docs.")
    else:
        print(f"\nPassed with {missing} missing items (threshold {min_coverage:.1%} met).")

    return 0 if sync_rate >= min_coverage else 1


if __name__ == "__main__":
    sys.exit(main())
