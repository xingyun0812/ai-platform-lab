# Phase W -- Self-Refine (Madaan et al., 2023) Research Report

## 1. Pattern Conventions from ToT / Debate / Research

### 1.1 Package Structure

Every new inference mode lives under `packages/agent/<mode>/` with the same structure:

```
packages/agent/<mode>/
    __init__.py       # public API: async run_<mode>() + Config/Result dataclasses
    models.py         # dataclass models: Config, Result, intermediate types
    ...engine files   # core logic split across modules (generator, evaluator, etc.)
```

### 1.2 `__init__.py` Pattern

Every mode follows these exact conventions:

```python
"""packages/agent/<mode> -- One-line description."""
from __future__ import annotations

import logging
import time
from typing import Any

# Internal imports (relative)
from packages.agent.<mode>.models import Config, Result

logger = logging.getLogger("ai_platform.agent.<mode>")

__all__ = ["run_<mode>", "Config", "Result"]


async def run_<mode>(
    prompt: str,
    config: Config | None = None,
    model: str | None = None,
    **kwargs,  # tenant_id, session_id, etc. as needed
) -> Result:
    """Docstring."""
    cfg = config or Config()
    start = time.time()
    trace: list[dict[str, Any]] = []

    try:
        trace.append({"event": "<mode>_start", ...})

        # ... core logic ...

        elapsed = (time.time() - start) * 1000
        trace.append({"event": "<mode>_complete", "elapsed_ms": elapsed})
        return Result(
            ...,
            execution_time_ms=elapsed,
            trace=trace,
        )
    except Exception as exc:
        elapsed = (time.time() - start) * 1000
        logger.exception("<mode> failed: %s", exc)
        trace.append({"event": "<mode>_error", "error": str(exc)})
        return Result(
            ...,
            execution_time_ms=elapsed,
            trace=trace,
            error=str(exc),
        )
```

**Key conventions:**
1. `from __future__ import annotations` -- always first (per CLAUDE.md)
2. `logger = logging.getLogger("ai_platform.agent.<mode>")` -- namespaced logger
3. `__all__` list exports 3-5 symbols: `run_<mode>`, `Config`, `Result`, plus subcomponents
4. Config default is `config or Config()` -- caller can pass None for defaults
5. `trace: list[dict[str, Any]]` -- every mode builds a trace event list
6. start/end timing wrapped around the full flow
7. try/except returning a valid result even on error (fail-open)
8. `logger.info(...)` and `logger.exception(...)` at key milestones

### 1.3 Config dataclass Pattern

```python
@dataclass
class SomeConfig:
    enabled: bool = True
    timeout_seconds: float = 120.0
    temperature: float = 0.7
    ...

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "timeout_seconds": self.timeout_seconds,
            "temperature": self.temperature,
            ...
        }
```

Every Config has:
- `to_dict()` method
- All defaults set at the class level
- No Pydantic dependency (bare dataclass)

### 1.4 Result dataclass Pattern

```python
@dataclass
class SomeResult:
    # domain-specific fields
    execution_time_ms: float = 0.0
    trace: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            ...,
            "execution_time_ms": self.execution_time_ms,
            "trace": self.trace,
            "error": self.error,
        }
```

Every Result has:
- `execution_time_ms` (float)
- `trace` (list of dicts)
- `error` (str | None, fail-open)
- `to_dict()` method

### 1.5 Gateway Route Pattern (`routes.py`)

All specialized inference routes are **merged into `apps/gateway/agent/routes.py`** (no separate file). Pattern:

```python
@router.post("/<mode>")
async def agent_<mode>(
    body: AgentRunRequest,  # REUSE existing AgentRunRequest
    x_tenant_id: ...,
    authorization: ...,
) -> Any:
    # 1. Tenant resolution + validation
    tenants = load_tenants()
    tenant = _require_tenant(x_tenant_id, authorization, tenants)
    if isinstance(tenant, JSONResponse):
        return tenant
    if not x_tenant_id or body.tenant_id.strip() != x_tenant_id.strip():
        return json_error(400, "TENANT_MISMATCH", ...)

    # 2. Settings + rate limit + budget + API key check
    settings = get_settings()
    rate_err = check_rate_limit(tenant)
    if rate_err: return rate_err
    budget_err = check_token_budget(tenant)
    if budget_err: return budget_err
    if not (settings.llm_api_key or "").strip():
        return json_error(503, ...)

    # 3. Extract input from body (goal or last user message)
    question = (body.goal or _last_user_goal(body.messages) or "").strip()
    if not question:
        return json_error(400, "INVALID_REQUEST", ...)

    # 4. Extract config from body (body.<mode>_config)
    cfg_from_body = body.<mode>_config
    from packages.agent.<mode> import Config as ModeConfig, run_<mode>

    cfg = ModeConfig(
        max_iterations=cfg_from_body.max_iterations if cfg_from_body else <default>,
        ...,
    )

    # 5. Run + catch
    try:
        result = await run_<mode>(
            prompt=question,
            config=cfg,
            model=body.model,
        )
    except Exception as e:
        logger.exception("agent_<mode> failed tenant=%s", tenant.tenant_id)
        return json_error(503, "<MODE>_ERROR", str(e))

    # 6. Return response wrapping result in Pydantic schema
    return JSONResponse({
        "tenant_id": tenant.tenant_id,
        "session_id": body.session_id.strip(),
        "model": body.model or settings.default_model,
        "final_message": ...,
        "<mode>_result": <ModeResultSchema>(
            ...fields from result...
        ).model_dump(exclude_none=True),
    })
```

**Important:** Computer Use (Phase V) does NOT have a route in routes.py or schemas in agent_schemas.py. For Phase W you SHOULD follow the ToT/Debate/Research pattern which DOES register routes and schemas.

### 1.6 AgentRunRequest Integration Pattern

Each mode adds its config field to `AgentRunRequest` in `packages/contracts/agent_schemas.py`:

```python
class AgentRunRequest(BaseModel):
    ...existing fields...
    tot_config: TotConfig | None = Field(default=None, ...)
    debate_config: DebateConfig | None = Field(default=None, ...)
    research_config: ResearchConfig | None = Field(default=None, ...)
    self_refine_config: SelfRefineConfig | None = Field(default=None, ...)  # NEW
```

### 1.7 AgentRunResponse Integration Pattern

Each mode adds its result field to `AgentRunResponse`:

```python
class AgentRunResponse(BaseModel):
    ...existing fields...
    tot_result: TotResult | None = Field(default=None, ...)
    debate_result: DebateResult | None = Field(default=None, ...)
    research_result: ResearchResult | None = Field(default=None, ...)
    self_refine_result: SelfRefineResult | None = Field(default=None, ...)  # NEW
```

### 1.8 Contract Schema Pydantic Classes Pattern

Each mode gets two Pydantic classes in `packages/contracts/agent_schemas.py`:
- `SelfRefineConfig(BaseModel)` -- Pydantic version of the config for API serialization
- `SelfRefineResult(BaseModel)` -- Pydantic version of the result for API serialization

These are the **serialization** models (Pydantic), distinct from the **internal** dataclass models.

### 1.9 Eval Quality Gate Pattern

File at `eval/self_refine_quality_gate.py` with:

```python
#!/usr/bin/env python3
"""Phase W: Self-Refine Eval Quality Gate.

Usage:
  python eval/self_refine_quality_gate.py run        # Run benchmark
  python eval/self_refine_quality_gate.py gate        # Gate check
"""

from __future__ import annotations

import json, logging, sys, time
from dataclasses import dataclass, field
from typing import Any

logging.basicConfig(level=logging.INFO, ...)
logger = logging.getLogger("self_refine_quality_gate")

_BENCHMARK = [...]  # simplified GSM8K or similar

# Two modes of `_cmd_run`: compare single-shot vs 1-refine vs 3-refine
# `run_benchmark(strategy, ...)` calls different configs
# Report saved to /tmp/self_refine_benchmark_report.json
# Gate check reads report, verifies refinement improves accuracy
```

**Key difference from ToT gate:** Phase W's gate compares three conditions (single-shot, 1-refine, 3-refine) rather than two (tot vs cot).

### 1.10 Integration Files Pattern

Each phase touches:

| File | Change |
|------|--------|
| `apps/gateway/agent/routes.py` | Add `POST /<mode>` route handler |
| `packages/contracts/agent_schemas.py` | Add Config/Result Pydantic schemas, add to AgentRunRequest/AgentRunResponse |
| `apps/gateway/settings.py` | Usually none for reasoning modes (no new env vars needed) |
| `docs/00-roadmap.md` | Add Phase W entry |
| `feature_list.json` | Add F39 entry |
| `eval/acceptance_smoke.py` | Extend smoke test |
| `CHANGELOG.md` | (optional) Add entry |

### 1.11 ADR Pattern

```markdown
# ADR-0007: Self-Refine (Phase W)

## Status
Proposed

## Context
...

## Decision
Implement Self-Refine as a new inference mode under packages/agent/self_refine/

## Consequences
...
```

ADR templates are at `docs/adr/TEMPLATE.md`. Latest ADR is 0007 (Computer Use).

### 1.12 Doc Pattern

Design docs go at `docs/02-phase-w-self-refine.md`. Key sections:
- Overview
- Architecture
- API Design
- Data Flow
- Configuration
- Integration Points

---

## 2. Key Code Snippets

### 2.1 SelfRefineConfig (Internal Dataclass)

```python
# packages/agent/self_refine/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ConvergenceStrategy = Literal["llm_judged", "similarity", "hybrid"]
FeedbackMode = Literal["structured", "free_text"]
FeedbackDimension = Literal["correctness", "clarity", "completeness", "consistency", "actionability"]


@dataclass
class SelfRefineConfig:
    enabled: bool = True
    max_iterations: int = 5  # max refinement rounds (upper limit 10)
    generator_model: str | None = None  # model used for generate()
    feedback_model: str | None = None  # model used for feedback() (can differ)
    convergence_strategy: ConvergenceStrategy = "hybrid"
    max_total_llm_calls: int = 15  # hard upper limit 30
    feedback_dimensions: tuple[FeedbackDimension, ...] = (
        "correctness", "clarity", "completeness", "consistency", "actionability",
    )
    feedback_mode: FeedbackMode = "structured"  # structured | free_text
    similarity_threshold: float = 0.85  # for convergence_strategy="similarity"
    temperature: float = 0.7
    timeout_seconds: float = 120.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_iterations": self.max_iterations,
            "convergence_strategy": self.convergence_strategy,
            "max_total_llm_calls": self.max_total_llm_calls,
            "feedback_mode": self.feedback_mode,
            "temperature": self.temperature,
            "timeout_seconds": self.timeout_seconds,
        }
```

### 2.2 SelfRefineResult (Internal Dataclass)

```python
@dataclass
class FeedbackRound:
    iteration: int
    output: str  # current generated output at this round
    feedback: str  # LLM feedback
    feedback_dimensions: dict[str, str] | None = None  # structured: {dimension: feedback}
    convergeed: bool = False
    execution_time_ms: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "output": self.output,
            "feedback": self.feedback,
            "feedback_dimensions": self.feedback_dimensions,
            "converged": self.convergeed,
            "execution_time_ms": self.execution_time_ms,
            "error": self.error,
        }


@dataclass
class SelfRefineTraceItem:
    event: str  # generate | feedback | refine | converge_check | error
    iteration: int
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class SelfRefineResult:
    prompt: str
    final_output: str
    iterations_completed: int
    convergeed: bool
    rounds: list[FeedbackRound] = field(default_factory=list)
    execution_time_ms: float = 0.0
    total_llm_calls: int = 0
    trace: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "final_output": self.final_output,
            "iterations_completed": self.iterations_completed,
            "converged": self.convergeed,
            "rounds": [r.to_dict() for r in self.rounds],
            "execution_time_ms": self.execution_time_ms,
            "total_llm_calls": self.total_llm_calls,
            "trace": self.trace,
            "error": self.error,
        }
```

### 2.3 Engine Core Pattern

```python
# packages/agent/self_refine/engine.py
from __future__ import annotations

import logging
import time
from typing import Any

from packages.agent.self_refine.models import (
    FeedbackRound,
    SelfRefineConfig,
    SelfRefineResult,
)

logger = logging.getLogger("ai_platform.agent.self_refine.engine")


async def generate(
    prompt: str,
    config: SelfRefineConfig,
    model: str | None = None,
) -> str:
    """Generate initial output."""
    ...LLM call via packages.platform.forward_with_model_router...


async def feedback(
    prompt: str,
    current_output: str,
    config: SelfRefineConfig,
    model: str | None = None,
) -> tuple[str, dict[str, str] | None]:
    """Generate self-feedback on current output.

    Returns (feedback_text, structured_dimensions | None).
    Error handling: retry 1x on failure, degrade to empty feedback.
    """
    ...LLM call with retry-1, returns ("", None) on final failure...


async def refine(
    prompt: str,
    current_output: str,
    feedback_text: str,
    feedback_dimensions: dict[str, str] | None,
    config: SelfRefineConfig,
    model: str | None = None,
) -> str:
    """Refine output based on feedback."""
    ...LLM call...


async def convergence_check(
    prompt: str,
    prev_output: str,
    current_output: str,
    iteration: int,
    config: SelfRefineConfig,
    model: str | None = None,
) -> bool:
    """Check if output has converged.

    - llm_judged: ask LLM if there's any new improvement point
    - similarity: semantic similarity between prev and current > threshold
    - hybrid: both, either triggers convergence
    """
    ...LLM call for llm_judged/hybrid, comparison for all...
```

### 2.4 Router/Endpoint Pattern

```python
# apps/gateway/agent/routes.py (NEW route, integrated into existing file)

@router.post("/self-refine")
async def agent_self_refine(
    body: AgentRunRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Any:
    """Phase W: Self-Refine — 自我反馈 + 自我修正迭代收敛。

    Agent 首轮输出 → 自我反馈（Self-Feedback）→ 自我修正（Self-Refine）→ 迭代收敛。
    """
    ...tenant validation, rate limit, budget checks (same pattern as ToT/Debate)...

    prompt = (body.goal or _last_user_goal(body.messages) or "").strip()
    if not prompt:
        return json_error(400, "INVALID_REQUEST", "Self-Refine 需要 goal 或 user 消息")

    src = body.self_refine_config
    from packages.agent.self_refine import SelfRefineConfig as SRConfig
    from packages.agent.self_refine import run_self_refine

    cfg = SRConfig(
        max_iterations=src.max_iterations if src else 5,
        generator_model=src.generator_model if src else None,
        feedback_model=src.feedback_model if src else None,
        convergence_strategy=src.convergence_strategy if src else "hybrid",
        max_total_llm_calls=src.max_total_llm_calls if src else 15,
        feedback_mode=src.feedback_mode if src else "structured",
        similarity_threshold=src.similarity_threshold if src else 0.85,
        timeout_seconds=src.timeout_seconds if src else 120.0,
        temperature=src.temperature if src else 0.7,
    )

    try:
        result = await run_self_refine(
            prompt=prompt,
            config=cfg,
            model=body.model,
        )
    except Exception as e:
        logger.exception("agent_self_refine failed tenant=%s", tenant.tenant_id)
        return json_error(503, "SELF_REFINE_ERROR", str(e))

    from packages.contracts.agent_schemas import SelfRefineResult as SRResultSchema

    return JSONResponse({
        "tenant_id": tenant.tenant_id,
        "session_id": body.session_id.strip(),
        "model": body.model or settings.default_model,
        "final_message": result.final_output or "",
        "self_refine_result": SRResultSchema(
            prompt=result.prompt,
            final_output=result.final_output,
            iterations_completed=result.iterations_completed,
            convergeed=result.convergeed,
            execution_time_ms=result.execution_time_ms,
            total_llm_calls=result.total_llm_calls,
            error=result.error,
        ).model_dump(exclude_none=True),
    })
```

### 2.5 Eval Gate Pattern (comparison of 3 strategies)

```python
# eval/self_refine_quality_gate.py

async def run_benchmark(strategy: str, sample_limit: int | None = None, model: str | None = None) -> EvalResult:
    """Run benchmark for given strategy.

    Strategy: "single_shot" | "one_refine" | "three_refine"
    """
    items = _BENCHMARK[:sample_limit] if sample_limit else _BENCHMARK
    result = EvalResult(strategy=strategy, total=len(items))

    for i, item in enumerate(items):
        question = item["question"]
        expected = item["answer"]

        if strategy == "single_shot":
            # Config with max_iterations=0 (generate only, no refinement)
            cfg = SelfRefineConfig(max_iterations=0, max_total_llm_calls=1)
        elif strategy == "one_refine":
            cfg = SelfRefineConfig(max_iterations=1, max_total_llm_calls=3)
        elif strategy == "three_refine":
            cfg = SelfRefineConfig(max_iterations=3, max_total_llm_calls=10)

        start = time.time()
        output = await run_self_refine(question, config=cfg, model=model)
        ...evaluate output against expected answer...

    return result


async def _cmd_gate() -> None:
    """Gate checks that 3-refine > 1-refine > single-shot (or at least not worse)."""
```

---

## 3. Potential Pitfalls and Integration Points

### 3.1 Potential Pitfalls

| Pitfall | Mitigation |
|---------|------------|
| **LLM self-feedback is weak** -- the same model critiquing its own output often misses flaws (the "self-blindness" problem). | Support separate generator_model and feedback_model (Debate pattern). For structured feedback, use specific rubrics per dimension. |
| **False convergence** -- LLM judge says "no more improvements" when there are still obvious issues. | Use "hybrid" convergence strategy by default: both LLM judge AND similarity check must agree. With similarity-only as fallback. |
| **Runaway LLM calls** -- each iteration does generate + feedback + refine + converge_check = 4 LLM calls per round. 5 iterations could reach 20+ calls. | Enforce max_total_llm_calls (default 15, hard max 30). Count each LLM call precisely. Fail-closed when budget exhausted. |
| **Similarity threshold tuning** -- semantic similarity thresholds are domain-dependent. | Default 0.85; document that this may need tuning per use case. Use embedding service (packages.embedding.service) which already exists. |
| **Long prompts repeated** -- each refinement round sends full prompt + full current output, causing linear context growth. | Consider summarizing prior rounds or only sending differences. For Phase W MVP, send full context; optimize later. |
| **Naming collision with existing `self_evolve.py`** -- Phase R already has `packages/agent/self_evolve.py` which handles post-run reflection and strategy patching. Self-Refine is a real-time output improvement loop, not post-hoc learning. | Use `self_refine/` directory and `run_self_refine` function name. Keep the concept distinct in docs. |
| **No Gateway route for Phase V** -- Computer Use (Phase V) has no route or schemas, so it's not a good template. ToT/Debate/Research are the correct pattern to follow. | Follow ToT/Debate/Research pattern which includes route + schemas. |
| **AgentRunRequest body already has many optional configs** -- adding self_refine_config adds clutter. | This is the established pattern (tot_config, debate_config, research_config all live there). |
| **Retry-1 pattern consistency** -- feedback() failure should retry once then gracefully degrade. | Implement exact retry-1 logic: first failure logs warning, second failure degrades to empty feedback. Never bubble up to caller. |
| **feature_list.json is at project root** -- it has features F01-F38. F39 will be Phase W Self-Refine. | Update file at `/Users/zhangyue/IdeaProjects/ai-platform-lab/feature_list.json`. |

### 3.2 Key Integration Points

1. **`packages/platform` LLM calling** -- every mode uses `packages.platform.forward_with_model_router(payload)` for LLM calls. Never call the LLM API directly. The payload format is:
   ```python
   payload = {
       "model": resolved_model,
       "messages": [
           {"role": "system", "content": "..."},
           {"role": "user", "content": prompt},
       ],
       "temperature": config.temperature,
   }
   route = await forward_with_model_router(payload)
   if route.status == 200 and route.body:
       content = route.body["choices"][0]["message"]["content"]
   ```

2. **`packages.embedding.service`** -- for similarity-based convergence strategy, use the existing embedding service:
   ```python
   from packages.embedding.service import get_embedding_service
   service = get_embedding_service()
   emb_a = await service.embed_one(model_id, text_a, tenant_id="system")
   emb_b = await service.embed_one(model_id, text_b, tenant_id="system")
   similarity = cosine_similarity(emb_a, emb_b)  # use existing _cosine_similarity from experience_store
   ```

3. **Gateway router_registry.py** -- if there's a central router registry for new endpoints, register `/v1/agent/self-refine` there. Check `apps/gateway/router_registry.py` for the pattern.

4. **Otel tracing** -- consider adding `component_span` for visibility:
   ```python
   from packages.observability.otel import component_span
   with component_span("agent.self_refine", component="agent", enabled=..., tenant_id=...):
       ...
   ```
   (Note: ToT/Debate/Research routes do NOT use component_span in their route handlers, only the main /plan and /run routes do. You may choose to add it or not.)

### 3.3 Feedback Prompt Design (Critical)

**Structured dimensions feedback:**
```
你是自我反馈助手。请对以下输出按维度评审。

原始任务：{prompt}
当前输出：{current_output}

请按以下维度给出反馈：
- correctness：事实正确性、逻辑漏洞
- clarity：表达清晰度、歧义
- completeness：是否遗漏关键信息
- consistency：内部一致性、与 prompt 要求一致
- actionability：输出是否可执行（针对代码/JSON）

输出格式：
correctness: <反馈>
clarity: <反馈>
...

如果你认为该输出在所有维度上已无改进空间，请在最后一行加上：
结论：无改进点|  # Note: this is the signal for convergence_check
```

**Free text feedback:**
```
你是自我反馈助手。请对以下输出给出改进建议。

原始任务：{prompt}
当前输出：{current_output}

请仔细检查输出中的问题：事实错误、逻辑漏洞、表达不清、遗漏关键信息等。
输出格式：
反馈：<你的反馈>

如果你认为输出已无改进空间，请在最后一行加上：
结论：无改进点|
```

**Convergence check (llm_judged):**
```
比较以下两版输出，判断新版是否有实质性的改进。

上一版：{prev_output}
当前版：{current_output}

请输出：
改进：<yes/no>  # yes = 有实质性改进，继续迭代；no = 已收敛
理由：<简要说明>
```

---

## 4. Complete File List with Implementation Guidance

### 4.1 Files to CREATE

| # | File | Purpose | Implementation Guidance |
|---|------|---------|------------------------|
| 1 | `packages/agent/self_refine/__init__.py` | Public API | Follow ToT `__init__.py` pattern exactly. Export `run_self_refine`, `SelfRefineConfig`, `SelfRefineResult`. Core loop: generate() -> feedback() -> refine() -> convergence_check() -> loop. |
| 2 | `packages/agent/self_refine/models.py` | Dataclasses | SelfRefineConfig (with feedback_dimensions, convergence_strategy, max_iterations, max_total_llm_calls, generator_model, feedback_model), FeedbackRound, SelfRefineResult, SelfRefineTraceItem. Each with to_dict(). |
| 3 | `packages/agent/self_refine/engine.py` | Core engine | generate(), feedback() (with retry-1 + graceful degrade), refine(), convergence_check() (3 strategies), run() orchestrator that loops through iterations. |
| 4 | `tests/test_self_refine.py` | Unit tests | >= 15 tests covering: normal converge, max_iterations cutoff, max_total_llm_calls cutoff, feedback retry+degrade, separate generator/feedback models, structured+free_text modes, empty feedback convergence, all 3 convergence strategies. |
| 5 | `eval/self_refine_quality_gate.py` | Quality gate | run + gate commands. Benchmark comparing single_shot vs one_refine vs three_refine on simplified GSM8K. |
| 6 | `docs/02-phase-w-self-refine.md` | Design doc | Overview, architecture, API design, data flow (with diagram), config reference, integration guide. |
| 7 | `docs/adr/0008-self-refine.md` | ADR | Decision record for self-refine implementation approach. |

### 4.2 Files to MODIFY

| # | File | Change |
|---|------|--------|
| 8 | `apps/gateway/agent/routes.py` | Add `POST /self-refine` route (between research and blackboard endpoints, around line 495). Import and wrap SelfRefineConfig/Result. |
| 9 | `packages/contracts/agent_schemas.py` | Add `SelfRefineConfig` Pydantic model (with `max_iterations`, `generator_model`, `feedback_model`, `convergence_strategy`, `max_total_llm_calls`, `feedback_mode`, `similarity_threshold`, optional `feedback_dimensions` override), `SelfRefineRoundSchema`, `SelfRefineResult` Pydantic model (with `prompt`, `final_output`, `iterations_completed`, `convergeed`, `execution_time_ms`, `total_llm_calls`, `error`, optional `rounds`). Add `self_refine_config` to `AgentRunRequest`. Add `self_refine_result` to `AgentRunResponse`. |
| 10 | `docs/00-roadmap.md` | Add Phase W entry after Phase V Computer Use. |
| 11 | `feature_list.json` | Add F39 with `status: "done"`, `phase: "W"`, `verifiedBy: ["pytest tests/", "eval/self_refine_quality_gate.py"]`, evidence notes. |
| 12 | `eval/acceptance_smoke.py` | Add self-refine smoke test case that calls the API with a simple prompt and validates response shape. |
| 13 | `apps/gateway/router_registry.py` | Possibly add `SELF_REFINE` route registration if this file maintains a registry. Check pattern used by TOT/DEBATE/RESEARCH there. |

### 4.3 Implementation Order

1. **Models first**: `packages/agent/self_refine/models.py` -- Config, Result, FeedbackRound dataclasses
2. **Engine**: `packages/agent/self_refine/engine.py` -- generate(), feedback(), refine(), convergence_check(), orchestrate run()
3. **Public API**: `packages/agent/self_refine/__init__.py` -- run_self_refine() wrapping engine
4. **Contract schemas**: `packages/contracts/agent_schemas.py` -- Pydantic models + AgentRunRequest/Response fields
5. **Gateway route**: `apps/gateway/agent/routes.py` -- `POST /self-refine` endpoint
6. **Router registry**: `apps/gateway/router_registry.py` -- register if needed
7. **Tests**: `tests/test_self_refine.py` -- 15+ tests
8. **Eval gate**: `eval/self_refine_quality_gate.py` -- run + gate commands
9. **Docs**: design doc + ADR
10. **Metadata**: roadmap.md, feature_list.json, acceptance_smoke.py

### 4.4 Convergence Strategy Implementation Details

**`llm_judged`:**
```python
async def _check_llm_judged(prompt, prev_output, current_output, model) -> bool:
    """Ask LLM if current output has substantive improvement over previous.
    Returns True if converged (no meaningful improvements possible)."""
    ...LLM call with comparison prompt...
    return response.strip().lower().startswith("no")
```

**`similarity`:**
```python
async def _check_similarity(prev_output, current_output, threshold) -> bool:
    """Compute semantic similarity. Returns True if above threshold (converged)."""
    ...use embedding service to get embeddings...
    similarity = cosine_similarity(emb_prev, emb_curr)
    return similarity >= threshold
```

**`hybrid`:**
```python
async def _check_hybrid(prompt, prev_output, current_output, threshold, model) -> bool:
    """Both conditions: EITHER LLM says converged OR similarity above threshold.
    i.e., converged = llm_judged_converged OR similarity_converged
    """
    judged = await _check_llm_judged(prompt, prev_output, current_output, model)
    similar = await _check_similarity(prev_output, current_output, threshold)
    return judged or similar
```

### 4.5 LLM Call Count Tracking

Each call to `forward_with_model_router` increments the call counter. The run orchestrator:

```python
total_calls = 0

async def _llm_call(payload, model) -> str:
    nonlocal total_calls
    if total_calls >= cfg.max_total_llm_calls:
        raise MaxLLMCallsExceeded()
    result = await forward_with_model_router(payload)
    total_calls += 1
    return extract_content(result)
```

This ensures hard enforcement of `max_total_llm_calls`.

---

## 5. Original Paper Reference (Madaan et al., 2023)

Self-Refine (https://arxiv.org/abs/2303.17651) operates as:

1. **Generate**: Produce initial output `y0` given input `x`
2. **Feedback**: Given `x` and `y_t`, produce feedback `fb_t` identifying issues
3. **Refine**: Given `x`, `y_t`, and `fb_t`, produce improved output `y_{t+1}`
4. **Iterate**: Repeat steps 2-3 until convergence or max iterations

Key insight: The **same LLM** is used for all three roles (generator, feedback provider, refiner). Phase W extends this by supporting separate feedback_model (for stronger critique capability) and structured feedback dimensions (for more actionable feedback).

---

## 6. Summary Checklist

- [x] Package structure understood (`packages/agent/<mode>/`)
- [x] `__init__.py` pattern documented (run_<mode> + Config/Result + trace/timing/error)
- [x] Config/Result dataclass conventions documented (to_dict, field defaults)
- [x] Gateway route pattern documented (routes.py merged, AgentRunRequest reuse)
- [x] Contract schema pattern documented (Pydantic models in agent_schemas.py)
- [x] Eval gate pattern documented (run + gate commands, JSON report)
- [x] Self-Refine specific design documented (feedback dimensions, convergence strategies)
- [x] Potential pitfalls identified (self-blindness, false convergence, call budget)
- [x] Complete file list with implementation order
- [x] Code snippets provided for all key components
- [x] Embedding service integration identified for similarity strategy
- [x] LLM call budget enforcement design included
- [x] Retry-1 + graceful degrade pattern for feedback`\n2. **Feedback**: Given `x` and `y_t`, produce feedback `fb_t` identifying issues\n3. **Refine**: Given `x`, `y_t`, and `fb_t`, produce improved output `y_{t+1}`\n4. **Iterate**: Repeat steps 2-3 until convergence or max iterations\n\nKey insight: The **same LLM** is used for all three roles (generator, feedback provider, refiner). Phase W extends this by supporting separate feedback_model (for stronger critique capability) and structured feedback dimensions (for more actionable feedback).\n\n---\n\n## 6. Summary Checklist\n\n- [x] Package structure understood (`packages/agent/<mode>/`)\n- [x] `__init__.py` pattern documented (run_<mode> + Config/Result + trace/timing/error)\n- [x] Config/Result dataclass conventions documented (to_dict, field defaults)\n- [x] Gateway route pattern documented (routes.py merged, AgentRunRequest reuse)\n- [x] Contract schema pattern documented (Pydantic models in agent_schemas.py)\n- [x] Eval gate pattern documented (run + gate commands, JSON report)\n- [x] Self-Refine specific design documented (feedback dimensions, convergence strategies)\n- [x] Potential pitfalls identified (self-blindness, false convergence, call budget)\n- [x] Complete file list with implementation order\n- [x] Code snippets provided for all key components\n- [x] Embedding service integration identified for similarity strategy\n- [x] LLM call budget enforcement design included\n- [x] Retry-1 + graceful degrade pattern for feedback\n"