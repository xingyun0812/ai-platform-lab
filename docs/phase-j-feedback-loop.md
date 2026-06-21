# Phase J — 在线质量监控 + 反馈飞轮

> **Issue #32 / Roadmap #48**  
> Real-time Bad Case 捕获 → 自动入库 eval baseline → LLM 生成 Prompt 优化建议 → 自动创建 Prompt A/B 实验

---

## 1. 设计要点

### 1.1 反馈采集（Feedback Capture）
- 用户在对话界面点击「👍/👎」或给出 1-5 星评分，前端（Console V2）通过 `POST /internal/feedback/` 上报。
- 后端同步写入 `FeedbackStore`（内存或 SQLite）。
- 负面反馈（`thumbs_down`、`bad_case`、`rating_1`、`rating_2`）自动触发 `FeedbackLoop.ingest_to_eval()`，将记录追加到 `eval/baselines/bad_cases.jsonl`。

### 1.2 质量聚合（Quality Aggregation）
- `QualityAggregator` 维护每个 tenant 的原始事件流（`_RawEvent`），按滑动时间窗口（默认 5 分钟）计算：
  - 总请求数、thumbs_up / thumbs_down、平均评分、bad_case 数量、满意度（thumbs_up / total）
- `get_trend()` 返回最近 N 个连续窗口（用于图表）。

### 1.3 质量告警（Quality Alerts）
- `AlertChecker` 提供三种检查：
  - **satisfaction_drop**：满意度低于阈值（默认 0.7）
  - **bad_case_spike**：差评数超过阈值（默认 10 条 / 窗口）
  - **rating_decline**：均分相比前一窗口下降超过阈值（默认 0.5 分）
- 告警分 `warning` / `critical` 两级。

### 1.4 反馈飞轮（Feedback Loop）
```
collect_bad_cases()
    ↓
ingest_to_eval()   →   eval/baselines/bad_cases.jsonl
    ↓
generate_prompt_suggestion()   →   调用 LLM（无 key 时返回模板建议）
    ↓
[人工审核] apply_suggestion()
    ↓
auto_create_experiment()   →   packages.prompt.experiment.ExperimentStore
```

- `run_full_cycle(tenant_id, prompt_id)` 一键编排全流程。
- `FEEDBACK_LOOP_AUTO_EXPERIMENT=false`（默认）：自动实验功能关闭，需人工调用 `/experiment/{suggestion_id}`。

---

## 2. 数据模型

### Feedback
| 字段 | 类型 | 说明 |
|---|---|---|
| `feedback_id` | `str` | `fb-{uuid12}` |
| `tenant_id` | `str` | 租户 ID |
| `session_id` | `str` | 对话 session |
| `message_id` | `str` | 消息 ID（关联 LLM 响应） |
| `feedback_type` | `str` | `FeedbackType` 枚举值 |
| `rating` | `int\|None` | 1-5 |
| `comment` | `str\|None` | 用户填写的文字反馈 |
| `user_id` | `str\|None` | 可选 |
| `created_at` | `float` | Unix 时间戳 |
| `metadata` | `dict` | 扩展字段 |

### FeedbackType（枚举）
`THUMBS_UP` | `THUMBS_DOWN` | `RATING_1` ... `RATING_5` | `BAD_CASE`

### QualityMetric
| 字段 | 类型 | 说明 |
|---|---|---|
| `tenant_id` | `str` | |
| `window_seconds` | `int` | 聚合窗口（秒） |
| `total_requests` | `int` | 窗口内总反馈数 |
| `thumbs_up` / `thumbs_down` | `int` | |
| `avg_rating` | `float` | 0 = 无评分数据 |
| `bad_case_count` | `int` | 负面反馈总数 |
| `satisfaction_rate` | `float` | thumbs_up / total；无数据默认 1.0 |
| `timestamp` | `float` | 窗口结束时间 |

### QualityAlert
| 字段 | 类型 | 说明 |
|---|---|---|
| `alert_id` | `str` | `alert-{uuid8}` |
| `alert_type` | `str` | `satisfaction_drop\|bad_case_spike\|rating_decline` |
| `threshold` | `float` | 配置阈值 |
| `current_value` | `float` | 实际值 |
| `severity` | `str` | `warning\|critical` |

### PromptSuggestion
| 字段 | 类型 | 说明 |
|---|---|---|
| `suggestion_id` | `str` | `sug-{uuid12}` |
| `prompt_id` | `str` | 关联 Prompt |
| `current_version` | `str` | 当前版本号 |
| `suggested_changes` | `str` | LLM 建议的修改内容 |
| `reasoning` | `str` | 修改理由 |
| `expected_impact` | `str` | 预期效果 |
| `bad_case_ids` | `list[str]` | 触发此建议的差评 IDs |
| `status` | `str` | `pending\|applied\|rejected` |

---

## 3. REST API 表

### Router 1: `/internal/feedback`

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| `POST` | `/internal/feedback/` | 任意租户 | 记录反馈 |
| `GET` | `/internal/feedback/{feedback_id}` | 任意租户 | 获取单条 |
| `GET` | `/internal/feedback/` | 任意租户 | 列出（`?tenant_id&feedback_type&limit`） |
| `GET` | `/internal/feedback/bad-cases` | admin | 列出差评（`?tenant_id&limit`） |
| `GET` | `/internal/feedback/stats` | 任意租户 | 按类型统计（`?tenant_id`） |

### Router 2: `/internal/quality`

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| `GET` | `/internal/quality/current/{tenant_id}` | 任意租户 | 当前质量指标（`?window_seconds=300`） |
| `GET` | `/internal/quality/trend/{tenant_id}` | 任意租户 | 趋势（`?windows=12`） |
| `GET` | `/internal/quality/alerts/{tenant_id}` | 任意租户 | 当前告警 |
| `POST` | `/internal/quality/alerts/check/{tenant_id}` | admin | 触发告警检查 |

### Router 3: `/internal/feedback-loop`

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| `POST` | `/internal/feedback-loop/collect/{tenant_id}` | admin | 收集差评（`?since=<timestamp>`） |
| `POST` | `/internal/feedback-loop/ingest` | admin | 入库 eval（body: `{bad_case_ids}`） |
| `POST` | `/internal/feedback-loop/suggest/{prompt_id}` | admin | 生成 Prompt 建议（body: `{bad_case_ids}`） |
| `POST` | `/internal/feedback-loop/experiment/{suggestion_id}` | admin | 自动创建 A/B 实验 |
| `POST` | `/internal/feedback-loop/cycle/{tenant_id}` | admin | 完整飞轮（body: `{prompt_id}`） |

---

## 4. 配置表（Settings Fields）

> 以下字段需添加到 `apps/gateway/settings.py`

| 字段名 | 环境变量 | 默认值 | 说明 |
|---|---|---|---|
| `feedback_enabled` | `FEEDBACK_ENABLED` | `True` | 启用反馈采集 |
| `feedback_store_database_url` | `FEEDBACK_STORE_DATABASE_URL` | `None` | 反馈存储；`None`=内存，`sqlite:///path`=SQLite |
| `quality_monitor_enabled` | `QUALITY_MONITOR_ENABLED` | `True` | 启用质量监控聚合 |
| `quality_monitor_window_seconds` | `QUALITY_MONITOR_WINDOW_SECONDS` | `300` | 聚合窗口（秒） |
| `quality_alert_satisfaction_threshold` | `QUALITY_ALERT_SATISFACTION_THRESHOLD` | `0.7` | 满意度告警阈值 |
| `quality_alert_bad_case_threshold` | `QUALITY_ALERT_BAD_CASE_THRESHOLD` | `10` | 差评数告警阈值 |
| `feedback_loop_enabled` | `FEEDBACK_LOOP_ENABLED` | `True` | 启用反馈飞轮 |
| `feedback_loop_bad_cases_path` | `FEEDBACK_LOOP_BAD_CASES_PATH` | `eval/baselines/bad_cases.jsonl` | bad cases JSONL 路径 |
| `feedback_loop_auto_experiment` | `FEEDBACK_LOOP_AUTO_EXPERIMENT` | `False` | 自动创建 A/B 实验（默认关闭，需人工审核） |

### Settings 代码（添加到 `apps/gateway/settings.py`）

```python
feedback_enabled: bool = Field(default=True, validation_alias="FEEDBACK_ENABLED", description="启用反馈采集")
feedback_store_database_url: str | None = Field(default=None, validation_alias="FEEDBACK_STORE_DATABASE_URL", description="反馈存储；None=内存")
quality_monitor_enabled: bool = Field(default=True, validation_alias="QUALITY_MONITOR_ENABLED", description="启用质量监控")
quality_monitor_window_seconds: int = Field(default=300, validation_alias="QUALITY_MONITOR_WINDOW_SECONDS", description="聚合窗口（秒）")
quality_alert_satisfaction_threshold: float = Field(default=0.7, validation_alias="QUALITY_ALERT_SATISFACTION_THRESHOLD")
quality_alert_bad_case_threshold: int = Field(default=10, validation_alias="QUALITY_ALERT_BAD_CASE_THRESHOLD")
feedback_loop_enabled: bool = Field(default=True, validation_alias="FEEDBACK_LOOP_ENABLED", description="启用反馈飞轮")
feedback_loop_bad_cases_path: Path = Field(default=REPO_ROOT / "eval" / "baselines" / "bad_cases.jsonl", validation_alias="FEEDBACK_LOOP_BAD_CASES_PATH")
feedback_loop_auto_experiment: bool = Field(default=False, validation_alias="FEEDBACK_LOOP_AUTO_EXPERIMENT", description="自动创建 A/B 实验（默认关闭，需人工审核）")
```

---

## 5. main.py 集成

> 在 `apps/gateway/main.py` 中添加以下内容

### 导入 routers
```python
from apps.gateway.feedback_routes import router as feedback_router
from apps.gateway.quality_routes import router as quality_router
from apps.gateway.feedback_loop_routes import router as feedback_loop_router
```

### 初始化
```python
if settings.feedback_enabled:
    from packages.feedback import init_feedback_store
    init_feedback_store(database_url=settings.feedback_store_database_url)

if settings.quality_monitor_enabled:
    from packages.quality_monitor import init_quality_monitor
    init_quality_monitor(window_seconds=settings.quality_monitor_window_seconds)

if settings.feedback_loop_enabled:
    from packages.feedback_loop import init_feedback_loop
    init_feedback_loop(
        bad_cases_path=settings.feedback_loop_bad_cases_path,
        auto_experiment=settings.feedback_loop_auto_experiment,
    )
```

### include_router
```python
app.include_router(feedback_router)
app.include_router(quality_router)
app.include_router(feedback_loop_router)
```

---

## 6. .env.example 添加内容

```dotenv
# ── 反馈采集 ──────────────────────────────────────────────
FEEDBACK_ENABLED=true
FEEDBACK_STORE_DATABASE_URL=           # 留空=内存；可填 sqlite:///data/feedback.db

# ── 质量监控 ──────────────────────────────────────────────
QUALITY_MONITOR_ENABLED=true
QUALITY_MONITOR_WINDOW_SECONDS=300
QUALITY_ALERT_SATISFACTION_THRESHOLD=0.7
QUALITY_ALERT_BAD_CASE_THRESHOLD=10

# ── 反馈飞轮 ──────────────────────────────────────────────
FEEDBACK_LOOP_ENABLED=true
FEEDBACK_LOOP_BAD_CASES_PATH=eval/baselines/bad_cases.jsonl
FEEDBACK_LOOP_AUTO_EXPERIMENT=false    # true 时自动创建 A/B 实验（需人工审核后谨慎开启）
```

---

## 7. README 章节内容

```markdown
### 在线质量监控 + 反馈飞轮（Phase J）

- **用户反馈**：`POST /internal/feedback/` 记录 👍/👎 或 1-5 星评分，负面反馈自动入库 eval baseline。
- **质量聚合**：`GET /internal/quality/current/{tenant_id}` 实时查看满意度、差评率、均分趋势（滑动窗口）。
- **质量告警**：满意度跌破阈值 / 差评数激增 / 均分骤降时触发分级告警。
- **Prompt 优化建议**：`POST /internal/feedback-loop/suggest/{prompt_id}` 基于差评样本调用 LLM 生成优化建议。
- **A/B 实验**：`POST /internal/feedback-loop/experiment/{suggestion_id}` 人工审核后自动创建 Prompt A/B 实验。
- **完整飞轮**：`POST /internal/feedback-loop/cycle/{tenant_id}` 一键触发 collect → ingest → suggest。
```

---

## 8. Roadmap 更新

> 在 `docs/roadmap.md` 中，将 Issue #32 / Phase J 标记为 `[x]`，并添加说明：

```
- [x] Phase J #48: 在线质量监控 + 反馈飞轮
  — packages/feedback, packages/quality_monitor, packages/feedback_loop
  — REST: /internal/feedback, /internal/quality, /internal/feedback-loop
```

---

## 9. 测试说明

| 测试文件 | 测试数 | 覆盖内容 |
|---|---|---|
| `tests/test_feedback.py` | 13 | FeedbackType 枚举、Feedback 数据类、InMemoryFeedbackStore CRUD + 差评列表 + 统计、API 层、singleton |
| `tests/test_quality_monitor.py` | 11 | QualityMetric 数据类、QualityAggregator 记录/聚合/趋势、AlertChecker 三种检查、singleton |
| `tests/test_feedback_loop.py` | 11 | PromptSuggestion 数据类、FeedbackLoop collect/ingest/suggest/experiment/full-cycle、singleton |

运行：
```bash
cd /Users/liuli/Downloads/ai-platform-lab
python3 tests/test_feedback.py
python3 tests/test_quality_monitor.py
python3 tests/test_feedback_loop.py
```

---

## 10. 代码导航

| 文件 | 说明 |
|---|---|
| `packages/feedback/store.py` | FeedbackType / Feedback / FeedbackStore ABC / InMemoryFeedbackStore / SqliteFeedbackStore / singleton |
| `packages/feedback/api.py` | record_feedback / get_feedback / list_feedback（负反馈触发 ingest） |
| `packages/feedback/__init__.py` | 包导出 |
| `packages/quality_monitor/aggregator.py` | QualityMetric / QualityAggregator（滑动窗口）/ singleton |
| `packages/quality_monitor/alerts.py` | QualityAlert / AlertChecker |
| `packages/quality_monitor/__init__.py` | 包导出 |
| `packages/feedback_loop/pipeline.py` | PromptSuggestion / FeedbackLoop（collect→ingest→suggest→experiment）/ singleton |
| `packages/feedback_loop/__init__.py` | 包导出 |
| `apps/gateway/feedback_routes.py` | `/internal/feedback` 路由 |
| `apps/gateway/quality_routes.py` | `/internal/quality` 路由 |
| `apps/gateway/feedback_loop_routes.py` | `/internal/feedback-loop` 路由 |

---

## 11. 已知限制

1. **无流式反馈**：流式（SSE/WebSocket）输出过程中无法实时采集评分，只支持对话完成后的反馈。
2. **无 per-user 聚合**：`QualityAggregator` 按 tenant 聚合，不区分具体用户，无法做用户级留存分析。
3. **LLM 建议质量未验证**：`generate_prompt_suggestion` 调用 LLM，建议内容的有效性依赖模型质量，需人工审核，不可盲目采纳。
4. **无自动应用机制**：`FEEDBACK_LOOP_AUTO_EXPERIMENT=false` 默认关闭，需人工调用 `/experiment/{suggestion_id}` 触发 A/B 实验，避免未经验证的 Prompt 上线。
5. **告警无去重**：每次调用 `run_all_checks` 都会重新生成 alert_id，无历史告警记录和去重逻辑，高频调用会产生重复告警。
6. **SQLite 并发限制**：`SqliteFeedbackStore` 基于 `aiosqlite` 单连接，高并发写入时有锁竞争，不适合生产大流量场景（建议换 PostgreSQL）。
7. **内存聚合无持久化**：`QualityAggregator` 重启后历史事件丢失，窗口内数据从零开始，可能引起重启后满意度虚高。

---

## 12. 面试要点

1. **反馈飞轮设计**：阐述 Bad Case → eval baseline → LLM 建议 → A/B 实验 → 新版本上线的完整闭环，重点强调"人工审核"节点防止无监督自动化引入回归。

2. **滑动窗口聚合**：`QualityAggregator` 保留原始事件流（带时间戳），每次查询动态过滤窗口内事件，支持任意时间范围聚合；对比 Redis sorted set 的优劣（内存 vs 持久化）。

3. **满意度定义**：`satisfaction_rate = thumbs_up / total`（thumbs-only）vs. 综合评分（含 rating_4、rating_5），当前选择简单实现，可扩展为加权满意度。

4. **告警分级**：`warning`（threshold 至 threshold×0.8）vs. `critical`（低于 threshold×0.8），体现渐进式响应设计；可扩展接入 PagerDuty / 企业微信。

5. **LLM 建议的 fallback**：无 API Key 时返回模板建议（不崩溃），保证服务可用性；有 Key 时调用 GPT-4o-mini，prompt 工程化设计（system + 差评样本 + 指令）。

6. **A/B 实验集成**：`auto_create_experiment` 复用 `packages.prompt.experiment.ExperimentStore`，新版本（current_ver + 1）50/50 分流，`success_metric=quality`，`min_samples=100` 防止过早判定胜者。

7. **graceful degradation**：`get_feedback_store()` / `get_quality_monitor()` / `get_feedback_loop()` 返回 `None` 时，路由层返回 503 而非 500，业务方可区分"功能未启用"和"内部错误"。
