"""tests/test_experience_persistence.py — Phase R R1+ 持久化与语义检索测试。

验证：
- InMemoryExperienceStore 的 embedding cosine 检索
- PostgresExperienceStore 的 mock SQL 执行
- compute_task_embedding 降级链
- backend 自动选择
- cosine similarity 计算正确
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from packages.agent.experience_store import (  # noqa: E402
    ExperienceRecord,
    InMemoryExperienceStore,
    PostgresExperienceStore,
    _cosine_similarity,
    build_experience_record,
    compute_task_embedding,
    compute_task_signature,
    get_experience_store,
    reset_experience_store_for_tests,
)
from packages.contracts.agent_schemas import AgentPlan, PlanStep  # noqa: E402


def _run_async(coro):
    return asyncio.run(coro)


def _make_plan(goal: str = "test goal") -> AgentPlan:
    return AgentPlan(goal=goal, steps=[PlanStep(id="s1", description="do thing", depends_on=[])])


def _make_record(
    goal: str = "查询销售数据",
    outcome: str = "success",
    lessons: str = "经验 P1",
    embedding: list[float] | None = None,
) -> ExperienceRecord:
    return build_experience_record(
        tenant_id="t1",
        goal=goal,
        plan=_make_plan(goal),
        outcome=outcome,
        lessons=lessons,
        embedding=embedding,
    )


# ---------------------------------------------------------------------------
# cosine similarity
# ---------------------------------------------------------------------------


class TestCosineSimilarity(unittest.TestCase):
    def test_identical_vectors_return_1(self) -> None:
        """相同向量 cosine = 1。"""
        v = [1.0, 0.0, 0.0]
        self.assertAlmostEqual(_cosine_similarity(v, v), 1.0, places=6)

    def test_orthogonal_vectors_return_0(self) -> None:
        """正交向量 cosine = 0。"""
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        self.assertAlmostEqual(_cosine_similarity(a, b), 0.0, places=6)

    def test_opposite_vectors_return_neg1(self) -> None:
        """反向向量 cosine = -1。"""
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        self.assertAlmostEqual(_cosine_similarity(a, b), -1.0, places=6)

    def test_dimension_mismatch_returns_0(self) -> None:
        """维度不匹配返回 0。"""
        a = [1.0, 0.0]
        b = [1.0, 0.0, 0.0]
        self.assertEqual(_cosine_similarity(a, b), 0.0)

    def test_empty_vectors_return_0(self) -> None:
        """空向量返回 0。"""
        self.assertEqual(_cosine_similarity([], []), 0.0)

    def test_zero_norm_returns_0(self) -> None:
        """零范数向量返回 0。"""
        self.assertEqual(_cosine_similarity([0.0, 0.0], [1.0, 0.0]), 0.0)


# ---------------------------------------------------------------------------
# InMemoryExperienceStore with embedding
# ---------------------------------------------------------------------------


class TestInMemoryEmbeddingRetrieval(unittest.TestCase):
    def setUp(self) -> None:
        reset_experience_store_for_tests()

    def test_retrieve_similar_with_embedding_ranks_by_cosine(self) -> None:
        """有 embedding 时按 cosine 排序，最相似的排第一。"""
        store = InMemoryExperienceStore()
        # query embedding [1, 0]
        # record A embedding [1, 0] → cosine 1.0（最相似）
        # record B embedding [0, 1] → cosine 0.0（不相似）
        r_a = _make_record(goal="task A", lessons="A", embedding=[1.0, 0.0])
        r_b = _make_record(goal="task B", lessons="B", embedding=[0.0, 1.0])
        _run_async(store.store(r_a))
        _run_async(store.store(r_b))

        # 用 r_a 的 signature 检索（精确匹配），但 embedding 用 [1, 0]
        results = _run_async(
            store.retrieve_similar(
                r_a.task_signature,
                task_embedding=[1.0, 0.0],
                top_k=2,
            )
        )
        self.assertGreaterEqual(len(results), 1)
        # 最相似的应该是 r_a（cosine=1.0）
        self.assertEqual(results[0].experience_id, r_a.experience_id)

    def test_retrieve_similar_without_embedding_falls_back_to_hash(self) -> None:
        """无 embedding 时降级到 task_signature 精确匹配。"""
        store = InMemoryExperienceStore()
        r = _make_record(goal="unique task", lessons="L")
        _run_async(store.store(r))

        sig = compute_task_signature("unique task")
        results = _run_async(store.retrieve_similar(sig, task_embedding=None, top_k=3))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].experience_id, r.experience_id)

    def test_retrieve_similar_embedding_filters_out_no_embedding_records(self) -> None:
        """有 embedding 检索时跳过无 embedding 的 record。"""
        store = InMemoryExperienceStore()
        r_with = _make_record(goal="with emb", lessons="L1", embedding=[1.0, 0.0])
        r_without = _make_record(goal="without emb", lessons="L2", embedding=None)
        _run_async(store.store(r_with))
        _run_async(store.store(r_without))

        # 用不同 signature，确保靠 embedding 而非 signature 匹配
        sig = compute_task_signature("different signature")
        results = _run_async(
            store.retrieve_similar(sig, task_embedding=[1.0, 0.0], top_k=5)
        )
        # 只应返回有 embedding 的 r_with
        ids = [r.experience_id for r in results]
        self.assertIn(r_with.experience_id, ids)
        self.assertNotIn(r_without.experience_id, ids)


# ---------------------------------------------------------------------------
# PostgresExperienceStore (mock psycopg)
# ---------------------------------------------------------------------------


class TestPostgresExperienceStore(unittest.TestCase):
    """用 mock psycopg 验证 SQL 执行和 schema 创建。"""

    def _make_mock_conn(self) -> MagicMock:
        """创建 mock connection + cursor。"""
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []
        return conn, cursor

    def test_ensure_schema_creates_table_and_indexes(self) -> None:
        """__init__ 应执行 CREATE TABLE + 2 个 CREATE INDEX。"""
        conn, cursor = self._make_mock_conn()
        with patch("psycopg.connect", return_value=conn):
            PostgresExperienceStore("postgresql://mock")
        # 验证执行了 schema 创建 SQL
        executed_sql = [call.args[0] for call in cursor.execute.call_args_list]
        # 至少有 CREATE TABLE experiences
        create_table_sql = [s for s in executed_sql if "CREATE TABLE" in s and "experiences" in s]
        self.assertGreater(len(create_table_sql), 0)
        # 至少有 2 个 CREATE INDEX（tenant + signature）
        create_index_sql = [s for s in executed_sql if "CREATE INDEX" in s]
        self.assertGreaterEqual(len(create_index_sql), 2)

    def test_store_executes_insert_with_all_fields(self) -> None:
        """store() 应执行 INSERT SQL，包含 embedding 字段。"""
        conn, cursor = self._make_mock_conn()
        with patch("psycopg.connect", return_value=conn):
            store = PostgresExperienceStore("postgresql://mock")

        record = _make_record(goal="test", lessons="L", embedding=[1.0, 0.0])
        _run_async(store.store(record))

        # 验证最后一次 execute 是 INSERT
        last_call = cursor.execute.call_args_list[-1]
        sql = last_call.args[0]
        self.assertIn("INSERT INTO experiences", sql)
        # 验证参数有 10 个字段
        params = last_call.args[1]
        self.assertEqual(len(params), 10)
        self.assertEqual(params[0], record.experience_id)
        self.assertEqual(params[3], record.goal)
        self.assertEqual(params[6], record.outcome)

    def test_store_with_none_embedding_passes_null(self) -> None:
        """embedding 为 None 时传 NULL 给 Postgres。"""
        conn, cursor = self._make_mock_conn()
        with patch("psycopg.connect", return_value=conn):
            store = PostgresExperienceStore("postgresql://mock")

        record = _make_record(goal="test", lessons="L", embedding=None)
        _run_async(store.store(record))

        last_call = cursor.execute.call_args_list[-1]
        params = last_call.args[1]
        # embedding 是第 9 个参数（index 8）
        self.assertIsNone(params[8])

    def test_retrieve_similar_with_embedding_fetches_all_and_scores(self) -> None:
        """有 embedding 时应取所有 records 算 cosine。"""
        conn, cursor = self._make_mock_conn()
        # mock 返回 2 条 record（with embedding）
        cursor.fetchall.return_value = [
            {
                "experience_id": "e1",
                "tenant_id": "t1",
                "task_signature": "sig1",
                "goal": "task A",
                "plan_json": '{"goal": "task A", "steps": [{"id": "s1", "description": "do", "depends_on": []}]}',
                "tool_calls_json": "[]",
                "outcome": "success",
                "lessons": "L1",
                "embedding": "[1.0, 0.0]",
                "created_at": 1000.0,
            },
            {
                "experience_id": "e2",
                "tenant_id": "t1",
                "task_signature": "sig2",
                "goal": "task B",
                "plan_json": '{"goal": "task B", "steps": [{"id": "s1", "description": "do", "depends_on": []}]}',
                "tool_calls_json": "[]",
                "outcome": "success",
                "lessons": "L2",
                "embedding": "[0.0, 1.0]",
                "created_at": 2000.0,
            },
        ]
        with patch("psycopg.connect", return_value=conn):
            store = PostgresExperienceStore("postgresql://mock")

        # query embedding [1, 0] → e1 cosine=1.0 应排第一
        results = _run_async(
            store.retrieve_similar(
                "any_sig",
                task_embedding=[1.0, 0.0],
                top_k=2,
            )
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].experience_id, "e1")

    def test_retrieve_similar_without_embedding_uses_signature_filter(self) -> None:
        """无 embedding 时用 signature 精确匹配。"""
        conn, cursor = self._make_mock_conn()
        with patch("psycopg.connect", return_value=conn):
            store = PostgresExperienceStore("postgresql://mock")

        _run_async(store.retrieve_similar("my_sig", task_embedding=None, top_k=5))

        # 验证 SQL 用了 task_signature = %s
        last_call = cursor.execute.call_args_list[-1]
        sql = last_call.args[0]
        self.assertIn("task_signature = %s", sql)

    def test_get_returns_none_when_not_found(self) -> None:
        """get() 找不到时返回 None。"""
        conn, cursor = self._make_mock_conn()
        cursor.fetchone.return_value = None
        with patch("psycopg.connect", return_value=conn):
            store = PostgresExperienceStore("postgresql://mock")

        result = _run_async(store.get("nonexistent"))
        self.assertIsNone(result)

    def test_delete_executes_delete_sql(self) -> None:
        """delete() 执行 DELETE SQL。"""
        conn, cursor = self._make_mock_conn()
        cursor.rowcount = 1
        with patch("psycopg.connect", return_value=conn):
            store = PostgresExperienceStore("postgresql://mock")

        ok = _run_async(store.delete("e1"))
        self.assertTrue(ok)
        last_call = cursor.execute.call_args_list[-1]
        sql = last_call.args[0]
        self.assertIn("DELETE FROM experiences", sql)


# ---------------------------------------------------------------------------
# compute_task_embedding
# ---------------------------------------------------------------------------


class TestComputeTaskEmbedding(unittest.TestCase):
    def test_returns_none_when_service_unavailable(self) -> None:
        """EmbeddingService 不可用时返回 None。"""
        with patch("packages.embedding.service.get_embedding_service", return_value=None):
            result = _run_async(compute_task_embedding("test goal"))
            self.assertIsNone(result)

    def test_returns_embedding_when_service_available(self) -> None:
        """service 可用时返回 embedding list。"""
        async def _mock_embed_one(*args, **kwargs):
            return [0.1, 0.2, 0.3]

        mock_service = MagicMock()
        mock_service.embed_one = _mock_embed_one
        with patch("packages.embedding.service.get_embedding_service", return_value=mock_service):
            with patch("packages.agent.experience_store.get_settings") as mock_settings:
                mock_settings.return_value.embedding_model = "test-model"
                result = _run_async(compute_task_embedding("test goal"))
        # mock 返回固定 list
        self.assertEqual(result, [0.1, 0.2, 0.3])

    def test_returns_none_on_exception(self) -> None:
        """异常时返回 None，不抛出。"""
        with patch(
            "packages.embedding.service.get_embedding_service",
            side_effect=RuntimeError("service down"),
        ):
            result = _run_async(compute_task_embedding("test goal"))
            self.assertIsNone(result)


# ---------------------------------------------------------------------------
# backend 自动选择
# ---------------------------------------------------------------------------


class TestBackendSelection(unittest.TestCase):
    def setUp(self) -> None:
        reset_experience_store_for_tests()

    def test_no_database_url_uses_memory(self) -> None:
        """无 DATABASE_URL 时选 InMemoryExperienceStore。"""
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("DATABASE_URL", None)
            store = get_experience_store()
            self.assertIsInstance(store, InMemoryExperienceStore)

    def test_database_url_uses_postgres(self) -> None:
        """有 DATABASE_URL 时选 PostgresExperienceStore。"""
        with patch.dict("os.environ", {"DATABASE_URL": "postgresql://mock"}):
            with patch("psycopg.connect") as mock_connect:
                mock_conn = MagicMock()
                mock_cursor = MagicMock()
                mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
                mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
                mock_connect.return_value = mock_conn
                store = get_experience_store()
            self.assertIsInstance(store, PostgresExperienceStore)

    def test_database_url_unreachable_falls_back_to_memory(self) -> None:
        """Postgres 不可达时回退到 InMemoryExperienceStore。"""
        with patch.dict("os.environ", {"DATABASE_URL": "postgresql://invalid"}):
            with patch("psycopg.connect", side_effect=RuntimeError("connection refused")):
                store = get_experience_store()
            self.assertIsInstance(store, InMemoryExperienceStore)


# ---------------------------------------------------------------------------
# from_row 反序列化
# ---------------------------------------------------------------------------


class TestFromRow(unittest.TestCase):
    def test_from_row_parses_json_fields(self) -> None:
        """from_row 应正确反序列化 plan_json / tool_calls_json / embedding。"""
        row = {
            "experience_id": "e1",
            "tenant_id": "t1",
            "task_signature": "sig",
            "goal": "test",
            "plan_json": '{"goal": "test", "steps": [{"id": "s1", "description": "do", "depends_on": []}]}',
            "tool_calls_json": '[{"tool": "x"}]',
            "outcome": "success",
            "lessons": "L",
            "embedding": "[0.1, 0.2]",
            "created_at": 1000.0,
        }
        record = ExperienceRecord.from_row(row)
        self.assertEqual(record.experience_id, "e1")
        self.assertEqual(record.goal, "test")
        self.assertEqual(len(record.tool_calls), 1)
        self.assertEqual(record.embedding, [0.1, 0.2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
