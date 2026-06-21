#!/usr/bin/env python3
"""Multi-Agent 框架单元测试 — Phase H #38

运行：
    python3 tests/test_multi_agent.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _load_module_from_path(module_name: str, file_path: Path):
    """加载模块文件并注册到 sys.modules（Python 3.9 dataclass 需要）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_registry_module():
    return _load_module_from_path(
        "multi_agent_registry_test",
        REPO_ROOT / "packages" / "agent" / "multi_agent" / "registry.py",
    )


# --------------------------------------------------------------------- #
# AgentSpec 测试
# --------------------------------------------------------------------- #

def test_agent_spec_creation():
    mod = _load_registry_module()
    spec = mod.AgentSpec(
        agent_id="rag_specialist",
        name="RAG 专家",
        role="specialist",
        description="擅长 RAG",
        system_prompt="你是 RAG 专家",
    )
    assert spec.agent_id == "rag_specialist"
    assert spec.role == "specialist"
    assert spec.can_delegate is False
    assert spec.can_be_delegated_to is True
    assert spec.max_delegation_depth == 3
    assert spec.enabled is True
    d = spec.to_dict()
    assert d["agent_id"] == "rag_specialist"
    print("PASS test_agent_spec_creation")


def test_agent_spec_default_role():
    mod = _load_registry_module()
    spec = mod.AgentSpec(agent_id="a1", name="A1")
    assert spec.role == "specialist"  # 默认
    print("PASS test_agent_spec_default_role")


def test_agent_spec_is_tool_allowed():
    mod = _load_registry_module()
    # 空列表 = 允许所有
    spec1 = mod.AgentSpec(agent_id="a1", name="A1", allowed_tools=[])
    assert spec1.is_tool_allowed("any_tool") is True
    # 白名单
    spec2 = mod.AgentSpec(
        agent_id="a2", name="A2", allowed_tools=["get_kb_snippet", "calc"]
    )
    assert spec2.is_tool_allowed("get_kb_snippet") is True
    assert spec2.is_tool_allowed("calc") is True
    assert spec2.is_tool_allowed("search_web") is False
    print("PASS test_agent_spec_is_tool_allowed")


# --------------------------------------------------------------------- #
# AgentRegistry 测试
# --------------------------------------------------------------------- #

def test_registry_creation():
    mod = _load_registry_module()
    reg = mod.AgentRegistry()
    assert reg.list_agents() == []
    assert reg.list_agent_ids() == []
    stats = reg.stats()
    assert stats["total_agents"] == 0
    print("PASS test_registry_creation")


def test_registry_add_and_get():
    mod = _load_registry_module()
    reg = mod.AgentRegistry()
    spec = mod.AgentSpec(agent_id="a1", name="A1", role="specialist")
    reg.add_agent(spec)
    assert reg.get_agent("a1") is spec
    assert "a1" in reg.list_agent_ids()
    assert reg.stats()["total_agents"] == 1
    print("PASS test_registry_add_and_get")


def test_registry_remove():
    mod = _load_registry_module()
    reg = mod.AgentRegistry()
    spec = mod.AgentSpec(agent_id="a1", name="A1")
    reg.add_agent(spec)
    assert reg.remove_agent("a1") is True
    assert reg.get_agent("a1") is None
    assert reg.remove_agent("nonexistent") is False
    print("PASS test_registry_remove")


def test_registry_update():
    mod = _load_registry_module()
    reg = mod.AgentRegistry()
    spec = mod.AgentSpec(agent_id="a1", name="A1", description="old")
    reg.add_agent(spec)
    updated = reg.update_agent("a1", description="new", enabled=False)
    assert updated.description == "new"
    assert updated.enabled is False
    # 不存在的
    assert reg.update_agent("nonexistent", name="x") is None
    print("PASS test_registry_update")


def test_registry_stats_by_role():
    mod = _load_registry_module()
    reg = mod.AgentRegistry()
    reg.add_agent(mod.AgentSpec(agent_id="a1", name="A1", role="specialist"))
    reg.add_agent(mod.AgentSpec(agent_id="a2", name="A2", role="specialist"))
    reg.add_agent(mod.AgentSpec(agent_id="a3", name="A3", role="reviewer"))
    stats = reg.stats()
    assert stats["total_agents"] == 3
    assert stats["by_role"].get("specialist") == 2
    assert stats["by_role"].get("reviewer") == 1
    print("PASS test_registry_stats_by_role")


def test_registry_mark_invoked():
    mod = _load_registry_module()
    reg = mod.AgentRegistry()
    reg.add_agent(mod.AgentSpec(agent_id="a1", name="A1"))
    reg.mark_invoked("a1")
    status = reg.get_status("a1")
    assert status.invocation_count == 1
    assert status.last_invoked > 0
    reg.mark_invoked("a1")
    assert reg.get_status("a1").invocation_count == 2
    print("PASS test_registry_mark_invoked")


def test_registry_mark_error():
    mod = _load_registry_module()
    reg = mod.AgentRegistry()
    reg.add_agent(mod.AgentSpec(agent_id="a1", name="A1"))
    reg.mark_error("a1", "LLM 超时")
    status = reg.get_status("a1")
    assert status.healthy is False
    assert status.last_error == "LLM 超时"
    # 恢复
    reg.mark_healthy("a1")
    assert reg.get_status("a1").healthy is True
    print("PASS test_registry_mark_error")


def test_registry_yaml_load(tmp_path=None):
    """从 YAML 加载 Agent 定义"""
    import tempfile
    mod = _load_registry_module()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""
agents:
  - agent_id: rag_specialist
    name: RAG 专家
    role: specialist
    description: RAG 检索专家
    system_prompt: 你是 RAG 专家
    allowed_tools:
      - get_kb_snippet
    can_delegate: false
    enabled: true
  - agent_id: code_reviewer
    name: 代码审核员
    role: reviewer
    description: 审核代码
    enabled: true
""")
        yaml_path = Path(f.name)
    reg = mod.AgentRegistry(yaml_path=yaml_path)
    reg.load()
    assert len(reg.list_agents()) == 2
    rag = reg.get_agent("rag_specialist")
    assert rag.role == "specialist"
    assert "get_kb_snippet" in rag.allowed_tools
    reviewer = reg.get_agent("code_reviewer")
    assert reviewer.role == "reviewer"
    print("PASS test_registry_yaml_load")


def test_registry_persist_overrides():
    """admin API 修改后持久化到 JSON"""
    import tempfile
    mod = _load_registry_module()
    with tempfile.TemporaryDirectory() as tmpdir:
        overrides_path = Path(tmpdir) / "overrides.json"
        reg = mod.AgentRegistry(overrides_path=overrides_path)
        reg.add_agent(mod.AgentSpec(agent_id="a1", name="A1"))
        # 文件应已写入
        assert overrides_path.is_file()
        import json
        data = json.loads(overrides_path.read_text())
        assert len(data["agents"]) == 1
        assert data["agents"][0]["agent_id"] == "a1"
        # 重新加载
        reg2 = mod.AgentRegistry(overrides_path=overrides_path)
        reg2.load()
        assert reg2.get_agent("a1") is not None
    print("PASS test_registry_persist_overrides")


def test_registry_parse_invalid_spec():
    """非法 spec 应被跳过"""
    mod = _load_registry_module()
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""
agents:
  - agent_id: valid
    name: Valid
  - name: MissingAgentId  # 缺 agent_id
  - agent_id: null  # null agent_id
""")
        yaml_path = Path(f.name)
    reg = mod.AgentRegistry(yaml_path=yaml_path)
    reg.load()
    assert len(reg.list_agents()) == 1  # 只加载了 valid
    print("PASS test_registry_parse_invalid_spec")


# --------------------------------------------------------------------- #
# 委托逻辑测试（独立，不依赖 apps.gateway）
# --------------------------------------------------------------------- #

def test_delegate_no_registry():
    """registry 未初始化时返回 MULTI_AGENT_DISABLED"""
    mod_reg = _load_registry_module()
    # 重置全局 registry
    mod_reg._global_registry = None
    # 加载 delegation 模块
    sys.modules["packages.agent.multi_agent.registry"] = mod_reg
    del_mod = _load_module_from_path(
        "multi_agent_delegation_test",
        REPO_ROOT / "packages" / "agent" / "multi_agent" / "delegation.py",
    )

    async def run():
        result = await del_mod.delegate_to_agent(
            agent_id="a1", task="test"
        )
        assert result.status == "failed"
        assert "MULTI_AGENT_DISABLED" in result.error

    _run_async(run())
    print("PASS test_delegate_no_registry")


def test_delegate_agent_not_found():
    mod_reg = _load_registry_module()
    # 初始化一个空 registry
    mod_reg._global_registry = mod_reg.AgentRegistry()
    sys.modules["packages.agent.multi_agent.registry"] = mod_reg
    del_mod = _load_module_from_path(
        "multi_agent_delegation_test",
        REPO_ROOT / "packages" / "agent" / "multi_agent" / "delegation.py",
    )

    async def run():
        result = await del_mod.delegate_to_agent(
            agent_id="nonexistent", task="test"
        )
        assert result.status == "failed"
        assert "AGENT_NOT_FOUND" in result.error

    _run_async(run())
    # 清理
    mod_reg._global_registry = None
    print("PASS test_delegate_agent_not_found")


def test_delegate_agent_disabled():
    mod_reg = _load_registry_module()
    reg = mod_reg.AgentRegistry()
    reg.add_agent(mod_reg.AgentSpec(
        agent_id="a1", name="A1", enabled=False
    ))
    mod_reg._global_registry = reg
    sys.modules["packages.agent.multi_agent.registry"] = mod_reg
    del_mod = _load_module_from_path(
        "multi_agent_delegation_test",
        REPO_ROOT / "packages" / "agent" / "multi_agent" / "delegation.py",
    )

    async def run():
        result = await del_mod.delegate_to_agent(
            agent_id="a1", task="test"
        )
        assert result.status == "failed"
        assert "AGENT_DISABLED" in result.error

    _run_async(run())
    mod_reg._global_registry = None
    print("PASS test_delegate_agent_disabled")


def test_delegate_not_delegatable():
    mod_reg = _load_registry_module()
    reg = mod_reg.AgentRegistry()
    reg.add_agent(mod_reg.AgentSpec(
        agent_id="a1", name="A1", can_be_delegated_to=False
    ))
    mod_reg._global_registry = reg
    sys.modules["packages.agent.multi_agent.registry"] = mod_reg
    del_mod = _load_module_from_path(
        "multi_agent_delegation_test",
        REPO_ROOT / "packages" / "agent" / "multi_agent" / "delegation.py",
    )

    async def run():
        result = await del_mod.delegate_to_agent(
            agent_id="a1", task="test"
        )
        assert result.status == "failed"
        assert "AGENT_NOT_DELEGATABLE" in result.error

    _run_async(run())
    mod_reg._global_registry = None
    print("PASS test_delegate_not_delegatable")


def test_delegate_cycle_detection():
    """委托栈包含自身时拒绝（防递归）"""
    mod_reg = _load_registry_module()
    reg = mod_reg.AgentRegistry()
    reg.add_agent(mod_reg.AgentSpec(
        agent_id="a1", name="A1", can_be_delegated_to=True
    ))
    mod_reg._global_registry = reg
    sys.modules["packages.agent.multi_agent.registry"] = mod_reg
    del_mod = _load_module_from_path(
        "multi_agent_delegation_test",
        REPO_ROOT / "packages" / "agent" / "multi_agent" / "delegation.py",
    )

    async def run():
        # 模拟 a1 已在栈中（递归委托）
        result = await del_mod.delegate_to_agent(
            agent_id="a1", task="test",
            delegation_stack=["a1"],
        )
        assert result.status == "failed"
        assert "DELEGATION_CYCLE" in result.error

    _run_async(run())
    mod_reg._global_registry = None
    print("PASS test_delegate_cycle_detection")


def test_delegate_max_depth_exceeded():
    """超过最大委托深度时拒绝"""
    mod_reg = _load_registry_module()
    reg = mod_reg.AgentRegistry()
    reg.add_agent(mod_reg.AgentSpec(
        agent_id="a1", name="A1", max_delegation_depth=2
    ))
    mod_reg._global_registry = reg
    sys.modules["packages.agent.multi_agent.registry"] = mod_reg
    del_mod = _load_module_from_path(
        "multi_agent_delegation_test",
        REPO_ROOT / "packages" / "agent" / "multi_agent" / "delegation.py",
    )

    async def run():
        # 栈已有 2 个，超过深度 2
        result = await del_mod.delegate_to_agent(
            agent_id="a1", task="test",
            delegation_stack=["parent1", "parent2"],
        )
        assert result.status == "failed"
        assert "MAX_DEPTH_EXCEEDED" in result.error

    _run_async(run())
    mod_reg._global_registry = None
    print("PASS test_delegate_max_depth_exceeded")


def test_delegation_result_dataclass():
    mod_reg = _load_registry_module()
    sys.modules["packages.agent.multi_agent.registry"] = mod_reg
    del_mod = _load_module_from_path(
        "multi_agent_delegation_test",
        REPO_ROOT / "packages" / "agent" / "multi_agent" / "delegation.py",
    )
    result = del_mod.DelegationResult(
        agent_id="a1",
        task="test",
        status="completed",
        output="result",
        execution_time_ms=100.0,
        delegation_depth=1,
    )
    assert result.agent_id == "a1"
    assert result.status == "completed"
    assert result.output == "result"
    assert result.delegation_depth == 1
    print("PASS test_delegation_result_dataclass")


def main() -> int:
    tests = [
        test_agent_spec_creation,
        test_agent_spec_default_role,
        test_agent_spec_is_tool_allowed,
        test_registry_creation,
        test_registry_add_and_get,
        test_registry_remove,
        test_registry_update,
        test_registry_stats_by_role,
        test_registry_mark_invoked,
        test_registry_mark_error,
        test_registry_yaml_load,
        test_registry_persist_overrides,
        test_registry_parse_invalid_spec,
        test_delegate_no_registry,
        test_delegate_agent_not_found,
        test_delegate_agent_disabled,
        test_delegate_not_delegatable,
        test_delegate_cycle_detection,
        test_delegate_max_depth_exceeded,
        test_delegation_result_dataclass,
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
