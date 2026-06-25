#!/usr/bin/env python3
"""tests/test_capability_profile.py — Phase R R3 模型能力画像单元测试

运行：
    python3 tests/test_capability_profile.py

覆盖：
  - ContextMgmtBenchmark: mock 模式 / scoring 正确 / needle 匹配逻辑
  - LongMemoryBenchmark: mock 模式 / keyword 匹配
  - ToolUseBenchmark: mock 模式 / schema 校验
  - PlanningBenchmark: mock 模式 / DAG 校验 / 步骤数范围
  - CapabilityScores: to_dict / overall / strength/weakness dimension
  - CapabilityProfileStore: store / get_latest / list_all / compare
  - run_capability_profile: 串联流程 / mock 模式
  - select_model_with_capability: 无 profile 时降级 / 有 profile 选最强
  - generate_capability_report route
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _load_module(rel_path: str, module_name: str):
    """通过 importlib 加载模块，避免触发 packages.agent.__init__ pydantic 链。"""
    full_path = REPO_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(module_name, full_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _run_async(coro):
    """Python 3.9 兼容的 async 测试运行。"""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 加载被测模块
# ---------------------------------------------------------------------------

benchmark_mod = _load_module(
    "eval/harness_capability_benchmark.py",
    "eval.harness_capability_benchmark",
)
CapabilityScores = benchmark_mod.CapabilityScores
ContextMgmtBenchmark = benchmark_mod.ContextMgmtBenchmark
LongMemoryBenchmark = benchmark_mod.LongMemoryBenchmark
ToolUseBenchmark = benchmark_mod.ToolUseBenchmark
PlanningBenchmark = benchmark_mod.PlanningBenchmark
run_all_benchmarks = benchmark_mod.run_all_benchmarks

profile_mod = _load_module(
    "packages/agent/capability_profile.py",
    "packages.agent.capability_profile",
)
CapabilityScoresRef = profile_mod.CapabilityScoresRef
ModelCapabilityProfile = profile_mod.ModelCapabilityProfile
CapabilityProfileStore = profile_mod.CapabilityProfileStore
get_capability_profile_store = profile_mod.get_capability_profile_store
reset_capability_profile_store_for_tests = profile_mod.reset_capability_profile_store_for_tests
new_profile_id = profile_mod.new_profile_id
dim_to_field = profile_mod.dim_to_field
run_capability_profile = profile_mod.run_capability_profile


# ---------------------------------------------------------------------------
# Helper：创建 mock profile
# ---------------------------------------------------------------------------


def _make_scores(c=0.8, m=0.6, t=0.9, p=0.7) -> CapabilityScoresRef:
    return CapabilityScoresRef(context_mgmt=c, long_memory=m, tool_use=t, planning=p)


def _make_profile(model_id: str, **kwargs) -> ModelCapabilityProfile:
    scores = _make_scores(**kwargs)
    return ModelCapabilityProfile(
        profile_id=new_profile_id(),
        model_id=model_id,
        scores=scores,
        timestamp=time.time(),
    )


# ===========================================================================
# 1. TestContextMgmtBenchmark
# ===========================================================================


def test_context_mgmt_mock_returns_float():
    """mock 模式跑完应返回 0-1 之间的浮点数。"""
    bench = ContextMgmtBenchmark()
    score = _run_async(bench.run("chat-fast", mock=True))
    assert isinstance(score, float), f"expected float, got {type(score)}"
    assert 0.0 <= score <= 1.0, f"score out of range: {score}"
    print(f"PASS test_context_mgmt_mock_returns_float (score={score:.3f})")


def test_context_mgmt_needle_match_correct():
    """needle 出现在回答中时 scoring 应为 1.0。"""
    bench = ContextMgmtBenchmark()
    case = {"expected": "ALPHA_7749", "needle": "PROJECT_CODE_ALPHA_7749"}
    score = bench._score_response(case, "根据文档内容，答案是：PROJECT_CODE_ALPHA_7749")
    assert score == 1.0, f"expected 1.0, got {score}"
    print("PASS test_context_mgmt_needle_match_correct")


def test_context_mgmt_needle_match_miss():
    """needle 不在回答中时 scoring 应为 0.0。"""
    bench = ContextMgmtBenchmark()
    case = {"expected": "ALPHA_7749", "needle": "PROJECT_CODE_ALPHA_7749"}
    score = bench._score_response(case, "文档中没有找到相关信息。")
    assert score == 0.0, f"expected 0.0, got {score}"
    print("PASS test_context_mgmt_needle_match_miss")


def test_context_mgmt_case_insensitive():
    """大小写不敏感匹配。"""
    bench = ContextMgmtBenchmark()
    case = {"expected": "ALPHA_7749", "needle": "PROJECT_CODE_ALPHA_7749"}
    score = bench._score_response(case, "答案是 alpha_7749 号项目")
    assert score == 1.0, f"expected 1.0, got {score}"
    print("PASS test_context_mgmt_case_insensitive")


def test_context_mgmt_builds_10_cases():
    """_build_cases 应返回 10 个 case。"""
    bench = ContextMgmtBenchmark()
    cases = bench._build_cases()
    assert len(cases) == 10, f"expected 10 cases, got {len(cases)}"
    for c in cases:
        assert "context" in c
        assert "needle" in c
        assert "question" in c
        assert "expected" in c
    print("PASS test_context_mgmt_builds_10_cases")


# ===========================================================================
# 2. TestLongMemoryBenchmark
# ===========================================================================


def test_long_memory_mock_returns_float():
    """mock 模式跑完应返回 0-1 之间的浮点数。"""
    bench = LongMemoryBenchmark()
    score = _run_async(bench.run("chat-fast", mock=True))
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0
    print(f"PASS test_long_memory_mock_returns_float (score={score:.3f})")


def test_long_memory_keyword_match_all():
    """所有 keyword 都匹配时应得满分 1.0。"""
    bench = LongMemoryBenchmark()
    score = bench._score_response("用户打羽毛球每周两次", ["羽毛球", "每周"])
    assert score == 1.0, f"expected 1.0, got {score}"
    print("PASS test_long_memory_keyword_match_all")


def test_long_memory_keyword_match_partial():
    """部分 keyword 匹配时应按比例得分。"""
    bench = LongMemoryBenchmark()
    score = bench._score_response("用户打羽毛球每周两次", ["羽毛球", "篮球"])
    assert score == 0.5, f"expected 0.5, got {score}"
    print("PASS test_long_memory_keyword_match_partial")


def test_long_memory_keyword_match_none():
    """无 keyword 匹配时应为 0.0。"""
    bench = LongMemoryBenchmark()
    score = bench._score_response("没有任何相关信息", ["羽毛球", "篮球"])
    assert score == 0.0, f"expected 0.0, got {score}"
    print("PASS test_long_memory_keyword_match_none")


# ===========================================================================
# 3. TestToolUseBenchmark
# ===========================================================================


def test_tool_use_mock_returns_float():
    """mock 模式跑完应返回 0-1 之间的浮点数。"""
    bench = ToolUseBenchmark()
    score = _run_async(bench.run("chat-fast", mock=True))
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0
    print(f"PASS test_tool_use_mock_returns_float (score={score:.3f})")


def test_tool_use_correct_tool_and_params():
    """工具名 + 参数键 + 参数值都正确时应得高分。"""
    bench = ToolUseBenchmark()
    response = json.dumps(
        {
            "tool_name": "web_search",
            "parameters": {"query": "python 3.12 release notes"},
        }
    )
    score = bench._score_response(
        response,
        "web_search",
        ["query"],
        {"query": "python 3.12"},
    )
    assert score >= 0.9, f"expected >=0.9, got {score}"
    print(f"PASS test_tool_use_correct_tool_and_params (score={score:.3f})")


def test_tool_use_wrong_tool():
    """工具名错误时，工具名分数为 0，但如有参数匹配可能有小额得分。"""
    bench = ToolUseBenchmark()
    response = json.dumps(
        {
            "tool_name": "wrong_tool",
            "parameters": {"query": "test"},
        }
    )
    score = bench._score_response(
        response,
        "web_search",
        ["query"],
        {"query": "test"},
    )
    # 工具名错误：tool_name score=0（wrong_tool 不包含 web_search）
    # 参数键存在：0.3，参数值匹配：0.3 → 最多 0.6，工具名 0
    assert score < 0.7, f"expected <0.7, got {score}"
    # 但不会得满分
    assert score < 1.0, f"should not be 1.0"
    print(f"PASS test_tool_use_wrong_tool (score={score:.3f})")


def test_tool_use_invalid_json():
    """无效 JSON 响应应得 0.0。"""
    bench = ToolUseBenchmark()
    score = bench._score_response(
        "这不是 JSON",
        "web_search",
        ["query"],
        {"query": "test"},
    )
    assert score == 0.0, f"expected 0.0, got {score}"
    print("PASS test_tool_use_invalid_json")


# ===========================================================================
# 4. TestPlanningBenchmark
# ===========================================================================


def test_planning_mock_returns_float():
    """mock 模式跑完应返回 0-1 之间的浮点数。"""
    bench = PlanningBenchmark()
    score = _run_async(bench.run("chat-fast", mock=True))
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0
    print(f"PASS test_planning_mock_returns_float (score={score:.3f})")


def test_planning_valid_dag_no_cycle():
    """有效 DAG（无环）应通过校验。"""
    bench = PlanningBenchmark()
    steps = [
        {"id": "s1", "description": "步骤1", "depends_on": []},
        {"id": "s2", "description": "步骤2", "depends_on": ["s1"]},
        {"id": "s3", "description": "步骤3", "depends_on": ["s1", "s2"]},
    ]
    assert bench._is_valid_dag(steps) is True
    print("PASS test_planning_valid_dag_no_cycle")


def test_planning_invalid_dag_with_cycle():
    """有环的依赖图应检测为非 DAG。"""
    bench = PlanningBenchmark()
    steps = [
        {"id": "s1", "description": "步骤1", "depends_on": ["s3"]},
        {"id": "s2", "description": "步骤2", "depends_on": ["s1"]},
        {"id": "s3", "description": "步骤3", "depends_on": ["s2"]},
    ]
    assert bench._is_valid_dag(steps) is False
    print("PASS test_planning_invalid_dag_with_cycle")


def test_planning_step_count_range():
    """步骤数在合理范围内时得分应更高。"""
    bench = PlanningBenchmark()
    plan_ok = json.dumps(
        {
            "goal": "test",
            "steps": [
                {
                    "id": f"s{i}",
                    "description": f"步骤{i}",
                    "depends_on": [f"s{i - 1}"] if i > 1 else [],
                }
                for i in range(1, 5)
            ],
        }
    )
    plan_too_few = json.dumps(
        {
            "goal": "test",
            "steps": [{"id": "s1", "description": "仅一步", "depends_on": []}],
        }
    )
    score_ok = bench._score_response(plan_ok, min_steps=3, max_steps=8, require_deps=True)
    score_too_few = bench._score_response(plan_too_few, min_steps=3, max_steps=8, require_deps=True)
    assert score_ok > score_too_few, f"ok={score_ok} should > too_few={score_too_few}"
    print(f"PASS test_planning_step_count_range (ok={score_ok:.2f}, too_few={score_too_few:.2f})")


# ===========================================================================
# 5. TestCapabilityScores
# ===========================================================================


def test_capability_scores_to_dict():
    """to_dict 应返回包含 4 维度键的字典。"""
    scores = CapabilityScores(context_mgmt=0.8, long_memory=0.6, tool_use=0.9, planning=0.7)
    d = scores.to_dict()
    assert set(d.keys()) == {"context_mgmt", "long_memory", "tool_use", "planning"}
    assert d["context_mgmt"] == 0.8
    print("PASS test_capability_scores_to_dict")


def test_capability_scores_overall():
    """overall() 应为 4 维度均值。"""
    scores = CapabilityScores(context_mgmt=1.0, long_memory=0.5, tool_use=0.8, planning=0.7)
    expected = (1.0 + 0.5 + 0.8 + 0.7) / 4
    assert abs(scores.overall() - expected) < 1e-9
    print("PASS test_capability_scores_overall")


def test_capability_scores_ref_strength_weakness():
    """CapabilityScoresRef strength/weakness 方向正确。"""
    scores = CapabilityScoresRef(context_mgmt=0.9, long_memory=0.3, tool_use=0.7, planning=0.5)
    profile = ModelCapabilityProfile(
        profile_id="test",
        model_id="test-model",
        scores=scores,
        timestamp=time.time(),
    )
    assert profile.strength_dimension() == "context"
    assert profile.weakness_dimension() == "memory"
    print("PASS test_capability_scores_ref_strength_weakness")


# ===========================================================================
# 6. TestCapabilityProfileStore
# ===========================================================================


def test_store_and_get_latest():
    """store + get_latest 应正常工作。"""
    reset_capability_profile_store_for_tests()
    store = CapabilityProfileStore()
    p = _make_profile("gpt-4o")
    store.store(p)
    latest = store.get_latest("gpt-4o")
    assert latest is not None
    assert latest.model_id == "gpt-4o"
    assert latest.profile_id == p.profile_id
    print("PASS test_store_and_get_latest")


def test_store_get_latest_returns_none_for_unknown():
    """未知模型 get_latest 应返回 None。"""
    store = CapabilityProfileStore()
    assert store.get_latest("nonexistent-model") is None
    print("PASS test_store_get_latest_returns_none_for_unknown")


def test_store_list_all():
    """list_all 应返回所有模型的最新 profile。"""
    store = CapabilityProfileStore()
    p1 = _make_profile("model-a")
    p2 = _make_profile("model-b")
    store.store(p1)
    store.store(p2)
    all_profiles = store.list_all()
    model_ids = {p.model_id for p in all_profiles}
    assert "model-a" in model_ids
    assert "model-b" in model_ids
    print("PASS test_store_list_all")


def test_store_multiple_profiles_keeps_latest():
    """同一模型多次入库，get_latest 应返回最新的。"""
    store = CapabilityProfileStore()
    p1 = ModelCapabilityProfile(
        profile_id="old",
        model_id="my-model",
        scores=_make_scores(c=0.5),
        timestamp=time.time() - 100,
    )
    p2 = ModelCapabilityProfile(
        profile_id="new",
        model_id="my-model",
        scores=_make_scores(c=0.9),
        timestamp=time.time(),
    )
    store.store(p1)
    store.store(p2)
    latest = store.get_latest("my-model")
    assert latest.profile_id == "new", f"expected 'new', got {latest.profile_id}"
    print("PASS test_store_multiple_profiles_keeps_latest")


def test_store_compare_two_models():
    """compare 两个有 profile 的模型，应返回对比 dict。"""
    store = CapabilityProfileStore()
    store.store(_make_profile("alpha", c=0.9, m=0.7, t=0.8, p=0.6))
    store.store(_make_profile("beta", c=0.5, m=0.8, t=0.6, p=0.9))
    result = store.compare("alpha", "beta")
    assert result["found_1"] is True
    assert result["found_2"] is True
    assert "comparison" in result
    comp = result["comparison"]
    # alpha 在 context_mgmt 上更强
    assert comp["context_mgmt"]["winner"] == "alpha"
    # beta 在 planning 上更强
    assert comp["planning"]["winner"] == "beta"
    print("PASS test_store_compare_two_models")


def test_store_compare_missing_model():
    """compare 时一个模型无 profile，返回 insufficient_data。"""
    store = CapabilityProfileStore()
    store.store(_make_profile("only-model"))
    result = store.compare("only-model", "nonexistent")
    assert result["comparison"] == "insufficient_data"
    print("PASS test_store_compare_missing_model")


def test_global_singleton():
    """全局单例应每次返回同一实例。"""
    reset_capability_profile_store_for_tests()
    s1 = get_capability_profile_store()
    s2 = get_capability_profile_store()
    assert s1 is s2
    print("PASS test_global_singleton")


# ===========================================================================
# 7. TestRunCapabilityProfile
# ===========================================================================


def test_run_capability_profile_mock():
    """mock 模式串联流程：profile 应入库且字段完整。"""
    reset_capability_profile_store_for_tests()
    profile = _run_async(run_capability_profile("test-model", mock=True))
    assert profile.model_id == "test-model"
    assert 0.0 <= profile.scores.overall() <= 1.0
    assert profile.profile_id.startswith("prof_")
    # 应已入库
    store = get_capability_profile_store()
    latest = store.get_latest("test-model")
    assert latest is not None
    assert latest.profile_id == profile.profile_id
    print(f"PASS test_run_capability_profile_mock (overall={profile.scores.overall():.3f})")


def test_run_all_benchmarks_mock():
    """run_all_benchmarks mock 模式返回 CapabilityScores。"""
    scores = _run_async(run_all_benchmarks("chat-fast", mock=True))
    assert hasattr(scores, "context_mgmt")
    assert hasattr(scores, "long_memory")
    assert hasattr(scores, "tool_use")
    assert hasattr(scores, "planning")
    assert 0.0 <= scores.overall() <= 1.0
    print(f"PASS test_run_all_benchmarks_mock (overall={scores.overall():.3f})")


# ===========================================================================
# 8. TestSelectModelWithCapability
# ===========================================================================


def test_select_model_no_profiles_returns_default():
    """无 profile 时应降级到默认模型（不抛异常）。"""
    reset_capability_profile_store_for_tests()
    # 动态加载 model_router 的 select_model_with_capability
    router_mod = _load_module(
        "apps/gateway/model_router.py",
        "apps.gateway.model_router",
    )
    result = router_mod.select_model_with_capability(
        prompt_type="test",
        required_dimension="tool",
    )
    assert isinstance(result, str), f"expected str, got {type(result)}"
    print(f"PASS test_select_model_no_profiles_returns_default (result={result})")


def test_select_model_picks_best_dimension():
    """有 profile 时应选出该维度得分最高的模型。"""
    reset_capability_profile_store_for_tests()

    # 手动放两个 profile 进全局 store
    store = get_capability_profile_store()
    store.store(
        ModelCapabilityProfile(
            profile_id="p1",
            model_id="chat-fast",
            scores=CapabilityScoresRef(
                context_mgmt=0.9, long_memory=0.7, tool_use=0.5, planning=0.6
            ),
            timestamp=time.time(),
        )
    )
    store.store(
        ModelCapabilityProfile(
            profile_id="p2",
            model_id="chat-smart",
            scores=CapabilityScoresRef(
                context_mgmt=0.6, long_memory=0.8, tool_use=0.95, planning=0.7
            ),
            timestamp=time.time(),
        )
    )

    # 让 router 的 select_model_with_capability 使用同一个全局 store
    router_mod = _load_module(
        "apps/gateway/model_router.py",
        "apps.gateway.model_router",
    )
    # 替换 get_capability_profile_store 引用
    import types

    old_packages = sys.modules.get("packages.agent.capability_profile")
    sys.modules["packages.agent.capability_profile"] = profile_mod  # type: ignore[assignment]
    try:
        result = router_mod.select_model_with_capability(
            prompt_type="test",
            required_dimension="tool",
        )
        # chat-smart tool_use=0.95 > chat-fast tool_use=0.5
        assert result == "chat-smart", f"expected chat-smart, got {result}"
        print(f"PASS test_select_model_picks_best_dimension (result={result})")
    finally:
        if old_packages is None:
            sys.modules.pop("packages.agent.capability_profile", None)
        else:
            sys.modules["packages.agent.capability_profile"] = old_packages


def test_select_model_unknown_dimension_returns_default():
    """未知 dimension 不应抛异常，而是返回默认模型。"""
    router_mod = _load_module(
        "apps/gateway/model_router.py",
        "apps.gateway.model_router",
    )
    result = router_mod.select_model_with_capability(
        prompt_type="test",
        required_dimension="unknown_dim_xyz",
    )
    assert isinstance(result, str)
    print(f"PASS test_select_model_unknown_dimension_returns_default (result={result})")


# ===========================================================================
# 9. TestCapabilityReportGeneration
# ===========================================================================


def _get_build_markdown_report():
    """直接从 harness_routes.py 提取 _build_markdown_report，绕过 pydantic 导入链。"""
    import importlib.util as ilu
    import types

    # 创建 stub 模块避免 pydantic 链
    for stub_name in [
        "apps",
        "apps.gateway",
        "apps.gateway.http_utils",
        "apps.gateway.tenants",
        "packages",
        "packages.auth",
        "packages.auth.rbac",
        "packages.contracts",
        "packages.contracts.errors",
        "packages.observability",
        "packages.observability.context",
        "fastapi",
        "fastapi.responses",
        "pydantic",
    ]:
        if stub_name not in sys.modules:
            sys.modules[stub_name] = types.ModuleType(stub_name)

    # Stub 必要的符号
    fastapi_mod = sys.modules["fastapi"]
    if not hasattr(fastapi_mod, "APIRouter"):

        class _FakeAPIRouter:
            def __init__(self, **kw):
                pass

            def get(self, *a, **kw):
                return lambda f: f

            def post(self, *a, **kw):
                return lambda f: f

        fastapi_mod.APIRouter = _FakeAPIRouter
        fastapi_mod.Header = lambda *a, **kw: None
        fastapi_mod.HTTPException = Exception
        fastapi_mod.Query = lambda *a, **kw: None

    fastapi_responses = sys.modules["fastapi.responses"]
    if not hasattr(fastapi_responses, "JSONResponse"):
        fastapi_responses.JSONResponse = dict

    pydantic_mod = sys.modules["pydantic"]
    if not hasattr(pydantic_mod, "BaseModel"):

        class _FakeBaseModel:
            def __init__(self, **kw):
                for k, v in kw.items():
                    setattr(self, k, v)

        class _FakeField:
            def __call__(self, *a, **kw):
                return None

            def __getattr__(self, name):
                return None

        pydantic_mod.BaseModel = _FakeBaseModel
        pydantic_mod.Field = _FakeField()

    http_utils_mod = sys.modules["apps.gateway.http_utils"]
    if not hasattr(http_utils_mod, "json_error"):
        http_utils_mod.json_error = lambda *a, **kw: None
        http_utils_mod.resolve_tenant = lambda *a, **kw: None

    tenants_mod = sys.modules["apps.gateway.tenants"]
    if not hasattr(tenants_mod, "TenantRecord"):
        tenants_mod.TenantRecord = object
        tenants_mod.load_tenants = lambda: {}

    rbac_mod = sys.modules["packages.auth.rbac"]
    if not hasattr(rbac_mod, "can_patch_tenant_limits"):
        rbac_mod.can_patch_tenant_limits = lambda *a: True

    routes_path = REPO_ROOT / "apps/gateway/harness_routes.py"
    spec = ilu.spec_from_file_location("apps.gateway.harness_routes_stub", routes_path)
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._build_markdown_report


def test_report_markdown_contains_model_name():
    """_build_markdown_report 应包含模型名。"""
    _build_markdown_report = _get_build_markdown_report()
    profiles = [
        ModelCapabilityProfile(
            profile_id="x1",
            model_id="gpt-4o",
            scores=CapabilityScoresRef(
                context_mgmt=0.9, long_memory=0.8, tool_use=0.85, planning=0.75
            ),
            timestamp=time.time(),
        ),
        ModelCapabilityProfile(
            profile_id="x2",
            model_id="chat-fast",
            scores=CapabilityScoresRef(
                context_mgmt=0.7, long_memory=0.6, tool_use=0.65, planning=0.8
            ),
            timestamp=time.time(),
        ),
    ]
    md = _build_markdown_report(profiles)
    assert "gpt-4o" in md
    assert "chat-fast" in md
    assert "context_mgmt" in md
    assert "# 模型能力对比报告" in md
    print("PASS test_report_markdown_contains_model_name")


def test_report_empty_profiles():
    """无 profile 时应生成占位报告。"""
    _build_markdown_report = _get_build_markdown_report()
    md = _build_markdown_report([])
    assert "暂无" in md
    print("PASS test_report_empty_profiles")


def test_report_task_recommendation_section():
    """报告应包含任务推荐章节。"""
    _build_markdown_report = _get_build_markdown_report()
    profiles = [
        ModelCapabilityProfile(
            profile_id="y1",
            model_id="model-x",
            scores=CapabilityScoresRef(
                context_mgmt=0.9, long_memory=0.5, tool_use=0.7, planning=0.6
            ),
            timestamp=time.time(),
        )
    ]
    md = _build_markdown_report(profiles)
    assert "任务适用推荐" in md or "推荐" in md
    print("PASS test_report_task_recommendation_section")


# ===========================================================================
# 10. TestDimToField
# ===========================================================================


def test_dim_to_field_mapping():
    """dim_to_field 应正确映射各种维度名。"""
    assert dim_to_field("context") == "context_mgmt"
    assert dim_to_field("context_mgmt") == "context_mgmt"
    assert dim_to_field("memory") == "long_memory"
    assert dim_to_field("long_memory") == "long_memory"
    assert dim_to_field("tool") == "tool_use"
    assert dim_to_field("tool_use") == "tool_use"
    assert dim_to_field("planning") == "planning"
    assert dim_to_field("unknown_xyz") is None
    print("PASS test_dim_to_field_mapping")


def test_profile_to_dict_structure():
    """ModelCapabilityProfile.to_dict() 应包含必要字段。"""
    p = _make_profile("my-model", c=0.8, m=0.6, t=0.7, p=0.9)
    d = p.to_dict()
    required_keys = {
        "profile_id",
        "model_id",
        "scores",
        "overall",
        "strength",
        "weakness",
        "timestamp",
    }
    assert required_keys.issubset(d.keys()), f"missing keys: {required_keys - d.keys()}"
    assert d["model_id"] == "my-model"
    assert isinstance(d["scores"], dict)
    assert isinstance(d["overall"], float)
    print("PASS test_profile_to_dict_structure")


# ===========================================================================
# Main
# ===========================================================================


def main() -> int:
    tests = [
        # ContextMgmtBenchmark
        test_context_mgmt_mock_returns_float,
        test_context_mgmt_needle_match_correct,
        test_context_mgmt_needle_match_miss,
        test_context_mgmt_case_insensitive,
        test_context_mgmt_builds_10_cases,
        # LongMemoryBenchmark
        test_long_memory_mock_returns_float,
        test_long_memory_keyword_match_all,
        test_long_memory_keyword_match_partial,
        test_long_memory_keyword_match_none,
        # ToolUseBenchmark
        test_tool_use_mock_returns_float,
        test_tool_use_correct_tool_and_params,
        test_tool_use_wrong_tool,
        test_tool_use_invalid_json,
        # PlanningBenchmark
        test_planning_mock_returns_float,
        test_planning_valid_dag_no_cycle,
        test_planning_invalid_dag_with_cycle,
        test_planning_step_count_range,
        # CapabilityScores
        test_capability_scores_to_dict,
        test_capability_scores_overall,
        test_capability_scores_ref_strength_weakness,
        # CapabilityProfileStore
        test_store_and_get_latest,
        test_store_get_latest_returns_none_for_unknown,
        test_store_list_all,
        test_store_multiple_profiles_keeps_latest,
        test_store_compare_two_models,
        test_store_compare_missing_model,
        test_global_singleton,
        # run_capability_profile
        test_run_capability_profile_mock,
        test_run_all_benchmarks_mock,
        # select_model_with_capability
        test_select_model_no_profiles_returns_default,
        test_select_model_picks_best_dimension,
        test_select_model_unknown_dimension_returns_default,
        # Report generation
        test_report_markdown_contains_model_name,
        test_report_empty_profiles,
        test_report_task_recommendation_section,
        # dim_to_field + to_dict
        test_dim_to_field_mapping,
        test_profile_to_dict_structure,
    ]

    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"FAIL {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            import traceback

            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1

    total = len(tests)
    passed = total - failed
    print(f"\n{'=' * 60}")
    print(f"Results: {passed}/{total} passed")
    if failed:
        print(f"FAILED: {failed} test(s)")
    else:
        print("All tests PASSED!")
    print(f"{'=' * 60}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
