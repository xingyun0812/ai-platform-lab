#!/usr/bin/env python3
"""Prompt 版本化注册表测试 — Phase F #29

运行：
    python3 tests/test_prompt_registry.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.prompt import (  # noqa: E402
    PromptRegistry,
    PromptRegistryError,
    extract_variables,
    get_registry,
    init_registry,
    render,
    reset_registry_for_tests,
    validate_template,
)


def _make_registry(tmpdir: Path) -> PromptRegistry:
    yaml_path = tmpdir / "prompts.yaml"
    overrides_path = tmpdir / "prompt_overrides.json"
    legacy_path = tmpdir / "legacy_rag.txt"
    yaml_path.write_text(
        """
prompts:
  - prompt_id: rag_query
    version: 1
    status: active
    changelog: "v1 init"
    created_by: test
    content: |
      参考资料：{{context}}
      问题：{{query}}
  - prompt_id: rag_query
    version: 2
    status: draft
    changelog: "v2 draft"
    created_by: test
    content: |
      [V2] 参考资料：{{context}}
      问题：{{query}}
""",
        encoding="utf-8",
    )
    legacy_path.write_text("legacy {context} {query}", encoding="utf-8")
    reg = PromptRegistry(
        yaml_path=yaml_path,
        overrides_path=overrides_path,
        legacy_fallback={"legacy_prompt": legacy_path},
    )
    reg.load()
    return reg


def test_render_basic():
    tpl = "Hello {{name}}, age {{age}}"
    out = render(tpl, {"name": "Alice", "age": 30})
    assert out == "Hello Alice, age 30"
    # 未提供变量保持原样
    out = render(tpl, {"name": "Alice"})
    assert "{{age}}" in out
    print("PASS test_render_basic")


def test_render_with_spaces():
    tpl = "Hello {{ name }}!"
    out = render(tpl, {"name": "Bob"})
    assert out == "Hello Bob!"
    print("PASS test_render_with_spaces")


def test_extract_variables():
    tpl = "{{a}} and {{b}} and {{a}}"
    vars_ = extract_variables(tpl)
    assert vars_ == ["a", "b"], f"expected ['a','b'], got {vars_}"
    print("PASS test_extract_variables")


def test_validate_template():
    tpl = "{{context}}\n{{query}}"
    missing = validate_template(tpl, ["context", "query"])
    assert missing == []
    missing = validate_template(tpl, ["context", "query", "extra"])
    assert missing == ["extra"]
    print("PASS test_validate_template")


def test_render_no_conflict_with_str_format():
    """确保 {{var}} 语法不与 str.format 的 {var} 冲突"""
    tpl = "Text with {curly} and {{var}}"
    out = render(tpl, {"var": "value"})
    assert out == "Text with {curly} and value"
    # str.format 仍可正常工作（不影响 legacy render_rag_prompt）
    assert tpl.replace("{{var}}", "value") == out
    print("PASS test_render_no_conflict_with_str_format")


def test_registry_load_yaml():
    with tempfile.TemporaryDirectory() as d:
        reg = _make_registry(Path(d))
        ids = reg.list_prompt_ids()
        assert "rag_query" in ids
        active = reg.get_active("rag_query")
        assert active is not None
        assert active.version == 1
        assert active.status == "active"
        assert "参考资料" in active.content
        print("PASS test_registry_load_yaml")


def test_registry_list_versions():
    with tempfile.TemporaryDirectory() as d:
        reg = _make_registry(Path(d))
        versions = reg.list_versions("rag_query")
        assert len(versions) == 2
        assert versions[0].version == 1
        assert versions[1].version == 2
        print("PASS test_registry_list_versions")


def test_registry_render():
    with tempfile.TemporaryDirectory() as d:
        reg = _make_registry(Path(d))
        rendered, v = reg.render("rag_query", {"context": "CTX", "query": "Q"})
        assert v.version == 1
        assert "CTX" in rendered
        assert "Q" in rendered
        print("PASS test_registry_render")


def test_registry_create_version_sets_active():
    with tempfile.TemporaryDirectory() as d:
        reg = _make_registry(Path(d))
        # 创建 v3，set_active=True
        v3 = reg.create_version(
            prompt_id="rag_query",
            content="v3 {{context}}",
            changelog="v3 test",
            created_by="admin",
            set_active=True,
        )
        assert v3.version == 3
        assert v3.status == "active"
        # 旧 v1 应变为 archived
        v1 = reg.get_version("rag_query", 1)
        assert v1 is not None
        assert v1.status == "archived"
        # active 应是 v3
        active = reg.get_active("rag_query")
        assert active.version == 3
        print("PASS test_registry_create_version_sets_active")


def test_registry_create_version_draft():
    with tempfile.TemporaryDirectory() as d:
        reg = _make_registry(Path(d))
        v3 = reg.create_version(
            prompt_id="rag_query",
            content="v3 draft",
            set_active=False,
        )
        assert v3.status == "draft"
        # active 仍是 v1
        active = reg.get_active("rag_query")
        assert active.version == 1
        print("PASS test_registry_create_version_draft")


def test_registry_set_active():
    with tempfile.TemporaryDirectory() as d:
        reg = _make_registry(Path(d))
        # 创建 v3 为 draft
        reg.create_version(
            prompt_id="rag_query",
            content="v3",
            set_active=False,
        )
        # 激活 v3
        v3 = reg.set_active("rag_query", 3)
        assert v3.status == "active"
        v1 = reg.get_version("rag_query", 1)
        assert v1.status == "archived"
        active = reg.get_active("rag_query")
        assert active.version == 3
        print("PASS test_registry_set_active")


def test_registry_set_active_not_found():
    with tempfile.TemporaryDirectory() as d:
        reg = _make_registry(Path(d))
        try:
            reg.set_active("rag_query", 999)
            assert False, "expected PromptRegistryError"
        except PromptRegistryError as e:
            assert e.code == "VERSION_NOT_FOUND"
        print("PASS test_registry_set_active_not_found")


def test_registry_persist_overrides():
    """创建新版本后应落 JSON overrides 文件"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        reg = _make_registry(tmp)
        reg.create_version(
            prompt_id="rag_query",
            content="v3 persisted",
            changelog="persist test",
            created_by="admin",
        )
        overrides_file = tmp / "prompt_overrides.json"
        assert overrides_file.is_file()
        data = json.loads(overrides_file.read_text(encoding="utf-8"))
        assert "prompts" in data
        # 至少包含原 yaml + 新 v3
        versions = [p["version"] for p in data["prompts"] if p["prompt_id"] == "rag_query"]
        assert 3 in versions
        print("PASS test_registry_persist_overrides")


def test_registry_overrides_load():
    """重启后从 overrides 加载"""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        reg = _make_registry(tmp)
        reg.create_version(
            prompt_id="rag_query",
            content="v3 persisted",
            created_by="admin",
        )
        # 新实例：YAML + JSON overrides
        reg2 = PromptRegistry(
            yaml_path=tmp / "prompts.yaml",
            overrides_path=tmp / "prompt_overrides.json",
        )
        reg2.load()
        v3 = reg2.get_version("rag_query", 3)
        assert v3 is not None
        assert v3.content == "v3 persisted"
        print("PASS test_registry_overrides_load")


def test_registry_legacy_fallback():
    """prompt_id 不在 registry 时回退 legacy txt"""
    with tempfile.TemporaryDirectory() as d:
        reg = _make_registry(Path(d))
        v = reg.get_active("legacy_prompt")
        assert v is not None
        assert v.version == 0  # 0 表示 legacy
        assert "legacy" in v.content
        print("PASS test_registry_legacy_fallback")


def test_registry_not_found():
    with tempfile.TemporaryDirectory() as d:
        reg = _make_registry(Path(d))
        v = reg.get_active("non_existent")
        assert v is None
        try:
            reg.render("non_existent", {})
            assert False
        except PromptRegistryError as e:
            assert e.code == "PROMPT_NOT_FOUND"
        print("PASS test_registry_not_found")


def test_registry_stats():
    with tempfile.TemporaryDirectory() as d:
        reg = _make_registry(Path(d))
        stats = reg.stats()
        assert stats["prompt_ids"] == 1
        assert stats["total_versions"] == 2
        assert stats["active_versions"] == 1
        print("PASS test_registry_stats")


def test_global_registry_init():
    """测试全局单例初始化"""
    reset_registry_for_tests()
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        yaml_path = tmp / "prompts.yaml"
        yaml_path.write_text(
            "prompts:\n  - prompt_id: test\n    version: 1\n    status: active\n    content: 'hi {{name}}'\n",
            encoding="utf-8",
        )
        init_registry(yaml_path=yaml_path, overrides_path=tmp / "overrides.json")
        reg = get_registry()
        assert reg is not None
        rendered, v = reg.render("test", {"name": "world"})
        assert "hi world" in rendered
        assert v.version == 1
    reset_registry_for_tests()
    print("PASS test_global_registry_init")


def main() -> int:
    tests = [
        test_render_basic,
        test_render_with_spaces,
        test_extract_variables,
        test_validate_template,
        test_render_no_conflict_with_str_format,
        test_registry_load_yaml,
        test_registry_list_versions,
        test_registry_render,
        test_registry_create_version_sets_active,
        test_registry_create_version_draft,
        test_registry_set_active,
        test_registry_set_active_not_found,
        test_registry_persist_overrides,
        test_registry_overrides_load,
        test_registry_legacy_fallback,
        test_registry_not_found,
        test_registry_stats,
        test_global_registry_init,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"FAIL {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
