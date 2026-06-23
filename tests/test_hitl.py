#!/usr/bin/env python3
"""HITL 审批工作流单元测试 — Phase H #40

运行：
    python3 tests/test_hitl.py

通过 importlib.util 加载模块，避免触发 packages.agent.__init__ 的 pydantic 链。
兼容 Python 3.9+。
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import tempfile
import time
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
store_mod = _load_module("packages/hitl/store.py", "packages.hitl.store")
webhook_mod = _load_module("packages/hitl/webhook.py", "packages.hitl.webhook")
service_mod = _load_module("packages/hitl/service.py", "packages.hitl.service")

ApprovalStatus = store_mod.ApprovalStatus
ApprovalRequest = store_mod.ApprovalRequest
ApprovalDecision = store_mod.ApprovalDecision
WebhookConfig = store_mod.WebhookConfig
InMemoryApprovalStore = store_mod.InMemoryApprovalStore
SqliteApprovalStore = store_mod.SqliteApprovalStore
init_approval_store = store_mod.init_approval_store
get_approval_store = store_mod.get_approval_store
reset_approval_store_for_tests = store_mod.reset_approval_store_for_tests

_compute_signature = webhook_mod._compute_signature
verify_signature = webhook_mod.verify_signature

request_approval = service_mod.request_approval
check_approval = service_mod.check_approval
approve = service_mod.approve
reject = service_mod.reject
timeout_expired_requests = service_mod.timeout_expired_requests


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


def _make_req(store, **kwargs) -> ApprovalRequest:
    now = time.time()
    req = ApprovalRequest(
        request_id=kwargs.get("request_id", "test-001"),
        tenant_id=kwargs.get("tenant_id", "tenant-a"),
        session_id=kwargs.get("session_id", "sess-1"),
        tool_name=kwargs.get("tool_name", "delete_file"),
        arguments=kwargs.get("arguments", {"path": "/tmp/x"}),
        created_at=kwargs.get("created_at", now),
        expires_at=kwargs.get("expires_at", now + 300),
    )
    _run_async(store.create(req))
    return req


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------

def test_inmemory_create_and_get():
    """InMemoryApprovalStore: create + get"""
    store = InMemoryApprovalStore()
    req = _make_req(store)
    found = _run_async(store.get(req.request_id))
    assert found is not None
    assert found.request_id == req.request_id
    assert found.status == "pending"
    print("PASS test_inmemory_create_and_get")


def test_inmemory_list_pending():
    """InMemoryApprovalStore: list_pending 返回正确租户的待审批"""
    store = InMemoryApprovalStore()
    _make_req(store, request_id="r1", tenant_id="t1")
    _make_req(store, request_id="r2", tenant_id="t2")
    _make_req(store, request_id="r3", tenant_id="t1")

    pending_t1 = _run_async(store.list_pending("t1"))
    ids = {r.request_id for r in pending_t1}
    assert "r1" in ids
    assert "r3" in ids
    assert "r2" not in ids
    print("PASS test_inmemory_list_pending")


def test_inmemory_approve():
    """InMemoryApprovalStore: decide approved"""
    store = InMemoryApprovalStore()
    _make_req(store, request_id="app-1")
    decision = ApprovalDecision(
        request_id="app-1",
        status="approved",
        decided_by="admin",
        reason="OK",
        decided_at=time.time(),
    )
    result = _run_async(store.decide(decision))
    assert result is not None
    assert result.status == "approved"
    assert result.decided_by == "admin"
    print("PASS test_inmemory_approve")


def test_inmemory_reject():
    """InMemoryApprovalStore: decide rejected"""
    store = InMemoryApprovalStore()
    _make_req(store, request_id="rej-1")
    decision = ApprovalDecision(
        request_id="rej-1",
        status="rejected",
        decided_by="admin",
        reason="危险操作",
        decided_at=time.time(),
    )
    result = _run_async(store.decide(decision))
    assert result is not None
    assert result.status == "rejected"
    print("PASS test_inmemory_reject")


def test_inmemory_double_decide_returns_none():
    """InMemoryApprovalStore: 重复决策应返回 None"""
    store = InMemoryApprovalStore()
    _make_req(store, request_id="dbl-1")
    decision = ApprovalDecision(
        request_id="dbl-1", status="approved",
        decided_by="admin", reason=None, decided_at=time.time(),
    )
    _run_async(store.decide(decision))
    result2 = _run_async(store.decide(decision))
    assert result2 is None
    print("PASS test_inmemory_double_decide_returns_none")


def test_inmemory_cancel():
    """InMemoryApprovalStore: cancel"""
    store = InMemoryApprovalStore()
    _make_req(store, request_id="cancel-1")
    ok = _run_async(store.cancel("cancel-1", by="admin"))
    assert ok is True
    req = _run_async(store.get("cancel-1"))
    assert req.status == "cancelled"
    # 再次取消应失败
    ok2 = _run_async(store.cancel("cancel-1", by="admin"))
    assert ok2 is False
    print("PASS test_inmemory_cancel")


def test_inmemory_expire_stale():
    """InMemoryApprovalStore: expire_stale 处理过期请求"""
    store = InMemoryApprovalStore()
    past = time.time() - 100
    req = ApprovalRequest(
        request_id="exp-1",
        tenant_id="t1",
        session_id="s1",
        tool_name="tool",
        arguments={},
        created_at=past,
        expires_at=past + 50,  # 已过期
    )
    _run_async(store.create(req))
    count = _run_async(store.expire_stale())
    assert count >= 1
    found = _run_async(store.get("exp-1"))
    assert found.status == "timeout"
    print("PASS test_inmemory_expire_stale")


def test_webhook_signature_compute():
    """webhook: HMAC-SHA256 签名格式正确"""
    secret = "test-secret"
    body = b'{"event":"test"}'
    sig = _compute_signature(secret, body)
    assert sig.startswith("sha256=")
    assert len(sig) == 7 + 64  # sha256= + 64 hex chars
    print("PASS test_webhook_signature_compute")


def test_webhook_signature_verify():
    """webhook: verify_signature 正确验证签名"""
    secret = "my-secret"
    body = b'{"event":"hitl.test"}'
    sig = _compute_signature(secret, body)
    assert verify_signature(secret, body, sig) is True
    # 错误签名
    assert verify_signature(secret, body, "sha256=wrong") is False
    print("PASS test_webhook_signature_verify")


def test_sqlite_store_create_get():
    """SqliteApprovalStore: create + get"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_url = f"sqlite:///{tmpdir}/test.db"
        store = SqliteApprovalStore(db_url)
        req = ApprovalRequest(
            request_id="sq-1",
            tenant_id="t1",
            session_id="s1",
            tool_name="tool_x",
            arguments={"a": 1},
            created_at=time.time(),
            expires_at=time.time() + 300,
        )
        _run_async(store.create(req))
        found = _run_async(store.get("sq-1"))
        assert found is not None
        assert found.tool_name == "tool_x"
        assert found.arguments == {"a": 1}
    print("PASS test_sqlite_store_create_get")


def test_sqlite_store_approve():
    """SqliteApprovalStore: approve 流程"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_url = f"sqlite:///{tmpdir}/test2.db"
        store = SqliteApprovalStore(db_url)
        req = ApprovalRequest(
            request_id="sq-approve",
            tenant_id="t1",
            session_id="s1",
            tool_name="tool_y",
            arguments={},
            created_at=time.time(),
            expires_at=time.time() + 300,
        )
        _run_async(store.create(req))
        decision = ApprovalDecision(
            request_id="sq-approve",
            status="approved",
            decided_by="admin",
            reason="fine",
            decided_at=time.time(),
        )
        result = _run_async(store.decide(decision))
        assert result is not None
        assert result.status == "approved"
        assert result.decided_by == "admin"
    print("PASS test_sqlite_store_approve")


def test_singleton_init_and_reset():
    """全局单例: init_approval_store / get_approval_store / reset"""
    reset_approval_store_for_tests()
    assert get_approval_store() is None

    store = init_approval_store()
    assert store is not None
    assert isinstance(store, InMemoryApprovalStore)
    assert get_approval_store() is store

    reset_approval_store_for_tests()
    assert get_approval_store() is None
    print("PASS test_singleton_init_and_reset")


def test_service_request_and_check():
    """service: request_approval + check_approval"""
    reset_approval_store_for_tests()
    init_approval_store()

    async def _run():
        req = await request_approval(
            tenant_id="t1",
            session_id="s1",
            tool_name="risky_op",
            arguments={"x": 1},
            timeout_seconds=300,
        )
        assert req.status == "pending"

        status = await check_approval(req.request_id)
        assert status == ApprovalStatus.PENDING

        return req.request_id

    _run_async(_run())
    reset_approval_store_for_tests()
    print("PASS test_service_request_and_check")


def test_service_approve_and_reject():
    """service: approve + reject 流程"""
    reset_approval_store_for_tests()
    init_approval_store()

    async def _run():
        req1 = await request_approval(
            tenant_id="t1", session_id="s1",
            tool_name="tool_a", arguments={},
        )
        approved = await approve(req1.request_id, decided_by="admin", reason="LGTM")
        assert approved.status == "approved"

        req2 = await request_approval(
            tenant_id="t1", session_id="s1",
            tool_name="tool_b", arguments={},
        )
        rejected = await reject(req2.request_id, decided_by="admin", reason="too risky")
        assert rejected.status == "rejected"

    _run_async(_run())
    reset_approval_store_for_tests()
    print("PASS test_service_approve_and_reject")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    tests = [
        test_inmemory_create_and_get,
        test_inmemory_list_pending,
        test_inmemory_approve,
        test_inmemory_reject,
        test_inmemory_double_decide_returns_none,
        test_inmemory_cancel,
        test_inmemory_expire_stale,
        test_webhook_signature_compute,
        test_webhook_signature_verify,
        test_sqlite_store_create_get,
        test_sqlite_store_approve,
        test_singleton_init_and_reset,
        test_service_request_and_check,
        test_service_approve_and_reject,
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
    total = len(tests)
    print(f"\n{total - failed}/{total} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
