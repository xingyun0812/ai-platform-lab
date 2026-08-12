# Phase U — Deep Research 推理模式

> **状态**：✅ **已交付**（Phase U · 2026-08-05）
> **前置**：Phase S（ToT）· Phase T（Debate）· web_search tool · fetch_url tool
> **门禁**：`python eval/research_quality_gate.py run && gate`

---

## 1. 动机

ToT 和 Debate 聚焦于「给定上下文后的推理」，但缺乏**自主获取外部信息**的能力。Deep Research 的定位是：给定一个研究问题，自动进行问题分解 → 并行搜索 → 阅读 → 综合 → 迭代深入。

## 2. 架构

### 基础流程（V1）

```
run_research(question, config)
  │
  ├─ 1. 问题分解 (QuestionDecomposer)
  │     └─ LLM: question → [sub_q1, sub_q2, ...]
  │
  ├─ 2. 对每个子问题:
  │     ├─ web_search(sub_q) → 结果列表
  │     ├─ fetch_url(url) → 正文
  │     ├─ LLM 摘要 → ResearchNote {summary, key_points, facts, confidence}
  │     └─ (可选) 多模态截图分析 → screenshot_analysis + visual_data
  │
  ├─ 3. 信息综合 (ResearchSynthesizer)
  │     └─ 所有 Notes → Markdown 报告
  │
  └─ 4. (可选 max_depth>=2) 迭代深入
        ├─ identify_gaps() → 识别信息缺口
        ├─ gap_filler.fill_gaps() → 并发补充搜索（含 URL 去重）
        └─ re-synthesize() → 最终报告
```

### 完整流程（当前版本）

```
run_research(question, max_depth=2)
  │
  ├─ Round 1:
  │   ├─ 问题分解 → [sub_q1, sub_q2, ...]
  │   ├─ 顺序搜索+阅读
  │   │   ├─ web_search sub_q → 结果列表
  │   │   ├─ fetch_url(url) → 正文
  │   │   ├─ LLM 摘要 → ResearchNote (含 facts + confidence)
  │   │   └─ 截图分析 → screenshot_analysis (可选)
  │   └─ 综合 → 中间报告
  │
  ├─ Round 2 (max_depth=2):
  │   ├─ identify_gaps → 识别信息缺口
  │   ├─ gap_filler：并发搜索（Semaphore=3）+ URL 去重
  │   └─ 重新综合 → 最终报告
  │
  └─ 返回 ResearchResult {report, notes, sub_questions, depth_completed, trace}
```

### ResearchNote 数据结构

```python
@dataclass
class ResearchNote:
    sub_question: str
    source_url: str
    source_title: str
    summary: str               # LLM 摘要
    key_points: list[str]      # 核心要点
    facts: list[str]           # 提取的独立事实点（Phase U 迭代完善）
    confidence: str            # high / medium / low（信息可靠度）
    screenshot_analysis: str   # 多模态截图分析（Phase V #201）
    visual_data: dict          # 提取的图表数据
    contradictions: list[str]  # 冲突的 note_id 列表（预留）
```

## 3. API

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
      "max_sub_questions": 5,
      "results_per_query": 3
    }
  }'
```

## 4. 核心文件

| 路径 | 职责 |
|------|------|
| `packages/agent/research/__init__.py` | `run_research()` 编排器 |
| `packages/agent/research/models.py` | 数据模型 |
| `packages/agent/research/decomposer.py` | 问题分解 |
| `packages/agent/research/searcher.py` | 搜索+阅读+摘要 |
| `packages/agent/research/gap_filler.py` | 信息缺口补充搜索（并发 + URL 去重） |
| `packages/agent/research/synthesizer.py` | 信息综合 + 缺口识别 |
| `packages/agent/tools/fetch_url.py` | fetch_url 工具 |
| `packages/agent/tools/computer_use.py` | 截图能力（多模态分析） |
| `apps/gateway/agent/routes.py` | `POST /v1/agent/research` |

## 5. 迭代完善记录

| 日期 | 变更 | Issue |
|------|------|-------|
| 2026-08-05 | 初始交付：分解→搜索→摘要→综合 | Phase U |
| 2026-08-06 | 迭代深入：identify_gaps + gap_filler + 重新综合 | #200 |
| 2026-08-06 | 多模态截图分析：ComputerUseExecutor 截图 → 多模态 LLM 分析 | #201 |
| 2026-08-06 | ResearchNote 扩展：facts、confidence、contradictions 字段 | Phase U 迭代 |
| 2026-08-06 | gap_filler 并发搜索：asyncio.Semaphore(3) + URL 去重 | Phase U 迭代 |

## 6. 与工业 Deep Research 的差距

| 维度 | 当前状态 | 后续改进 |
|------|---------|---------|
| **迭代反思** | 1 轮 gap fill（max_depth=2） | 多轮迭代 + 信息充分度判断 |
| **Note 元数据** | facts、confidence、contradictions 已预留 | 事实冲突检测 + 引用标注 |
| **子问题管理** | 并发搜索（Semaphore=3）+ URL 去重 | 子问题去重、相关性过滤 |
| **冲突校验** | contradictions 字段已预留 | Synthesizer 事实冲突检测 |
| **终止条件** | max_depth + timeout | LLM 判断信息充分度 |
