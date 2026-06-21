# 可粘贴的 Issue 正文 — Phase J & K

> 维护者使用：将以下每个 Issue 的 **标题** 和 **正文** 复制到 [GitHub New Issue](https://github.com/xingyun0812/ai-platform-lab/issues/new) 创建。
> 贡献者使用：在 [Issues 页面](https://github.com/xingyun0812/ai-platform-lab/issues) 认领未分配的 Issue。

创建顺序建议：按 Phase 顺序创建（#45→#46→#47→#48→#49→#50→#51→#52），方便编号连续。

Labels 建议（在 GitHub Issue 右侧添加）：
- `phase-task` + `feature` + `phase-j` (或 `phase-k`)

---

## #45 — Python SDK

**标题**：`[Phase J] Python SDK — 封装 Gateway/Agent/RAG API`

**正文**：

### 目标

封装平台 REST API 为 Python SDK，让外部开发者可以像用 OpenAI SDK 一样调用 ai-platform-lab。

### 验收标准

- [ ] `sdk/python/` 目录：`ai_platform_lab/` 包
  - `client.py` — 主 Client 类（同步 + 异步）
  - `resources/` — chat / rag / agent / embedding / memory / orchestrator 各 1 个资源类
  - `exceptions.py` — 异常体系
  - `__init__.py` — 导出
- [ ] 参考 OpenAI SDK 风格：`client.chat.completions.create(...)`
- [ ] 类型注解完整（py.typed）
- [ ] 单测 ≥ 15 个（mock HTTP，不依赖真实服务）
- [ ] `pyproject.toml` 可 `pip install -e sdk/python`
- [ ] README.md SDK 使用示例
- [ ] 设计文档 `docs/phase-j-python-sdk.md`

### 实施计划

**新增文件**：
- `sdk/python/ai_platform_lab/__init__.py`
- `sdk/python/ai_platform_lab/client.py`
- `sdk/python/ai_platform_lab/resources/{chat,rag,agent,embedding,memory,orchestrator}.py`
- `sdk/python/ai_platform_lab/exceptions.py`
- `sdk/python/pyproject.toml`
- `tests/test_sdk.py`
- `docs/phase-j-python-sdk.md`

**修改文件**（父 Agent 集成）：
- `README.md` — SDK 章节
- `docs/roadmap.md` — 标记 ✅

### 依赖

无

### 预估工期

3w

### 测试计划

1. `python3 tests/test_sdk.py` — ≥15 用例
2. `cd sdk/python && pip install -e . && python -c "from ai_platform_lab import Client"`
3. 端到端：启动 gateway + SDK 调用 `/v1/chat/completions`

---

## #46 — Console V2

**标题**：`[Phase J] Console V2 — React 管理界面（替换 HTML stub）`

**正文**：

### 目标

用 React 实现真正的管理 UI，替换现有 `apps/console/templates/index.html` stub。串联所有后端能力（Agent / RAG / Memory / Orchestrator / 监控）。

### 验收标准

- [ ] `console-v2/` 目录：React + Vite + TypeScript
- [ ] 页面：
  - Dashboard — 平台总览（QPS / token 消耗 / 错误率）
  - Tenants — 租户管理
  - Agents — Agent 定义 + 版本 + 委托测试
  - RAG — 知识库管理 + 查询测试
  - Memory — 长记忆查看 + 搜索
  - Orchestrator — 工作流可视化编辑 + 执行
  - Audit — 审计日志 + 动作分级
  - Settings — 配置管理
- [ ] 登录页（JWT 鉴权）
- [ ] 单测 ≥ 10 个（组件渲染 + API mock）
- [ ] `npm run build` 产出静态文件
- [ ] 部署到 `apps/console/static/` 由 FastAPI 托管
- [ ] 设计文档 `docs/phase-j-console-v2.md`

### 实施计划

**新增文件**：
- `console-v2/package.json` / `vite.config.ts` / `tsconfig.json`
- `console-v2/src/{App,main,router}.tsx`
- `console-v2/src/pages/{Dashboard,Tenants,Agents,RAG,Memory,Orchestrator,Audit,Settings}.tsx`
- `console-v2/src/components/` — 复用组件
- `console-v2/src/api/` — API 封装
- `tests/test_console_v2.py` — 部署后 smoke test
- `docs/phase-j-console-v2.md`

**修改文件**：
- `apps/console/routes.py` — 托管 build 产物
- `apps/gateway/main.py` — mount console
- `README.md` + `docs/roadmap.md`

### 依赖

无（但 #45 SDK 可选提供 API 封装）

### 预估工期

4w

---

## #47 — 评测数据集 + 离线 Pipeline

**标题**：`[Phase J] 评测数据集扩充 + CI 评测门禁`

**正文**：

### 目标

扩充基准数据集到 ≥ 200 条，建立 CI 评测门禁（PR 时自动跑评测，质量回退则 block merge）。

### 验收标准

- [ ] `eval/baselines/` 扩充到 ≥ 200 条（覆盖 RAG / Agent / 安全）
- [ ] `eval/run.py` 支持分类评测 + 趋势对比
- [ ] `.github/workflows/eval.yml` — PR 时自动跑评测
- [ ] 评测门禁：分数低于 main 分支 5% 则 block
- [ ] 评测报告：Markdown + JSON 双格式
- [ ] 设计文档 `docs/phase-j-eval-pipeline.md`

### 实施计划

**新增文件**：
- `eval/baselines/rag_extended.jsonl` — RAG 用例 100 条
- `eval/baselines/agent_scenarios.jsonl` — Agent 用例 50 条
- `eval/baselines/safety.jsonl` — 安全用例 50 条
- `eval/pipeline.py` — 评测 pipeline
- `eval/gate.py` — 门禁逻辑
- `.github/workflows/eval.yml`
- `tests/test_eval_pipeline.py`
- `docs/phase-j-eval-pipeline.md`

### 依赖

无

### 预估工期

2w

---

## #48 — 在线质量监控 + 反馈飞轮

**标题**：`[Phase J] 在线质量监控 + 反馈飞轮（Bad Case → Eval → Prompt 迭代）`

**正文**：

### 目标

实时捕获线上 Bad Case（用户点踩 / 低分），自动入库 → 离线评测 → 生成 Prompt 优化建议 → 推送 Prompt A/B 实验。

### 验收标准

- [ ] `packages/feedback/` — 反馈采集模块
  - 点赞 / 点踩 API
  - Bad Case 自动入库（Postgres）
- [ ] `packages/quality_monitor/` — 质量监控
  - 实时聚合（Redis 滑窗）
  - 异常检测（分数骤降告警）
- [ ] `packages/feedback_loop/` — 反馈飞轮
  - Bad Case → 评测集
  - 评测结果 → Prompt 优化建议（调 LLM 生成）
  - 建议 → Prompt A/B 实验自动创建
- [ ] REST API：`/internal/feedback/*` + `/internal/quality/*`
- [ ] 单测 ≥ 12 个
- [ ] 设计文档 `docs/phase-j-feedback-loop.md`

### 实施计划

**新增文件**：
- `packages/feedback/__init__.py` + `store.py` + `api.py`
- `packages/quality_monitor/__init__.py` + `aggregator.py` + `alerts.py`
- `packages/feedback_loop/__init__.py` + `pipeline.py`
- `apps/gateway/feedback_routes.py`
- `tests/test_feedback.py` + `tests/test_quality_monitor.py`
- `docs/phase-j-feedback-loop.md`

### 依赖

- #46 Console V2（反馈 UI 入口）
- #47 评测 Pipeline（Bad Case 入评测集）

### 预估工期

3w

---

## #49 — 对象存储接入

**标题**：`[Phase K] 对象存储接入（S3/OSS 替换本地文件）`

**正文**：

### 目标

将 RAG 上传文件、审计日志归档、Memory 快照等从本地文件迁移到 S3/OSS，支撑生产部署。

### 验收标准

- [ ] `packages/storage/` — 存储抽象
  - `StorageBackend` 接口（put/get/delete/list）
  - `LocalStorageBackend`（现有，回退）
  - `S3StorageBackend`（boto3）
  - `OSStorageBackend`（oss2，阿里云）
- [ ] 配置化切换：`STORAGE_BACKEND=local|s3|oss`
- [ ] RAG 上传走对象存储
- [ ] 审计日志归档走对象存储
- [ ] 单测 ≥ 10 个（mock boto3/oss2）
- [ ] 设计文档 `docs/phase-k-object-storage.md`

### 依赖

无

### 预估工期

1w

---

## #50 — K8s Helm Chart

**标题**：`[Phase K] K8s Helm Chart（Gateway/Worker/Qdrant + HPA）`

**正文**：

### 目标

提供生产级 Helm Chart，支持一键部署到 K8s，含 HPA 自动伸缩。

### 验收标准

- [ ] `deploy/helm/ai-platform-lab/` — Helm Chart
  - `Chart.yaml` + `values.yaml`
  - `templates/{gateway,worker,qdrant,redis,postgres}.yaml`
  - HPA for Gateway + Worker
  - Ingress + TLS
  - Secret 管理（引用外部 secret manager）
- [ ] `deploy/helm/values-prod.yaml` — 生产配置示例
- [ ] `deploy/k8s/` — Kustomize overlay（可选）
- [ ] 文档 `docs/phase-k-helm.md`（部署 + 升级 + 回滚）
- [ ] CI：`helm lint` + `helm template` 验证

### 依赖

无

### 预估工期

4w

---

## #51 — 多 AZ 高可用

**标题**：`[Phase K] 多 AZ 高可用（跨 AZ 部署 + Qdrant 副本 + Redis Sentinel）`

**正文**：

### 目标

支持跨 AZ 部署，单 AZ 故障不影响服务。Qdrant 副本 + Redis Sentinel + Postgres streaming replication。

### 验收标准

- [ ] Gateway 多 AZ 部署文档
- [ ] Qdrant 副本配置（read-only 节点）
- [ ] Redis Sentinel 故障转移
- [ ] Postgres streaming replication
- [ ] 跨 AZ 配额同步验证（Redis 共享已支持，需验证）
- [ ] 故障注入测试：杀单 AZ 节点，服务可用
- [ ] 文档 `docs/phase-k-multi-az.md`

### 依赖

- #50 K8s Helm Chart

### 预估工期

3w

---

## #52 — GPU 弹性调度

**标题**：`[Phase K] GPU 弹性调度（Embedding/Rerank GPU 节点 + 自动伸缩）`

**正文**：

### 目标

为 Embedding / Rerank 服务提供 GPU 节点池，按负载自动伸缩，降低成本。

### 验收标准

- [ ] GPU 节点池配置（K8s nodeSelector + taints）
- [ ] Embedding 服务 GPU 部署模板
- [ ] Rerank 服务 GPU 部署模板
- [ ] HPA 基于 QPS + GPU 利用率
- [ ] 冷启动优化（模型预加载）
- [ ] 成本看板：GPU 小时费 + 节省率
- [ ] 文档 `docs/phase-k-gpu-scheduling.md`

### 依赖

- #50 K8s Helm Chart

### 预估工期

3w

---

## 创建后的维护动作

维护者创建完所有 Issue 后：

1. **加 Labels**：每个 Issue 加 `phase-task` + `feature` + `phase-j`/`phase-k`
2. **设置 Milestone**：创建 `Phase J` / `Phase K` Milestone，关联对应 Issue
3. **关联依赖**：在 #48 评论 `Depends on: #46, #47`；在 #51/#52 评论 `Depends on: #50`
4. **置顶 roadmap**：在仓库 About 链接 `docs/roadmap.md`
5. **Discussions 开启**：Settings → Features → Discussions，方便提问

贡献者认领流程见 [CONTRIBUTING.md](../CONTRIBUTING.md)。
