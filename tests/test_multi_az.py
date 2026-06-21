"""
tests/test_multi_az.py
Python 3.9-compatible tests for the multi-AZ HA Helm overlay and templates.
No Helm CLI required -- validates YAML structure and key config via file reads.
"""
from __future__ import annotations

import os
import re
import sys
import yaml

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)
_CHART_DIR = os.path.join(_REPO_ROOT, "deploy", "helm", "ai-platform-lab")
_TEMPLATES_DIR = os.path.join(_CHART_DIR, "templates")
_DEPLOY_HELM_DIR = os.path.join(_REPO_ROOT, "deploy", "helm")
_DEPLOY_K8S_DIR = os.path.join(_REPO_ROOT, "deploy", "k8s")
_DOCS_DIR = os.path.join(_REPO_ROOT, "docs")

OPEN_B = "{{"
CLOSE_B = "}}"


def strip_go_templates(text):
    # type: (str) -> str
    """Remove Go template directives for yaml.safe_load structural testing."""
    result_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        # Blank out lines that are purely template directives
        if stripped and stripped.startswith(OPEN_B) and stripped.endswith(CLOSE_B):
            result_lines.append("")
            continue
        # If the line contains any Go template expression, replace the whole value
        if OPEN_B in line:
            # Try to find a YAML key: value pattern and replace only the value
            m = re.match(r'^(\s*(?:-\s+)?[^\s:][^:]*:\s*)', line)
            if m:
                result_lines.append(m.group(1) + '"TEMPLATE_PLACEHOLDER"')
            else:
                # No key:value structure — blank the line
                result_lines.append("")
        else:
            result_lines.append(line)
    return "\n".join(result_lines)


def load_all_yaml_docs(path):
    # type: (str) -> list
    with open(path, "r") as fh:
        raw = fh.read()
    cleaned = strip_go_templates(raw)
    docs = list(yaml.safe_load_all(cleaned))
    return [d for d in docs if d is not None]


def _read_raw(path):
    # type: (str) -> str
    with open(path, "r") as fh:
        return fh.read()


_passed = []
_failed = []


def run_test(name, fn):
    try:
        fn()
        _passed.append(name)
        print("  [PASS] " + name)
    except Exception as exc:
        _failed.append(name)
        print("  [FAIL] " + name + ": " + str(exc))


# ---- Test cases ---------------------------------------------------------

def test_values_multi_az_parses():
    path = os.path.join(_DEPLOY_HELM_DIR, "values-multi-az.yaml")
    assert os.path.exists(path), "values-multi-az.yaml not found"
    with open(path) as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), "must be a mapping"


def test_values_multi_az_gateway_replicas():
    path = os.path.join(_DEPLOY_HELM_DIR, "values-multi-az.yaml")
    with open(path) as fh:
        data = yaml.safe_load(fh)
    assert data["gateway"]["replicas"] == 6, "gateway.replicas must be 6"


def test_values_multi_az_qdrant_replicas():
    path = os.path.join(_DEPLOY_HELM_DIR, "values-multi-az.yaml")
    with open(path) as fh:
        data = yaml.safe_load(fh)
    assert data["qdrant"]["replicas"] == 3, "qdrant.replicas must be 3"


def test_values_multi_az_redis_sentinel():
    path = os.path.join(_DEPLOY_HELM_DIR, "values-multi-az.yaml")
    with open(path) as fh:
        data = yaml.safe_load(fh)
    sentinel = data["redis"]["sentinel"]
    assert sentinel["enabled"] is True, "sentinel.enabled must be true"
    assert sentinel["replicas"] == 3, "sentinel.replicas must be 3"
    assert sentinel["quorum"] == 2, "sentinel.quorum must be 2"


def test_values_multi_az_global_flag():
    path = os.path.join(_DEPLOY_HELM_DIR, "values-multi-az.yaml")
    with open(path) as fh:
        data = yaml.safe_load(fh)
    assert data["global"]["multi_az"]["enabled"] is True, "global.multi_az.enabled must be true"


def test_values_multi_az_topology_spread():
    path = os.path.join(_DEPLOY_HELM_DIR, "values-multi-az.yaml")
    with open(path) as fh:
        data = yaml.safe_load(fh)
    gw = data["gateway"]
    assert "topologySpreadConstraints" in gw, "topologySpreadConstraints missing"
    assert gw["topologySpreadConstraints"][0]["topologyKey"] == "topology.kubernetes.io/zone"


def test_qdrant_replica_template():
    path = os.path.join(_TEMPLATES_DIR, "qdrant-replica.yaml")
    assert os.path.exists(path), "qdrant-replica.yaml not found"
    content = _read_raw(path)
    assert "StatefulSet" in content, "must define StatefulSet"
    assert "readinessProbe" in content, "must have readinessProbe"
    assert "volumeClaimTemplates" in content, "must have volumeClaimTemplates"
    assert "qdrant-replica" in content, "must reference qdrant-replica"


def test_redis_sentinel_template():
    path = os.path.join(_TEMPLATES_DIR, "redis-sentinel.yaml")
    assert os.path.exists(path), "redis-sentinel.yaml not found"
    content = _read_raw(path)
    assert "Deployment" in content, "must define Deployment"
    assert "26379" in content, "must reference port 26379"
    assert "quorum" in content.lower() or "QUORUM" in content, "must reference quorum"
    assert "kind: Service" in content, "must define Service"


def test_postgres_replica_template():
    path = os.path.join(_TEMPLATES_DIR, "postgres-replica.yaml")
    assert os.path.exists(path), "postgres-replica.yaml not found"
    content = _read_raw(path)
    assert "StatefulSet" in content, "must define StatefulSet"
    assert "pg_basebackup" in content, "must use pg_basebackup"
    assert "primary_conninfo" in content, "must configure primary_conninfo"
    assert "standby.signal" in content, "must write standby.signal"
    assert "volumeClaimTemplates" in content, "must define volumeClaimTemplates"


def test_poddisruptionbudget_template():
    path = os.path.join(_TEMPLATES_DIR, "poddisruptionbudget.yaml")
    assert os.path.exists(path), "poddisruptionbudget.yaml not found"
    content = _read_raw(path)
    assert "PodDisruptionBudget" in content, "must define PodDisruptionBudget"
    assert "minAvailable" in content, "must have minAvailable"
    assert "gateway" in content, "must cover gateway"
    assert "worker" in content, "must cover worker"
    assert "qdrant" in content, "must cover qdrant"


def test_networkpolicy_template():
    path = os.path.join(_TEMPLATES_DIR, "networkpolicy.yaml")
    assert os.path.exists(path), "networkpolicy.yaml not found"
    content = _read_raw(path)
    assert "NetworkPolicy" in content, "must define NetworkPolicy"
    assert "qdrant" in content, "must reference qdrant"
    assert "postgres" in content, "must reference postgres"


def test_topology_spread_constraints_template():
    path = os.path.join(_TEMPLATES_DIR, "topology-spread-constraints.yaml")
    assert os.path.exists(path), "topology-spread-constraints.yaml not found"
    content = _read_raw(path)
    assert "define" in content, "must define named templates"
    assert "topology.kubernetes.io/zone" in content, "must reference zone topology key"
    assert "topologySpreadConstraints" in content, "must reference topologySpreadConstraints"


def test_chaos_test_yaml():
    path = os.path.join(_DEPLOY_K8S_DIR, "chaos-test.yaml")
    assert os.path.exists(path), "chaos-test.yaml not found"
    content = _read_raw(path)
    assert "chaos-mesh.org/v1alpha1" in content, "must use chaos-mesh apiVersion"
    assert "PodChaos" in content, "must define PodChaos"
    assert "us-east-1a" in content, "must target us-east-1a"
    assert "pod-kill" in content, "must use pod-kill action"
    assert "duration" in content, "must specify duration"


def test_all_templates_parse_after_stripping():
    """All new multi-AZ YAML templates parse cleanly after stripping Go templates."""
    templates_to_check = [
        "qdrant-replica.yaml",
        "redis-sentinel.yaml",
        "postgres-replica.yaml",
        "poddisruptionbudget.yaml",
        "networkpolicy.yaml",
    ]
    for fname in templates_to_check:
        fpath = os.path.join(_TEMPLATES_DIR, fname)
        assert os.path.exists(fpath), "Template missing: " + fname
        try:
            load_all_yaml_docs(fpath)
        except yaml.YAMLError as exc:
            raise AssertionError(fname + " YAML parse failed: " + str(exc))
    # topology-spread-constraints.yaml is define-only; just check it exists and is valid Python-readable
    tsc_path = os.path.join(_TEMPLATES_DIR, "topology-spread-constraints.yaml")
    assert os.path.exists(tsc_path), "topology-spread-constraints.yaml missing"
    # No YAML parse needed for pure-define template


def test_worker_replicas_and_postgres_config():
    path = os.path.join(_DEPLOY_HELM_DIR, "values-multi-az.yaml")
    with open(path) as fh:
        data = yaml.safe_load(fh)
    assert data["worker"]["replicas"] == 4, "worker.replicas must be 4"
    pg = data["postgres"]
    assert pg["replica"]["enabled"] is True, "postgres.replica.enabled must be true"
    assert pg["replica"]["replicas"] == 1, "postgres.replica.replicas must be 1"
    assert len(pg["standby_zones"]) == 2, "standby_zones must have 2 entries"


def test_docs_exist():
    path = os.path.join(_DOCS_DIR, "phase-k-multi-az.md")
    assert os.path.exists(path), "docs/phase-k-multi-az.md not found"
    content = _read_raw(path)
    for section in ["Topology", "Qdrant", "Redis Sentinel", "Postgres",
                    "PodDisruptionBudget", "NetworkPolicy", "Chaos", "Known Limits"]:
        assert section in content, "docs missing section: " + section


# ---- Main ---------------------------------------------------------------

if __name__ == "__main__":
    print("\n=== Multi-AZ HA Tests ===\n")
    run_test("01 values-multi-az.yaml parses as valid YAML", test_values_multi_az_parses)
    run_test("02 gateway.replicas=6", test_values_multi_az_gateway_replicas)
    run_test("03 qdrant.replicas=3", test_values_multi_az_qdrant_replicas)
    run_test("04 Redis Sentinel enabled with quorum=2", test_values_multi_az_redis_sentinel)
    run_test("05 global.multi_az.enabled=true", test_values_multi_az_global_flag)
    run_test("06 topologySpreadConstraints present for gateway", test_values_multi_az_topology_spread)
    run_test("07 qdrant-replica.yaml StatefulSet with readinessProbe + PVC", test_qdrant_replica_template)
    run_test("08 redis-sentinel.yaml Deployment + port 26379 + quorum", test_redis_sentinel_template)
    run_test("09 postgres-replica.yaml pg_basebackup + primary_conninfo + standby.signal", test_postgres_replica_template)
    run_test("10 poddisruptionbudget.yaml minAvailable for gateway/worker/qdrant", test_poddisruptionbudget_template)
    run_test("11 networkpolicy.yaml covers qdrant + postgres", test_networkpolicy_template)
    run_test("12 topology-spread-constraints.yaml defines named templates", test_topology_spread_constraints_template)
    run_test("13 chaos-test.yaml PodChaos targeting us-east-1a pod-kill", test_chaos_test_yaml)
    run_test("14 All multi-AZ templates parse after stripping Go templates", test_all_templates_parse_after_stripping)
    run_test("15 worker.replicas=4, postgres replica enabled, standby_zones", test_worker_replicas_and_postgres_config)
    run_test("16 docs/phase-k-multi-az.md exists with required sections", test_docs_exist)

    total = len(_passed) + len(_failed)
    print("\n=== Results: {}/{} passed ===".format(len(_passed), total))
    if _failed:
        print("Failed: " + str(_failed))
        sys.exit(1)
    else:
        print("All tests passed!")
        sys.exit(0)
