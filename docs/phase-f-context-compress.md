# Phase F — 上下文压缩策略（#33）

> **目标**：基于 #31 长记忆，实现滑窗截断 + LLM 摘要压缩 + Token 感知注入的三层压缩策略。

对标「Agent 平台架构全景」中的「能力中台 — 上下文管理」能力。

---

构建思路、使用链路与逐文件代码说明见 [phase-f-build-and-code-guide.md](./phase-f-build-and-code-guide.md)。

## 1. 设计要点

### 1.1 三层压缩策略

| 层级 | 策略 | 触发条件 | 实现 |
|------|------|---------|------|
| L1 | **滑窗截断** | 总是生效 | 保留最近 N 轮，老对话丢弃（已有 `assemble_llm_messages`） |
| L2 | **LLM 摘要压缩** | 每 `MEMORY_SUMMARIZE_EVERY_N_TURNS` 轮 | 调用 LLM 将老对话压缩为 summary（替换 `stub_summarize`） |
| L3 | **Token 感知注入** | 每次请求 | 检索 session 长记忆，按剩余 budget 动态注入 system prompt |

### 1.2 LLM 增强摘要

**`maybe_compact_with_llm`** 替换原 `maybe_compact_session`：

```python
# 原行为（stub_summarize）：
summary = "user: 问题1 | assistant: 回答1 | user: 问题2 | ..."

# 新行为（LLM 调用）：
summary = """
- 用户询问了 RAG 管道设计
- 用户偏好简洁回答
- 已推荐使用 hybrid 检索 + rerank
"""
```

**降级链**：
1. LLM 调用成功 → `source="llm"`
2. LLM 失败（无 Key / 超时 / 错误） → 回退 `stub_summarize`，`source="stub"`
3. 关闭开关 → 完全使用原 `maybe_compact_session`

### 1.3 Token 感知注入

**`retrieve_and_inject_memory`**：

1. 提取最新 user message 作为 query
2. 计算剩余 Token budget = `agent_context_token_budget - estimated_tokens`
3. 若剩余 < `CONTEXT_MEMORY_INJECTION_MIN_BUDGET`（默认 500）→ 跳过
4. 检索 session 长记忆 top_k 条
5. 逐条估算 Token，超过 budget 即停（动态裁剪）
6. 构造 system message 注入

**注入位置**：
- `after_summary`（默认）：在 `[session_summary]` system 消息后、第一个非 system 消息前插入
- `prepend`：在最前插入

### 1.4 集成点

```
Agent Runner:
├── assemble_llm_messages()         # L1 滑窗（已有）
├── retrieve_and_inject_memory()    # L3 Token 感知注入（新）
├── inject_memory_into_messages()   # 注入到 messages
├── [LLM 调用循环]
└── maybe_compact_with_llm()        # L2 LLM 摘要（新，替换 stub）
    └── llm_summarize()
        └── packages.memory.summarize.summarize_messages()  # 复用 #31
```

---

## 2. 配置项

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `CONTEXT_LLM_SUMMARY_ENABLED` | `true` | 启用 LLM 摘要；关闭则回退 stub |
| `CONTEXT_MEMORY_INJECTION_ENABLED` | `true` | 启用长记忆注入 |
| `CONTEXT_MEMORY_INJECTION_TOP_K` | `3` | 注入记忆条数上限 |
| `CONTEXT_MEMORY_INJECTION_MIN_BUDGET` | `500` | 剩余 Token 低于此值时跳过注入 |

---

## 3. 响应体扩展

Agent 响应 `_platform` 新增字段：

```json
{
  "_platform": {
    "context_budget": {
      "budget": 8000,
      "estimated_tokens": 1200,
      "truncated_messages": 0,
      "summary_applied": true
    },
    "memory_injection": {
      "injected": true,
      "memory_count": 2,
      "injected_tokens": 80
    },
    "memory_persisted": true,
    "session_summary": true
  }
}
```

---

## 4. 测试与验收

```bash
# 1. 单元测试
python3 tests/test_context_compress.py
# 期望：15/15 passed

# 2. 验收冒烟（PF 段）
python3 eval/acceptance_smoke.py
# 期望：
# PF  上下文压缩 inject + meta   PASS
```

---

## 5. 代码导航

```
packages/agent/context_compress.py
├── llm_summarize()                  # LLM 摘要（复用 packages.memory.summarize）
├── maybe_compact_with_llm()         # LLM 增强版 compact
├── MemoryInjection                  # 注入结果数据类
├── retrieve_and_inject_memory()     # Token 感知检索 + 注入构造
├── inject_memory_into_messages()    # 注入到 messages 列表
├── compression_platform_meta()      # platform_meta 序列化
└── memory_injection_platform_meta()

packages/agent/runner.py
├── assemble_llm_messages() 后       # L3 注入
└── save_session_state 后             # L2 LLM 摘要
```

---

## 6. 已知限制（面试时主动说）

1. **依赖 LLM Key**：LLM 摘要需 `LLM_API_KEY`；无 Key 时降级为 stub（拼接 snippet）。
2. **额外 LLM 调用成本**：每次摘要触发一次 LLM 调用；可通过 `CONTEXT_LLM_SUMMARY_ENABLED=false` 关闭。
3. **注入仅 session scope**：当前只注入 session 级记忆；user/tenant 级需扩展 `retrieve_and_inject_memory` 的 scope 参数。
4. **无缓存**：每次请求都重新检索；可加进程内 LRU 缓存（同 query 短期复用）。
5. **Token 估算粗略**：用 `len(text) // 4` 估算；非精确 tokenizer。生产应换 tiktoken。

---

## 7. 后续演进

| Issue | 内容 | 依赖 |
|-------|------|------|
| 未来 | user/tenant scope 注入 | — |
| 未来 | 注入结果缓存（LRU） | — |
| 未来 | 精确 Token 估算（tiktoken） | — |
| 未来 | 多轮摘要合并策略 | — |

---

## 8. 面试讲法

1. **三层压缩**：L1 滑窗（已有）→ L2 LLM 摘要（新）→ L3 Token 感知注入（新），逐层递进。
2. **降级链**：LLM 失败自动回退 stub，保证可用性；可开关控制。
3. **Token 感知**：注入条数动态根据剩余 budget 调整，避免溢出。
4. **复用 #31**：LLM 摘要直接调用 `packages.memory.summarize.summarize_messages`，无重复实现。
5. **诚实边界**：仅 session scope 注入；Token 估算粗略（len//4）；无注入缓存。

参考代码：
- `packages/agent/context_compress.py:60` — llm_summarize
- `packages/agent/context_compress.py:110` — maybe_compact_with_llm
- `packages/agent/context_compress.py:140` — retrieve_and_inject_memory
- `packages/agent/runner.py:320` — Agent 集成注入点
