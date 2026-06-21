# Phase K — K8s Helm Chart (#34 / roadmap #50)

> **目标**: 创建生产级 Helm Chart，支持一键 K8s 部署与 HPA 自动扩缩容。

---

## 1. 设计要点

### 1.1 Chart 结构

```
deploy/helm/
├── ai-platform-lab/         # Helm chart root
│   ├── Chart.yaml           # Chart metadata
│   ├── values.yaml          # Default values (all-in-one mode)
│   └── templates/
│       ├── _helpers.tpl         # Named template helpers
│       ├── configmap.yaml       # Non-sensitive env vars
│       ├── secret.yaml          # Sensitive env vars (LLM key, passwords)
│       ├── serviceaccount.yaml  # SA for gateway + worker
│       ├── gateway-deployment.yaml
│       ├── gateway-service.yaml
│       ├── gateway-hpa.yaml
│       ├── worker-deployment.yaml
│       ├── worker-hpa.yaml
│       ├── qdrant-statefulset.yaml
│       ├── qdrant-service.yaml
│       ├── redis-deployment.yaml  (+ PVC + Service)
│       ├── postgres-statefulset.yaml  (+ Services + init ConfigMap)
│       └── ingress.yaml
├── values-prod.yaml         # Production override example
└── README.md
deploy/k8s/
└── kustomization.yaml       # Kustomize overlay (kustomize v5+)
```

### 1.2 部署模式

| 模式 | 说明 | 适用场景 |
|------|------|---------|
| **All-in-One** | 默认；内置 Qdrant + Redis + Postgres | 开发 / 评测环境 |
| **外部依赖** | 关闭内置服务，通过 `*.external.url` 指定 | 生产（使用托管服务）|
| **纯 Gateway** | 只启用 gateway + worker | 仅用 API 路由场景 |

### 1.3 分层配置

```
values.yaml (默认)
    ↓ 覆盖
values-prod.yaml (生产覆盖)
    ↓ 覆盖
--set flags (单次部署参数)
```

---

## 2. 数据模型 / Values 结构

### 2.1 完整 Values 结构

| 路径 | 默认值 | 说明 |
|------|--------|------|
| `global.imageRegistry` | `""` | 镜像仓库前缀 |
| `global.imagePullSecrets` | `[]` | 拉取镜像的 Secret 列表 |
| `gateway.replicas` | `2` | Gateway Pod 副本数 |
| `gateway.image.repository` | `ai-platform-lab/gateway` | 镜像 |
| `gateway.image.tag` | `latest` | 镜像 Tag |
| `gateway.resources.requests.cpu` | `250m` | CPU 请求 |
| `gateway.resources.requests.memory` | `512Mi` | 内存请求 |
| `gateway.service.port` | `8000` | 服务端口 |
| `worker.replicas` | `1` | Worker Pod 副本数 |
| `qdrant.enabled` | `true` | 是否部署内置 Qdrant |
| `qdrant.persistence.size` | `10Gi` | Qdrant 存储大小 |
| `qdrant.external.url` | `""` | 外部 Qdrant URL（设置后禁用内置）|
| `redis.enabled` | `true` | 是否部署内置 Redis |
| `redis.external.url` | `""` | 外部 Redis URL |
| `postgres.enabled` | `true` | 是否部署内置 Postgres |
| `postgres.auth.database` | `aiplatform` | 数据库名 |
| `postgres.external.url` | `""` | 外部 Postgres URL |
| `ingress.enabled` | `false` | 是否启用 Ingress |
| `hpa.gateway.minReplicas` | `2` | Gateway HPA 最小副本 |
| `hpa.gateway.maxReplicas` | `10` | Gateway HPA 最大副本 |
| `hpa.gateway.targetCPUUtilizationPercentage` | `70` | CPU 目标利用率 |
| `hpa.worker.minReplicas` | `1` | Worker HPA 最小副本 |
| `hpa.worker.maxReplicas` | `5` | Worker HPA 最大副本 |
| `hpa.worker.targetCPUUtilizationPercentage` | `80` | Worker CPU 目标 |
| `secrets.llmApiKey` | `""` | LLM API Key（inline，不推荐生产）|
| `secrets.llmApiKeySecretRef.name` | `""` | 引用外部 Secret 名（推荐）|
| `config.LOG_LEVEL` | `INFO` | 日志级别 |
| `config.DEFAULT_MODEL` | `gpt-4o-mini` | 默认 LLM 模型 |

---

## 3. REST API（无新 Python 接口）

Helm Chart 是纯基础设施代码，不引入新 Python API。

部署后，现有 Gateway REST API 暴露在：
```
http://<service-or-ingress-host>:8000/
```

---

## 4. HPA 配置

### 4.1 Gateway HPA

```yaml
# autoscaling/v2
minReplicas: 2   # 保证高可用
maxReplicas: 10  # 防止过度扩缩
metrics:
  - cpu: 70%     # CPU 达到 70% 时触发扩容
scaleDown:
  stabilizationWindowSeconds: 300  # 缩容冷却 5 分钟
scaleUp:
  stabilizationWindowSeconds: 60   # 扩容冷却 1 分钟
```

### 4.2 Worker HPA

```yaml
minReplicas: 1
maxReplicas: 5
metrics:
  - cpu: 80%  # Worker 是 CPU 密集型任务
```

### 4.3 Qdrant Autoscaling

默认关闭（`qdrant.autoscaling.enabled: false`）。
Qdrant 是有状态服务，扩容需要数据迁移，建议手动操作或使用 Qdrant 原生集群模式。

---

## 5. Secret 管理策略

### 5.1 三级策略

| 级别 | 方式 | 推荐场景 |
|------|------|---------|
| **Level 1（不安全）** | `secrets.llmApiKey: "sk-..."` inline 写入 values | 本地开发只 |
| **Level 2（安全）** | `secrets.llmApiKeySecretRef.name: "my-secret"` 引用已有 K8s Secret | 生产基础配置 |
| **Level 3（企业级）** | External Secrets Operator + Vault / AWS Secrets Manager | 企业生产 |

### 5.2 Secret 模板

```yaml
# secret.yaml 生成的 K8s Secret
apiVersion: v1
kind: Secret
metadata:
  annotations:
    helm.sh/resource-policy: keep  # helm uninstall 不删除 Secret
type: Opaque
data:
  llm-api-key: <base64>
  redis-password: <base64>
  postgres-password: <base64>
  jwt-secret: <base64>
```

> 注意：`helm.sh/resource-policy: keep` 防止误 uninstall 时泄露敏感数据。

---

## 6. TLS / Ingress

```yaml
ingress:
  enabled: true
  className: "nginx"
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
  hosts:
    - host: api.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: ai-platform-lab-tls
      hosts:
        - api.example.com
```

前提：集群已安装 cert-manager 并配置 ClusterIssuer。

---

## 7. 升级与回滚

```bash
# 升级（滚动更新，零停机）
helm upgrade ai-platform-lab deploy/helm/ai-platform-lab/ \
  -f deploy/helm/values-prod.yaml

# 查看历史
helm history ai-platform-lab

# 回滚到上一版本
helm rollback ai-platform-lab

# 回滚到指定版本
helm rollback ai-platform-lab 2
```

Deployment 默认使用 `RollingUpdate` 策略，`maxUnavailable: 0, maxSurge: 1` 保证零停机。

---

## 8. 外部依赖集成

### 8.1 外部 Redis（ElastiCache / Redis Cloud）

```yaml
redis:
  enabled: false
  external:
    url: "redis://my-redis.cache.amazonaws.com:6379"
```

### 8.2 外部 Postgres（RDS / Cloud SQL）

```yaml
postgres:
  enabled: false
  external:
    url: "postgresql://user:pass@my-db.rds.amazonaws.com:5432/aiplatform"
```

### 8.3 外部 Qdrant（Qdrant Cloud）

```yaml
qdrant:
  enabled: false
  external:
    url: "https://my-cluster.qdrant.io"
```

---

## 9. CI 集成

### 9.1 GitHub Actions

```yaml
- name: Helm lint
  run: |
    helm lint deploy/helm/ai-platform-lab/
    helm lint deploy/helm/ai-platform-lab/ -f deploy/helm/values-prod.yaml

- name: Helm template (dry-run)
  run: |
    helm template ai-platform-lab deploy/helm/ai-platform-lab/ \
      --set secrets.llmApiKey="test-key" \
      > /tmp/rendered.yaml

- name: Python chart tests
  run: python3 tests/test_helm_chart.py
```

---

## 10. 测试说明

测试文件：`tests/test_helm_chart.py`

| 测试 | 说明 |
|------|------|
| `test_chart_yaml_valid` | Chart.yaml 格式和字段验证 |
| `test_values_yaml_parses` | values.yaml 有效 YAML |
| `test_values_has_required_top_level_keys` | 所有顶层 sections 存在 |
| `test_gateway_values_structure` | gateway 子结构完整 |
| `test_hpa_min_max_valid` | HPA min <= max，默认值正确 |
| `test_all_expected_template_files_exist` | 全部 14 个模板文件存在 |
| `test_template_files_have_go_template_syntax` | 模板含 `{{ }}` 语法 |
| `test_gateway_deployment_has_image_and_probes` | 探针和镜像模板引用 |
| `test_worker_deployment_has_image` | Worker 镜像和 TIER 环境变量 |
| `test_secret_template_references_secretkeyref` | 安全引用模式 |
| `test_hpa_templates_use_autoscaling_v2` | autoscaling/v2 API |
| `test_values_prod_yaml_parses` | 生产 values 有效 |
| `test_readme_exists_and_has_install_command` | README 包含操作命令 |
| `test_qdrant_statefulset_has_pvc` | Qdrant PVC 模板存在 |
| `test_helpers_tpl_defines_required_templates` | 所有 helper 模板定义 |
| `test_configmap_template_uses_config_values` | ConfigMap 遍历 config |
| `test_ingress_template_has_tls_support` | Ingress TLS 支持 |
| `test_chart_yaml_has_keywords_and_maintainers` | Chart 元数据完整 |
| `test_kustomization_yaml_exists` | kustomization.yaml 存在 |
| `test_docs_phase_k_helm_exists` | 设计文档存在 |

运行：
```bash
python3 tests/test_helm_chart.py
```

---

## 11. 代码导航

| 文件 | 说明 |
|------|------|
| `deploy/helm/ai-platform-lab/Chart.yaml` | Chart 元数据 |
| `deploy/helm/ai-platform-lab/values.yaml` | 默认配置（所有可配置项）|
| `deploy/helm/ai-platform-lab/templates/_helpers.tpl` | 命名模板 helpers |
| `deploy/helm/ai-platform-lab/templates/gateway-deployment.yaml` | Gateway Deployment |
| `deploy/helm/ai-platform-lab/templates/gateway-hpa.yaml` | Gateway HPA (autoscaling/v2) |
| `deploy/helm/ai-platform-lab/templates/worker-deployment.yaml` | Worker Deployment |
| `deploy/helm/ai-platform-lab/templates/worker-hpa.yaml` | Worker HPA |
| `deploy/helm/ai-platform-lab/templates/qdrant-statefulset.yaml` | Qdrant StatefulSet + PVC |
| `deploy/helm/ai-platform-lab/templates/redis-deployment.yaml` | Redis Deployment + PVC + Service |
| `deploy/helm/ai-platform-lab/templates/postgres-statefulset.yaml` | Postgres StatefulSet + Services |
| `deploy/helm/ai-platform-lab/templates/secret.yaml` | K8s Secret (b64enc) |
| `deploy/helm/ai-platform-lab/templates/configmap.yaml` | ConfigMap |
| `deploy/helm/ai-platform-lab/templates/ingress.yaml` | Ingress (optional TLS) |
| `deploy/helm/values-prod.yaml` | 生产覆盖示例 |
| `deploy/helm/README.md` | 快速上手文档 |
| `deploy/k8s/kustomization.yaml` | Kustomize overlay |
| `tests/test_helm_chart.py` | 20 个结构验证测试 |

---

## 12. 已知限制

1. **无 cert-manager 默认集成**：TLS 需要用户预先安装 cert-manager 并配置 ClusterIssuer，未内置自动化证书流程。

2. **单节点 Qdrant**：默认 1 个 Qdrant 副本，不支持原生 Qdrant 分布式集群。生产建议使用 Qdrant Cloud 或手动配置分布式模式。

3. **无 PodDisruptionBudget (PDB)**：未配置 PDB，滚动升级时不保证最小可用副本约束。建议为 gateway 添加 `minAvailable: 1` 的 PDB。

4. **无 NetworkPolicy**：Pod 间网络未加固，所有 Pod 可互访。生产建议添加 NetworkPolicy 限制跨服务访问。

5. **无 Service Mesh 集成**：不含 Istio/Linkerd mTLS 配置。流量加密需外部 Service Mesh 支持。

6. **Secret 明文存储风险**：inline secrets (`secrets.llmApiKey: "sk-..."`) 会写入 Helm release history（存储在 K8s Secret 中）。生产必须使用 `secretKeyRef` 或 External Secrets Operator。

7. **无 PodSecurityPolicy/AdmissionWebhook**：未配置 PSP 或 OPA/Kyverno 策略。

8. **Redis 单副本**：内置 Redis 为单副本 Deployment，无主从或 Sentinel 模式，存在单点故障。

---

## 13. 面试讲解要点

1. **Helm Chart vs raw K8s YAML 的优势**
   - 参数化模板：一份 Chart 支持开发/测试/生产多环境，通过 values 覆盖差异化配置
   - Release 管理：helm history / rollback 提供版本控制和回滚能力
   - 依赖管理：Chart.yaml 可声明子 Chart 依赖（如 bitnami/redis）

2. **HPA autoscaling/v2 相比 v1 的改进**
   - v2 支持多指标（CPU + Memory + 自定义指标）
   - v2 支持 behavior 配置（scaleDown/scaleUp 冷却窗口和速率限制）
   - 防止 "flapping"（频繁扩缩）：`stabilizationWindowSeconds: 300`

3. **StatefulSet vs Deployment 的选择**
   - Qdrant/Postgres 用 StatefulSet：稳定网络标识（Pod DNS）、有序部署/缩容、volumeClaimTemplates
   - Redis/Gateway/Worker 用 Deployment：无状态、快速滚动更新

4. **Secret 管理三级策略**
   - Level 1: inline values（仅开发）
   - Level 2: secretKeyRef 引用已有 Secret
   - Level 3: External Secrets Operator + Vault/AWS Secrets Manager（企业级）
   - helm.sh/resource-policy: keep 防止误删包含敏感数据的 Secret

5. **零停机部署**
   - `RollingUpdate` 策略 + `maxUnavailable: 0`
   - Readiness/Liveness 探针确保旧 Pod 流量切断前新 Pod 已就绪
   - HPA minReplicas >= 2 保证单 Pod 故障时仍可用

6. **配置分层与 checksum 注解**
   - ConfigMap + Secret 变更时触发 Deployment 滚动更新
   - `checksum/config: {{ include ... | sha256sum }}` 注解确保配置变更被检测到
   - values.yaml → values-prod.yaml → --set flags 三层覆盖机制

7. **Kustomize 与 Helm 的互补**
   - Helm 擅长参数化模板和 release 管理
   - Kustomize 擅长无模板的 overlay patch（GitOps 友好）
   - `deploy/k8s/kustomization.yaml` 演示了 kustomize v5+ helmCharts 集成

---

## 14. 集成指引（给 parent agent）

### settings.py 变更
无 — Helm values 是 K8s 配置，不影响 Python settings。

### main.py 变更
无 — Helm 部署现有 Gateway 容器，不修改应用代码。

### .env.example 变更
无 — Helm values.yaml 已覆盖所有环境变量定义。

### README.md 建议新增章节

```markdown
## Kubernetes Deployment

One-command K8s deployment via Helm:

\`\`\`bash
helm install ai-platform-lab deploy/helm/ai-platform-lab/ \\
  --set secrets.llmApiKey="sk-your-key" \\
  --namespace ai-platform-lab --create-namespace
\`\`\`

See [deploy/helm/README.md](deploy/helm/README.md) for full configuration.
```

### roadmap.md 建议更新
Phase K (#50) → ✅ Completed: K8s Helm Chart with HPA auto-scaling
