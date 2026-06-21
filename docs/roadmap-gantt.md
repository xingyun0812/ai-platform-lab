# Roadmap 甘特图 — ai-platform-lab

> Phase F ~ Phase K 演进计划（对标「Agent 平台架构全景」目标架构）

```mermaid
gantt
    title "ai-platform-lab vs 目标架构 Roadmap"
    dateFormat  YYYY-MM
    axisFormat  %Y-%m

    section "Phase F — 能力中台补全"
    "Prompt 版本化 + A/B 测试 + 审计"     :f1, 2026-07, 3w
    "长记忆持久化 (Postgres+Redis 双写)"  :f2, 2026-07, 2w
    "MCP 完整集成 (工具注册·鉴权·版本)"   :f3, 2026-08, 4w
    "上下文压缩策略 (滑窗·摘要·注入)"     :f4, 2026-08, 3w

    section "Phase G — 模型服务增强"
    "语义缓存 (Embedding相似度+Redis)"    :g1, 2026-07, 3w
    "Embedding 独立服务治理"              :g2, 2026-08, 2w
    "多模态 Embedding 支持"               :g3, 2026-09, 3w

    section "Phase H — Agent 高阶能力"
    "控制流编排引擎 (DAG/循环/条件分支)"  :h1, 2026-08, 4w
    "Multi-Agent 协作框架 (委托·通信)"   :h2, 2026-09, 5w
    "Agent 生命周期管理 (灰度·蓝绿部署)"  :h3, 2026-09, 3w
    "HITL 完整工作流 (审批·上报·监督)"   :h4, 2026-10, 3w

    section "Phase I — 安全与合规深化"
    "沙箱容器隔离 (gVisor / seccomp)"    :i1, 2026-09, 3w
    "动作分级 + 审计日志强化"             :i2, 2026-09, 2w
    "PII 脱敏 + 内容安全策略"             :i3, 2026-10, 3w
    "OAuth2 / mTLS 生产级鉴权"           :i4, 2026-10, 4w

    section "Phase J — 平台开发者体验"
    "开发者 SDK (Python/TS)"             :j1, 2026-10, 4w
    "API Playground / Console V2"        :j2, 2026-11, 4w
    "评测数据集 + 离线 Pipeline"           :j3, 2026-11, 3w
    "在线质量监控 + 反馈飞轮"             :j4, 2026-12, 4w

    section "Phase K — 生产基础设施"
    "对象存储 (S3/OSS 集成)"              :k1, 2026-10, 2w
    "GPU 弹性资源调度"                    :k2, 2026-11, 4w
    "K8s Helm Chart + 多 AZ"             :k3, 2026-12, 6w
```
