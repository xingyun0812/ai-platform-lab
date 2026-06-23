#!/usr/bin/env python3
"""动作分级审计单元测试 — Phase I #42

运行：
    python3 tests/test_audit_actions.py

通过 importlib.util 加载模块，避免触发 packages.agent.__init__ 的 pydantic 链。
兼容 Python 3.9+。
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# 辅助：直接加载模块（绕过包初始化链）
# ---------------------------------------------------------------------------

def _load_module(rel_path: str, module_name: str):
    spec = importlib.util.spec_from_file_location(
        module_name, REPO_ROOT / rel_path
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# 按依赖顺序加载
levels_mod = _load_module(
    "packages/audit/action_levels.py", "packages.audit.action_levels"
)
logger_mod = _load_module(
    "packages/audit/action_logger.py", "packages.audit.action_logger"
)

# 导出符号
ActionLevel = levels_mod.ActionLevel
ToolActionClassification = levels_mod.ToolActionClassification
ActionClassifier = levels_mod.ActionClassifier
init_classifier = levels_mod.init_classifier
get_classifier = levels_mod.get_classifier
reset_for_tests = levels_mod.reset_for_tests

ActionAuditEntry = logger_mod.ActionAuditEntry
ActionAuditLogger = logger_mod.ActionAuditLogger
init_action_logger = logger_mod.init_action_logger
get_action_logger = logger_mod.get_action_logger
reset_logger_for_tests = logger_mod.reset_for_tests


# ---------------------------------------------------------------------------
# 测试工具
# ---------------------------------------------------------------------------

def _run_async(coro):
    """兼容 Python 3.9 的 async 测试运行器。"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


_passed = 0
_failed = 0


def _assert(cond: bool, msg: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✓ {msg}")
    else:
        _failed += 1
        print(f"  ✗ FAIL: {msg}")


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------

# --- Test 1: ActionLevel 常量正确 ---
def test_action_level_constants():
    print("\n[1] ActionLevel 常量")
    _assert(ActionLevel.READ_ONLY == "read_only", "READ_ONLY == 'read_only'")
    _assert(ActionLevel.WRITE == "write", "WRITE == 'write'")
    _assert(ActionLevel.DESTRUCTIVE == "destructive", "DESTRUCTIVE == 'destructive'")
    _assert(ActionLevel.NETWORK == "network", "NETWORK == 'network'")
    _assert(ActionLevel.UNKNOWN == "unknown", "UNKNOWN == 'unknown'")
    _assert(ActionLevel.is_valid("read_only"), "is_valid('read_only')")
    _assert(not ActionLevel.is_valid("bogus"), "not is_valid('bogus')")


# --- Test 2: ToolActionClassification 数据模型 ---
def test_tool_action_classification_dataclass():
    print("\n[2] ToolActionClassification 数据模型")
    cls_ = ToolActionClassification(
        tool_name="my_tool",
        action_level=ActionLevel.WRITE,
        requires_approval=True,
        description="测试工具",
        metadata={"owner": "team-a"},
    )
    _assert(cls_.tool_name == "my_tool", "tool_name")
    _assert(cls_.action_level == ActionLevel.WRITE, "action_level")
    _assert(cls_.requires_approval is True, "requires_approval")
    d = cls_.to_dict()
    _assert(d["tool_name"] == "my_tool", "to_dict tool_name")
    _assert(d["metadata"] == {"owner": "team-a"}, "to_dict metadata")


# --- Test 3: ActionClassifier 注册与查询 ---
def test_classifier_register_get_list():
    print("\n[3] ActionClassifier register/get/list")
    reset_for_tests()
    c = ActionClassifier()
    cls_ = ToolActionClassification(
        tool_name="custom_tool",
        action_level=ActionLevel.NETWORK,
        description="自定义工具",
    )
    c.register_classification(cls_)
    got = c.get_classification("custom_tool")
    _assert(got is not None, "get_classification 不为 None")
    _assert(got.action_level == ActionLevel.NETWORK, "action_level == network")
    all_cls = c.list_classifications()
    names = [x.tool_name for x in all_cls]
    _assert("custom_tool" in names, "list_classifications 包含 custom_tool")
    # 内置也在列表中
    _assert("calc" in names, "list_classifications 包含内置 calc")


# --- Test 4: 启发式分类 — destructive ---
def test_heuristic_destructive():
    print("\n[4] 启发式分类 — destructive")
    c = ActionClassifier()
    _assert(c.classify("delete_records") == ActionLevel.DESTRUCTIVE, "delete_records → destructive")
    _assert(c.classify("drop_database") == ActionLevel.DESTRUCTIVE, "drop_database → destructive")
    _assert(c.classify("rm_file") == ActionLevel.DESTRUCTIVE, "rm_file → destructive")


# --- Test 5: 启发式分类 — write ---
def test_heuristic_write():
    print("\n[5] 启发式分类 — write")
    c = ActionClassifier()
    _assert(c.classify("create_user") == ActionLevel.WRITE, "create_user → write")
    _assert(c.classify("update_profile") == ActionLevel.WRITE, "update_profile → write")
    _assert(c.classify("send_notification") == ActionLevel.WRITE, "send_notification → write")


# --- Test 6: 启发式分类 — read_only ---
def test_heuristic_read_only():
    print("\n[6] 启发式分类 — read_only")
    c = ActionClassifier()
    _assert(c.classify("get_user") == ActionLevel.READ_ONLY, "get_user → read_only")
    _assert(c.classify("list_orders") == ActionLevel.READ_ONLY, "list_orders → read_only")
    _assert(c.classify("search_items") == ActionLevel.READ_ONLY, "search_items → read_only")
    _assert(c.classify("read_file") == ActionLevel.READ_ONLY, "read_file → read_only")


# --- Test 7: requires_approval ---
def test_requires_approval():
    print("\n[7] requires_approval")
    c = ActionClassifier()
    # 注册一个 destructive 工具
    c.register_classification(
        ToolActionClassification("nuke_all", ActionLevel.DESTRUCTIVE, requires_approval=True)
    )
    _assert(c.requires_approval("nuke_all"), "nuke_all 需要审批")
    # 注册一个非 destructive 但 requires_approval=True
    c.register_classification(
        ToolActionClassification("risky_write", ActionLevel.WRITE, requires_approval=True)
    )
    _assert(c.requires_approval("risky_write"), "risky_write requires_approval=True")
    # read_only 不需要审批
    _assert(not c.requires_approval("calc"), "calc 不需要审批")
    # 启发式 destructive
    _assert(c.requires_approval("delete_something_unknown"), "启发式 destructive 需审批")


# --- Test 8: 内置默认分类 ---
def test_builtin_classifications():
    print("\n[8] 内置默认分类")
    c = ActionClassifier()
    _assert(c.classify("calc") == ActionLevel.READ_ONLY, "calc → read_only")
    _assert(c.classify("search_web_stub") == ActionLevel.NETWORK, "search_web_stub → network")
    _assert(c.classify("httpbin_delay") == ActionLevel.NETWORK, "httpbin_delay → network")
    _assert(c.classify("math_llm_stub") == ActionLevel.READ_ONLY, "math_llm_stub → read_only")
    _assert(c.classify("get_kb_snippet") == ActionLevel.READ_ONLY, "get_kb_snippet → read_only")


# --- Test 9: YAML 加载 ---
def test_yaml_load():
    print("\n[9] YAML 加载")
    try:
        import yaml as _yaml_check  # noqa: F401
    except ImportError:
        print("  ⚠ PyYAML 未安装，跳过 YAML 测试")
        global _passed
        _passed += 1
        return

    yaml_content = """
classifications:
  - tool_name: yaml_tool
    action_level: write
    requires_approval: false
    description: YAML 测试工具
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        f.write(yaml_content)
        yaml_path = Path(f.name)

    try:
        c = ActionClassifier()
        c.load_yaml(yaml_path)
        got = c.get_classification("yaml_tool")
        _assert(got is not None, "YAML 工具已加载")
        _assert(got.action_level == ActionLevel.WRITE, "YAML 工具 action_level == write")
    finally:
        yaml_path.unlink(missing_ok=True)


# --- Test 10: JSON 覆盖加载 ---
def test_json_overrides():
    print("\n[10] JSON 覆盖加载")
    overrides = [
        {
            "tool_name": "json_tool",
            "action_level": "destructive",
            "requires_approval": True,
            "description": "JSON 覆盖测试",
        }
    ]
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(overrides, f)
        json_path = Path(f.name)

    try:
        c = ActionClassifier()
        c.load_json_overrides(json_path)
        got = c.get_classification("json_tool")
        _assert(got is not None, "JSON 工具已加载")
        _assert(got.action_level == ActionLevel.DESTRUCTIVE, "JSON 工具 action_level == destructive")
        _assert(got.requires_approval is True, "JSON 工具 requires_approval == True")
    finally:
        json_path.unlink(missing_ok=True)


# --- Test 11: ActionAuditLogger — log / get / list ---
def test_action_audit_logger_basic():
    print("\n[11] ActionAuditLogger — log / get / list")
    reset_logger_for_tests()
    logger = ActionAuditLogger()

    entry = ActionAuditEntry(
        entry_id="eid-001",
        tenant_id="tenant-a",
        session_id="sess-1",
        tool_name="delete_file",
        action_level=ActionLevel.DESTRUCTIVE,
        arguments={"path": "/tmp/secret"},
        result_summary="file deleted",
        status="denied",
    )
    eid = _run_async(logger.log_action(entry))
    _assert(eid == "eid-001", f"log_action 返回 entry_id == {eid}")

    got = _run_async(logger.get_action("eid-001"))
    _assert(got is not None, "get_action 不为 None")
    _assert(got.tool_name == "delete_file", "got.tool_name")
    _assert(got.status == "denied", "got.status == denied")

    all_entries = _run_async(logger.list_actions("tenant-a"))
    _assert(len(all_entries) >= 1, "list_actions 返回 >= 1 条")


# --- Test 12: list_destructive_actions ---
def test_list_destructive_actions():
    print("\n[12] list_destructive_actions")
    logger = ActionAuditLogger()

    # 加入一条 read_only 记录
    e_read = ActionAuditEntry(
        entry_id="eid-r01",
        tenant_id="tenant-b",
        session_id="sess-2",
        tool_name="get_data",
        action_level=ActionLevel.READ_ONLY,
    )
    _run_async(logger.log_action(e_read))

    # 加入一条 destructive 记录
    e_dest = ActionAuditEntry(
        entry_id="eid-d01",
        tenant_id="tenant-b",
        session_id="sess-2",
        tool_name="drop_table",
        action_level=ActionLevel.DESTRUCTIVE,
        status="pending",
    )
    _run_async(logger.log_action(e_dest))

    destructive = _run_async(logger.list_destructive_actions("tenant-b"))
    _assert(len(destructive) == 1, "list_destructive_actions 返回 1 条")
    _assert(destructive[0].tool_name == "drop_table", "tool_name == drop_table")

    # read_only 应不出现
    names = [e.tool_name for e in destructive]
    _assert("get_data" not in names, "get_data 不在 destructive 列表中")


# --- Test 13: ActionAuditLogger 按 action_level 过滤 ---
def test_list_actions_filter():
    print("\n[13] list_actions 按 action_level 过滤")
    logger = ActionAuditLogger()

    for i, level in enumerate([ActionLevel.READ_ONLY, ActionLevel.WRITE, ActionLevel.WRITE]):
        e = ActionAuditEntry(
            entry_id=f"filter-{i}",
            tenant_id="tenant-c",
            session_id="s",
            tool_name=f"tool_{i}",
            action_level=level,
        )
        _run_async(logger.log_action(e))

    write_entries = _run_async(logger.list_actions("tenant-c", action_level=ActionLevel.WRITE))
    _assert(len(write_entries) == 2, "WRITE 过滤返回 2 条")

    read_entries = _run_async(logger.list_actions("tenant-c", action_level=ActionLevel.READ_ONLY))
    _assert(len(read_entries) == 1, "READ_ONLY 过滤返回 1 条")


# --- Test 14: 全局单例 init / get / reset ---
def test_singleton_lifecycle():
    print("\n[14] 全局单例 lifecycle")
    reset_for_tests()
    _assert(get_classifier() is None, "reset 后 get_classifier() == None")
    c1 = init_classifier()
    c2 = init_classifier()
    _assert(c1 is c2, "init_classifier 两次返回同一实例")
    _assert(get_classifier() is c1, "get_classifier() 返回单例")
    reset_for_tests()
    _assert(get_classifier() is None, "再次 reset 后 == None")

    reset_logger_for_tests()
    _assert(get_action_logger() is None, "reset 后 get_action_logger() == None")
    lg1 = init_action_logger()
    lg2 = init_action_logger()
    _assert(lg1 is lg2, "init_action_logger 两次返回同一实例")
    reset_logger_for_tests()
    _assert(get_action_logger() is None, "再次 reset logger 后 == None")


# --- Test 15: remove_classification ---
def test_remove_classification():
    print("\n[15] remove_classification")
    c = ActionClassifier()
    c.register_classification(
        ToolActionClassification("temp_tool", ActionLevel.WRITE)
    )
    _assert(c.get_classification("temp_tool") is not None, "注册后存在")
    ok = c.remove_classification("temp_tool")
    _assert(ok is True, "remove 返回 True")
    _assert(c.get_classification("temp_tool") is None, "删除后不存在")
    ok2 = c.remove_classification("temp_tool")
    _assert(ok2 is False, "再次 remove 返回 False")


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_action_level_constants()
    test_tool_action_classification_dataclass()
    test_classifier_register_get_list()
    test_heuristic_destructive()
    test_heuristic_write()
    test_heuristic_read_only()
    test_requires_approval()
    test_builtin_classifications()
    test_yaml_load()
    test_json_overrides()
    test_action_audit_logger_basic()
    test_list_destructive_actions()
    test_list_actions_filter()
    test_singleton_lifecycle()
    test_remove_classification()

    print(f"\n{'='*50}")
    total = _passed + _failed
    print(f"Results: {_passed}/{total} passed, {_failed} failed")
    if _failed > 0:
        sys.exit(1)
    else:
        print("All tests passed!")
