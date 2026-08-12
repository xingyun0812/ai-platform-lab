# Deep Research Agent 模板

> 给定一个研究问题，自动进行：问题分解 → 并行搜索 → 阅读 → 综合 → 迭代深入。

## 架构

```
run_research(question, config)
  │
  ├─ 1. 问题分解 (QuestionDecomposer)
  │     └─ LLM: question → [sub_q1, sub_q2, sub_q3, ...]
  │
  ├─ 2. 对每个子问题:
  │     ├─ web_search(sub_q) → title/snippet/url
  │     ├─ fetch_url(url) → 正文
  │     └─ LLM 摘要 → ResearchNote {summary, key_points}
  │
  ├─ 3. 信息综合 (ResearchSynthesizer)
  │     └─ 所有 Notes → Markdown 报告
  │
  └─ 4. (可选 max_depth=2) 迭代深入
        ├─ identify_gaps() → 识别信息缺口
        ├─ gap_filler.fill_gaps() → 补充搜索
        └─ re-synthesize() → 最终报告
```

## 代码骨架

```python
from packages.agent.research import run_research, ResearchConfig

result = await run_research(
    question="量子计算的最新进展",
    config=ResearchConfig(
        max_sub_questions=5,   # 分解成 5 个子问题
        results_per_query=5,   # 每个搜索保留 5 个结果
        max_depth=2,           # 迭代深入
    ),
)
print(result.report)                # Markdown 研究报告
print(result.sub_questions)         # 子问题列表
print(result.num_sources_consulted) # 信息来源数
```

## curl 调用

```bash
curl -s http://127.0.0.1:8000/v1/agent/research \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{
    "tenant_id": "admin",
    "session_id": "research-demo",
    "goal": "量子计算的最新进展",
    "research_config": {
      "max_sub_questions": 3,
      "results_per_query": 3,
      "max_depth": 2
    }
  }'
```

## 关键配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_sub_questions` | 5 | 分解出的子问题数 |
| `results_per_query` | 5 | 每个搜索保留的结果数 |
| `max_depth` | 2 | 迭代深入轮数（1=不迭代）|
| `timeout_seconds` | 300 | 总超时 |
| `temperature` | 0.3 | LLM 生成温度 |

## 核心文件

| 路径 | 职责 |
|------|------|
| `packages/agent/research/__init__.py` | `run_research()` 编排器 |
| `packages/agent/research/decomposer.py` | 问题分解 |
| `packages/agent/research/searcher.py` | 搜索+阅读+摘要循环 |
| `packages/agent/research/synthesizer.py` | 信息综合 + 缺口识别 |
| `packages/agent/research/gap_filler.py` | 补充搜索 |
| `packages/agent/tools/web_search.py` | 搜索引擎接口 |
| `packages/agent/tools/fetch_url.py` | 网页全文提取 |

## 与其它模式的关系

- Research 使用 `web_search` 和 `fetch_url` 工具（注册在 `ToolRegistry`）
- Research 的 LLM 调用复用 `forward_with_model_router()`
- 结合 Computer Use 可对网页截图进行多模态分析（#201）
- 可扩展为 Research + Debate：多个 Researcher 各自研究后 Debate 综合
