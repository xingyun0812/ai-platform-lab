# Phase K — Multi-AZ High Availability

> **Issue**: #35 (Roadmap #51) — 多 AZ 高可用  
> **Depends on**: #34 K8s Helm Chart  
> **Owner**: Platform Engineering  
> **Status**: Implemented

---

## Overview

This phase adds multi-AZ (Availability Zone) high-availability capabilities on top of the base Helm Chart produced in #34. A single AZ failure (node failures, zone outage, scheduled maintenance) must not take down the AI Platform service.

**Key design goal**: ≥N+1 redundancy at every tier — Gateway, Worker, Qdrant, Redis, Postgres — with each tier spreading pods across at least 2 AZs.

---

## 2-AZ Topology

```
                     ┌─────────────────────────────────────────────┐
                     │            Load Balancer / Ingress           │
                     └──────────────┬──────────────────────────────┘
                                    │
              ┌─────────────────────┴────────────────────────┐
              │                                              │
      ┌───────▼───────┐                            ┌────────▼────────┐
      │   AZ: us-east-1a                           │   AZ: us-east-1b
      │                │                           │                 │
      │  Gateway ×3    │                           │  Gateway ×3     │
      │  Worker  ×2    │                           │  Worker  ×2     │
      │  Qdrant        │   ←── Replication ──►    │  Qdrant Replica │
      │  Redis Primary │                           │  Redis Sentinel │
      │  Postgres      │   ←── Streaming  ──►    │  Postgres Replica│
      │  Sentinel      │                           │  Sentinel       │
      └────────────────┘                           └─────────────────┘
```

**Survivability**: When AZ us-east-1a fails:
- 3 Gateway + 2 Worker pods remain in us-east-1b → service continues
- Redis Sentinel quorum (2/3) detects primary loss, promotes replica
- Postgres replica in us-east-1b must be manually promoted via `pg_promote()`
- Qdrant read replicas in us-east-1b serve read queries; write traffic pauses until Qdrant cluster heals

---

## Topology Spread Constraints

All multi-pod components use `topologySpreadConstraints` with `topologyKey: topology.kubernetes.io/zone` to ensure pods are distributed across AZs. This prevents all replicas from landing in the same zone during cluster scale-up.

```yaml
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: topology.kubernetes.io/zone
    whenUnsatisfiable: DoNotSchedule
    labelSelector:
      matchLabels:
        app.kubernetes.io/component: gateway
```

- `maxSkew: 1` — at most 1 more pod in one zone than another
- `whenUnsatisfiable: DoNotSchedule` — hard constraint; pod stays pending rather than land in already-full zone
- A helper partial template `ai-platform-lab.topologySpreadConstraints` is defined in `topology-spread-constraints.yaml`

---

## Qdrant Read Replicas

**File**: `deploy/helm/ai-platform-lab/templates/qdrant-replica.yaml`

Adds a second StatefulSet (`qdrant-replica`) running 2 read-only replica nodes in addition to the primary Qdrant StatefulSet.

| Aspect | Detail |
|---|---|
| StatefulSet name | `<release>-qdrant-replica` |
| Replicas | 2 (spread across AZs) |
| Mode | Read-only replica (connected to primary cluster via P2P port 6335) |
| Service | `<release>-qdrant-replica` (ClusterIP, port 6333/6334) |
| Readiness probe | `GET /readyz` — passes only when replica is in sync |
| PVC | `qdrant-replica-data` per pod, 10Gi default |
| Enabled by | `qdrant.replica.enabled: true` in values overlay |

**Note**: Qdrant's built-in distributed mode handles replication internally. The replica StatefulSet connects to the primary cluster; no manual replication setup is required beyond cluster config env vars.

---

## Redis Sentinel Failover

**File**: `deploy/helm/ai-platform-lab/templates/redis-sentinel.yaml`

Deploys 3 Redis Sentinel processes, each in a different AZ, that monitor the Redis primary and automatically elect a new primary on failure.

| Aspect | Detail |
|---|---|
| Kind | Deployment (Sentinel processes are stateless) |
| Replicas | 3 (one per AZ) |
| Service | `<release>-redis-sentinel` (ClusterIP, port 26379) |
| Quorum | 2 — majority of sentinels must agree before failover |
| Primary name | `mymaster` (configurable via `redis.sentinel.primaryName`) |
| Sentinel config | Written at pod startup by init container |
| Down-after | 5000ms — primary considered down if unreachable for 5s |
| Failover timeout | 60000ms |
| Spread | `requiredDuringSchedulingIgnoredDuringExecution` on `topology.kubernetes.io/zone` |
| Enabled by | `redis.sentinel.enabled: true` in values overlay |

**Client configuration**: Applications must connect to the Sentinel service (port 26379) and use the `SENTINEL get-master-addr-by-name mymaster` command to discover the current primary. Redis client libraries (redis-py, ioredis) support Sentinel mode natively.

---

## Postgres Streaming Replication

**File**: `deploy/helm/ai-platform-lab/templates/postgres-replica.yaml`

Adds a Postgres standby StatefulSet that continuously replicates from the primary using physical streaming replication (WAL shipping).

| Aspect | Detail |
|---|---|
| StatefulSet name | `<release>-postgres-replica` |
| Replicas | 1 (in different AZ from primary) |
| Replication method | Streaming replication (physical WAL shipping) |
| Bootstrap | `pg_basebackup` init container runs once to clone primary |
| Recovery config | `standby.signal` + `primary_conninfo` in `postgresql.auto.conf` |
| Standby mode | `hot_standby = on` — replica accepts read-only queries |
| Service | `<release>-postgres-replica` (ClusterIP, port 5432) |
| Readiness probe | `pg_is_in_recovery()` returns true (confirms standby mode) |
| PVC | `postgres-replica-data`, 10Gi default |
| AZ placement | `podAntiAffinity` prevents co-location with primary |
| Enabled by | `postgres.replica.enabled: true` in values overlay |

**Failover procedure** (MANUAL):
```bash
# 1. When primary is confirmed dead:
kubectl exec -it <postgres-replica-pod> -- psql -U aiplatform -c "SELECT pg_promote();"

# 2. Update application connection strings to point to replica service
# 3. Rebuild original primary as new replica (requires re-running pg_basebackup)
```

---

## PodDisruptionBudget

**File**: `deploy/helm/ai-platform-lab/templates/poddisruptionbudget.yaml`

PDBs prevent voluntary disruptions (node drain, rolling updates) from violating the minimum availability thresholds.

| Component | minAvailable | Rationale |
|---|---|---|
| Gateway | 2 | At least 2 replicas to serve traffic during rolling update |
| Worker | 1 | At least 1 worker to process queued tasks |
| Qdrant | 1 | At least 1 vector DB instance for query serving |
| Enabled by | `podDisruptionBudget.<component>.enabled: true` | Per-component opt-in |

PDBs block `kubectl drain` if the operation would violate `minAvailable`. This protects against accidental full-AZ drain during maintenance.

---

## NetworkPolicy

**File**: `deploy/helm/ai-platform-lab/templates/networkpolicy.yaml`

Restricts pod-to-pod traffic using Kubernetes NetworkPolicy (requires CNI with NetworkPolicy support: Calico, Cilium, etc.).

| Policy | Ingress Allowed From | Ports |
|---|---|---|
| gateway | ingress-nginx namespace + same namespace | 8000 |
| qdrant | gateway, qdrant-replica, qdrant (cluster P2P) | 6333, 6334, 6335 |
| postgres | gateway, worker, postgres-replica | 5432 |

Gateway egress is also restricted — it may only reach Qdrant, Postgres, Redis, Redis Sentinel, DNS (UDP/TCP 53), and external HTTPS (443).

**Enabled by**: `networkPolicy.enabled: true` in values overlay.

---

## Chaos Testing

**File**: `deploy/k8s/chaos-test.yaml`

Uses [ChaosMesh](https://chaos-mesh.org) to simulate AZ failure by killing all pods in AZ `us-east-1a`.

```yaml
apiVersion: chaos-mesh.org/v1alpha1
kind: PodChaos
spec:
  action: pod-kill
  mode: all
  selector:
    nodeSelectors:
      topology.kubernetes.io/zone: us-east-1a
  duration: "30s"
```

**Running the test**:
```bash
# Install ChaosMesh
helm install chaos-mesh chaos-mesh/chaos-mesh -n chaos-testing --create-namespace

# Apply the chaos experiment
kubectl apply -f deploy/k8s/chaos-test.yaml

# Monitor service health during chaos window
curl -s http://ai-platform-lab.local/healthz
# Expected: HTTP 200 within 5s

# Stop the experiment
kubectl delete -f deploy/k8s/chaos-test.yaml
```

**Expected outcome**: Gateway responds to health checks within 5 seconds of AZ failure. Error rate remains < 1% due to pod spreading across AZs.

---

## Config Table (values-multi-az.yaml Overlay)

| Key | Default (base) | Multi-AZ Override | Description |
|---|---|---|---|
| `global.multi_az.enabled` | — | `true` | Enable multi-AZ mode globally |
| `global.multi_az.zones` | — | `[us-east-1a, us-east-1b]` | Target AZ names |
| `gateway.replicas` | 2 | **6** | 3 per AZ × 2 AZs |
| `gateway.topologySpreadConstraints` | — | Set | Zone spreading constraint |
| `gateway.affinity.podAntiAffinity` | — | Set | Prevent co-location |
| `worker.replicas` | 1 | **4** | 2 per AZ × 2 AZs |
| `qdrant.replicas` | 1 | **3** | Primary + 2 replicas |
| `qdrant.replica.enabled` | — | `true` | Enable qdrant-replica StatefulSet |
| `qdrant.replica.replicas` | — | `2` | Read replica count |
| `redis.sentinel.enabled` | — | `true` | Enable Redis Sentinel |
| `redis.sentinel.replicas` | — | `3` | Sentinel pod count |
| `redis.sentinel.quorum` | — | `2` | Failover quorum |
| `postgres.replica.enabled` | — | `true` | Enable Postgres standby |
| `postgres.replica.replicas` | — | `1` | Standby count |
| `postgres.standby_zones` | — | `[us-east-1a, us-east-1b]` | Zone placement hints |
| `podDisruptionBudget.gateway.minAvailable` | — | `2` | Min available gateway pods |
| `podDisruptionBudget.worker.minAvailable` | — | `1` | Min available worker pods |
| `podDisruptionBudget.qdrant.minAvailable` | — | `1` | Min available Qdrant pods |
| `networkPolicy.enabled` | — | `true` | Enable NetworkPolicies |
| `hpa.gateway.minReplicas` | 2 | `6` | HPA floor matches multi-AZ count |
| `hpa.gateway.maxReplicas` | 10 | `20` | HPA ceiling |

---

## Deployment Instructions

```bash
# Full multi-AZ deployment (extend prod overlay with multi-AZ overlay):
helm upgrade --install ai-platform-lab \
  ./deploy/helm/ai-platform-lab \
  -f ./deploy/helm/values-prod.yaml \
  -f ./deploy/helm/values-multi-az.yaml \
  --namespace ai-platform \
  --create-namespace

# Verify topology spread:
kubectl get pods -n ai-platform -o wide | grep gateway

# Verify PDB:
kubectl get pdb -n ai-platform

# Verify Redis Sentinel:
kubectl exec -it <sentinel-pod> -- redis-cli -p 26379 sentinel masters

# Verify Postgres streaming replication:
kubectl exec -it <postgres-replica-pod> -- psql -U aiplatform -c "SELECT pg_is_in_recovery();"
```

---

## Test Section

Tests are in `tests/test_multi_az.py` and run without Helm CLI or a live cluster.

```bash
cd /path/to/ai-platform-lab
python3 tests/test_multi_az.py
```

**Test cases (16 total)**:
1. `values-multi-az.yaml` parses as valid YAML
2. `gateway.replicas=6`
3. `qdrant.replicas=3`
4. Redis Sentinel enabled with `quorum=2`
5. `global.multi_az.enabled=true`
6. `topologySpreadConstraints` present for gateway
7. `qdrant-replica.yaml` is StatefulSet with readinessProbe + PVC
8. `redis-sentinel.yaml` has Deployment, port 26379, quorum config
9. `postgres-replica.yaml` has `pg_basebackup` + `primary_conninfo` + `standby.signal`
10. `poddisruptionbudget.yaml` has `minAvailable` for gateway/worker/qdrant
11. `networkpolicy.yaml` covers qdrant + postgres
12. `topology-spread-constraints.yaml` defines named templates
13. `chaos-test.yaml` has PodChaos targeting `us-east-1a` with `pod-kill`
14. All new YAML templates parse cleanly after stripping Go templates
15. `worker.replicas=4`, `postgres.replica.enabled=true`, `standby_zones` set
16. `docs/phase-k-multi-az.md` exists with required sections

---

## Code Navigation

| File | Purpose |
|---|---|
| `deploy/helm/values-multi-az.yaml` | Multi-AZ overlay: replicas, spread, sentinel, replication |
| `deploy/helm/ai-platform-lab/templates/qdrant-replica.yaml` | Qdrant read-replica StatefulSet + services |
| `deploy/helm/ai-platform-lab/templates/redis-sentinel.yaml` | Redis Sentinel Deployment + service |
| `deploy/helm/ai-platform-lab/templates/postgres-replica.yaml` | Postgres streaming replica StatefulSet + services |
| `deploy/helm/ai-platform-lab/templates/poddisruptionbudget.yaml` | PDBs for gateway, worker, qdrant |
| `deploy/helm/ai-platform-lab/templates/networkpolicy.yaml` | NetworkPolicies restricting inter-component traffic |
| `deploy/helm/ai-platform-lab/templates/topology-spread-constraints.yaml` | Helper partial templates for AZ spreading |
| `deploy/k8s/chaos-test.yaml` | ChaosMesh PodChaos experiment for AZ failure simulation |
| `tests/test_multi_az.py` | 16 Python tests validating multi-AZ templates |
| `docs/phase-k-multi-az.md` | This design doc |
| `deploy/helm/ai-platform-lab/values.yaml` | Base values (NOT modified by this phase) |
| `deploy/helm/ai-platform-lab/templates/qdrant-statefulset.yaml` | Base Qdrant StatefulSet (NOT modified) |
| `deploy/helm/ai-platform-lab/templates/postgres-statefulset.yaml` | Base Postgres StatefulSet (NOT modified) |

---

## Known Limits

1. **No automatic AZ failover detection for Postgres** — Postgres streaming replication is passive. When the primary dies, the replica does NOT automatically promote. An operator or external tool (Patroni, pg_auto_failover) must run `pg_promote()`. Until promotion, write traffic is blocked.

2. **Qdrant replica lag possible** — Qdrant cluster replication is eventually consistent. Under heavy write load, replicas may lag behind the primary by 100s of milliseconds. Read-after-write consistency is not guaranteed when reading from the `qdrant-replica` service.

3. **Redis Sentinel split-brain risk** — If network partition isolates 1 sentinel, it cannot reach quorum (2/3) and will not trigger failover. However, a partition that isolates 2+ sentinels from the primary could create a split-brain if the isolated sentinels elect a new primary while the old one recovers. Mitigation: ensure `min-replicas-to-write 1` on primary Redis.

4. **No cross-region support** — This implementation targets 2 AZs within a single AWS region. Cross-region replication (active-active or active-passive across regions) requires additional infrastructure: Route 53 geolocation routing, cross-region RDS read replicas, Global Qdrant clusters.

5. **Postgres failover is manual and data may be lost** — In asynchronous streaming replication, the replica may not have received the last few WAL records from the primary at the time of failure. Transactions committed on the primary but not yet streamed to the replica will be lost upon promotion. Use `synchronous_commit = on` to prevent this at the cost of write latency.

6. **topologySpreadConstraints require AZ node labels** — The spread constraints rely on nodes being labeled `topology.kubernetes.io/zone`. Managed K8s services (EKS, GKE, AKS) add these labels automatically, but self-managed clusters must label nodes manually.

7. **NetworkPolicy requires compatible CNI** — NetworkPolicies only work if the cluster uses a CNI plugin that supports them (Calico, Cilium, AWS VPC CNI with network policy add-on). Clusters using Flannel without a policy engine will silently ignore NetworkPolicy resources.

---

## Interview Talking Points

1. **Why topologySpreadConstraints over podAntiAffinity?**  
   `topologySpreadConstraints` with `maxSkew: 1` provides fine-grained control over imbalance between zones, whereas `podAntiAffinity` with `requiredDuringScheduling` only prevents strict co-location. With 6 gateway replicas, `topologySpreadConstraints` ensures 3/3 split across 2 zones instead of just "not on the same node."

2. **Why Redis Sentinel instead of Redis Cluster?**  
   Redis Cluster shards data across nodes (horizontal scaling), whereas Sentinel provides HA for a single-primary setup (failover only). For this platform, Redis is used as a cache and session store — sharding adds complexity without proportional benefit. Sentinel gives automatic failover with 3 watchers and quorum=2 in ~5s without client-side sharding logic.

3. **Why is Postgres failover manual?**  
   PostgreSQL streaming replication is physical, unidirectional (primary→replica). Automatic promotion requires a failover agent (Patroni, repmgr, pg_auto_failover) that runs alongside Postgres, monitors the primary, and coordinates promotion with fencing to prevent split-brain. Adding Patroni is a follow-up item — the current implementation lays the groundwork (replica running, `standby.signal` present) so Patroni can be added without re-architecting.

4. **What happens to in-flight requests during an AZ failure?**  
   - Active connections to the failed pods are reset (TCP RST)
   - Load balancer health checks detect unhealthy pods within `failureThreshold × periodSeconds` (default: 3 × 10s = 30s)
   - New connections are routed to surviving AZ pods immediately (if health checks pass)
   - Total impact: ~30s of potential error for connections that were on the failed pods; the remaining pods continue serving normally

5. **How do PodDisruptionBudgets improve reliability?**  
   Without PDBs, `kubectl drain node-in-az-a` could evict all gateway pods from AZ-a simultaneously, leaving only AZ-b pods — and if AZ-b has a failure, the service goes down. PDB with `minAvailable: 2` prevents `drain` from proceeding until enough pods are rescheduled elsewhere, enforcing a rolling drain.

6. **Why 3 Redis Sentinels instead of 2?**  
   Sentinel failover requires a quorum (majority). With 2 sentinels and quorum=1, a single sentinel's false detection triggers failover (prone to network flaps causing unnecessary failovers). With 3 sentinels and quorum=2, 2 must independently observe the primary as unreachable — significantly reducing false positives while still providing failover when 1 sentinel is in a failed AZ.

7. **How does the overlay pattern prevent configuration drift?**  
   Using `values-multi-az.yaml` as an additive overlay (not modifying `values.yaml`) means: (a) the base chart remains deployable in single-AZ mode without the overlay, (b) multi-AZ config is auditable in a dedicated file, (c) CI can test both `helm template .` and `helm template . -f values-multi-az.yaml` independently, catching regressions in either path.

---

## Shared File Integration Notes

**No changes required to**:
- `apps/gateway/main.py` — Gateway code is AZ-agnostic
- `apps/gateway/settings.py` — No new Python settings
- `.env.example` — No new env vars (multi-AZ is K8s deployment config)
- `README.md` — Add the following section:

```markdown
### Multi-AZ High Availability

Deploy with multi-AZ HA overlay for production:

\```bash
helm upgrade --install ai-platform-lab ./deploy/helm/ai-platform-lab \
  -f ./deploy/helm/values-prod.yaml \
  -f ./deploy/helm/values-multi-az.yaml \
  --namespace ai-platform
\```

See [docs/phase-k-multi-az.md](docs/phase-k-multi-az.md) for full topology details.
```

**Roadmap update** (docs/roadmap.md): Mark #51 (多 AZ 高可用) as ✅ Completed.
