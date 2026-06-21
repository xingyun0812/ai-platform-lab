# Phase K — GPU 弹性调度 (GPU Elastic Scheduling)

**Issue #36 / Roadmap #52** | Status: Implemented

## Overview

GPU elastic scheduling adds dedicated GPU node pools for the Embedding and Rerank services. Instead of routing to external APIs, both services run as **separate GPU-aware Kubernetes Deployments** with multi-metric Horizontal Pod Autoscaling (HPA) that reacts to CPU utilization, GPU utilization (via DCGM Exporter), and gateway QPS — reducing idle cost by scaling to 1 replica and scaling out during peak load.

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                   Kubernetes Cluster                            │
│                                                                 │
│  ┌──────────────┐     ┌──────────────┐     ┌───────────────┐  │
│  │  Gateway Pods│     │  Worker Pods │     │  Redis / PG   │  │
│  │  (CPU nodes) │     │  (CPU nodes) │     │  (CPU nodes)  │  │
│  │  replicas: 4 │     │  replicas: 3 │     │               │  │
│  └──────┬───────┘     └──────────────┘     └───────────────┘  │
│         │ ClusterIP routing                                     │
│         ▼                                                       │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │              GPU Node Pool (accelerator: nvidia)          │  │
│  │  ┌─────────────────────┐   ┌───────────────────────────┐ │  │
│  │  │  Embedding Service  │   │    Rerank Service         │ │  │
│  │  │  port 8100          │   │    port 8200              │ │  │
│  │  │  replicas: 1–8      │   │    replicas: 1–4          │ │  │
│  │  │  1× GPU (T4/A100)   │   │    1× GPU (T4/A100)       │ │  │
│  │  │  HPA: CPU+GPU+QPS   │   │    HPA: CPU+GPU           │ │  │
│  │  └─────────────────────┘   └───────────────────────────┘ │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌───────────────────────────┐                                 │
│  │  DCGM Exporter DaemonSet  │  (monitoring namespace)        │
│  │  + Prometheus Adapter     │                                 │
│  └───────────────────────────┘                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Design Points

### 1. Separate Deployments, Not In-Process

Embedding and Rerank are **separate Kubernetes Deployments** (not code loaded inside the gateway process). This allows:
- Independent GPU scheduling and node affinity
- Independent HPA per service
- Zero-downtime rolling updates for each model independently
- Gateway simply proxies via ClusterIP (`<release>-embedding:8100`, `<release>-rerank:8200`)

### 2. Multi-Metric HPA (v2)

The embedding HPA uses **three scaling signals**:

| Signal | Source | Target |
|---|---|---|
| CPU utilization | `resource.cpu` | 70% |
| GPU utilization | `containerResource nvidia.com/gpu` | 70% |
| Gateway QPS | External metric `gateway_embedding_qps_per_replica` | 100 req/s |

HPA picks the metric that requires the most replicas (conservative scale-up). Scale-down is stabilized for 5 minutes to prevent GPU cold-start thrashing.

### 3. GPU Node Taint + Toleration Pattern

GPU nodes are tainted `nvidia=:NoSchedule`. Only pods that explicitly tolerate this taint (embedding, rerank, DCGM exporter) land on GPU nodes. CPU workloads (gateway, worker, Redis, PG) never land on expensive GPU nodes.

```yaml
# values-gpu.yaml (applied to embedding + rerank)
tolerations:
  - key: nvidia
    operator: Exists
    effect: NoSchedule
nodeSelector:
  accelerator: nvidia
```

### 4. Model Warmup (Cold Start Mitigation)

Each GPU pod runs an **initContainer** that:
1. Downloads the model weights to `/model-cache` shared volume
2. Runs a dummy inference to trigger CUDA kernel compilation
3. Main container mounts the same volume → model is already in RAM on first request

Without warmup: first request latency 30–90 seconds.  
With warmup: first request latency < 100ms (model already loaded).

### 5. Dev (T4) vs Prod (A100) Tiers

| Attribute | T4 (dev/staging) | A100 (prod) |
|---|---|---|
| VRAM | 16 GB | 40 / 80 GB |
| FP16 throughput | ~65 TFLOPS | ~312 TFLOPS |
| Batch size | 32–64 | 128–512 |
| Hourly cost (GKE) | ~$0.35 | ~$2.93 |
| Use case | embedding, small rerank | large batches, heavy rerank |

Both tiers use the same `accelerator: nvidia` label, so the same values overlay works for both. Add `gpu-type: t4` / `gpu-type: a100` node label + `nodeSelector` to target a specific tier.

### 6. Cost Optimization

- `min_replicas: 1` — idle system costs 1 GPU/service instead of N
- Scale-down stabilization window: 300s — prevents rapid scale-up/down cycles that waste GPU warm-up time
- Use **preemptible / spot** T4 nodes for dev (cluster admin config, not managed here — see Known Limits)

---

## Data Model (values-gpu.yaml)

### Config Table

| Key | Type | Default | Description |
|---|---|---|---|
| `embedding.enabled` | bool | `true` | Deploy embedding as separate K8s Deployment |
| `embedding.image.repository` | string | `ai-platform-lab/embedding` | Container image repo |
| `embedding.image.tag` | string | `latest-gpu` | Image tag (GPU build) |
| `embedding.replicas` | int | `2` | Initial replica count |
| `embedding.resources.limits.nvidia.com/gpu` | string | `"1"` | GPU limit per pod (1 GPU) |
| `embedding.nodeSelector` | map | `{accelerator: nvidia}` | Schedule on GPU nodes |
| `embedding.tolerations` | list | `[{key: nvidia, operator: Exists}]` | Tolerate GPU taint |
| `embedding.servicePort` | int | `8100` | ClusterIP service port |
| `rerank.enabled` | bool | `true` | Deploy rerank as separate K8s Deployment |
| `rerank.image.tag` | string | `latest-gpu` | Image tag (GPU build) |
| `rerank.replicas` | int | `1` | Initial replica count |
| `rerank.resources.limits.nvidia.com/gpu` | string | `"1"` | GPU limit per pod |
| `rerank.servicePort` | int | `8200` | ClusterIP service port |
| `gateway.replicas` | int | `4` | CPU gateway pod count |
| `worker.replicas` | int | `3` | CPU worker pod count |
| `gpu_hpa.enabled` | bool | `true` | Enable GPU HPA |
| `gpu_hpa.target_gpu_utilization` | int | `70` | Target GPU utilization % for HPA |
| `gpu_hpa.target_qps` | int | `100` | Target QPS per embedding replica |
| `gpu_hpa.min_replicas` | int | `1` | Min GPU pod count (cost saving) |
| `gpu_hpa.max_replicas` | int | `8` | Max embedding pod count |
| `gpu_hpa.target_cpu_utilization` | int | `70` | Fallback CPU utilization % |
| `gpu_hpa.scale_down_stabilization_seconds` | int | `300` | Anti-thrashing window |
| `gpu_hpa.scale_up_stabilization_seconds` | int | `60` | Fast scale-up window |

---

## REST API

This feature has **no new REST API endpoints**. The embedding and rerank services expose their own HTTP APIs (port 8000 inside pod, ClusterIP port 8100/8200), but those are internal service APIs called by the gateway — not documented in the external API reference.

---

## Helm Templates (files created)

| File | Kind | Purpose |
|---|---|---|
| `deploy/helm/values-gpu.yaml` | Values overlay | GPU production config |
| `templates/embedding-gpu-deployment.yaml` | Deployment | Embedding on GPU nodes + model warmup |
| `templates/embedding-gpu-service.yaml` | Service | ClusterIP port 8100 |
| `templates/embedding-gpu-hpa.yaml` | HPA v2 | CPU + GPU + QPS metrics |
| `templates/rerank-gpu-deployment.yaml` | Deployment | Rerank on GPU nodes + model warmup |
| `templates/rerank-gpu-service.yaml` | Service | ClusterIP port 8200 |
| `templates/rerank-gpu-hpa.yaml` | HPA v2 | CPU + GPU metrics |
| `templates/gpu-model-warmup.yaml` | ConfigMap | Warmup scripts (bash + Python) |
| `templates/gpu-cost-dashboard.yaml` | ConfigMap | Grafana dashboard JSON + Prometheus queries |
| `deploy/k8s/gpu-node-pool.yaml` | Reference | GKE/EKS GPU node pool setup guide |

---

## Deployment Instructions

### Prerequisites

```bash
# 1. NVIDIA Device Plugin (once per cluster)
kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.14.1/nvidia-device-plugin.yml

# 2. DCGM Exporter (for GPU utilization metrics)
helm repo add gpu-helm-charts https://nvidia.github.io/dcgm-exporter/helm-charts
helm install dcgm-exporter gpu-helm-charts/dcgm-exporter -n monitoring --create-namespace

# 3. Prometheus Adapter (for HPA custom/external metrics)
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install prometheus-adapter prometheus-community/prometheus-adapter \
  -n monitoring --set prometheus.url=http://prometheus.monitoring.svc.cluster.local

# 4. Create GPU node pool (see deploy/k8s/gpu-node-pool.yaml for reference)
gcloud container node-pools create gpu-t4-dev \
  --cluster=ai-platform-dev \
  --zone=us-central1-a \
  --machine-type=n1-standard-4 \
  --accelerator=type=nvidia-tesla-t4,count=1 \
  --num-nodes=2 --min-nodes=1 --max-nodes=8 \
  --enable-autoscaling \
  --node-labels="accelerator=nvidia,gpu-type=t4" \
  --node-taints="nvidia=:NoSchedule"
```

### Install / Upgrade with GPU overlay

```bash
# First install
helm install ai-platform-lab ./deploy/helm/ai-platform-lab \
  -f deploy/helm/ai-platform-lab/values.yaml \
  -f deploy/helm/values-gpu.yaml \
  -n ai-platform

# Upgrade
helm upgrade ai-platform-lab ./deploy/helm/ai-platform-lab \
  -f deploy/helm/ai-platform-lab/values.yaml \
  -f deploy/helm/values-gpu.yaml \
  -n ai-platform

# Override specific values
helm upgrade ai-platform-lab ./deploy/helm/ai-platform-lab \
  -f deploy/helm/ai-platform-lab/values.yaml \
  -f deploy/helm/values-gpu.yaml \
  --set embedding.replicas=4 \
  --set gpu_hpa.target_gpu_utilization=60 \
  -n ai-platform
```

### Verify GPU pods

```bash
# Check pods landed on GPU nodes
kubectl get pods -n ai-platform -o wide | grep -E "embedding|rerank"

# Verify GPU resource allocated
kubectl describe pod <embedding-pod> -n ai-platform | grep -A5 "Limits:"

# Check HPA status
kubectl get hpa -n ai-platform

# Watch GPU utilization
kubectl top pods -n ai-platform --containers | grep -E "embedding|rerank"
```

---

## Test Section

### Running Tests

```bash
cd /path/to/ai-platform-lab
pip install pyyaml  # if not already installed
python3 tests/test_gpu_scheduling.py
```

### Test Coverage (26 test cases)

| Test | What It Validates |
|---|---|
| `test_01` | values-gpu.yaml parses as valid YAML |
| `test_02` | `embedding.replicas == 2` |
| `test_03` | `rerank.replicas == 1` |
| `test_04` | `gpu_hpa.min_replicas=1, max_replicas=8, target_gpu_utilization=70, target_qps=100` |
| `test_05` | embedding-gpu-deployment.yaml exists |
| `test_06` | Deployment references `nvidia.com/gpu` resource limit |
| `test_07` | Deployment comments reference `accelerator: nvidia` node selector |
| `test_08` | Deployment has `tolerations` for GPU taint |
| `test_09` | Deployment has `readinessProbe` on `/healthz` |
| `test_10` | Deployment has `initContainers` (model warmup) |
| `test_11` | Service exposes port 8100 as ClusterIP |
| `test_12` | HPA uses `autoscaling/v2` + references `nvidia.com/gpu` metric |
| `test_13` | rerank-gpu-deployment.yaml exists |
| `test_14` | Rerank Deployment has `nvidia.com/gpu` + `accelerator` nodeSelector |
| `test_15` | Rerank Service exposes port 8200 as ClusterIP |
| `test_16` | Rerank HPA is valid HorizontalPodAutoscaler |
| `test_17` | gpu-model-warmup.yaml is a ConfigMap with warmup script content |
| `test_18` | gpu-node-pool.yaml references T4 + A100 GPU types |
| `test_19` | gpu-cost-dashboard.yaml is a ConfigMap with DCGM + Grafana content |
| `test_20` | All 8 GPU templates parse as valid YAML after Go template stripping |
| `test_21` | `embedding.resources.limits.nvidia.com/gpu == "1"` |
| `test_22` | `rerank.resources.limits.nvidia.com/gpu == "1"` |
| `test_23` | `gateway.replicas == 4` |
| `test_24` | `worker.replicas == 3` |
| `test_25` | `embedding.nodeSelector.accelerator == "nvidia"` |
| `test_26` | `embedding.tolerations` contains `key: nvidia` |

---

## Code Navigation

```
deploy/
├── helm/
│   ├── values-gpu.yaml                   ← GPU overlay (this issue)
│   └── ai-platform-lab/
│       └── templates/
│           ├── embedding-gpu-deployment.yaml   ← Embedding on GPU + warmup
│           ├── embedding-gpu-service.yaml      ← ClusterIP :8100
│           ├── embedding-gpu-hpa.yaml          ← HPA v2: CPU + GPU + QPS
│           ├── rerank-gpu-deployment.yaml      ← Rerank on GPU + warmup
│           ├── rerank-gpu-service.yaml         ← ClusterIP :8200
│           ├── rerank-gpu-hpa.yaml             ← HPA v2: CPU + GPU
│           ├── gpu-model-warmup.yaml           ← Model cache warmup ConfigMap
│           └── gpu-cost-dashboard.yaml         ← Grafana dashboard ConfigMap
└── k8s/
    └── gpu-node-pool.yaml                ← Node pool setup reference (GKE/EKS)

tests/
└── test_gpu_scheduling.py                ← 26 test cases

docs/
└── phase-k-gpu-scheduling.md            ← This document

packages/
└── embedding/                            ← Python package (unchanged — #35)
```

---

## Settings Integration (document only — DO NOT edit settings.py)

GPU scheduling is pure Kubernetes configuration — no Python settings changes required.

The gateway may optionally add these env vars to route to the internal embedding/rerank services:

| Setting | Env Var | Default | Description |
|---|---|---|---|
| `embedding_service_url` | `EMBEDDING_SERVICE_URL` | `""` | Internal ClusterIP URL for GPU embedding service (e.g., `http://<release>-embedding:8100`) |
| `rerank_service_url` | `RERANK_SERVICE_URL` | `""` | Internal ClusterIP URL for GPU rerank service (e.g., `http://<release>-rerank:8200`) |

Add to `settings.py` (optional routing):
```python
embedding_service_url: str = Field(default="", validation_alias="EMBEDDING_SERVICE_URL",
    description="Internal URL of GPU embedding service pod")
rerank_service_url: str = Field(default="", validation_alias="RERANK_SERVICE_URL",
    description="Internal URL of GPU rerank service pod")
```

Add to `.env.example`:
```
# GPU Elastic Scheduling (Issue #36)
EMBEDDING_SERVICE_URL=http://ai-platform-lab-embedding:8100
RERANK_SERVICE_URL=http://ai-platform-lab-rerank:8200
```

## main.py Integration (document only — DO NOT edit main.py)

No changes required. The embedding and rerank services run as separate K8s Deployments and are accessed via ClusterIP. The gateway can route to them by configuring `EMBEDDING_SERVICE_URL` / `RERANK_SERVICE_URL`.

## README Section (document only — DO NOT edit README.md)

Add to README.md under "Deployment" section:

```markdown
### GPU Elastic Scheduling (Issue #36)

Deploy embedding and rerank services on dedicated GPU nodes with auto-scaling:

```bash
helm upgrade --install ai-platform-lab ./deploy/helm/ai-platform-lab \
  -f deploy/helm/ai-platform-lab/values.yaml \
  -f deploy/helm/values-gpu.yaml
```

See `docs/phase-k-gpu-scheduling.md` for full setup including GPU node pool creation.
```

## Roadmap Update (document only — DO NOT edit roadmap.md)

Update `docs/roadmap.md` entry #52:
- Status: `completed`
- Files: `deploy/helm/values-gpu.yaml`, `deploy/helm/ai-platform-lab/templates/embedding-gpu-*.yaml`, `deploy/helm/ai-platform-lab/templates/rerank-gpu-*.yaml`, `deploy/helm/ai-platform-lab/templates/gpu-model-warmup.yaml`, `deploy/helm/ai-platform-lab/templates/gpu-cost-dashboard.yaml`, `deploy/k8s/gpu-node-pool.yaml`

---

## Known Limits

1. **No GPU time-slicing / MIG partitioning**: Each pod claims 1 full GPU. NVIDIA Multi-Instance GPU (MIG) and time-slicing (`nvidia.com/gpu: 0.5`) are not configured. Small models (BGE-small) underutilize A100; acceptable on T4. Mitigation: switch to `nvidia.com/mig-*` resources for A100 MIG if needed.

2. **No model parallelism / tensor parallelism**: Larger models (7B+ params) require multi-GPU setups with NVLink. This implementation targets single-GPU inference models only. For multi-GPU: switch to `nvidia.com/gpu: N` + affinity rules.

3. **Cold start latency 30–90s on first pod**: Even with initContainer warmup, a freshly scheduled pod takes 30–90 seconds before the first request completes, because: (a) image pull (if not cached), (b) initContainer download (if model not in node cache), (c) CUDA context init. The warmup initContainer mitigates (c) only. Use `PodPresets` or Persistent Volume for model cache across restarts to mitigate (b).

4. **No spot/preemptible instance support**: `values-gpu.yaml` does not configure spot node pools. GKE spot nodes can reduce cost ~70% but require interruption handling (graceful shutdown hook to drain in-flight requests). This is a cluster-admin concern outside this chart's scope.

5. **DCGM Exporter required for GPU HPA**: Without DCGM Exporter + Prometheus Adapter, the `ContainerResource nvidia.com/gpu` HPA metric will not function and HPA will fall back to CPU-only scaling. The `gpu_hpa.enabled` flag still applies, but GPU utilization signal is silent. Deploy DCGM Exporter as documented above.

6. **External QPS metric is a stub**: The `gateway_embedding_qps_per_replica` external metric in the embedding HPA requires a Prometheus Adapter rule mapping the Prometheus query to the Kubernetes external metrics API. The stub metric name + query is documented in `embedding-gpu-hpa.yaml` but the Prometheus Adapter `ConfigMap` is not shipped with this chart — it is cluster-infra responsibility.

7. **No ingress for embedding/rerank services**: Both services are intentionally ClusterIP-only (internal). Exposing them externally would bypass the gateway's auth/rate-limiting layer. Do not add Ingress to these services.

---

## Interview Talking Points

1. **Why separate Deployments instead of sidecar or in-process?**  
   Separation enables independent scaling (embedding QPS vs rerank QPS may differ by 10×), independent image updates (embedding model upgrade doesn't require gateway redeploy), and correct GPU node affinity (gateway must run on CPU nodes for cost; embedding must run on GPU nodes for performance). It also enables separate HPA policies per service.

2. **How does multi-metric HPA work, and why three metrics?**  
   Kubernetes HPA v2 evaluates all metrics and takes the maximum required replica count across them (conservative). CPU covers general load, GPU utilization catches GPU memory pressure even at low request rates (model inference can saturate GPU memory at low QPS if batch sizes are large), and QPS from the gateway is the most direct leading indicator of load. Using QPS alone would miss cold GPU memory scenarios; using GPU alone would miss CPU-bound preprocessing.

3. **How does the model warmup initContainer reduce P99 latency?**  
   The initContainer runs before the main container starts, downloads model weights to a shared `emptyDir` volume, and runs a dummy forward pass to trigger CUDA kernel JIT compilation. When the main container starts, the model is already in GPU VRAM and CUDA kernels are compiled, so the first real request completes in <100ms instead of 30–90s. The tradeoff is longer pod startup time (+30–60s), acceptable because scale-up events are stabilized for 60s anyway.

4. **How do you ensure GPU pods don't land on CPU nodes and vice versa?**  
   The node taint `nvidia=:NoSchedule` prevents any pod without a matching toleration from being scheduled on GPU nodes. GPU pods (embedding, rerank) carry the toleration + `nodeSelector: {accelerator: nvidia}`. CPU pods (gateway, worker) carry neither — the taint blocks them from GPU nodes. The combination is bidirectional isolation: GPU → GPU nodes only, CPU → CPU nodes only.

5. **What is the cost model for this system?**  
   The Grafana dashboard tracks `gpu_replicas × hourly_rate_per_gpu`. With T4 @ $0.35/hr: min state (1+1=2 replicas) costs ~$0.70/hr; max state (8+4=12 replicas) costs ~$4.20/hr. The `min_replicas: 1` policy ensures idle hours (nights/weekends) cost minimum GPU budget. A100 production tier would cost 8.4× more, justified only for batch throughput requirements >100 RPS sustained.

6. **How does this scale to handle bursty embedding workloads?**  
   The HPA `scaleUp.stabilizationWindowSeconds: 60` with `100%` percent policy allows doubling replica count every 60 seconds. Starting from `min_replicas: 1`, the system can reach 8 replicas in ~3 scale-up cycles (~3 minutes). For sub-minute bursts, pre-warming is needed (set `min_replicas: 2` during peak hours via `kubectl patch hpa`). True instant burst handling requires pre-provisioned warm pools (out of scope for this issue).

7. **Why not use Knative / KServe for model serving instead of raw Kubernetes?**  
   For this stage of the project (portfolio/lab), raw Kubernetes + HPA gives full control and visibility. KServe adds powerful features (model versioning, canary routing, gRPC, batching server) but adds operational complexity and requires the KServe CRD stack. The architecture is designed to be migration-friendly: the Deployment structure maps 1:1 to KServe `InferenceService`, so migrating later is a template replacement, not a redesign.
