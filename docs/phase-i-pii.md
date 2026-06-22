# Phase I — PII 脱敏 + 内容安全

> Issue #43 · Phase I · 状态：已实现

---

## 1. 设计目标

构建思路、使用链路与逐文件代码说明见 [phase-i-build-and-code-guide.md](./phase-i-build-and-code-guide.md)。

在 AI 平台网关层提供统一的 **PII（个人身份信息）检测与脱敏**及**内容安全过滤**能力，满足 GDPR、CCPA 等合规要求，保护用户隐私，防止敏感数据泄露至 LLM 提供商。

核心原则：
- **渐进降级**：功能未启用时返回透传，不崩溃；
- **可扩展**：正则模式 + 策略均支持运行时注册；
- **线程安全**：所有注册表均使用 `threading.RLock`；
- **stub-friendly**：内容安全当前为关键词规则，可平滑升级为 LLM Moderation API。

---

## 2. 数据模型

### 2.1 PIIPattern

| 字段 | 类型 | 说明 |
|------|------|------|
| `pattern_id` | `str` | 唯一标识，如 `email` |
| `name` | `str` | 人类可读名称 |
| `regex` | `str` | 正则表达式 |
| `entity_type` | `str` | `email`/`phone`/`ssn`/`credit_card`/`ip`/`id_card`/`custom` |
| `confidence` | `float` | 置信度 0–1 |
| `redaction_template` | `str` | 替换模板，如 `[REDACTED_EMAIL]` |
| `enabled` | `bool` | 是否启用 |

### 2.2 PIIMatch

| 字段 | 类型 | 说明 |
|------|------|------|
| `pattern_id` | `str` | 匹配到的模式 ID |
| `start` / `end` | `int` | 字符位置 |
| `matched_text` | `str` | 原始命中文本 |
| `entity_type` | `str` | 实体类型 |
| `confidence` | `float` | 置信度 |

### 2.3 RedactionPolicy

| 字段 | 类型 | 说明 |
|------|------|------|
| `policy_id` | `str` | 策略 ID |
| `entity_types` | `list[str]` | 空列表 = 匹配所有类型 |
| `action` | `str` | `redact`/`mask`/`hash`/`block` |
| `mask_char` | `str` | 遮盖字符，默认 `*` |
| `keep_first` | `int` | mask 保留首 N 位 |
| `keep_last` | `int` | mask 保留尾 N 位 |
| `hash_salt` | `str` | hash 盐值 |

### 2.4 RedactionResult

| 字段 | 类型 | 说明 |
|------|------|------|
| `original` | `str` | 原始文本 |
| `redacted` | `str` | 脱敏后文本 |
| `matches_count` | `int` | 命中 PII 数量 |
| `redactions` | `list[dict]` | 每处脱敏详情 |

### 2.5 ContentSafetyResult

| 字段 | 类型 | 说明 |
|------|------|------|
| `safe` | `bool` | 是否安全 |
| `categories` | `dict[str,bool]` | 各分类是否命中 |
| `scores` | `dict[str,float]` | 各分类分数 0–1 |
| `blocked_reason` | `str\|None` | 不安全原因描述 |

---

## 3. 脱敏动作说明

| Action | 效果 | 示例 |
|--------|------|------|
| `redact` | 替换为模板 | `john@example.com` → `[REDACTED_EMAIL]` |
| `mask` | 保留首尾 N 位，中间用 `*` | `john@example.com` → `jo**@ex*****.com` |
| `hash` | SHA256(salt+原文)前 8 位 hex | `john@example.com` → `a3f7b2c1` |
| `block` | 含任意 PII 时返回空字符串 | 整段文本 → `""` |

---

## 4. 默认配置

### 4.1 内置 PII 模式

| pattern_id | entity_type | 覆盖范围 |
|------------|-------------|----------|
| `email` | email | RFC 5322 简化 |
| `phone_us` | phone | 美国手机号（多格式） |
| `ssn` | ssn | XXX-XX-XXXX |
| `credit_card` | credit_card | Visa/MC/Amex/Discover 等 |
| `ipv4` | ip | 0.0.0.0–255.255.255.255 |
| `cn_id_card` | id_card | 中国居民身份证 18 位 |
| `cn_phone` | phone | 中国手机 1[3-9]XXXXXXXXX |

### 4.2 默认脱敏策略

| policy_id | action | entity_types |
|-----------|--------|--------------|
| `default` | redact | 全部 |
| `mask` | mask | email, phone |
| `strict` | block | 全部 |

### 4.3 内容安全分类

| 分类 | 默认关键词示例 |
|------|----------------|
| `hate` | "hate speech", "racial slur" |
| `violence` | "kill", "murder", "bomb" |
| `sexual` | "pornograph", "explicit sexual" |
| `self_harm` | "suicide", "self harm" |

---

## 5. REST API 接口表

| Method | Path | Auth | 说明 |
|--------|------|------|------|
| GET | `/internal/pii/patterns` | tenant | 列出所有 PII 模式 |
| POST | `/internal/pii/patterns` | admin | 注册新模式 |
| DELETE | `/internal/pii/patterns/{pattern_id}` | admin | 删除模式 |
| GET | `/internal/pii/policies` | tenant | 列出所有脱敏策略 |
| POST | `/internal/pii/policies` | admin | 注册策略 |
| POST | `/internal/pii/detect` | tenant | 检测 PII |
| POST | `/internal/pii/redact` | tenant | 脱敏文本 |
| POST | `/internal/pii/safety` | tenant | 内容安全检查 |
| POST | `/internal/pii/process` | tenant | 完整流水线 |

### 5.1 POST /internal/pii/detect

请求体：
```json
{ "text": "Contact alice@example.com" }
```
响应：
```json
{
  "matches": [
    { "pattern_id": "email", "start": 8, "end": 25,
      "matched_text": "alice@example.com", "entity_type": "email", "confidence": 0.95 }
  ],
  "count": 1
}
```

### 5.2 POST /internal/pii/redact

请求体：
```json
{ "text": "Email: alice@example.com", "policy_id": "default" }
```
响应：
```json
{
  "original": "Email: alice@example.com",
  "redacted": "Email: [REDACTED_EMAIL]",
  "matches_count": 1,
  "redactions": [...]
}
```

### 5.3 POST /internal/pii/process

请求体：
```json
{ "text": "...", "policy_id": "default", "check_safety": true }
```
响应包含 `original`, `pii_detected`, `pii_matches`, `redaction`, `safety`。

---

## 6. 配置表（settings.py 待添加）

| 字段名 | 环境变量 | 默认值 | 说明 |
|--------|----------|--------|------|
| `pii_service_enabled` | `PII_SERVICE_ENABLED` | `True` | 启用 PII 脱敏 + 内容安全 |
| `pii_patterns_config_path` | `PII_PATTERNS_CONFIG_PATH` | `config/pii_patterns.yaml` | PII 模式 YAML |
| `pii_patterns_overrides_path` | `PII_PATTERNS_OVERRIDES_PATH` | `data/pii_patterns_overrides.json` | PII 模式 JSON 覆盖 |
| `pii_safety_keywords_path` | `PII_SAFETY_KEYWORDS_PATH` | `config/safety_keywords.yaml` | 安全关键词 YAML |
| `pii_default_policy` | `PII_DEFAULT_POLICY` | `"default"` | 默认脱敏策略 |
| `pii_block_on_safety_failure` | `PII_BLOCK_ON_SAFETY_FAILURE` | `False` | 内容安全失败时是否阻断 |

### settings.py 代码片段

```python
pii_service_enabled: bool = Field(default=True, validation_alias="PII_SERVICE_ENABLED", description="启用 PII 脱敏 + 内容安全")
pii_patterns_config_path: Path = Field(default=REPO_ROOT / "config" / "pii_patterns.yaml", validation_alias="PII_PATTERNS_CONFIG_PATH")
pii_patterns_overrides_path: Path = Field(default=REPO_ROOT / "data" / "pii_patterns_overrides.json", validation_alias="PII_PATTERNS_OVERRIDES_PATH")
pii_safety_keywords_path: Path = Field(default=REPO_ROOT / "config" / "safety_keywords.yaml", validation_alias="PII_SAFETY_KEYWORDS_PATH")
pii_default_policy: str = Field(default="default", validation_alias="PII_DEFAULT_POLICY", description="默认脱敏策略")
pii_block_on_safety_failure: bool = Field(default=False, validation_alias="PII_BLOCK_ON_SAFETY_FAILURE", description="内容安全检查失败时是否阻断")
```

---

## 7. main.py 集成指引

```python
from apps.gateway.pii_routes import router as pii_router

# 在 lifespan / startup 中：
if settings.pii_service_enabled:
    from packages.pii import init_pii_service
    init_pii_service(
        detector_yaml=settings.pii_patterns_config_path,
        detector_overrides=settings.pii_patterns_overrides_path,
        safety_yaml=settings.pii_safety_keywords_path,
    )

app.include_router(pii_router)
```

---

## 8. .env.example 补充

```ini
# PII 脱敏 + 内容安全
PII_SERVICE_ENABLED=true
PII_PATTERNS_CONFIG_PATH=config/pii_patterns.yaml
PII_PATTERNS_OVERRIDES_PATH=data/pii_patterns_overrides.json
PII_SAFETY_KEYWORDS_PATH=config/safety_keywords.yaml
PII_DEFAULT_POLICY=default
PII_BLOCK_ON_SAFETY_FAILURE=false
```

---

## 9. README 更新内容

```markdown
### Phase I — PII 脱敏 + 内容安全 (#43)

- 正则引擎 + 7 种内置 PII 模式（email/phone_us/ssn/credit_card/ipv4/cn_id_card/cn_phone）
- 4 种脱敏动作：redact / mask / hash / block
- 关键词内容安全检查（hate/violence/sexual/self_harm）
- REST API: `POST /internal/pii/detect|redact|safety|process`
- YAML + JSON 运行时配置覆盖
```

---

## 10. Roadmap 更新

```markdown
| Phase I | Issue #43 | PII 脱敏 + 内容安全 | ✅ Done |
```

---

## 11. 测试覆盖

运行：
```bash
python3 tests/test_pii.py
```

| # | 测试名称 | 验证点 |
|---|----------|--------|
| 1 | `test_pii_pattern_dataclass` | dataclass 创建 + to_dict |
| 2 | `test_detector_register_list_remove` | 注册/列出/删除模式 |
| 3 | `test_detect_email` | 邮件地址检测 |
| 4 | `test_detect_phone_us` | 美国手机号检测 |
| 5 | `test_detect_ssn` | SSN 检测 |
| 6 | `test_detect_credit_card` | 信用卡号检测 |
| 7 | `test_detect_ipv4` | IPv4 地址检测 |
| 8 | `test_detect_cn_id_card` | 中国身份证检测 |
| 9 | `test_detect_cn_phone` | 中国手机号检测 |
| 10 | `test_detect_all_grouped` | detect_all 分组 |
| 11 | `test_redaction_policy_redact` | redact 动作 |
| 12 | `test_redaction_policy_mask` | mask 动作 |
| 13 | `test_redaction_policy_hash` | hash 动作 |
| 14 | `test_redaction_policy_block` | block 动作 |
| 15 | `test_mask_keep_first_last` | mask 首尾保留逻辑 |
| 16 | `test_content_safety_keyword_detection` | 关键词安全检测 |
| 17 | `test_content_safety_register_keyword` | 动态注册关键词 |
| 18 | `test_pii_service_full_process` | 完整流水线 |
| 19 | `test_pii_service_no_pii` | 无 PII 文本 |
| 20 | `test_yaml_load` | YAML 模式加载 |
| 21 | `test_json_overrides_load` | JSON 覆盖加载 |
| 22 | `test_singleton_pattern` | 单例生命周期 |
| 23 | `test_thread_safety_detect` | 并发线程安全 |
| 24 | `test_redaction_no_pii_unchanged` | 无 PII 时原样返回 |

---

## 12. 代码导航

```
packages/pii/
├── __init__.py          # 统一导出
├── detectors.py         # PIIPattern + PIIDetector (正则引擎)
├── redactor.py          # RedactionPolicy + Redactor (脱敏执行)
├── content_safety.py    # ContentSafetyChecker (关键词安全)
└── service.py           # PIIService (异步门面)

apps/gateway/
└── pii_routes.py        # REST API (/internal/pii/*)

tests/
└── test_pii.py          # 24 个测试用例

docs/
└── phase-i-pii.md       # 本文档
```

---

## 13. 已知限制

1. **正则误报**：信用卡号正则可能匹配纯数字字符串；电话号码正则可能匹配邮政编码或年份。
2. **无 NER 模型**：未集成命名实体识别（NER），无法检测"姓名"等非结构化 PII。
3. **无 LLM-based 检测**：内容安全仅为关键词规则，无法理解语境；生产应集成 OpenAI Moderation API 或 Azure Content Safety。
4. **无流式处理**：不支持对流式文本（streaming）逐 token 检测；需在消息完整后处理。
5. **语言覆盖局限**：默认模式针对英文和中文；其他语言（法语、德语等）的电话/证件格式未覆盖。
6. **无持久化**：运行时注册的自定义模式和策略在服务重启后丢失（需持久化到 JSON overrides）。
7. **hash 不可逆但非密码学安全**：SHA256[:8] 仅 8 字节，存在碰撞风险，不应用于审计追溯场景。

---

## 14. 面试要点

1. **为什么用正则而不是 NER？**
   正则推理延迟为 μs 级，NER 模型推理需数十 ms；对于结构化 PII（email/phone/SSN）正则精度已足够，同时支持自定义扩展。NER 适合非结构化实体（如人名、机构名），可作为第二阶段集成。

2. **block 动作的安全价值？**
   在合规场景（如 HIPAA），任何含 PII 的消息不得发往外部 LLM，直接阻断比脱敏更安全，避免正则漏检风险。

3. **mask vs redact 的选择场景？**
   `mask` 保留部分信息，适合客服场景（操作员可辨识 email 域名）；`redact` 完全不可识别，适合数据湖存储和日志；`hash` 适合需要一致性关联但不可逆的分析场景。

4. **如何集成 OpenAI Moderation API？**
   在 `ContentSafetyChecker.check()` 中，当关键词检测 `safe=True` 时，异步调用 `POST https://api.openai.com/v1/moderations`，将响应合并到 `ContentSafetyResult`。当前 stub 保证接口签名一致，零改动升级。

5. **线程安全如何保证？**
   所有注册表操作均在 `threading.RLock` 保护下执行；`_compiled` 缓存在锁外读（仅写操作加锁），利用 Python GIL 保证读的原子性。

6. **如何应对正则漏检？**
   建立多层防线：(1) 正则 → 快速初筛；(2) NER 模型 → 深度语义检测；(3) LLM Moderation → 上下文理解；在高合规场景三层叠加，低延迟场景仅启用第一层。

7. **YAML + JSON 双配置的设计意图？**
   YAML 进 git，管理默认模式（版本控制 + Code Review）；JSON overrides 存 data/ 目录不进 git，供运营团队通过 Admin API 实时调整，避免每次改模式都触发部署。
