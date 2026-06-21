#!/usr/bin/env python3
"""Agent 生命周期管理单元测试 — Phase H #39

运行：
    python3 tests/test_agent_lifecycle.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


# --------------------------------------------------------------------- #
# 加载辅助
# --------------------------------------------------------------------- #

def _load_module_from_path(module_name: str, file_path: Path):
    """加载模块文件并注册到 sys.modules（Python 3.9 dataclass 需要）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_registry_mod():
    return _load_module_from_path(
        "lifecycle_registry_test",
        REPO_ROOT / "packages" / "agent" / "lifecycle" / "registry.py",
    )


# --------------------------------------------------------------------- #
# 测试 1：AgentVersion 数据模型基础
# --------------------------------------------------------------------- #

def test_agent_version_dataclass():
    mod = _load_registry_mod()
    ver = mod.AgentVersion(
        version_id="v1",
        agent_id="rag",
        version=1,
        spec_snapshot={"agent_id": "rag", "name": "RAG Agent"},
        created_at=1000.0,
        created_by="alice",
        status="draft",
        metadata={"note": "initial"},
    )
    assert ver.version_id == "v1"
    assert ver.agent_id == "rag"
    assert ver.version == 1
    assert ver.status == "draft"
    d = ver.to_dict()
    assert d["version_id"] == "v1"
    assert d["spec_snapshot"]["name"] == "RAG Agent"
    print("PASS test_agent_version_dataclass")


# --------------------------------------------------------------------- #
# 测试 2：RolloutStrategy 常量验证
# --------------------------------------------------------------------- #

def test_rollout_strategy_constants():
    mod = _load_registry_mod()
    RS = mod.RolloutStrategy
    assert RS.ALL_AT_ONCE == "all_at_once"
    assert RS.BLUE_GREEN == "blue_green"
    assert RS.CANARY == "canary"
    assert RS.is_valid("all_at_once") is True
    assert RS.is_valid("blue_green") is True
    assert RS.is_valid("canary") is True
    assert RS.is_valid("unknown") is False
    print("PASS test_rollout_strategy_constants")


# --------------------------------------------------------------------- #
# 测试 3：register_version 自增版本号
# --------------------------------------------------------------------- #

def test_register_version_auto_increment():
    mod = _load_registry_mod()
    reg = mod.AgentLifecycleRegistry()
    v1 = reg.register_version("agent_a", {"name": "A"}, created_by="bob")
    v2 = reg.register_version("agent_a", {"name": "A v2"}, created_by="bob")
    v3 = reg.register_version("agent_a", {"name": "A v3"})
    assert v1.version == 1
    assert v2.version == 2
    assert v3.version == 3
    assert v1.status == "draft"
    assert v2.status == "draft"
    # 不同 agent 各自从 1 开始
    vb1 = reg.register_version("agent_b", {"name": "B"})
    assert vb1.version == 1
    print("PASS test_register_version_auto_increment")


# --------------------------------------------------------------------- #
# 测试 4：list_versions 和 get_version
# --------------------------------------------------------------------- #

def test_list_and_get_versions():
    mod = _load_registry_mod()
    reg = mod.AgentLifecycleRegistry()
    reg.register_version("agent_x", {"name": "X v1"})
    reg.register_version("agent_x", {"name": "X v2"})
    versions = reg.list_versions("agent_x")
    assert len(versions) == 2
    assert versions[0].version == 1
    assert versions[1].version == 2
    # get_version by version_id
    vid = versions[0].version_id
    fetched = reg.get_version(vid)
    assert fetched is not None
    assert fetched.version_id == vid
    # 不存在的 version_id
    assert reg.get_version("nonexistent") is None
    # 空 agent 返回空列表
    assert reg.list_versions("unknown_agent") == []
    print("PASS test_list_and_get_versions")


# --------------------------------------------------------------------- #
# 测试 5：activate_version all_at_once
# --------------------------------------------------------------------- #

def test_activate_version_all_at_once():
    mod = _load_registry_mod()
    reg = mod.AgentLifecycleRegistry()
    v1 = reg.register_version("agent_c", {"name": "C"})
    rs = reg.activate_version(v1.version_id)
    assert rs.agent_id == "agent_c"
    assert rs.active_version == v1.version_id
    assert rs.strategy == "all_at_once"
    assert rs.traffic_split == {v1.version_id: 100}
    assert rs.previous_version is None
    # 版本状态已更新为 active
    ver = reg.get_version(v1.version_id)
    assert ver.status == "active"
    print("PASS test_activate_version_all_at_once")


# --------------------------------------------------------------------- #
# 测试 6：activate_version 归档旧版本 + canary 灰度
# --------------------------------------------------------------------- #

def test_activate_archives_old_and_canary():
    mod = _load_registry_mod()
    reg = mod.AgentLifecycleRegistry()
    v1 = reg.register_version("agent_d", {"name": "D v1"})
    reg.activate_version(v1.version_id)
    v2 = reg.register_version("agent_d", {"name": "D v2"})
    rs = reg.activate_version(v2.version_id, strategy="canary")
    # v1 应被归档
    assert reg.get_version(v1.version_id).status == "archived"
    # v2 为 active
    assert reg.get_version(v2.version_id).status == "active"
    assert rs.previous_version == v1.version_id
    assert rs.strategy == "canary"
    # canary 初始 traffic split: 90/10
    assert rs.traffic_split.get(v1.version_id) == 90
    assert rs.traffic_split.get(v2.version_id) == 10
    print("PASS test_activate_archives_old_and_canary")


# --------------------------------------------------------------------- #
# 测试 7：get_active
# --------------------------------------------------------------------- #

def test_get_active():
    mod = _load_registry_mod()
    reg = mod.AgentLifecycleRegistry()
    # 无激活版本
    assert reg.get_active("no_agent") is None
    v1 = reg.register_version("agent_e", {"name": "E"})
    reg.activate_version(v1.version_id)
    active = reg.get_active("agent_e")
    assert active is not None
    assert active.version_id == v1.version_id
    assert active.status == "active"
    print("PASS test_get_active")


# --------------------------------------------------------------------- #
# 测试 8：rollback_version
# --------------------------------------------------------------------- #

def test_rollback_version():
    mod = _load_registry_mod()
    reg = mod.AgentLifecycleRegistry()
    v1 = reg.register_version("agent_f", {"name": "F v1"})
    reg.activate_version(v1.version_id)
    v2 = reg.register_version("agent_f", {"name": "F v2"})
    reg.activate_version(v2.version_id)
    # 回滚回 v1
    rs = reg.rollback_version("agent_f")
    assert rs is not None
    assert rs.active_version == v1.version_id
    assert rs.previous_version is None
    # v1 重新 active
    assert reg.get_version(v1.version_id).status == "active"
    # v2 被归档
    assert reg.get_version(v2.version_id).status == "archived"
    print("PASS test_rollback_version")


# --------------------------------------------------------------------- #
# 测试 9：rollback 无前一版本时返回 None
# --------------------------------------------------------------------- #

def test_rollback_no_previous():
    mod = _load_registry_mod()
    reg = mod.AgentLifecycleRegistry()
    v1 = reg.register_version("agent_g", {"name": "G"})
    reg.activate_version(v1.version_id)
    # 无 previous_version
    result = reg.rollback_version("agent_g")
    assert result is None
    # 不存在的 agent
    result2 = reg.rollback_version("nonexistent_agent")
    assert result2 is None
    print("PASS test_rollback_no_previous")


# --------------------------------------------------------------------- #
# 测试 10：archive_version
# --------------------------------------------------------------------- #

def test_archive_version():
    mod = _load_registry_mod()
    reg = mod.AgentLifecycleRegistry()
    v1 = reg.register_version("agent_h", {"name": "H v1"})
    v2 = reg.register_version("agent_h", {"name": "H v2"})
    reg.activate_version(v2.version_id)
    # v1 是 draft，可归档
    ok = reg.archive_version(v1.version_id)
    assert ok is True
    assert reg.get_version(v1.version_id).status == "archived"
    # 二次归档返回 False
    ok2 = reg.archive_version(v1.version_id)
    assert ok2 is False
    # 不存在版本返回 False
    ok3 = reg.archive_version("nonexistent_vid")
    assert ok3 is False
    # active 版本不能直接归档
    ok4 = reg.archive_version(v2.version_id)
    assert ok4 is False
    print("PASS test_archive_version")


# --------------------------------------------------------------------- #
# 测试 11：set_traffic_split
# --------------------------------------------------------------------- #

def test_set_traffic_split():
    mod = _load_registry_mod()
    reg = mod.AgentLifecycleRegistry()
    v1 = reg.register_version("agent_i", {"name": "I v1"})
    reg.activate_version(v1.version_id)
    v2 = reg.register_version("agent_i", {"name": "I v2"})
    reg.activate_version(v2.version_id, strategy="blue_green")
    # 调整 traffic split
    rs = reg.set_traffic_split("agent_i", {v1.version_id: 30, v2.version_id: 70})
    assert rs.traffic_split[v1.version_id] == 30
    assert rs.traffic_split[v2.version_id] == 70
    print("PASS test_set_traffic_split")


# --------------------------------------------------------------------- #
# 测试 12：set_traffic_split 验证错误
# --------------------------------------------------------------------- #

def test_set_traffic_split_validation():
    mod = _load_registry_mod()
    reg = mod.AgentLifecycleRegistry()
    v1 = reg.register_version("agent_j", {"name": "J"})
    reg.activate_version(v1.version_id)
    # 合计不等于 100
    try:
        reg.set_traffic_split("agent_j", {v1.version_id: 50})
        assert False, "应抛出 ValueError"
    except ValueError as e:
        assert "100" in str(e)
    # agent 无发布状态
    try:
        reg.set_traffic_split("unknown_agent", {v1.version_id: 100})
        assert False, "应抛出 KeyError"
    except KeyError:
        pass
    # 不存在的 version_id
    try:
        reg.set_traffic_split("agent_j", {"nonexistent_vid": 100})
        assert False, "应抛出 ValueError"
    except ValueError as e:
        assert "不存在" in str(e)
    print("PASS test_set_traffic_split_validation")


# --------------------------------------------------------------------- #
# 测试 13：activate 无效 strategy 抛出 ValueError
# --------------------------------------------------------------------- #

def test_activate_invalid_strategy():
    mod = _load_registry_mod()
    reg = mod.AgentLifecycleRegistry()
    v1 = reg.register_version("agent_k", {"name": "K"})
    try:
        reg.activate_version(v1.version_id, strategy="invalid_strategy")
        assert False, "应抛出 ValueError"
    except ValueError as e:
        assert "invalid_strategy" in str(e)
    print("PASS test_activate_invalid_strategy")


# --------------------------------------------------------------------- #
# 测试 14：activate 不存在的 version_id 抛出 KeyError
# --------------------------------------------------------------------- #

def test_activate_nonexistent_version():
    mod = _load_registry_mod()
    reg = mod.AgentLifecycleRegistry()
    try:
        reg.activate_version("nonexistent_version_id")
        assert False, "应抛出 KeyError"
    except KeyError:
        pass
    print("PASS test_activate_nonexistent_version")


# --------------------------------------------------------------------- #
# 测试 15：YAML 加载
# --------------------------------------------------------------------- #

def test_yaml_load():
    mod = _load_registry_mod()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""
versions:
  - version_id: "yaml-v1"
    agent_id: "yaml_agent"
    version: 1
    spec_snapshot:
      agent_id: yaml_agent
      name: YAML Agent
    created_at: 1700000000.0
    created_by: "system"
    status: "draft"
    metadata: {}
  - version_id: "yaml-v2"
    agent_id: "yaml_agent"
    version: 2
    spec_snapshot:
      agent_id: yaml_agent
      name: YAML Agent v2
    created_at: 1700001000.0
    created_by: "system"
    status: "active"
    metadata: {}
""")
        yaml_path = Path(f.name)
    try:
        reg = mod.AgentLifecycleRegistry(yaml_path=yaml_path)
        versions = reg.list_versions("yaml_agent")
        assert len(versions) == 2
        assert versions[0].version_id == "yaml-v1"
        assert versions[1].version_id == "yaml-v2"
        # active version 正确加载
        active = reg.get_active("yaml_agent")
        assert active is not None
        assert active.version_id == "yaml-v2"
    finally:
        yaml_path.unlink(missing_ok=True)
    print("PASS test_yaml_load")


# --------------------------------------------------------------------- #
# 测试 16：JSON overrides 持久化
# --------------------------------------------------------------------- #

def test_json_persist():
    mod = _load_registry_mod()
    with tempfile.TemporaryDirectory() as tmpdir:
        overrides_path = Path(tmpdir) / "overrides.json"
        reg = mod.AgentLifecycleRegistry(overrides_path=overrides_path)
        v1 = reg.register_version("persist_agent", {"name": "P"})
        reg.activate_version(v1.version_id)
        # 文件已写入
        assert overrides_path.exists()
        data = json.loads(overrides_path.read_text())
        assert "versions" in data
        version_ids = [v["version_id"] for v in data["versions"]]
        assert v1.version_id in version_ids
        # 重新加载验证
        reg2 = mod.AgentLifecycleRegistry(overrides_path=overrides_path)
        versions2 = reg2.list_versions("persist_agent")
        assert len(versions2) == 1
        assert versions2[0].version_id == v1.version_id
    print("PASS test_json_persist")


# --------------------------------------------------------------------- #
# 测试 17：全局单例
# --------------------------------------------------------------------- #

def test_global_singleton():
    mod = _load_registry_mod()
    mod.reset_lifecycle_registry_for_tests()
    assert mod.get_lifecycle_registry() is None
    reg = mod.init_lifecycle_registry()
    assert reg is not None
    assert mod.get_lifecycle_registry() is reg
    # 再次初始化覆盖
    reg2 = mod.init_lifecycle_registry()
    assert mod.get_lifecycle_registry() is reg2
    mod.reset_lifecycle_registry_for_tests()
    assert mod.get_lifecycle_registry() is None
    print("PASS test_global_singleton")


# --------------------------------------------------------------------- #
# 测试 18：stats
# --------------------------------------------------------------------- #

def test_stats():
    mod = _load_registry_mod()
    reg = mod.AgentLifecycleRegistry()
    assert reg.stats()["total_agents"] == 0
    v1 = reg.register_version("s1", {"name": "S1"})
    reg.register_version("s1", {"name": "S1 v2"})
    reg.activate_version(v1.version_id)
    stats = reg.stats()
    assert stats["total_agents"] == 1
    assert stats["total_versions"] == 2
    assert stats["active_agents"] == 1
    assert stats["by_status"]["active"] == 1
    assert stats["by_status"]["draft"] == 1
    print("PASS test_stats")


# --------------------------------------------------------------------- #
# 运行所有测试
# --------------------------------------------------------------------- #

if __name__ == "__main__":
    tests = [
        test_agent_version_dataclass,
        test_rollout_strategy_constants,
        test_register_version_auto_increment,
        test_list_and_get_versions,
        test_activate_version_all_at_once,
        test_activate_archives_old_and_canary,
        test_get_active,
        test_rollback_version,
        test_rollback_no_previous,
        test_archive_version,
        test_set_traffic_split,
        test_set_traffic_split_validation,
        test_activate_invalid_strategy,
        test_activate_nonexistent_version,
        test_yaml_load,
        test_json_persist,
        test_global_singleton,
        test_stats,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as ex:
            import traceback
            print(f"FAIL {t.__name__}: {ex}")
            traceback.print_exc()
            failed += 1

    total = passed + failed
    print(f"\n{'='*50}")
    print(f"结果：{passed}/{total} passed, {failed} failed")
    if failed > 0:
        sys.exit(1)
    else:
        print("All tests passed!")
