# Phase J — 评测数据集扩充 + CI 评测门禁（#31）

> **目标**：扩充基准数据集到 ≥ 200 条，建立 CI 评测门禁（PR 时自动跑评测，质量回退 > 5% 则 block merge）。

---

## 1. 设计要点

### 1.1 三类基准数据集

| 文件 | 类别 | 数量 | 说明 |
|------|------|------|------|
| `eval/baselines/rag_extended.jsonl` | RAG | ≥100 | 事实/推理/多跳/负例/多语言/长上下文 |
| `eval/baselines/agent_scenarios.jsonl` | Agent | ≥50 | 工具调用/多步/澄清/拒绝/安全 |
| `eval/baselines/safety.jsonl` | 安全 | ≥50 | PII/注入/越狱/有害/边界 |

**总计**：≥200 条用例

### 1.2 评测 Pipeline

```
load_baselines(category)
    ↓
run_category(category) → CategoryResult
    ↓
run_all() → EvalReport
    ↓
compare_to_baseline() → ComparisonResult
    ↓
check_gate() → GateResult (pass/fail)
```

### 1.3 门禁逻辑

- **基线**：`eval/baselines/main_baseline.json` 记录 main 分支各类别 pass_rate
- **阈值**：`EVAL_GATE_THRESHOLD_PCT=5.0`（默认）
- **规则**：当前 pass_rate 相对 main 回退超过阈值 → gate fail → block PR
- **退出码**：gate 通过返回 0，失败返回 1（CI 据此 block）

### 1.4 无 LLM 降级

- 无 `EVAL_API_KEY` 时，跳过 live 用例（标记 skipped）
- 仍可运行语法/结构验证类用例
- 保证 CI 在无 key 环境下不报错

---

## 2. 核心数据模型

```python
@dataclass
class EvalReport:
    total_cases: int
    passed: int
    failed: int
    skipped: int
    pass_rate: float
    by_category: dict[str, CategoryResult]
    timestamp: float
    commit_sha: str

@dataclass
class CategoryResult:
    category: str
    total: int
    passed: int
    failed: int
    skipped: int
    cases: list[dict]  # [{id, expected, actual, passed, ...}]

@dataclass
class ComparisonResult:
    baseline_pass_rate: float
    current_pass_rate: float
    delta_pct: float
    gate_passed: bool

@dataclass
class GateResult:
    passed: bool
    reason: str
    delta: float
    threshold: float
```

---

## 3. 使用方式

### 3.1 运行评测

```bash
# 全量评测（需 LLM_API_KEY 调 gateway）
python eval/run.py run-eval

# 指定类别
python eval/run.py run-eval --category rag

# 输出报告到 eval/reports/
```

### 3.2 门禁检查

```bash
# CI 使用：对比 main baseline，回退 >5% 则 exit 1
python eval/run.py gate --threshold 5
```

### 3.3 报告格式

- `eval/reports/report-<timestamp>.md` — 人类可读 Markdown
- `eval/reports/report-<timestamp>.json` — CI artifact

---

## 4. CI 集成

`.github/workflows/eval.yml`：

```yaml
name: Eval Gate
on:
  pull_request:
    branches: [main]
jobs:
  eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -e ".[dev]"
      - name: Run eval gate
        env:
          EVAL_API_KEY: ${{ secrets.EVAL_API_KEY }}
        run: python eval/run.py gate --threshold 5
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: eval-report
          path: eval/reports/
```

---

## 5. 配置项

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `EVAL_PIPELINE_ENABLED` | `true` | 启用评测 Pipeline |
| `EVAL_GATEWAY_URL` | `http://127.0.0.1:8000` | 评测调用的 gateway URL |
| `EVAL_API_KEY` | — | 评测用 API key（无则跳过 live 用例） |
| `EVAL_GATE_THRESHOLD_PCT` | `5.0` | 门禁阈值 |
| `EVAL_BASELINE_PATH` | `eval/baselines/main_baseline.json` | main baseline 路径 |

---

## 6. 测试与验收

```bash
# 1. 单元测试（63 个用例）
python3 tests/test_eval_pipeline.py
# 期望：63 passed

# 2. baseline 验证
python eval/run.py validate-baseline

# 3. 用例数量验证
wc -l eval/baselines/*.jsonl
# 期望：rag_extended + agent_scenarios + safety ≥ 200
```

---

## 7. 代码导航

```
eval/
├── baselines/
│   ├── rag_extended.jsonl      # RAG ≥100 条
│   ├── agent_scenarios.jsonl   # Agent ≥50 条
│   ├── safety.jsonl            # 安全 ≥50 条
│   └── main_baseline.json      # main 分支基线
├── pipeline.py                 # EvalPipeline + EvalReport
├── gate.py                     # check_gate + GateResult
├── report.py                   # Markdown/JSON 报告格式化
└── run.py                      # CLI 入口（run-eval / gate / validate-baseline）

tests/test_eval_pipeline.py     # 63 个单测

.github/workflows/eval.yml      # CI 评测门禁
```

---

## 8. 已知限制

1. **仅关键词匹配评分**：未用 LLM-based grading，复杂语义判断有限
2. **无并行执行**：用例串行跑，200 条较慢（生产可加 asyncio 并发）
3. **baseline 手动维护**：main_baseline.json 需人工更新，易漂移
4. **无 A/B 评测**：不支持两个版本对比（需手动跑两次）
5. **无 per-tenant 评测**：所有用例用同一 tenant，未覆盖多租户场景
6. **依赖 gateway 在线**：live 用例需 gateway + LLM key，CI 无 key 时大量 skipped

---

## 9. 面试讲法

1. **为什么需要评测门禁**：防止 PR 引入质量回退，保护 main 分支质量基线
2. **三类用例设计**：RAG（检索能力）+ Agent（工具调用）+ Safety（安全合规）覆盖核心场景
3. **门禁阈值 5%**：平衡灵敏度与误报率，回退 5% 是显著退化信号
4. **无 LLM 降级**：CI 无 key 时跳过 live 用例，不阻塞 PR 流程
5. **报告双格式**：Markdown 给人看，JSON 给 CI 解析
6. **诚实边界**：仅关键词匹配、无并行、baseline 手动维护；这些是生产化需补的

参考代码：
- `eval/pipeline.py:EvalPipeline` — 核心管道
- `eval/gate.py:check_gate` — 门禁逻辑
- `eval/baselines/main_baseline.json` — 基线
- `.github/workflows/eval.yml` — CI 集成
