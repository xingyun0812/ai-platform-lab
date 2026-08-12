# Phase W — Self-Refine 实施计划

## 概述

实现 Self-Refine（Madaan et al., 2023）推理模式：Agent 首轮输出 → 自我反馈 → 自我修正 → 迭代收敛。遵循项目已有的高级推理模式惯例（ToT/Debate/Research）。

## 任务拆解

### W1 — Self-Refine 核心引擎

**目标**：实现 `packages/agent/self_refine/` 子包

**文件**：
- `packages/agent/self_refine/__init__.py` — `run_self_refine(prompt, context=None, config=None, model=None, ...)` 公开 API，参考 `run_debate()` 的 `context` 参数模式
- `packages/agent/self_refine/config.py` — `SelfRefineConfig` dataclass
- `packages/agent/self_refine/orchestrator.py` — `generate()` → `feedback()` → `refine()` → `convergence_check()` 主循环（遵循 Debate 用 `__init__.py` 作为入口、ToT 用 `searcher.py` 命名语义），而非泛泛 `engine.py`
- `packages/agent/self_refine/models.py` — `SelfRefineResult`、`FeedbackRound`、`TraceItem` dataclass

**SelfRefineConfig**（参考 `DebateConfig` 风格）：

```python
@dataclass
class SelfRefineConfig:
    enabled: bool = True
    max_iterations: int = 5  # 上限 10
    generator_model: str | None = None  # 可分离
    feedback_model: str | None = None   # 可分离，None 时复用 generator_model
    convergence_strategy: str = "hybrid"  # "llm_judged" | "similarity" | "hybrid"
    convergence_threshold: float = 0.85  # similarity 模式阈值
    max_total_llm_calls: int = 15  # 硬上限 30
    feedback_dimensions: tuple[str, ...] = (
        "correctness", "clarity", "completeness", "consistency", "actionability",
    )
    temperature: float = 0.3
    timeout_seconds: float = 120.0

    def to_dict(self) -> dict[str, Any]: ...
```

**收敛策略详情**：
1. `llm_judged`：LLM 接收「当前输出 + 上一轮反馈」判断是否还有新改进点，若无则收敛
2. `similarity`：当前轮与上一轮输出计算语义 embedding 相似度，> threshold 即收敛
3. `hybrid`（sequential AND）：`if similarity >= threshold → converged (skip LLM judge); else → ask LLM judge; if LLM says no improvement → converged; else → continue`

**错误恢复**：
- `feedback()` 失败 → 重试 1 次 → 降级返回空反馈 → 不断送整次请求
- `refine()` 失败 → 重试 1 次 → 保留上一轮输出继续
- 任何阶段超过 `timeout_seconds` → 截断返回当前最佳结果

**返回结果**：
```python
@dataclass
class FeedbackRound:
    iteration: int
    feedback: str
    feedback_dimension: str | None  # structured dimension or None for free-text
    feedback_error: str | None

@dataclass
class SelfRefineResult:
    prompt: str
    final_output: str
    config: SelfRefineConfig
    iterations_completed: int
    converged: bool
    convergence_reason: str  # "llm_judged" | "similarity" | "max_iterations" | "max_calls"
    trace: list[FeedbackRound]
    execution_time_ms: float
    total_llm_calls: int
    error: str | None
    success: bool

    def to_dict(self) -> dict[str, Any]: ...
```

### W2 — Pydantic Schemas

**目标**：在 `packages/contracts/agent_schemas.py` 添加 SelfRefineConfig/Result 的 Pydantic 版本

参考 `TotConfig`/`DebateConfig` 模式：

```python
class SelfRefineConfig(BaseModel):
    enabled: bool = True
    max_iterations: int = Field(default=5, ge=1, le=10)
    generator_model: str | None = None
    feedback_model: str | None = None
    convergence_strategy: str = Field(default="hybrid", pattern="^(llm_judged|similarity|hybrid)$")
    convergence_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    max_total_llm_calls: int = Field(default=15, ge=1, le=30)
    feedback_dimensions: list[str] | None = None
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    timeout_seconds: float = Field(default=120.0, ge=1.0)

class FeedbackRoundSchema(BaseModel): ...
class SelfRefineResult(BaseModel): ...
```

同时在 `AgentRunRequest` 添加 `self_refine_config: SelfRefineConfig | None = None` 字段。

### W3 — REST API 路由

**目标**：在 `apps/gateway/agent/routes.py` 添加 `POST /v1/agent/self-refine`

遵循 `agent_tot()` 模式：
- 租户校验、配额校验、API Key 校验
- 从 `body.self_refine_config` 解析配置（参考 `body.tot_config`）
- **支持 `context` 参数**（参考 `run_debate()` 的 `context: str | None` 模式）
- **与 Gateway 已有 trace 集成**：使用 `component_span` 包裹每次 iteration，trace_id 沿袭网关请求链路，tool_trace 记录每次 LLM 调用
- 调用 `run_self_refine(prompt, context=context, config=cfg, model=body.model, ...)`
- 返回 JSONResponse 含迭代轨迹

不需要独立 routes 文件（与 ToT/Debate/Research 一致）。

### W4 — 命名避免与 `self_evolve` 冲突

自进化 Agent（`packages/agent/self_evolve.py`）是跨 session 经验积累 + 策略补丁，Self-Refine 是单次请求内的迭代修正。命名已足够区分，但代码注释需明确说明边界。

### W5 — 单元测试

**测试文件**：`tests/test_self_refine.py`

至少 15 个用例：
| # | 场景 | 验证点 |
|---|------|--------|
| 1 | 正常迭代到收敛 (llm_judged) | iterations_completed < max_iterations, converged=True |
| 2 | 正常迭代到收敛 (similarity) | convergence_reason="similarity" |
| 3 | 正常迭代到收敛 (hybrid) | convergence_reason 正确 |
| 4 | 达到 max_iterations 截断 | iterations_completed == max_iterations, converged=False |
| 5 | max_total_llm_calls 耗尽截断 | total_llm_calls == max_total_llm_calls |
| 6 | feedback() 失败重试 | 重试 1 次后降级，请求不中断 |
| 7 | refine() 失败重试 | 保留上一轮输出继续 |
| 8 | generator_model != feedback_model | 分别传递 |
| 9 | 结构化维度反馈 | feedback_dimension 不为 None |
| 10 | 自由文本反馈 | feedback_dimension 为 None |
| 11 | 空反馈（已最优） | 直接收敛 |
| 12 | timeout 截断 | execution_time_ms < timeout_seconds + buffer |
| 13 | 配置默认值 | 不传 config 时用默认值正常跑 |
| 14 | 迭代轨迹完整 | trace 长度 == iterations_completed |
| 15 | 空反馈（已最优） | 直接收敛 |
| 16 | hybrid 优化路径：similarity >= threshold 时跳过 LLM judge | total_llm_calls 节省 1 次 |
| 17 | 上下文增长不导致超时 | long prompt 也能在 timeout 内完成 |

### W6 — 质量门禁

**文件**：`eval/self_refine_quality_gate.py`

参考 `eval/tot_quality_gate.py`：
- 基准：30+ GSM8K 风格数学题（复用已有 benchmark 并扩展），使统计误差降到 ~+/-9%
- 对比策略：single-shot vs 1-refine vs 3-refine
- 门禁条件（二选一，实现时确认）：
  - 严格：self-refine(3) accuracy >= single-shot accuracy + 5%
  - 宽松（推荐，避免 flake）：self-refine(3) accuracy >= single-shot accuracy（无回归）
- 记录每次迭代的中间结果供分析

**注意**：10 题样本的 margin of error ~+/-15%，5% 阈值不可靠。建议用 30+ 题或 no-regression 门禁。

### W7 — 设计文档

**文件**：
- `docs/02-phase-w-self-refine.md` — 设计文档
- `docs/adr/0008-self-refine.md` — ADR

**已知限制（需在文档中说明）**：
- **上下文增长**：每轮 refinement 将 `当前输出 + 历史反馈` 追加到 prompt 中，5 轮后 context 会线性膨胀。当前接受此设计，极端情况下可能触及 token 限制。未来优化：只传增量 diff。
- **Similarity 策略依赖 Embedding 服务**：需要 Phase P 的 Embedding 服务可用，否则回退到 `llm_judged`。

### W8 — 验收烟雾测试

在 `eval/acceptance_smoke.py` 添加 Self-Refine 检查点。

### W9 — 文档同步

- `docs/00-roadmap.md` — 添加 Phase W 条目
- `feature_list.json` — 添加 F39
- `README.md` — 高级推理章节更新

## 文件清单

### 新增
| 文件 | 说明 |
|------|------|
| packages/agent/self_refine/__init__.py | 公开 API |
| packages/agent/self_refine/config.py | SelfRefineConfig |
| packages/agent/self_refine/engine.py | 核心循环 |
| packages/agent/self_refine/models.py | 数据结构 |
| tests/test_self_refine.py | 单元测试 |
| eval/self_refine_quality_gate.py | 质量门禁 |
| docs/02-phase-w-self-refine.md | 设计文档 |
| docs/adr/0008-self-refine.md | ADR |

### 修改
| 文件 | 变更 |
|------|------|
| packages/contracts/agent_schemas.py | +SelfRefineConfig, +SelfRefineResult, +AgentRunRequest.self_refine_config |
| apps/gateway/agent/routes.py | +agent_self_refine() 路由（含 context 参数 + trace 集成） |
| apps/gateway/settings.py | +Self-Refine 相关配置项 |
| .env.example | +Self-Refine 默认配置项 |
| apps/gateway/router_registry.py | 不变（Self-Refine 路由在 routes.py 内，不需独立 router；与 ToT/Debate 一致） |
| docs/00-roadmap.md | +Phase W |
| feature_list.json | +F39 |
| eval/acceptance_smoke.py | +Self-Refine 检查 |
| README.md | 高级推理章节更新 |

## 依赖关系

```
W1 (核心引擎) ──→ W2 (Schemas) ──→ W3 (API 路由)
  │                                  │
  ├── W5 (单元测试)                  │
  ├── W6 (质量门禁) ◄───────────────┘
  └── W7 (设计文档)
```

实施顺序：W1 → W2 → W3 → W4 → W5 → W6 → W7 → W8 → W9

## 预计工期

- W1 核心引擎：3d
- W2 Schemas：0.5d
- W3 API 路由：0.5d
- W4 命名确认：0d（无额外工时）
- W5 单元测试：2d
- W6 质量门禁：1d
- W7 设计文档：0.5d
- W8-W9 集成验收：0.5d

**总计：~1.5w**
