# AI Platform Lab — Helm Chart

Production-grade Kubernetes deployment for AI Platform Lab using Helm.

## Prerequisites

- Kubernetes 1.24+
- Helm 3.10+
- `kubectl` configured for your cluster

## Quick Start

### 1. Install with defaults (all-in-one, includes Qdrant + Redis + Postgres)

```bash
helm install ai-platform-lab deploy/helm/ai-platform-lab/ \
  --set secrets.llmApiKey="sk-your-api-key-here" \
  --namespace ai-platform-lab \
  --create-namespace
```

### 2. Install with production overrides

```bash
helm install ai-platform-lab deploy/helm/ai-platform-lab/ \
  -f deploy/helm/values-prod.yaml \
  --namespace ai-platform-lab \
  --create-namespace
```

### 3. Install with custom values file

```bash
# Edit values first
cp deploy/helm/ai-platform-lab/values.yaml my-values.yaml
vim my-values.yaml

helm install ai-platform-lab deploy/helm/ai-platform-lab/ \
  -f my-values.yaml \
  --namespace ai-platform-lab \
  --create-namespace
```

## Configuration

Edit `deploy/helm/ai-platform-lab/values.yaml` or use `-f <overrides-file>.yaml`.

### Key configuration options

| Parameter | Default | Description |
|-----------|---------|-------------|
| `gateway.replicas` | `2` | Number of gateway pods |
| `worker.replicas` | `1` | Number of worker pods |
| `qdrant.enabled` | `true` | Deploy built-in Qdrant |
| `qdrant.persistence.size` | `10Gi` | Qdrant storage size |
| `redis.enabled` | `true` | Deploy built-in Redis |
| `postgres.enabled` | `true` | Deploy built-in Postgres |
| `hpa.gateway.maxReplicas` | `10` | Max gateway HPA replicas |
| `hpa.worker.maxReplicas` | `5` | Max worker HPA replicas |
| `ingress.enabled` | `false` | Enable Ingress |
| `secrets.llmApiKey` | `""` | LLM API key (inline, insecure) |

### Using External Dependencies

For production, point to existing Redis/Postgres/Qdrant:

```yaml
# values-external.yaml
qdrant:
  enabled: false
  external:
    url: "http://qdrant-cluster.internal:6333"

redis:
  enabled: false
  external:
    url: "redis://redis-cluster.internal:6379"

postgres:
  enabled: false
  external:
    url: "postgresql://user:pass@postgres-cluster.internal:5432/aiplatform"
```

```bash
helm install ai-platform-lab deploy/helm/ai-platform-lab/ -f values-external.yaml
```

### Secret Management

**Option 1 — Inline (dev only)**:
```bash
helm install ai-platform-lab deploy/helm/ai-platform-lab/ \
  --set secrets.llmApiKey="sk-..." \
  --set secrets.jwtSecret="supersecret"
```

**Option 2 — External Secret ref (recommended for production)**:
```yaml
secrets:
  llmApiKeySecretRef:
    name: "my-external-secret"
    key: "llm-api-key"
```

**Option 3 — Kubernetes External Secrets / Vault**:
Pre-create a secret then reference via `existingSecret` fields.

## Upgrade

```bash
helm upgrade ai-platform-lab deploy/helm/ai-platform-lab/ \
  -f deploy/helm/values-prod.yaml \
  --namespace ai-platform-lab
```

## Rollback

```bash
# List revisions
helm history ai-platform-lab --namespace ai-platform-lab

# Rollback to previous revision
helm rollback ai-platform-lab --namespace ai-platform-lab

# Rollback to specific revision
helm rollback ai-platform-lab 3 --namespace ai-platform-lab
```

## Uninstall

```bash
helm uninstall ai-platform-lab --namespace ai-platform-lab
```

> **Note**: PersistentVolumeClaims are NOT automatically deleted. To clean up storage:
> ```bash
> kubectl delete pvc -l app.kubernetes.io/instance=ai-platform-lab --namespace ai-platform-lab
> ```

## CI Integration

### Helm lint

```bash
helm lint deploy/helm/ai-platform-lab/
helm lint deploy/helm/ai-platform-lab/ -f deploy/helm/values-prod.yaml
```

### Helm template (dry run)

```bash
helm template ai-platform-lab deploy/helm/ai-platform-lab/ \
  --set secrets.llmApiKey="test" \
  > /tmp/rendered-templates.yaml

# Validate with kubeval
kubeval /tmp/rendered-templates.yaml
```

### GitHub Actions example

```yaml
- name: Helm lint
  run: helm lint deploy/helm/ai-platform-lab/

- name: Helm template
  run: helm template ai-platform-lab deploy/helm/ai-platform-lab/ > /dev/null
```

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  Ingress (optional)              │
└───────────────────────┬─────────────────────────┘
                        │
              ┌─────────▼──────────┐
              │  Gateway Deployment │ (HPA: 2-10 pods)
              │    port 8000        │
              └──────┬──────┬──────┘
                     │      │
           ┌─────────▼─┐  ┌─▼──────────────┐
           │   Worker   │  │  Qdrant SS      │
           │Deployment  │  │  port 6333/6334 │
           │(HPA: 1-5)  │  └────────────────┘
           └─────────┬──┘
                     │
        ┌────────────┴────────────┐
   ┌────▼────┐               ┌────▼────┐
   │  Redis  │               │Postgres │
   │Deployment│              │ SS      │
   └─────────┘               └─────────┘
```

## Troubleshooting

```bash
# Check pod status
kubectl get pods -n ai-platform-lab

# Check gateway logs
kubectl logs -l app.kubernetes.io/component=gateway -n ai-platform-lab

# Check HPA status
kubectl get hpa -n ai-platform-lab

# Describe failing pod
kubectl describe pod <pod-name> -n ai-platform-lab
```
