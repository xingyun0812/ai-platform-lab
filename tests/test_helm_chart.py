"""
tests/test_helm_chart.py
Python 3.9-compatible tests for the ai-platform-lab Helm chart.
No Helm CLI required — validates chart structure, YAML syntax,
and key configuration via direct file reads.
"""
from __future__ import annotations

import os
import re
import sys

import yaml

# ---- Resolve chart root relative to this file --------------------------
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_TESTS_DIR)
_CHART_DIR = os.path.join(_REPO_ROOT, "deploy", "helm", "ai-platform-lab")
_TEMPLATES_DIR = os.path.join(_CHART_DIR, "templates")
_DEPLOY_HELM_DIR = os.path.join(_REPO_ROOT, "deploy", "helm")


# ---- Helpers ------------------------------------------------------------

def strip_go_templates(text: str) -> str:
    """
    Replace Go template directives {{ ... }} with safe YAML placeholders
    so the file can be parsed by yaml.safe_load for structural testing.
    Multi-line blocks like {{- if ... }} ... {{- end }} are replaced too.
    """
    # Remove block-level template tags (lines that are only template tags)
    text = re.sub(r'^\s*\{\{-?\s*.*?-?\}\}\s*$', '', text, flags=re.MULTILINE)
    # Replace inline template expressions with placeholder strings
    text = re.sub(r'\{\{-?.*?-?\}\}', '"__TEMPLATE__"', text, flags=re.DOTALL)
    return text


def load_yaml_file(path: str) -> object:
    """Load a YAML file, returning None if empty after stripping templates."""
    with open(path) as fh:
        raw = fh.read()
    cleaned = strip_go_templates(raw)
    # Use safe_load_all to handle multi-document YAML
    docs = list(yaml.safe_load_all(cleaned))
    docs = [d for d in docs if d is not None]
    if not docs:
        return None
    return docs[0] if len(docs) == 1 else docs


def _read_raw(path: str) -> str:
    with open(path) as fh:
        return fh.read()


# ---- Test runner --------------------------------------------------------

_passed: list[str] = []
_failed: list[str] = []


def test(name: str, fn) -> None:
    try:
        fn()
        _passed.append(name)
        print(f"  [PASS] {name}")
    except Exception as exc:
        _failed.append(name)
        print(f"  [FAIL] {name}: {exc}")


# ---- Test cases ---------------------------------------------------------

def test_chart_yaml_valid():
    """Chart.yaml exists and has required apiVersion, name, version."""
    path = os.path.join(_CHART_DIR, "Chart.yaml")
    assert os.path.exists(path), f"Chart.yaml not found at {path}"
    with open(path) as fh:
        data = yaml.safe_load(fh)
    assert data["apiVersion"] == "v2", "apiVersion must be v2"
    assert data["name"] == "ai-platform-lab", f"name mismatch: {data['name']}"
    assert "version" in data, "version field missing"
    assert "appVersion" in data, "appVersion field missing"


def test_values_yaml_parses():
    """values.yaml exists and is valid YAML."""
    path = os.path.join(_CHART_DIR, "values.yaml")
    assert os.path.exists(path), "values.yaml not found"
    with open(path) as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), "values.yaml root must be a mapping"


def test_values_has_required_top_level_keys():
    """values.yaml has all required top-level sections."""
    path = os.path.join(_CHART_DIR, "values.yaml")
    with open(path) as fh:
        data = yaml.safe_load(fh)
    required = ["global", "gateway", "worker", "qdrant", "redis", "postgres", "hpa", "config", "secrets", "ingress"]
    for key in required:
        assert key in data, f"Missing key in values.yaml: {key}"


def test_gateway_values_structure():
    """values.yaml gateway section has image, replicas, resources, service."""
    path = os.path.join(_CHART_DIR, "values.yaml")
    with open(path) as fh:
        data = yaml.safe_load(fh)
    gw = data["gateway"]
    assert "image" in gw, "gateway.image missing"
    assert "repository" in gw["image"], "gateway.image.repository missing"
    assert "replicas" in gw, "gateway.replicas missing"
    assert gw["replicas"] >= 1, "gateway.replicas must be >= 1"
    assert "resources" in gw, "gateway.resources missing"
    assert "service" in gw, "gateway.service missing"
    assert gw["service"]["port"] == 8000, "gateway.service.port must be 8000"


def test_hpa_min_max_valid():
    """HPA minReplicas <= maxReplicas for gateway and worker."""
    path = os.path.join(_CHART_DIR, "values.yaml")
    with open(path) as fh:
        data = yaml.safe_load(fh)
    hpa = data["hpa"]
    gw_hpa = hpa["gateway"]
    assert gw_hpa["minReplicas"] <= gw_hpa["maxReplicas"], \
        f"gateway HPA min ({gw_hpa['minReplicas']}) > max ({gw_hpa['maxReplicas']})"
    wk_hpa = hpa["worker"]
    assert wk_hpa["minReplicas"] <= wk_hpa["maxReplicas"], \
        f"worker HPA min ({wk_hpa['minReplicas']}) > max ({wk_hpa['maxReplicas']})"
    # Check default values match requirements
    assert gw_hpa["minReplicas"] == 2, "gateway HPA default minReplicas should be 2"
    assert gw_hpa["maxReplicas"] == 10, "gateway HPA default maxReplicas should be 10"
    assert gw_hpa["targetCPUUtilizationPercentage"] == 70, "gateway HPA CPU target should be 70"
    assert wk_hpa["minReplicas"] == 1, "worker HPA default minReplicas should be 1"
    assert wk_hpa["maxReplicas"] == 5, "worker HPA default maxReplicas should be 5"


def test_all_expected_template_files_exist():
    """All expected template files are present."""
    expected = [
        "_helpers.tpl",
        "configmap.yaml",
        "secret.yaml",
        "serviceaccount.yaml",
        "gateway-deployment.yaml",
        "gateway-service.yaml",
        "gateway-hpa.yaml",
        "worker-deployment.yaml",
        "worker-hpa.yaml",
        "qdrant-statefulset.yaml",
        "qdrant-service.yaml",
        "redis-deployment.yaml",
        "postgres-statefulset.yaml",
        "ingress.yaml",
    ]
    for fname in expected:
        fpath = os.path.join(_TEMPLATES_DIR, fname)
        assert os.path.exists(fpath), f"Template file not found: {fname}"


def test_template_files_have_go_template_syntax():
    """All .yaml template files use Go template syntax ({{ }})."""
    template_files = [
        f for f in os.listdir(_TEMPLATES_DIR)
        if f.endswith(".yaml")
    ]
    assert len(template_files) > 0, "No .yaml template files found"
    for fname in template_files:
        fpath = os.path.join(_TEMPLATES_DIR, fname)
        content = _read_raw(fpath)
        assert "{{" in content and "}}" in content, \
            f"{fname} does not contain Go template syntax"


def test_gateway_deployment_has_image_and_probes():
    """gateway-deployment.yaml references image template and probes."""
    path = os.path.join(_TEMPLATES_DIR, "gateway-deployment.yaml")
    content = _read_raw(path)
    # Should reference the image helper
    assert "ai-platform-lab.gateway-image" in content, \
        "gateway-deployment.yaml should reference ai-platform-lab.gateway-image"
    # Should have readiness and liveness probe sections
    assert "readinessProbe" in content, "readinessProbe missing in gateway-deployment"
    assert "livenessProbe" in content, "livenessProbe missing in gateway-deployment"
    # Should reference selectorLabels helper
    assert "ai-platform-lab.selectorLabels" in content, \
        "gateway-deployment.yaml should use ai-platform-lab.selectorLabels"


def test_worker_deployment_has_image():
    """worker-deployment.yaml references worker image template."""
    path = os.path.join(_TEMPLATES_DIR, "worker-deployment.yaml")
    content = _read_raw(path)
    assert "ai-platform-lab.worker-image" in content, \
        "worker-deployment.yaml should reference ai-platform-lab.worker-image"
    # Worker should have TIER=worker env
    assert "TIER" in content, "worker-deployment.yaml should set TIER env var"


def test_secret_template_references_secretkeyref():
    """secret.yaml and deployments reference secretKeyRef for sensitive values."""
    secret_path = os.path.join(_TEMPLATES_DIR, "secret.yaml")
    deploy_path = os.path.join(_TEMPLATES_DIR, "gateway-deployment.yaml")
    secret_content = _read_raw(secret_path)
    deploy_content = _read_raw(deploy_path)
    # secret.yaml should have b64enc
    assert "b64enc" in secret_content, \
        "secret.yaml should base64-encode values using b64enc"
    # gateway deployment should reference secretKeyRef
    assert "secretKeyRef" in deploy_content, \
        "gateway-deployment.yaml should use secretKeyRef for secret values"
    # Check LLM_API_KEY is referenced
    assert "LLM_API_KEY" in deploy_content, \
        "gateway-deployment.yaml should set LLM_API_KEY from secret"


def test_hpa_templates_use_autoscaling_v2():
    """HPA templates use autoscaling/v2 API."""
    for hpa_file in ["gateway-hpa.yaml", "worker-hpa.yaml"]:
        path = os.path.join(_TEMPLATES_DIR, hpa_file)
        content = _read_raw(path)
        assert "autoscaling/v2" in content, \
            f"{hpa_file} must use autoscaling/v2"
        assert "HorizontalPodAutoscaler" in content, \
            f"{hpa_file} must define HorizontalPodAutoscaler"
        assert "minReplicas" in content, f"minReplicas missing in {hpa_file}"
        assert "maxReplicas" in content, f"maxReplicas missing in {hpa_file}"


def test_values_prod_yaml_parses():
    """values-prod.yaml exists and parses as valid YAML."""
    path = os.path.join(_DEPLOY_HELM_DIR, "values-prod.yaml")
    assert os.path.exists(path), f"values-prod.yaml not found at {path}"
    with open(path) as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), "values-prod.yaml root must be a mapping"
    # Prod should set higher replicas
    assert "gateway" in data, "values-prod.yaml should have gateway section"


def test_readme_exists_and_has_install_command():
    """deploy/helm/README.md exists and contains helm install instructions."""
    path = os.path.join(_DEPLOY_HELM_DIR, "README.md")
    assert os.path.exists(path), f"README.md not found at {path}"
    content = _read_raw(path)
    assert "helm install" in content, "README.md should contain 'helm install'"
    assert "helm upgrade" in content, "README.md should contain 'helm upgrade'"
    assert "helm uninstall" in content, "README.md should contain 'helm uninstall'"


def test_qdrant_statefulset_has_pvc():
    """qdrant-statefulset.yaml references PVC for persistence."""
    path = os.path.join(_TEMPLATES_DIR, "qdrant-statefulset.yaml")
    content = _read_raw(path)
    assert "volumeClaimTemplates" in content, \
        "qdrant-statefulset.yaml must define volumeClaimTemplates"
    assert "StatefulSet" in content, \
        "qdrant-statefulset.yaml must define StatefulSet kind"


def test_helpers_tpl_defines_required_templates():
    """_helpers.tpl defines all required named templates."""
    path = os.path.join(_TEMPLATES_DIR, "_helpers.tpl")
    content = _read_raw(path)
    required_defines = [
        "ai-platform-lab.name",
        "ai-platform-lab.fullname",
        "ai-platform-lab.labels",
        "ai-platform-lab.selectorLabels",
        "ai-platform-lab.gateway-image",
        "ai-platform-lab.worker-image",
    ]
    for define in required_defines:
        assert define in content, f"_helpers.tpl missing template: {define}"


def test_configmap_template_uses_config_values():
    """configmap.yaml iterates over .Values.config."""
    path = os.path.join(_TEMPLATES_DIR, "configmap.yaml")
    content = _read_raw(path)
    assert "ConfigMap" in content, "configmap.yaml must define ConfigMap kind"
    assert ".Values.config" in content, \
        "configmap.yaml should reference .Values.config"


def test_ingress_template_has_tls_support():
    """ingress.yaml supports optional TLS configuration."""
    path = os.path.join(_TEMPLATES_DIR, "ingress.yaml")
    content = _read_raw(path)
    assert "Ingress" in content, "ingress.yaml must define Ingress kind"
    assert "tls" in content.lower(), "ingress.yaml must support TLS"
    assert "ingress.enabled" in content or "if .Values.ingress.enabled" in content, \
        "ingress.yaml must be gated by .Values.ingress.enabled"


def test_chart_yaml_has_keywords_and_maintainers():
    """Chart.yaml has keywords list and maintainers."""
    path = os.path.join(_CHART_DIR, "Chart.yaml")
    with open(path) as fh:
        data = yaml.safe_load(fh)
    assert "keywords" in data, "Chart.yaml missing keywords"
    assert isinstance(data["keywords"], list), "keywords must be a list"
    assert len(data["keywords"]) > 0, "keywords must not be empty"
    assert "maintainers" in data, "Chart.yaml missing maintainers"
    assert isinstance(data["maintainers"], list), "maintainers must be a list"


def test_kustomization_yaml_exists():
    """deploy/k8s/kustomization.yaml exists and parses."""
    path = os.path.join(_REPO_ROOT, "deploy", "k8s", "kustomization.yaml")
    assert os.path.exists(path), f"kustomization.yaml not found at {path}"
    with open(path) as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict), "kustomization.yaml must be a mapping"
    assert data.get("apiVersion") == "kustomize.config.k8s.io/v1beta1"


def test_docs_phase_k_helm_exists():
    """docs/phase-k-helm.md design doc exists."""
    path = os.path.join(_REPO_ROOT, "docs", "phase-k-helm.md")
    assert os.path.exists(path), f"docs/phase-k-helm.md not found at {path}"
    content = _read_raw(path)
    # Should contain key sections
    assert "Helm" in content, "design doc should mention Helm"
    assert "HPA" in content, "design doc should mention HPA"


# ---- Main ---------------------------------------------------------------

if __name__ == "__main__":
    print("\n=== Helm Chart Tests ===\n")

    test("01 Chart.yaml valid (apiVersion v2, name, version)", test_chart_yaml_valid)
    test("02 values.yaml parses as valid YAML", test_values_yaml_parses)
    test("03 values.yaml has required top-level keys", test_values_has_required_top_level_keys)
    test("04 gateway values structure (image, replicas, resources, service port 8000)", test_gateway_values_structure)
    test("05 HPA minReplicas <= maxReplicas for gateway and worker", test_hpa_min_max_valid)
    test("06 All expected template files exist", test_all_expected_template_files_exist)
    test("07 All template files contain Go template syntax {{ }}", test_template_files_have_go_template_syntax)
    test("08 gateway-deployment uses image helper and probes", test_gateway_deployment_has_image_and_probes)
    test("09 worker-deployment uses worker image and sets TIER", test_worker_deployment_has_image)
    test("10 secret.yaml uses b64enc; gateway uses secretKeyRef for LLM_API_KEY", test_secret_template_references_secretkeyref)
    test("11 HPA templates use autoscaling/v2", test_hpa_templates_use_autoscaling_v2)
    test("12 values-prod.yaml parses and has gateway section", test_values_prod_yaml_parses)
    test("13 README.md has helm install / upgrade / uninstall", test_readme_exists_and_has_install_command)
    test("14 qdrant-statefulset.yaml has volumeClaimTemplates", test_qdrant_statefulset_has_pvc)
    test("15 _helpers.tpl defines all required named templates", test_helpers_tpl_defines_required_templates)
    test("16 configmap.yaml references .Values.config", test_configmap_template_uses_config_values)
    test("17 ingress.yaml supports TLS and is gated by .Values.ingress.enabled", test_ingress_template_has_tls_support)
    test("18 Chart.yaml has keywords and maintainers", test_chart_yaml_has_keywords_and_maintainers)
    test("19 deploy/k8s/kustomization.yaml exists and parses", test_kustomization_yaml_exists)
    test("20 docs/phase-k-helm.md design doc exists", test_docs_phase_k_helm_exists)

    total = len(_passed) + len(_failed)
    print(f"\n=== Results: {len(_passed)}/{total} passed ===")
    if _failed:
        print(f"Failed: {_failed}")
        sys.exit(1)
    else:
        print("All tests passed!")
        sys.exit(0)
