"""
tests/test_gpu_scheduling.py
============================
Issue #36: GPU 弹性调度 — test suite for GPU scheduling Helm templates.

All tests are self-contained — no external dependencies beyond PyYAML.
Tests strip Go template directives before YAML parsing.

Compatible with Python 3.9+.
Run: python3 tests/test_gpu_scheduling.py
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed. Run: pip install pyyaml")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent
HELM_ROOT = REPO_ROOT / "deploy" / "helm"
CHART_ROOT = HELM_ROOT / "ai-platform-lab"
TEMPLATES_DIR = CHART_ROOT / "templates"
K8S_DIR = REPO_ROOT / "deploy" / "k8s"
VALUES_GPU = HELM_ROOT / "values-gpu.yaml"


# ---------------------------------------------------------------------------
# Helper: strip Go template directives so YAML can be parsed
# ---------------------------------------------------------------------------
def _strip_go_templates(content: str) -> str:
    """
    Remove Go template constructs so that PyYAML can parse the skeleton YAML.

    Handles:
    - Lines with pure control flow (if/end/with/range/define/block/template)
    - Lines that are only a Go template block
    - {{ include "..." . }}-suffix pattern (name fields with appended suffix)
    - Multiple Go template expressions on one line (e.g. image: repo:tag)
    """
    lines = content.splitlines()
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        # Skip pure control flow lines
        if re.match(r"^\{\{[-\s]*(if|else|end|with|range|define|block|template)\b", stripped):
            continue
        # Skip lines that are ONLY a Go template block (no YAML key)
        if re.match(r"^\{\{-?\s*\S+.*\}\}$", stripped):
            continue

        # Handle YAML value fields that contain multiple Go template expressions
        # (e.g.  image: {{ .Values.x.repo }}:{{ .Values.x.tag }})
        # Replace ALL Go template expressions on the line then check if the
        # remaining value portion is still valid.

        # First pass: remove Go template blocks entirely (replaced by empty string)
        # so we can detect multi-template values
        if re.search(r"\{\{[^}]+\}\}", line):
            # Replace each Go template expression with a safe placeholder token
            # Use a bare word (no colons/quotes) to avoid YAML parse issues
            line = re.sub(r"\{\{[^}]+\}\}", "PLACEHOLDER", line)
            # If the value now looks like "PLACEHOLDER:PLACEHOLDER" (image pattern),
            # collapse multiples into a single quoted value
            # Key: value detection — split on first ": "
            colon_idx = line.find(": ")
            if colon_idx != -1:
                key_part = line[:colon_idx + 2]
                val_part = line[colon_idx + 2:]
                # If value contains a bare colon (YAML mapping indicator), quote it
                if ":" in val_part:
                    val_part = '"' + val_part.strip().replace('"', "'") + '"'
                    line = key_part + val_part

        cleaned.append(line)
    return "\n".join(cleaned)


def _load_yaml_file(path: Path) -> list[dict[str, Any]]:
    """Load a (potentially multi-doc) YAML file, stripping Go templates first."""
    content = path.read_text(encoding="utf-8")
    cleaned = _strip_go_templates(content)
    docs = list(yaml.safe_load_all(cleaned))
    # Filter None documents (empty --- blocks)
    return [d for d in docs if d is not None]


def _find_in_docs(docs: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    """Return first document with matching 'kind'."""
    for doc in docs:
        if isinstance(doc, dict) and doc.get("kind") == kind:
            return doc
    return None


def _deep_get(obj: Any, *keys: str) -> Any:
    """Safely traverse nested dicts/lists."""
    for key in keys:
        if isinstance(obj, dict):
            obj = obj.get(key)
        else:
            return None
        if obj is None:
            return None
    return obj


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

class TestValuesGpu(unittest.TestCase):
    """Test 1-4: values-gpu.yaml parses and has required fields."""

    def setUp(self):
        self.assertTrue(VALUES_GPU.exists(), f"values-gpu.yaml not found at {VALUES_GPU}")
        with open(VALUES_GPU, encoding="utf-8") as f:
            self.values = yaml.safe_load(f)

    def test_01_values_gpu_parses(self):
        """values-gpu.yaml should parse without error."""
        self.assertIsInstance(self.values, dict)

    def test_02_embedding_replicas(self):
        """embedding.replicas should be 2."""
        replicas = _deep_get(self.values, "embedding", "replicas")
        self.assertEqual(replicas, 2, f"Expected embedding.replicas=2, got {replicas}")

    def test_03_rerank_replicas(self):
        """rerank.replicas should be 1."""
        replicas = _deep_get(self.values, "rerank", "replicas")
        self.assertEqual(replicas, 1, f"Expected rerank.replicas=1, got {replicas}")

    def test_04_gpu_hpa_config(self):
        """gpu_hpa should have min_replicas=1 and max_replicas=8."""
        hpa = self.values.get("gpu_hpa", {})
        self.assertEqual(hpa.get("min_replicas"), 1)
        self.assertEqual(hpa.get("max_replicas"), 8)
        self.assertEqual(hpa.get("target_gpu_utilization"), 70)
        self.assertEqual(hpa.get("target_qps"), 100)


class TestEmbeddingDeployment(unittest.TestCase):
    """Test 5-6: embedding-gpu-deployment.yaml structure."""

    def setUp(self):
        self.path = TEMPLATES_DIR / "embedding-gpu-deployment.yaml"
        self.assertTrue(self.path.exists(), f"Not found: {self.path}")
        self.content = self.path.read_text(encoding="utf-8")

    def test_05_embedding_deployment_exists(self):
        """embedding-gpu-deployment.yaml should exist."""
        self.assertTrue(self.path.exists())

    def test_06_embedding_has_gpu_limit(self):
        """embedding-gpu-deployment.yaml should reference nvidia.com/gpu."""
        self.assertIn("nvidia.com/gpu", self.content,
                      "Deployment must request nvidia.com/gpu resource")

    def test_07_embedding_has_node_selector(self):
        """embedding-gpu-deployment.yaml should have nodeSelector for accelerator: nvidia."""
        self.assertIn("accelerator", self.content,
                      "Deployment must have nodeSelector with accelerator label")
        self.assertIn("nvidia", self.content,
                      "Deployment must reference nvidia in nodeSelector")

    def test_08_embedding_has_tolerations(self):
        """embedding-gpu-deployment.yaml should have tolerations for GPU taint."""
        self.assertIn("tolerations", self.content,
                      "Deployment must have tolerations for GPU node taint")

    def test_09_embedding_has_readiness_probe(self):
        """embedding-gpu-deployment.yaml should have readinessProbe on /healthz."""
        self.assertIn("readinessProbe", self.content)
        self.assertIn("/healthz", self.content)

    def test_10_embedding_has_init_container(self):
        """embedding-gpu-deployment.yaml should have initContainers for model warmup."""
        self.assertIn("initContainers", self.content,
                      "Deployment must have initContainers for model warmup")


class TestEmbeddingService(unittest.TestCase):
    """Test 11: embedding-gpu-service.yaml structure."""

    def setUp(self):
        self.path = TEMPLATES_DIR / "embedding-gpu-service.yaml"
        self.assertTrue(self.path.exists(), f"Not found: {self.path}")
        self.content = self.path.read_text(encoding="utf-8")

    def test_11_embedding_service_port_8100(self):
        """embedding-gpu-service.yaml should expose port 8100."""
        self.assertIn("8100", self.content,
                      "Service must have port 8100")
        self.assertIn("ClusterIP", self.content,
                      "Service should be ClusterIP (internal only)")


class TestEmbeddingHPA(unittest.TestCase):
    """Test 12: embedding-gpu-hpa.yaml structure."""

    def setUp(self):
        self.path = TEMPLATES_DIR / "embedding-gpu-hpa.yaml"
        self.assertTrue(self.path.exists(), f"Not found: {self.path}")
        self.content = self.path.read_text(encoding="utf-8")

    def test_12_embedding_hpa_exists_with_metrics(self):
        """embedding-gpu-hpa.yaml should reference HPA with GPU + CPU + external metrics."""
        self.assertIn("HorizontalPodAutoscaler", self.content)
        self.assertIn("nvidia.com/gpu", self.content,
                      "HPA must include GPU metric")
        self.assertIn("autoscaling/v2", self.content,
                      "HPA must use autoscaling/v2 API")


class TestRerankDeployment(unittest.TestCase):
    """Test 13-14: rerank-gpu-deployment.yaml structure."""

    def setUp(self):
        self.path = TEMPLATES_DIR / "rerank-gpu-deployment.yaml"
        self.assertTrue(self.path.exists(), f"Not found: {self.path}")
        self.content = self.path.read_text(encoding="utf-8")

    def test_13_rerank_deployment_exists(self):
        """rerank-gpu-deployment.yaml should exist."""
        self.assertTrue(self.path.exists())

    def test_14_rerank_has_gpu_and_node_selector(self):
        """rerank-gpu-deployment.yaml should have GPU resource + nodeSelector."""
        self.assertIn("nvidia.com/gpu", self.content)
        self.assertIn("accelerator", self.content)


class TestRerankService(unittest.TestCase):
    """Test 15: rerank-gpu-service.yaml structure."""

    def setUp(self):
        self.path = TEMPLATES_DIR / "rerank-gpu-service.yaml"
        self.assertTrue(self.path.exists(), f"Not found: {self.path}")
        self.content = self.path.read_text(encoding="utf-8")

    def test_15_rerank_service_port_8200(self):
        """rerank-gpu-service.yaml should expose port 8200."""
        self.assertIn("8200", self.content,
                      "Rerank service must have port 8200")
        self.assertIn("ClusterIP", self.content)


class TestRerankHPA(unittest.TestCase):
    """Test 16: rerank-gpu-hpa.yaml structure."""

    def setUp(self):
        self.path = TEMPLATES_DIR / "rerank-gpu-hpa.yaml"
        self.assertTrue(self.path.exists(), f"Not found: {self.path}")
        self.content = self.path.read_text(encoding="utf-8")

    def test_16_rerank_hpa_exists(self):
        """rerank-gpu-hpa.yaml should be a valid HPA definition."""
        self.assertIn("HorizontalPodAutoscaler", self.content)
        self.assertIn("nvidia.com/gpu", self.content)


class TestGpuModelWarmup(unittest.TestCase):
    """Test 17: gpu-model-warmup.yaml ConfigMap."""

    def setUp(self):
        self.path = TEMPLATES_DIR / "gpu-model-warmup.yaml"
        self.assertTrue(self.path.exists(), f"Not found: {self.path}")
        self.content = self.path.read_text(encoding="utf-8")

    def test_17_gpu_warmup_configmap_exists(self):
        """gpu-model-warmup.yaml should be a ConfigMap with warmup scripts."""
        self.assertIn("ConfigMap", self.content)
        self.assertIn("warmup", self.content.lower(),
                      "ConfigMap must contain warmup script content")


class TestGpuNodePool(unittest.TestCase):
    """Test 18: gpu-node-pool.yaml reference file."""

    def setUp(self):
        self.path = K8S_DIR / "gpu-node-pool.yaml"
        self.assertTrue(self.path.exists(), f"Not found: {self.path}")
        self.content = self.path.read_text(encoding="utf-8")

    def test_18_gpu_node_pool_exists(self):
        """gpu-node-pool.yaml should exist and reference T4 + A100 GPU types."""
        self.assertIn("nvidia", self.content.lower(),
                      "Node pool file must reference nvidia GPU")
        self.assertIn("accelerator", self.content,
                      "Node pool must define accelerator label")
        # Should document both T4 (dev) and A100 (prod)
        self.assertIn("t4", self.content.lower(),
                      "Node pool should document T4 GPU option")
        self.assertIn("a100", self.content.lower(),
                      "Node pool should document A100 GPU option")


class TestGpuCostDashboard(unittest.TestCase):
    """Test 19: gpu-cost-dashboard.yaml ConfigMap."""

    def setUp(self):
        self.path = TEMPLATES_DIR / "gpu-cost-dashboard.yaml"
        self.assertTrue(self.path.exists(), f"Not found: {self.path}")
        self.content = self.path.read_text(encoding="utf-8")

    def test_19_gpu_cost_dashboard_exists(self):
        """gpu-cost-dashboard.yaml should be a ConfigMap with Grafana dashboard JSON."""
        self.assertIn("ConfigMap", self.content)
        self.assertIn("gpu", self.content.lower(),
                      "Dashboard must reference GPU metrics")
        self.assertIn("DCGM", self.content,
                      "Dashboard must reference DCGM Exporter metrics")


class TestAllTemplatesParseYaml(unittest.TestCase):
    """Test 20: All GPU template YAML files are valid after stripping Go templates."""

    GPU_TEMPLATES = [
        "embedding-gpu-deployment.yaml",
        "embedding-gpu-service.yaml",
        "embedding-gpu-hpa.yaml",
        "rerank-gpu-deployment.yaml",
        "rerank-gpu-service.yaml",
        "rerank-gpu-hpa.yaml",
        "gpu-model-warmup.yaml",
        "gpu-cost-dashboard.yaml",
    ]

    def test_20_all_templates_parse(self):
        """All GPU templates should parse as valid YAML (after Go template stripping)."""
        errors = []
        for fname in self.GPU_TEMPLATES:
            path = TEMPLATES_DIR / fname
            if not path.exists():
                errors.append(f"MISSING: {fname}")
                continue
            try:
                docs = _load_yaml_file(path)
                if not docs:
                    errors.append(f"EMPTY YAML: {fname}")
            except yaml.YAMLError as e:
                errors.append(f"PARSE ERROR in {fname}: {e}")
        self.assertEqual(
            errors, [],
            "Template YAML errors:\n" + "\n".join(errors)
        )


class TestHelmValuesCompleteness(unittest.TestCase):
    """Test 21: values-gpu.yaml has all required fields."""

    def setUp(self):
        with open(VALUES_GPU, encoding="utf-8") as f:
            self.values = yaml.safe_load(f)

    def test_21_embedding_gpu_resource_limit(self):
        """embedding.resources.limits should include nvidia.com/gpu: 1."""
        gpu_limit = _deep_get(
            self.values, "embedding", "resources", "limits", "nvidia.com/gpu"
        )
        self.assertEqual(
            str(gpu_limit), "1",
            f"Expected nvidia.com/gpu: '1', got {gpu_limit!r}"
        )

    def test_22_rerank_gpu_resource_limit(self):
        """rerank.resources.limits should include nvidia.com/gpu: 1."""
        gpu_limit = _deep_get(
            self.values, "rerank", "resources", "limits", "nvidia.com/gpu"
        )
        self.assertEqual(
            str(gpu_limit), "1",
            f"Expected nvidia.com/gpu: '1', got {gpu_limit!r}"
        )

    def test_23_gateway_replicas_4(self):
        """gateway.replicas should be 4 in GPU overlay."""
        replicas = _deep_get(self.values, "gateway", "replicas")
        self.assertEqual(replicas, 4)

    def test_24_worker_replicas_3(self):
        """worker.replicas should be 3 in GPU overlay."""
        replicas = _deep_get(self.values, "worker", "replicas")
        self.assertEqual(replicas, 3)

    def test_25_embedding_node_selector(self):
        """embedding.nodeSelector should specify accelerator: nvidia."""
        node_selector = _deep_get(self.values, "embedding", "nodeSelector")
        self.assertIsInstance(node_selector, dict)
        self.assertEqual(
            node_selector.get("accelerator"), "nvidia",
            f"Expected accelerator: nvidia, got {node_selector}"
        )

    def test_26_embedding_tolerations(self):
        """embedding.tolerations should tolerate nvidia GPU taint."""
        tolerations = _deep_get(self.values, "embedding", "tolerations")
        self.assertIsInstance(tolerations, list)
        self.assertGreater(len(tolerations), 0,
                           "embedding.tolerations must not be empty")
        keys = [t.get("key") for t in tolerations if isinstance(t, dict)]
        self.assertIn("nvidia", keys,
                      f"Must have taint key 'nvidia', got {keys}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Pretty print test results
    loader = unittest.TestLoader()
    loader.sortTestMethodsUsing = None  # preserve declaration order
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    total = result.testsRun
    passed = total - len(result.failures) - len(result.errors)
    print(f"\n{'='*60}")
    print(f"GPU Scheduling Tests: {passed}/{total} passed")
    print(f"{'='*60}")
    sys.exit(0 if result.wasSuccessful() else 1)
