# Phase Q Q6 — Plan Quality Gate

## 设计目标

Phase Q 最后一个 Issue（#121）建立 **Planner 质量评测门禁**，让 CI 可以在无外部 API 依赖的情况下验证规划质量是否退化。
核心思路：维护一份 `eval/plan_baseline.jsonl` 作为"规划质量标准"，用 mock 模式离线验证
每个用例的 Plan 是否满足步骤数量、工具提示、无循环等约束。

---

## 数据模型

### BaselineCase（JSONL 行格式）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `str` | 用例唯一 ID（如 `pb01`） |
| `goal` | `str` | 规划目标（自然语言）|
| `min_steps` | `int` | 最少步骤数 |
| `max_steps` | `int` | 最多步骤数 |
| `required_tool_hints` | `list[str \| null] \| null` | 至少有一个 step 的 tool_hint 在此列表中；`null` = 无约束；列表中含 `null` = 任意 tool_hint 均可 |
| `description` | `str` | 人类可读说明 |

### QualityResult（check_plan_quality 返回值）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `str` | 用例 ID |
| `goal` | `str` | 用例目标 |
| `steps_count` | `int` | 实际步骤数 |
| `steps_ok` | `bool` | 步骤数量在 [min_steps, max_steps] 内 |
| `tool_hints_ok` | `bool` | 工具提示约束满足 |
| `no_cycle` | `bool` | 无循环依赖 |
| `overall_pass` | `bool` | 全部条件均为 True |

---

## REST API

> 本模块为纯 eval 工具，不提供 HTTP 端点。通过 CLI 调用。

---

## CLI 用法

```bash
# 静态校验 baseline 格式（无 LLM）
python eval/plan_quality_gate.py check

# Mock 模式运行全部用例（不调 API）
python eval/plan_quality_gate.py run

# 打印 baseline 摘要
python eval/plan_quality_gate.py summary
```

---

## 配置表

本模块无需新增 settings 字段，不依赖外部配置。

| 配置项 | 环境变量 | 默认值 | 说明 |
|--------|----------|--------|------|
| — | — | — | 无新增配置 |

> **shared 文件集成说明**（供父 Agent 参考）：
> - `apps/gateway/main.py`：无需修改（本模块仅为 eval 工具）
> - `apps/gateway/settings.py`：无需修改
> - `.env.example`：无需添加
> - `README.md`：可在 `eval/` 章节添加：`plan_quality_gate.py — Phase Q Q6 Plan 质量门禁（mock 离线运行）`
> - `docs/roadmap.md`：Phase Q Q6 可标记 `[x] Plan quality gate — baseline + CI`

---

## 代码导航

| 文件 | 说明 |
|------|------|
| `eval/plan_baseline.jsonl` | 7 条 Plan 质量基线用例（单步、多步、tool_hint 约束） |
| `eval/plan_quality_gate.py` | 门禁主逻辑：`load_baseline` / `check_plan_quality` / `run_gate` / `main` |
| `tests/test_plan_quality_gate.py` | 20 个单测，全部无外部依赖 |

### 关键函数

```
eval/plan_quality_gate.py
  load_baseline(path)         # 加载 JSONL baseline 文件
  check_plan_quality(plan, case)  # 单用例质量验证
  run_gate(mock_generate)     # 批量运行门禁
  static_check_baseline(path) # 静态格式校验
  _topological_sort(steps)    # Kahn 拓扑排序，检测循环
  _build_mock_plan_for_case(case)  # 构造满足约束的 mock Plan
  main()                      # CLI 入口
```

---

## 测试说明

```bash
python tests/test_plan_quality_gate.py
```

测试分组：

| 类 | 测试数 | 覆盖点 |
|----|--------|--------|
| `TestLoadBaseline` | 5 | 加载、必填字段、数量下限、路径错误、JSON 格式错误 |
| `TestCheckPlanQuality` | 10 | 步骤数 ok/too many/too few、tool_hint 匹配/None/不匹配、无环/有环、overall_pass/fail |
| `TestRunGate` | 5 | 返回类型、total 正确、passed+failed=total、mock 全通、结果字段 |
| `TestStaticCheckBaseline` | 3 | 正常、文件缺失、用例过少 |
| `TestTopologicalSort` | 2 | 链式正常、有环返回 None |

---

## 已知限制

1. **真实 LLM 路径未做集成测试**：`run_gate(mock_generate=False)` 需要真实 API Key，CI 中始终用 mock 模式。
2. **tool_hint 约束为"至少一个"**：当前策略只要求 required_tool_hints 中有一个出现在 plan 中即可，未来可扩展为"每个 required hint 都必须出现"。
3. **步骤并发未评测**：当前只统计步骤总数，未验证并行步骤的正确性。
4. **goal 语义未评测**：只做结构验证，不评测 plan goal 与用户目标的语义相似度。

---

## 面试要点

- **为什么建 baseline JSONL 而不是单元断言？**
  JSONL 格式易于扩展和 diff，方便在 PR review 中看到评测数据变化；单元断言依赖硬编码数值，维护成本高。

- **如何在无 LLM 的 CI 环境中运行质量门禁？**
  `_build_mock_plan_for_case` 根据 baseline 约束（min_steps / required_tool_hints）构造满足条件的 mock Plan，实现完全离线的结构性验证。

- **拓扑排序在规划质量中的作用？**
  Kahn 算法检测 depends_on 是否存在循环依赖；若 Plan 有环，执行引擎将死锁，因此无环是质量门禁的必要条件。

- **门禁如何防止退化？**
  通过 `run_gate` 的 `failed` 计数；任何 failed > 0 都会导致 CLI 以 exit(1) 退出，从而使 CI 失败。未来可将历史 pass_rate 写入 baseline JSON，与 `eval/gate.py` 的对比逻辑对称。
