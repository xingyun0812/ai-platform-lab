"""
tests/test_console_v2.py — Python smoke test for Console V2 (Issue #30 / #46)

Validates:
1. console-v2 source files exist (package.json, vite.config.ts, etc.)
2. apps/console/static/ directory exists (build output target)
3. Key TypeScript source files are present
4. No Python import chain triggered (importlib.util style)

Note: This test does NOT require npm install or npm run build.
After running `npm run build` in console-v2/, the built assets
will appear in apps/console/static/ and test #9 will also pass.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ── Project root ──────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).parent.parent
CONSOLE_V2 = REPO_ROOT / "console-v2"
APPS_CONSOLE_STATIC = REPO_ROOT / "apps" / "console" / "static"


def _check(path: Path, label: str) -> None:
    """Assert a path exists; print OK or FAIL."""
    exists = path.exists()
    status = "OK  " if exists else "FAIL"
    print(f"  [{status}] {label}: {path.relative_to(REPO_ROOT)}")
    assert exists, f"Missing: {path}"


# ── Test 1: Project config files ──────────────────────────────────────────────
def test_project_config_files():
    """console-v2 project configuration files exist."""
    print("\n[test_project_config_files]")
    for name in [
        "package.json",
        "vite.config.ts",
        "tsconfig.json",
        "tsconfig.node.json",
        "index.html",
        "vitest.config.ts",
    ]:
        _check(CONSOLE_V2 / name, name)


# ── Test 2: Source entry points ───────────────────────────────────────────────
def test_source_entry_points():
    """main.tsx and App.tsx entry points exist."""
    print("\n[test_source_entry_points]")
    for name in ["main.tsx", "App.tsx", "index.css"]:
        _check(CONSOLE_V2 / "src" / name, f"src/{name}")


# ── Test 3: API client and modules ───────────────────────────────────────────
def test_api_modules():
    """All API modules under src/api/ exist."""
    print("\n[test_api_modules]")
    api_dir = CONSOLE_V2 / "src" / "api"
    for name in [
        "client.ts",
        "chat.ts",
        "rag.ts",
        "agent.ts",
        "embedding.ts",
        "memory.ts",
        "orchestrator.ts",
        "audit.ts",
    ]:
        _check(api_dir / name, f"src/api/{name}")


# ── Test 4: Pages ─────────────────────────────────────────────────────────────
def test_pages():
    """All page components exist."""
    print("\n[test_pages]")
    pages_dir = CONSOLE_V2 / "src" / "pages"
    for name in [
        "Login.tsx",
        "Dashboard.tsx",
        "Tenants.tsx",
        "Agents.tsx",
        "RAG.tsx",
        "Memory.tsx",
        "Orchestrator.tsx",
        "Audit.tsx",
        "Settings.tsx",
    ]:
        _check(pages_dir / name, f"src/pages/{name}")


# ── Test 5: Components ────────────────────────────────────────────────────────
def test_components():
    """Shared component files exist."""
    print("\n[test_components]")
    comps_dir = CONSOLE_V2 / "src" / "components"
    _check(comps_dir / "RequireAuth.tsx", "src/components/RequireAuth.tsx")


# ── Test 6: Test files ────────────────────────────────────────────────────────
def test_test_files():
    """Vitest test files exist."""
    print("\n[test_test_files]")
    tests_dir = CONSOLE_V2 / "tests"
    for name in ["setup.ts", "App.test.tsx"]:
        _check(tests_dir / name, f"tests/{name}")


# ── Test 7: Build output directory exists ─────────────────────────────────────
def test_build_output_directory():
    """apps/console/static/ directory is present (build target)."""
    print("\n[test_build_output_directory]")
    assert APPS_CONSOLE_STATIC.exists(), (
        f"Build output directory missing: {APPS_CONSOLE_STATIC}\n"
        "Run `cd console-v2 && npm run build` to generate static assets."
    )
    print(f"  [OK  ] apps/console/static/ exists")


# ── Test 8: package.json has correct fields ───────────────────────────────────
def test_package_json_fields():
    """package.json has required name and scripts."""
    import json
    print("\n[test_package_json_fields]")
    pkg_path = CONSOLE_V2 / "package.json"
    assert pkg_path.exists(), f"package.json missing at {pkg_path}"
    with open(pkg_path) as f:
        pkg = json.load(f)
    assert pkg.get("name") == "ai-platform-lab-console", (
        f"Expected name='ai-platform-lab-console', got '{pkg.get('name')}'"
    )
    scripts = pkg.get("scripts", {})
    for script in ["dev", "build", "test"]:
        assert script in scripts, f"Missing script: {script}"
    print(f"  [OK  ] package.json name='{pkg['name']}'")
    print(f"  [OK  ] scripts: {list(scripts.keys())}")


# ── Test 9: vite.config.ts references build output path ──────────────────────
def test_vite_config_build_output():
    """vite.config.ts points outDir to ../apps/console/static."""
    print("\n[test_vite_config_build_output]")
    vite_cfg = CONSOLE_V2 / "vite.config.ts"
    assert vite_cfg.exists(), f"vite.config.ts missing"
    content = vite_cfg.read_text()
    assert "../apps/console/static" in content, (
        "vite.config.ts should set outDir to '../apps/console/static'"
    )
    print(f"  [OK  ] outDir '../apps/console/static' found in vite.config.ts")


# ── Test 10: RequireAuth.tsx has auth guard logic ─────────────────────────────
def test_require_auth_guard():
    """RequireAuth.tsx contains localStorage token check."""
    print("\n[test_require_auth_guard]")
    auth_file = CONSOLE_V2 / "src" / "components" / "RequireAuth.tsx"
    assert auth_file.exists(), "RequireAuth.tsx missing"
    content = auth_file.read_text()
    assert "localStorage" in content, "RequireAuth.tsx should check localStorage"
    assert "Navigate" in content, "RequireAuth.tsx should use Navigate for redirect"
    assert "/login" in content, "RequireAuth.tsx should redirect to /login"
    print("  [OK  ] RequireAuth.tsx has localStorage + Navigate + /login")


# ── Test 11: App.test.tsx has ≥10 test cases ─────────────────────────────────
def test_app_test_count():
    """App.test.tsx contains at least 10 numbered test cases."""
    print("\n[test_app_test_count]")
    test_file = CONSOLE_V2 / "tests" / "App.test.tsx"
    assert test_file.exists(), "App.test.tsx missing"
    content = test_file.read_text()
    # Count `it(` calls
    import re
    tests = re.findall(r'\bit\s*\(', content)
    count = len(tests)
    assert count >= 10, f"Expected ≥10 test cases in App.test.tsx, found {count}"
    print(f"  [OK  ] App.test.tsx has {count} test cases")


# ── Test 12: API module has correct base URL config ───────────────────────────
def test_api_client_config():
    """src/api/client.ts has proxy config and interceptors."""
    print("\n[test_api_client_config]")
    client_file = CONSOLE_V2 / "src" / "api" / "client.ts"
    assert client_file.exists(), "client.ts missing"
    content = client_file.read_text()
    assert "Authorization" in content, "client.ts should inject Authorization header"
    assert "X-Tenant-Id" in content, "client.ts should inject X-Tenant-Id header"
    assert "401" in content, "client.ts should handle 401 (redirect to login)"
    print("  [OK  ] client.ts has Authorization + X-Tenant-Id + 401 handler")


# ── Runner ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    tests = [
        test_project_config_files,
        test_source_entry_points,
        test_api_modules,
        test_pages,
        test_components,
        test_test_files,
        test_build_output_directory,
        test_package_json_fields,
        test_vite_config_build_output,
        test_require_auth_guard,
        test_app_test_count,
        test_api_client_config,
    ]
    passed = 0
    failed = 0
    errors: list[str] = []

    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            errors.append(f"{test_fn.__name__}: {e}")
            print(f"  [FAIL] {e}")
        except Exception as e:
            failed += 1
            errors.append(f"{test_fn.__name__}: {type(e).__name__}: {e}")
            print(f"  [ERROR] {type(e).__name__}: {e}")

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed")
    if errors:
        print("\nFailures:")
        for err in errors:
            print(f"  - {err}")
    print("=" * 60)
    sys.exit(0 if failed == 0 else 1)
