# ai-platform-lab 项目状态总览

> **最后更新**：2026-06-22
> **当前状态**：Phase A-K 全部完成，484 单测全通过，已推送 GitHub
> **主分支**：`main` @ `98be9d1`

---

## 1. 项目定位

一个面向学习与面试的 **AI 平台参考实现**，覆盖从模型网关到生产基础设施的完整链路。对标大厂 AI 平台架构，按 Phase 渐进交付。

## 2. 完成度总览

| 层次 | 完成度 | 强项 | 主要缺口 |
|------|--------|------|---------|
| 模型服务层 | ~95% | Gateway、路由、熔断、计费、语义缓存、Embedding 服务 | 多模态 Embedding |
| 基础设施层 | ~90% | 对象存储、K8s Helm、多 AZ、GPU 调度 | 跨 Region、Service Mesh |
| 能力中台 | ~90% | RAG、Prompt 版本化+A/B、长记忆、MCP、上下文压缩 | — |
| Agent 应用层 | ~90% | 控制流编排、Multi-Agent、生命周期、HITL | — |
| AgentOps 治理 | ~90% | 沙箱、分级审计、PII、OAuth2/mTLS | 在线评测飞轮（已有反馈飞轮） |
| 开发者体验 | ~90% | Python SDK、Console V2、评测 Pipeline、反馈飞轮 | Console Demo/SDK smoke 待补全 |

## 3. Phase 完成历史线

```
Phase A — 内测基线          ✅ tag: phase-a-internal-beta
Phase B1 — 计费             ✅ tag: phase-b1-billing
Phase B2 — 并行化           ✅ tag: phase-b2-parallel
Phase B3 — Rerank + Canary  ✅ tag: phase-b3-rerank-canary
Phase C — 平台能力          ✅ tag: phase-c-platform
Phase D — 运维治理          ✅ tag: phase-d-ops
Phase E — Agent 质量        ✅ (无单独 tag，已合并)
Phase F — 能力中台补全      ✅ tag: phase-f-capabilities     ← 今日
Phase G — Embedding 服务    ✅ tag: phase-g-embedding          ← 今日
Phase H — Agent 高阶能力    ✅ tag: phase-h-agent-advanced     ← 今日
Phase I — 安全合规          ✅ tag: phase-i-security            ← 今日
Phase J — 开发者体验        ✅ tag: phase-j-developer-experience + phase-jk-complete ← 今日
Phase K — 生产基础设施      ✅ tag: phase-k-infra-base + phase-jk-complete            ← 今日
```

## 4. 代码规模

| 指标 | 数量 |
|------|------|
| **累计单测** | **484 个全通过**（25 套件） |
| **packages 模块 Python 文件** | 130 个 |
| **REST 路由文件** | 20 个 |
| **测试套件** | 25 个 |
| **Phase 设计文档** | 31 篇 |
| **GitHub Tags** | 13 个 |
| **GitHub Issues** | 8 个（全部关闭） |

## 5. 能力清单（按 Phase）

### Phase F — 能力中台补全 (#29-#34)

| Issue | 能力 | 关键文件 |
|-------|------|---------|
| #29 | Prompt 版本化 + 渲染 | `packages/prompt/` |
| #30 | Prompt A/B 实验 | `packages/prompt/experiment.py` |
| #31 | 长记忆持久化 | `packages/memory/` |
| #32 | MCP 真实集成 | `packages/mcp/` |
| #33 | 上下文压缩 | `packages/agent/context_compress.py` |
| #34 | 语义缓存 | `packages/semantic_cache/` |

### Phase G — Embedding 独立服务 (#35)

| Issue | 能力 | 关键文件 |
|-------|------|---------|
| #35 | Provider 抽象 + LRU 缓存 | `packages/embedding/` |

### Phase H — Agent 高阶能力 (#37-#40)

| Issue | 能力 | 关键文件 |
|-------|------|---------|
| #37 | DAG 控制流编排 | `packages/agent/orchestrator/` |
| #38 | Multi-Agent 协作 | `packages/agent/multi_agent/` |
| #39 | Agent 版本 + 灰度 + 回滚 | `packages/agent/lifecycle/` |
| #40 | HITL 完整工作流 | `packages/hitl/` |

### Phase I — 安全合规 (#41-#44)

| Issue | 能力 | 关键文件 |
|-------|------|---------|
| #41 | 沙箱容器隔离 | `packages/sandbox/` |
| #42 | 动作分级审计 | `packages/audit/action_levels.py` |
| #43 | PII 脱敏 + 内容安全 | `packages/pii/` |
| #44 | OAuth2 / mTLS | `packages/auth/oauth2.py` + `mtls.py` |

### Phase J — 开发者体验 (#29-#32, GitHub)

| Issue | 能力 | 关键文件 |
|-------|------|---------|
| #29 | Python SDK | `sdk/python/ai_platform_lab/` |
| #30 | Console V2 (React) | `console-v2/` |
| #31 | 评测 Pipeline + CI 门禁 | `eval/pipeline.py` + `.github/workflows/eval.yml` |
| #32 | 反馈飞轮 | `packages/feedback/` + `packages/quality_monitor/` + `packages/feedback_loop/` |

### Phase K — 生产基础设施 (#33-#36, GitHub)

| Issue | 能力 | 关键文件 |
|-------|------|---------|
| #33 | 对象存储 (local/s3/oss) | `packages/storage/` |
| #34 | K8s Helm Chart | `deploy/helm/ai-platform-lab/` |
| #35 | 多 AZ 高可用 | `deploy/helm/values-multi-az.yaml` + templates |
| #36 | GPU 弹性调度 | `deploy/helm/values-gpu.yaml` + templates |

## 6. 今日提交记录

| Commit | 内容 |
|--------|------|
| `e75e50d` | Phase F — 能力中台补全 (#29-#34) |
| `35b6ff6` | Phase G — Embedding 独立服务 (#35) |
| `6f732cc` | Phase H — Agent 高阶能力 (#37-#40) |
| `36c1ac6` | Phase I — 安全合规 (#41-#44) |
| `2119cc0` | 协作流程：CONTRIBUTING + Issue/PR 模板 |
| `69fb6ee` | Roadmap 关联 GitHub Issues |
| `1dc1d5d` | Phase J/K 第一波：SDK + Console + Eval + Storage + Helm |
| `e5623e7` | Phase J/K 第二波：反馈飞轮 + 多AZ + GPU |
| `98be9d1` | chore: eval CLI 扩展 + kustomize overlay |

## 7. 关键设计决策

### 7.1 向后兼容
- 所有新功能 **opt-in**（默认 `false`），不破坏现有行为
- `packages/agent/hitl.py` 改为 shim 委托到新 `packages/hitl/`
- OAuth2/mTLS 默认关闭，保持 JWT HS256 鉴权

### 7.2 共享文件保护
- `main.py` / `settings.py` / `.env.example` 等共享文件由父 Agent 统一集成
- 子 Agent 只创建自己独有的文件（package + routes + tests + docs）
- 避免 PR 合并时写冲突

### 7.3 Python 3.9 兼容
- 所有文件 `from __future__ import annotations`
- 测试用 `importlib.util` 加载模块避免 dataclass 链问题
- 避免 `datetime.UTC`（用 `datetime.utcnow()`）

### 7.4 测试要求
- 每个新模块 ≥ 10 个单测
- 无外部依赖（无 LLM API、无 Postgres、无 Redis）可跑通
- S3/OSS 用 mock，不调真实云

## 8. 协作流程

### Issue 驱动开发
```
roadmap.md → GitHub Issue → feature branch → PR → merge → tag
```

- **认领 Issue**：在 [Issues](https://github.com/xingyun0812/ai-platform-lab/issues) 评论认领
- **分支命名**：`feat/issue-<N>-<short-name>`
- **Commit 规范**：Conventional Commits（`feat:` / `fix:` / `docs:`）
- **PR 模板**：`.github/PULL_REQUEST_TEMPLATE.md`
- **合并策略**：Squash merge

### 文档
- [CONTRIBUTING.md](../CONTRIBUTING.md) — 协作指南
- [.github/ISSUE_TEMPLATE/](../.github/ISSUE_TEMPLATE/) — Issue 模板
- [docs/issues-backlog.md](./issues-backlog.md) — #45-#52 可粘贴 Issue 正文
- [docs/roadmap.md](./roadmap.md) — 完整 Roadmap

## 9. 已知限制（诚实声明）

### 计费与用量
- 按 token 落库 + 日/月预算拦截；未区分 input/output 单价
- 按请求次数日配额与 token 预算并存

### 可用性
- 单进程 Gateway（K8s 部署后可水平扩展）
- Qdrant 单节点（多 AZ 配置后可副本）

### 安全
- 沙箱默认关闭（`SANDBOX_ENABLED=false`）
- OAuth2/mTLS 默认关闭，需手动启用

### 开发者体验
- Console V2：**已 build + 挂载** → http://127.0.0.1:8000/console/
- Demo + SDK smoke：**已可跑** → `./eval/platform_demo.sh --no-llm`、`eval/sdk_smoke.py`（见 [demo-walkthrough.md](./demo-walkthrough.md)）
- 面试口述稿：[interview-narrative.md](./interview-narrative.md)
- SDK 未发布到 PyPI（需 `pip install -e sdk/python`）
- 评测 Pipeline 的 live 用例需 LLM API key

## 10. 下一步建议

> **Phase L**：Wave1 ✅、Wave2 ✅、**#58 Agent 三率 ✅**；下一步 **#59 Vertical**。GitHub Issue [#37～#47](https://github.com/xingyun0812/ai-platform-lab/issues?q=label%3Aphase-l)。

### 建议执行顺序（ROI）

| 优先级 | Issue | 内容 | 状态 |
|--------|-------|------|------|
| 🥇 P1 | #62-console | Console 集成跑真 | ✅ |
| 🥇 P1 | #62、#63 | Demo 脚本 + SDK smoke | ✅ |
| P5 并行 | #53 | 文档状态对齐 | ✅ |
| 🥈 P2 | #54～#57 | RAG 工程深度 | ✅ |
| 🥉 P3 | #58 | Agent 三率指标 | ✅ |
| 🥉 P3 | #59 | Agent Vertical | ✅ |
| 🥉 P3 | #60 | Baseline + CI gate | ✅ |
| P4 | #61 | 反馈飞轮 E2E | ✅ |
| P4 | #62 | Demo + 面试叙事 | ✅ **Phase L 完成** |

Issue 正文与 GitHub 映射见 [issues-backlog-phase-l.md](./issues-backlog-phase-l.md)。

> **Phase L**：#53～#63 全部交付，可打 tag `phase-l-engineering-depth`。

## 11. 核心面试讲法

> **一句话**：这是一个从模型网关到生产基础设施的完整 AI 平台参考实现，按 Phase 渐进交付，484 个单测全通过。

**分层讲法**：
1. **模型服务层**：Gateway + 路由 + 熔断 + 计费 + 语义缓存 + Embedding 服务
2. **能力中台**：RAG + Prompt 版本化/A/B + 长记忆 + MCP + 上下文压缩
3. **Agent 应用层**：控制流编排 + Multi-Agent + 生命周期 + HITL
4. **AgentOps 治理**：沙箱 + 分级审计 + PII + OAuth2/mTLS
5. **开发者体验**：Python SDK + Console V2 + 评测 Pipeline + 反馈飞轮
6. **生产基础设施**：对象存储 + K8s Helm + 多 AZ + GPU 调度

**诚实边界**：
- 单进程 Gateway（K8s 可扩展）
- Rerank / LLM Judge 仍为 stub（Phase L #54～#56）
- 多 AZ/GPU 配置未实际部署验证

---

**项目仓库**：https://github.com/xingyun0812/ai-platform-lab
**最新提交**：`98be9d1` @ main
**最新 Tag**：`phase-jk-complete`
