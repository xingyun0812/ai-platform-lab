#!/usr/bin/env python3
"""上下文压缩策略单元测试 — Phase F #33

运行：
    python3 tests/test_context_compress.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.agent.context_compress import (  # noqa: E402
    MemoryInjection,
    inject_memory_into_messages,
    llm_summarize,
    maybe_compact_with_llm,
    memory_injection_platform_meta,
    retrieve_and_inject_memory,
)
from packages.agent.session_state import SessionState  # noqa: E402
from packages.memory import (  # noqa: E402
    MemoryRecord,
)
from packages.memory.store import (  # noqa: E402
    reset_memory_store_for_tests,
)


def _setup():
    reset_memory_store_for_tests()


def test_llm_summarize_stub_fallback():
    """无 LLM_API_KEY 时应回退 stub_summarize"""
    _setup()
    messages = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好，有什么可以帮你？"},
    ]

    async def run():
        summary, source = await llm_summarize(messages, tenant_id="t1")
        # 无 Key 时 summarize_messages 返回截断 history，但仍算 "llm"（因为函数返回了非空）
        # 若完全失败则回退 stub
        assert source in ("llm", "stub")
        assert summary  # 非空

    asyncio.run(run())
    print("PASS test_llm_summarize_stub_fallback")


def test_llm_summarize_empty_messages():
    _setup()

    async def run():
        summary, source = await llm_summarize([], tenant_id="t1")
        assert summary == ""
        assert source == "none"

    asyncio.run(run())
    print("PASS test_llm_summarize_empty_messages")


def test_maybe_compact_with_llm_no_compact_needed():
    """turn_count 不满足周期时直接返回原 state"""
    _setup()
    state = SessionState(
        messages=[{"role": "user", "content": "hi"}],
        summary=None,
        turn_count=3,
    )

    async def run():
        result = await maybe_compact_with_llm(
            state, every_n_turns=8, keep_recent_turns=4, tenant_id="t1"
        )
        assert result is state  # 原对象

    asyncio.run(run())
    print("PASS test_maybe_compact_with_llm_no_compact_needed")


def test_maybe_compact_with_llm_few_turns():
    """turns 数量 <= keep_recent_turns 时不压缩"""
    _setup()
    state = SessionState(
        messages=[
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
        ],
        summary=None,
        turn_count=8,
    )

    async def run():
        result = await maybe_compact_with_llm(
            state, every_n_turns=8, keep_recent_turns=4, tenant_id="t1"
        )
        # turns=1, keep=4 → 不压缩
        assert len(result.messages) == 2

    asyncio.run(run())
    print("PASS test_maybe_compact_with_llm_few_turns")


def test_maybe_compact_with_llm_compressed():
    """多轮时触发压缩：保留 recent，老对话进 summary"""
    _setup()
    messages = []
    for i in range(10):
        messages.append({"role": "user", "content": f"问题 {i}"})
        messages.append({"role": "assistant", "content": f"回答 {i}"})
    state = SessionState(messages=messages, summary=None, turn_count=10)

    async def run():
        result = await maybe_compact_with_llm(
            state, every_n_turns=10, keep_recent_turns=3, tenant_id="t1"
        )
        # 10 turns，keep 3 → recent 6 messages，老 7 turns 压缩为 summary
        assert len(result.messages) <= 6
        assert result.summary is not None

    asyncio.run(run())
    print("PASS test_maybe_compact_with_llm_compressed")


def test_retrieve_and_inject_memory_low_budget():
    """剩余 budget 不足时跳过注入"""
    _setup()

    async def run():
        injection = await retrieve_and_inject_memory(
            tenant_id="t1",
            session_id="s1",
            query="test",
            budget_remaining=100,  # 低于 200 阈值
            top_k=3,
        )
        assert injection.injected is False
        assert injection.system_message is None

    asyncio.run(run())
    print("PASS test_retrieve_and_inject_memory_low_budget")


def test_retrieve_and_inject_memory_empty_query():
    _setup()

    async def run():
        injection = await retrieve_and_inject_memory(
            tenant_id="t1",
            session_id="s1",
            query="",
            budget_remaining=5000,
            top_k=3,
        )
        assert injection.injected is False

    asyncio.run(run())
    print("PASS test_retrieve_and_inject_memory_empty_query")


def test_retrieve_and_inject_memory_no_store():
    """无 memory store 时返回 not injected"""
    _setup()

    async def run():
        injection = await retrieve_and_inject_memory(
            tenant_id="t1",
            session_id="s1",
            query="test",
            budget_remaining=5000,
            top_k=3,
        )
        # 全局 store 未初始化 → get_memory_store() 返回 None
        assert injection.injected is False

    asyncio.run(run())
    print("PASS test_retrieve_and_inject_memory_no_store")


def test_retrieve_and_inject_memory_with_data():
    """有记忆数据时应注入"""
    _setup()
    from packages.memory import init_memory_store

    store = init_memory_store(database_url=None)

    async def setup():
        await store.add(
            MemoryRecord(
                memory_id="m1",
                tenant_id="t1",
                scope="session",
                scope_id="s1",
                content="用户偏好：喜欢简洁回答",
                metadata={"turn_count": 1},
            )
        )

    async def run():
        injection = await retrieve_and_inject_memory(
            tenant_id="t1",
            session_id="s1",
            query="偏好",
            budget_remaining=5000,
            top_k=3,
        )
        assert injection.injected is True
        assert injection.memory_count >= 1
        assert injection.system_message is not None
        assert "偏好" in injection.system_message["content"]

    asyncio.run(setup())
    asyncio.run(run())
    print("PASS test_retrieve_and_inject_memory_with_data")


def test_retrieve_and_inject_memory_budget_limits_tokens():
    """budget 不足时只注入部分记忆"""
    _setup()
    from packages.memory import init_memory_store

    store = init_memory_store(database_url=None)

    async def setup():
        # 添加 5 条长记忆
        for i in range(5):
            await store.add(
                MemoryRecord(
                    memory_id=f"m{i}",
                    tenant_id="t1",
                    scope="session",
                    scope_id="s1",
                    content=f"记忆 {i}：" + "x" * 500,
                    metadata={},
                )
            )

    async def run():
        # budget 仅够 1 条
        injection = await retrieve_and_inject_memory(
            tenant_id="t1",
            session_id="s1",
            query="记忆",
            budget_remaining=400,  # 仅够 1-2 条
            top_k=5,
        )
        # 注入条数应受 budget 限制
        assert injection.injected is True
        assert injection.memory_count >= 1
        assert injection.injected_tokens <= 400

    asyncio.run(setup())
    asyncio.run(run())
    print("PASS test_retrieve_and_inject_memory_budget_limits_tokens")


def test_inject_memory_into_messages_after_summary():
    """after_summary 模式：在第一个非 system 消息前插入"""
    messages = [
        {"role": "system", "content": "[session_summary] xxx"},
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "回答"},
    ]
    injection = MemoryInjection(
        injected=True,
        memory_count=1,
        injected_tokens=10,
        memories=[],
        system_message={"role": "system", "content": "记忆要点"},
    )
    result = inject_memory_into_messages(messages, injection, position="after_summary")
    assert len(result) == 4
    assert result[0]["content"] == "[session_summary] xxx"
    assert result[1]["content"] == "记忆要点"
    assert result[2]["role"] == "user"
    print("PASS test_inject_memory_into_messages_after_summary")


def test_inject_memory_into_messages_prepend():
    messages = [{"role": "user", "content": "问题"}]
    injection = MemoryInjection(
        injected=True,
        memory_count=1,
        injected_tokens=10,
        memories=[],
        system_message={"role": "system", "content": "记忆"},
    )
    result = inject_memory_into_messages(messages, injection, position="prepend")
    assert len(result) == 2
    assert result[0]["role"] == "system"
    assert result[0]["content"] == "记忆"
    print("PASS test_inject_memory_into_messages_prepend")


def test_inject_memory_into_messages_not_injected():
    """injection.injected=False 时不修改 messages"""
    messages = [{"role": "user", "content": "问题"}]
    injection = MemoryInjection(
        injected=False,
        memory_count=0,
        injected_tokens=0,
        memories=[],
        system_message=None,
    )
    result = inject_memory_into_messages(messages, injection)
    assert result is messages
    assert len(result) == 1
    print("PASS test_inject_memory_into_messages_not_injected")


def test_memory_injection_platform_meta():
    injection = MemoryInjection(
        injected=True,
        memory_count=3,
        injected_tokens=120,
        memories=[{"memory_id": "m1"}],
        system_message={"role": "system", "content": "..."},
    )
    meta = memory_injection_platform_meta(injection)
    assert meta["injected"] is True
    assert meta["memory_count"] == 3
    assert meta["injected_tokens"] == 120
    print("PASS test_memory_injection_platform_meta")


def test_memory_injection_scope_isolation():
    """scope=session 时仅检索该 session 的记忆"""
    _setup()
    from packages.memory import init_memory_store

    store = init_memory_store(database_url=None)

    async def setup():
        # session s1 的记忆
        await store.add(
            MemoryRecord(
                memory_id="m1",
                tenant_id="t1",
                scope="session",
                scope_id="s1",
                content="s1 的记忆",
            )
        )
        # session s2 的记忆（不应被 s1 检索到）
        await store.add(
            MemoryRecord(
                memory_id="m2",
                tenant_id="t1",
                scope="session",
                scope_id="s2",
                content="s2 的记忆",
            )
        )

    async def run():
        injection = await retrieve_and_inject_memory(
            tenant_id="t1",
            session_id="s1",
            query="记忆",
            budget_remaining=5000,
            top_k=5,
            scope="session",
        )
        assert injection.injected is True
        assert injection.memory_count == 1
        assert "s1" in injection.system_message["content"]
        assert "s2" not in injection.system_message["content"]

    asyncio.run(setup())
    asyncio.run(run())
    print("PASS test_memory_injection_scope_isolation")


def main() -> int:
    tests = [
        test_llm_summarize_stub_fallback,
        test_llm_summarize_empty_messages,
        test_maybe_compact_with_llm_no_compact_needed,
        test_maybe_compact_with_llm_few_turns,
        test_maybe_compact_with_llm_compressed,
        test_retrieve_and_inject_memory_low_budget,
        test_retrieve_and_inject_memory_empty_query,
        test_retrieve_and_inject_memory_no_store,
        test_retrieve_and_inject_memory_with_data,
        test_retrieve_and_inject_memory_budget_limits_tokens,
        test_inject_memory_into_messages_after_summary,
        test_inject_memory_into_messages_prepend,
        test_inject_memory_into_messages_not_injected,
        test_memory_injection_platform_meta,
        test_memory_injection_scope_isolation,
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
