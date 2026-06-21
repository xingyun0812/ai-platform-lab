#!/usr/bin/env python3
"""PII 脱敏 + 内容安全 单元测试 — Phase I #43

运行：
    python3 tests/test_pii.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import tempfile
import threading
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径设置：确保 Python 3.9 下可直接 import packages.pii.*
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _load_module(name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(name, file_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# 按依赖顺序预加载
_load_module("packages.pii.detectors", REPO_ROOT / "packages/pii/detectors.py")
_load_module("packages.pii.redactor", REPO_ROOT / "packages/pii/redactor.py")
_load_module("packages.pii.content_safety", REPO_ROOT / "packages/pii/content_safety.py")
_load_module("packages.pii.service", REPO_ROOT / "packages/pii/service.py")

from packages.pii.detectors import (  # noqa: E402
    PIIPattern,
    PIIMatch,
    PIIDetector,
    init_detector,
    get_detector,
    reset_detector_for_tests,
)
from packages.pii.redactor import (  # noqa: E402
    RedactionPolicy,
    RedactionResult,
    Redactor,
    init_redactor,
    get_redactor,
    reset_redactor_for_tests,
)
from packages.pii.content_safety import (  # noqa: E402
    ContentSafetyResult,
    ContentSafetyChecker,
    init_safety_checker,
    get_safety_checker,
    reset_safety_checker_for_tests,
)
from packages.pii.service import (  # noqa: E402
    PIIService,
    init_pii_service,
    get_pii_service,
    reset_for_tests,
)

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

_PASS = 0
_FAIL = 0


def _run_async(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _setup():
    """每个测试前重置单例。"""
    reset_for_tests()


def _ok(name: str) -> None:
    global _PASS
    _PASS += 1
    print(f"PASS  {name}")


def _err(name: str, exc: Exception) -> None:
    global _FAIL
    _FAIL += 1
    print(f"FAIL  {name}  →  {exc}")
    import traceback
    traceback.print_exc()


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------


def test_pii_pattern_dataclass():
    """PIIPattern 数据类可正常创建，to_dict 输出完整。"""
    try:
        p = PIIPattern(
            pattern_id="test_email",
            name="Test Email",
            regex=r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
            entity_type="email",
            confidence=0.95,
            redaction_template="[REDACTED_EMAIL]",
        )
        d = p.to_dict()
        assert d["pattern_id"] == "test_email"
        assert d["entity_type"] == "email"
        assert d["confidence"] == 0.95
        _ok("test_pii_pattern_dataclass")
    except Exception as e:
        _err("test_pii_pattern_dataclass", e)


def test_detector_register_list_remove():
    """PIIDetector register / list / remove 正常工作。"""
    try:
        _setup()
        det = init_detector()
        initial_count = len(det.list_patterns())
        assert initial_count > 0  # 默认模式已加载

        custom = PIIPattern(
            pattern_id="custom_test",
            name="Custom Test",
            regex=r"\bTEST\d{4}\b",
            entity_type="custom",
            confidence=0.8,
            redaction_template="[REDACTED_CUSTOM]",
        )
        det.register_pattern(custom)
        assert len(det.list_patterns()) == initial_count + 1

        ok = det.remove_pattern("custom_test")
        assert ok is True
        assert len(det.list_patterns()) == initial_count

        ok2 = det.remove_pattern("nonexistent")
        assert ok2 is False

        _ok("test_detector_register_list_remove")
    except Exception as e:
        _err("test_detector_register_list_remove", e)


def test_detect_email():
    """检测邮件地址。"""
    try:
        _setup()
        det = init_detector()
        matches = det.detect("请联系 alice@example.com 或 bob.smith+tag@company.org")
        emails = [m for m in matches if m.entity_type == "email"]
        assert len(emails) == 2, f"Expected 2 emails, got {len(emails)}: {[m.matched_text for m in emails]}"
        assert any("alice@example.com" in m.matched_text for m in emails)
        _ok("test_detect_email")
    except Exception as e:
        _err("test_detect_email", e)


def test_detect_phone_us():
    """检测美国手机号。"""
    try:
        _setup()
        det = init_detector()
        matches = det.detect("Call me at (555) 123-4567 or 555-987-6543")
        phones = [m for m in matches if m.entity_type == "phone"]
        assert len(phones) >= 2, f"Expected ≥2 phones, got {[m.matched_text for m in phones]}"
        _ok("test_detect_phone_us")
    except Exception as e:
        _err("test_detect_phone_us", e)


def test_detect_ssn():
    """检测社会安全号（SSN）。"""
    try:
        _setup()
        det = init_detector()
        matches = det.detect("My SSN is 123-45-6789")
        ssns = [m for m in matches if m.entity_type == "ssn"]
        assert len(ssns) == 1, f"Expected 1 SSN, got {[m.matched_text for m in ssns]}"
        assert ssns[0].matched_text == "123-45-6789"
        _ok("test_detect_ssn")
    except Exception as e:
        _err("test_detect_ssn", e)


def test_detect_credit_card():
    """检测信用卡号。"""
    try:
        _setup()
        det = init_detector()
        matches = det.detect("Card: 4532015112830366")
        cards = [m for m in matches if m.entity_type == "credit_card"]
        assert len(cards) >= 1, f"Expected ≥1 credit card, got {[m.matched_text for m in cards]}"
        _ok("test_detect_credit_card")
    except Exception as e:
        _err("test_detect_credit_card", e)


def test_detect_ipv4():
    """检测 IPv4 地址。"""
    try:
        _setup()
        det = init_detector()
        matches = det.detect("Server IP: 192.168.1.100 and 10.0.0.1")
        ips = [m for m in matches if m.entity_type == "ip"]
        assert len(ips) == 2, f"Expected 2 IPs, got {[m.matched_text for m in ips]}"
        _ok("test_detect_ipv4")
    except Exception as e:
        _err("test_detect_ipv4", e)


def test_detect_cn_id_card():
    """检测中国居民身份证号。"""
    try:
        _setup()
        det = init_detector()
        # 18 位身份证（含校验位）
        matches = det.detect("身份证: 11010519491231002X")
        id_cards = [m for m in matches if m.entity_type == "id_card"]
        assert len(id_cards) >= 1, f"Expected ≥1 ID card, got {[m.matched_text for m in id_cards]}"
        _ok("test_detect_cn_id_card")
    except Exception as e:
        _err("test_detect_cn_id_card", e)


def test_detect_cn_phone():
    """检测中国手机号。"""
    try:
        _setup()
        det = init_detector()
        matches = det.detect("联系方式: 13812345678 或 15987654321")
        phones = [m for m in matches if m.entity_type == "phone"]
        assert len(phones) >= 2, f"Expected ≥2 CN phones, got {[m.matched_text for m in phones]}"
        _ok("test_detect_cn_phone")
    except Exception as e:
        _err("test_detect_cn_phone", e)


def test_detect_all_grouped():
    """detect_all 按 entity_type 分组正常。"""
    try:
        _setup()
        det = init_detector()
        grouped = det.detect_all("Email: test@example.com IP: 1.2.3.4")
        assert "email" in grouped
        assert "ip" in grouped
        assert len(grouped["email"]) >= 1
        assert len(grouped["ip"]) >= 1
        _ok("test_detect_all_grouped")
    except Exception as e:
        _err("test_detect_all_grouped", e)


def test_redaction_policy_redact():
    """RedactionPolicy action=redact 替换为模板。"""
    try:
        _setup()
        init_pii_service()
        red = get_redactor()
        assert red is not None
        result = red.redact("Email me at john@example.com please", policy_id="default")
        assert "john@example.com" not in result.redacted
        assert "[REDACTED_EMAIL]" in result.redacted
        assert result.matches_count >= 1
        _ok("test_redaction_policy_redact")
    except Exception as e:
        _err("test_redaction_policy_redact", e)


def test_redaction_policy_mask():
    """RedactionPolicy action=mask 保留首尾字符。"""
    try:
        _setup()
        init_pii_service()
        red = get_redactor()
        assert red is not None
        result = red.redact("Email me at john@example.com", policy_id="mask")
        assert "john@example.com" not in result.redacted
        # 应保留前 2 字符 "jo"
        assert result.redacted.startswith("Email me at jo") or "jo" in result.redacted
        assert result.matches_count >= 1
        _ok("test_redaction_policy_mask")
    except Exception as e:
        _err("test_redaction_policy_mask", e)


def test_redaction_policy_hash():
    """RedactionPolicy action=hash 用 SHA256 前 8 位替换。"""
    try:
        _setup()
        init_pii_service()
        red = get_redactor()

        # 注册 hash 策略
        from packages.pii.redactor import RedactionPolicy
        hash_policy = RedactionPolicy(
            policy_id="hash_test",
            entity_types=[],
            action="hash",
            hash_salt="mysalt",
        )
        red.register_policy(hash_policy)
        result = red.redact("Email: test@example.com", policy_id="hash_test")
        assert result.matches_count >= 1
        # hash 结果是 8 位 hex
        for r in result.redactions:
            assert len(r["redaction"]) == 8, f"Hash length: {len(r['redaction'])}"
        _ok("test_redaction_policy_hash")
    except Exception as e:
        _err("test_redaction_policy_hash", e)


def test_redaction_policy_block():
    """RedactionPolicy action=block 若有 PII 则返回空字符串。"""
    try:
        _setup()
        init_pii_service()
        red = get_redactor()
        assert red is not None
        result = red.redact("Email: secret@example.com", policy_id="strict")
        assert result.redacted == ""
        assert result.matches_count >= 1
        _ok("test_redaction_policy_block")
    except Exception as e:
        _err("test_redaction_policy_block", e)


def test_mask_keep_first_last():
    """mask 动作保留首 2、尾 2 字符，中间用 * 替换。"""
    try:
        from packages.pii.redactor import Redactor
        r = Redactor()
        masked = r._mask("hello_world_long", "*", 2, 2)
        assert masked.startswith("he"), f"Got: {masked}"
        assert masked.endswith("ng"), f"Got: {masked}"
        assert "*" in masked
        _ok("test_mask_keep_first_last")
    except Exception as e:
        _err("test_mask_keep_first_last", e)


def test_content_safety_keyword_detection():
    """ContentSafetyChecker 关键词检测正常工作。"""
    try:
        _setup()
        checker = ContentSafetyChecker()
        # 安全文本
        safe = checker.check("今天天气真好，我想去公园散步。")
        assert safe.safe is True
        # 包含暴力关键词
        unsafe = checker.check("I want to kill all enemies with a bomb weapon")
        assert unsafe.safe is False
        assert unsafe.categories.get("violence") is True
        assert unsafe.blocked_reason is not None
        _ok("test_content_safety_keyword_detection")
    except Exception as e:
        _err("test_content_safety_keyword_detection", e)


def test_content_safety_register_keyword():
    """动态注册关键词后能检测到。"""
    try:
        checker = ContentSafetyChecker()
        checker.register_keyword("xyzabc_custom", category="custom")
        result = checker.check("This text contains xyzabc_custom word")
        assert result.safe is False
        assert result.categories.get("custom") is True
        _ok("test_content_safety_register_keyword")
    except Exception as e:
        _err("test_content_safety_register_keyword", e)


def test_pii_service_full_process():
    """PIIService.process 完整流水线返回结构正确。"""
    try:
        _setup()
        svc = init_pii_service()

        async def run():
            result = await svc.process(
                "Contact alice@example.com, phone 13812345678",
                policy_id="default",
                check_safety=True,
            )
            assert "original" in result
            assert "pii_detected" in result
            assert result["pii_detected"] is True
            assert "redaction" in result
            assert "safety" in result
            assert result["safety"] is not None

        _run_async(run())
        _ok("test_pii_service_full_process")
    except Exception as e:
        _err("test_pii_service_full_process", e)


def test_pii_service_no_pii():
    """无 PII 的纯文本，process 应返回 pii_detected=False。"""
    try:
        _setup()
        svc = init_pii_service()

        async def run():
            result = await svc.process("Hello, how are you?", check_safety=False)
            assert result["pii_detected"] is False
            assert result["safety"] is None  # check_safety=False

        _run_async(run())
        _ok("test_pii_service_no_pii")
    except Exception as e:
        _err("test_pii_service_no_pii", e)


def test_yaml_load():
    """从 YAML 文件加载自定义 PII 模式。"""
    try:
        _setup()
        import yaml

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            yaml.dump(
                [
                    {
                        "pattern_id": "yaml_test",
                        "name": "YAML Test Pattern",
                        "regex": r"\bYAML\d{3}\b",
                        "entity_type": "custom",
                        "confidence": 0.7,
                        "redaction_template": "[REDACTED_YAML]",
                    }
                ],
                f,
            )
            yaml_path = Path(f.name)

        det = init_detector(yaml_path=yaml_path)
        ids = [p.pattern_id for p in det.list_patterns()]
        assert "yaml_test" in ids, f"YAML pattern not loaded. Patterns: {ids}"

        matches = det.detect("Code YAML123 is here")
        customs = [m for m in matches if m.pattern_id == "yaml_test"]
        assert len(customs) >= 1

        yaml_path.unlink(missing_ok=True)
        _ok("test_yaml_load")
    except Exception as e:
        _err("test_yaml_load", e)


def test_json_overrides_load():
    """从 JSON 文件覆盖 PII 模式配置。"""
    try:
        _setup()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(
                [
                    {
                        "pattern_id": "json_override_test",
                        "name": "JSON Override",
                        "regex": r"\bOVERRIDE\d{4}\b",
                        "entity_type": "custom",
                        "confidence": 0.75,
                        "redaction_template": "[REDACTED_OVERRIDE]",
                    }
                ],
                f,
            )
            json_path = Path(f.name)

        det = init_detector(overrides_path=json_path)
        ids = [p.pattern_id for p in det.list_patterns()]
        assert "json_override_test" in ids, f"JSON override not loaded. Patterns: {ids}"

        json_path.unlink(missing_ok=True)
        _ok("test_json_overrides_load")
    except Exception as e:
        _err("test_json_overrides_load", e)


def test_singleton_pattern():
    """全局单例 init/get/reset 生命周期正确。"""
    try:
        _setup()
        assert get_pii_service() is None
        svc1 = init_pii_service()
        svc2 = get_pii_service()
        assert svc1 is svc2
        reset_for_tests()
        assert get_pii_service() is None
        _ok("test_singleton_pattern")
    except Exception as e:
        _err("test_singleton_pattern", e)


def test_thread_safety_detect():
    """并发检测不崩溃，结果一致。"""
    try:
        _setup()
        det = init_detector()
        text = "Email: bob@test.org, phone 13900139000"
        results = []
        errors = []

        def worker():
            try:
                results.append(len(det.detect(text)))
            except Exception as ex:
                errors.append(ex)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Thread errors: {errors}"
        # 所有线程返回相同的 match 数
        assert len(set(results)) == 1, f"Inconsistent results: {set(results)}"
        _ok("test_thread_safety_detect")
    except Exception as e:
        _err("test_thread_safety_detect", e)


def test_redaction_no_pii_unchanged():
    """无 PII 文本 redact 后原样返回，matches_count=0。"""
    try:
        _setup()
        init_pii_service()
        red = get_redactor()
        text = "Hello world, this is a normal sentence."
        result = red.redact(text, policy_id="default")
        assert result.redacted == text
        assert result.matches_count == 0
        _ok("test_redaction_no_pii_unchanged")
    except Exception as e:
        _err("test_redaction_no_pii_unchanged", e)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_pii_pattern_dataclass()
    test_detector_register_list_remove()
    test_detect_email()
    test_detect_phone_us()
    test_detect_ssn()
    test_detect_credit_card()
    test_detect_ipv4()
    test_detect_cn_id_card()
    test_detect_cn_phone()
    test_detect_all_grouped()
    test_redaction_policy_redact()
    test_redaction_policy_mask()
    test_redaction_policy_hash()
    test_redaction_policy_block()
    test_mask_keep_first_last()
    test_content_safety_keyword_detection()
    test_content_safety_register_keyword()
    test_pii_service_full_process()
    test_pii_service_no_pii()
    test_yaml_load()
    test_json_overrides_load()
    test_singleton_pattern()
    test_thread_safety_detect()
    test_redaction_no_pii_unchanged()

    total = _PASS + _FAIL
    print(f"\n{'='*50}")
    print(f"Results: {_PASS}/{total} passed, {_FAIL} failed")
    if _FAIL > 0:
        sys.exit(1)
