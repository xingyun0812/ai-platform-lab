"""eval/harness_capability_benchmark.py — Phase R R3 模型能力 4 维度 benchmark。

维度：
  context_mgmt  — 长上下文召回（needle-in-haystack 变体）
  long_memory   — 跨 session 记忆检索准确率
  tool_use      — 工具调用成功率 + 参数 schema 准确率
  planning      — Plan 结构合理性 + 步骤数 / 依赖正确率

用法：
  python eval/harness_capability_benchmark.py run --model chat-fast
  python eval/harness_capability_benchmark.py run --model chat-fast --mock
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai_platform.eval.capability_benchmark")

# ---------------------------------------------------------------------------
# 共享数据结构
# ---------------------------------------------------------------------------


@dataclass
class CapabilityScores:
    """4 维度能力分数，每维 0-1。"""

    context_mgmt: float
    long_memory: float
    tool_use: float
    planning: float

    def to_dict(self) -> dict[str, float]:
        return {
            "context_mgmt": self.context_mgmt,
            "long_memory": self.long_memory,
            "tool_use": self.tool_use,
            "planning": self.planning,
        }

    def overall(self) -> float:
        return (self.context_mgmt + self.long_memory + self.tool_use + self.planning) / 4


# ---------------------------------------------------------------------------
# 内部 LLM 调用辅助
# ---------------------------------------------------------------------------


async def _call_model(model: str, messages: list[dict[str, str]]) -> str:
    """真实调用 forward_with_model_router；仅在非 mock 模式下触发。"""
    REPO_ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(REPO_ROOT))
    from apps.gateway.model_router import forward_with_model_router

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 512,
        "temperature": 0.0,
    }
    result = await forward_with_model_router(payload, requested_model=model)
    if result.body and result.body.get("choices"):
        choice = result.body["choices"][0]
        return choice.get("message", {}).get("content", "") or ""
    return ""


# ---------------------------------------------------------------------------
# 维度 1：长上下文召回 (needle-in-haystack)
# ---------------------------------------------------------------------------

# 10 个 needle case：(context_tokens_approx, needle, question, expected_keyword)
_CONTEXT_CASES = [
    (
        4000,
        "PROJECT_CODE_ALPHA_7749",
        "文档中提到的项目代号是什么？",
        "ALPHA_7749",
    ),
    (
        4000,
        "联系方式：18800001234",
        "文档中的联系电话是多少？",
        "18800001234",
    ),
    (
        8000,
        "会议室预订密码：ROOM#8821",
        "会议室预订密码是什么？",
        "8821",
    ),
    (
        8000,
        "紧急联系人：张伟，工号 EMP-00421",
        "紧急联系人工号是什么？",
        "EMP-00421",
    ),
    (
        8000,
        "报告版本号：v2.9.14-hotfix",
        "报告的版本号是什么？",
        "v2.9.14",
    ),
    (
        12000,
        "数据库密钥哈希：SHA256:d9f3a8b2",
        "文档中的数据库密钥哈希值是什么？",
        "d9f3a8b2",
    ),
    (
        12000,
        "部署时间窗口：每周三凌晨 2:00-4:00",
        "系统部署时间窗口是什么？",
        "凌晨 2",
    ),
    (
        16000,
        "合规编号：COMP-CN-2024-0091",
        "合规备案编号是什么？",
        "COMP-CN-2024-0091",
    ),
    (
        16000,
        "最大并发用户数：15000",
        "系统支持的最大并发用户数是多少？",
        "15000",
    ),
    (
        16000,
        "灾难恢复 RTO 目标：4 小时",
        "系统灾难恢复的 RTO 是多少？",
        "4",
    ),
]

_FILLER_PARAGRAPH = (
    "本段落为填充内容，仅用于构造长上下文环境，不包含答案信息。"
    "系统各组件按照预定流程协同工作，确保端到端数据一致性。"
    "所有操作均经过严格安全审查，满足合规要求。"
)


def _build_context_case(tokens_approx: int, needle: str, question: str) -> dict[str, Any]:
    """构造 needle-in-haystack 上下文。"""
    # 每个中文字符约 1.5 token，英文约 0.3 token
    chars_needed = int(tokens_approx / 1.5)
    filler_chars = len(_FILLER_PARAGRAPH)
    repeat_count = max(1, chars_needed // filler_chars)

    paragraphs = []
    insert_at = repeat_count // 2  # 中间插入 needle
    for i in range(repeat_count):
        if i == insert_at:
            paragraphs.append(f"【重要信息】{needle}【重要信息结束】")
        paragraphs.append(_FILLER_PARAGRAPH)

    long_context = "\n".join(paragraphs)
    return {
        "context": long_context,
        "question": question,
        "needle": needle,
    }


# Mock LLM responses for context benchmark
_CONTEXT_MOCK_CORRECT = [True, True, True, True, True, False, True, True, True, True]


class ContextMgmtBenchmark:
    """长上下文召回：在长文本中插入 needle，问模型 needle 内容。"""

    def _build_cases(self) -> list[dict[str, Any]]:
        return [
            {**_build_context_case(tokens, needle, question), "expected": expected}
            for tokens, needle, question, expected in _CONTEXT_CASES
        ]

    async def _call_llm(
        self,
        model: str,
        case: dict[str, Any],
        mock: bool = False,
        case_idx: int = 0,
    ) -> str:
        if mock:
            # Mock 模式：当 case_idx 在"正确"列表中时返回含 needle 的答案
            if _CONTEXT_MOCK_CORRECT[case_idx % len(_CONTEXT_MOCK_CORRECT)]:
                return f"根据文档内容，答案是：{case['needle']}"
            else:
                return "文档中没有找到相关信息。"
        messages = [
            {
                "role": "system",
                "content": "你是文档阅读助手，只从给定文档中提取信息，不添加推断。",
            },
            {
                "role": "user",
                "content": f"请阅读以下文档，然后回答问题。\n\n文档：\n{case['context']}\n\n问题：{case['question']}",
            },
        ]
        return await _call_model(model, messages)

    def _score_response(self, case: dict[str, Any], response: str) -> float:
        """substring 匹配：expected 关键字是否出现在回答中（大小写不敏感）。"""
        expected = case["expected"].lower()
        resp_lower = response.lower()
        return 1.0 if expected in resp_lower else 0.0

    async def run(self, model: str, mock: bool = False) -> float:
        """返回 0-1 分。10 个 case，每个 case 在长上下文里插 needle。"""
        cases = self._build_cases()
        scores = []
        for idx, case in enumerate(cases):
            resp = await self._call_llm(model, case, mock=mock, case_idx=idx)
            score = self._score_response(case, resp)
            scores.append(score)
            logger.debug("context_mgmt case=%d score=%.1f", idx, score)
        result = sum(scores) / len(scores) if scores else 0.0
        logger.info("context_mgmt final=%.3f", result)
        return result


# ---------------------------------------------------------------------------
# 维度 2：长记忆检索 (long_memory)
# ---------------------------------------------------------------------------

# 10 个 case：(session_a_memory, question, expected_keywords)
_MEMORY_CASES = [
    (
        "用户上次提到他的爱好是打羽毛球，每周打两次。",
        "用户有什么运动爱好？",
        ["羽毛球"],
    ),
    (
        "用户告知其公司名称为深圳科创有限公司，主营 AI 软件。",
        "用户的公司名称是什么？",
        ["科创", "深圳"],
    ),
    (
        "用户在上次会话中询问过 Python 异步编程，特别是 asyncio 的用法。",
        "用户上次询问了什么技术问题？",
        ["asyncio", "异步"],
    ),
    (
        "用户反馈说产品的搜索功能太慢，希望优化到 200ms 以内。",
        "用户对产品有什么性能反馈？",
        ["搜索", "200"],
    ),
    (
        "用户的预算上限为 50 万元人民币，项目截止日期为 2024 年 12 月。",
        "用户的项目预算和截止时间是什么？",
        ["50", "2024"],
    ),
    (
        "用户提到他们团队有 8 名工程师，使用 GitLab 进行代码管理。",
        "用户团队规模和使用的代码管理工具是什么？",
        ["8", "GitLab"],
    ),
    (
        "用户在上次对话中确认了使用 PostgreSQL 14 作为主数据库。",
        "用户使用什么版本的数据库？",
        ["PostgreSQL", "14"],
    ),
    (
        "用户的联系邮箱为 dev.manager@example.corp，工作时区为 UTC+8。",
        "用户的联系邮箱是什么？",
        ["dev.manager", "example.corp"],
    ),
    (
        "用户表示他们已购买专业版许可证，有效期到 2025 年 6 月。",
        "用户持有什么级别的许可证，有效期到什么时候？",
        ["专业", "2025"],
    ),
    (
        "用户上次反馈 API 延迟高达 3 秒，期望降低到 500ms。",
        "用户反馈的 API 延迟问题是什么？",
        ["3", "500"],
    ),
]

_MEMORY_MOCK_CORRECT = [True, True, False, True, True, True, True, False, True, True]


class LongMemoryBenchmark:
    """跨 session 记忆检索：给定 session A 记忆，session B 问相关问题。"""

    async def _call_llm(
        self,
        model: str,
        memory: str,
        question: str,
        mock: bool = False,
        case_idx: int = 0,
    ) -> str:
        if mock:
            if _MEMORY_MOCK_CORRECT[case_idx % len(_MEMORY_MOCK_CORRECT)]:
                # 返回包含记忆关键词的答案
                return f"根据历史记录，{memory}"
            else:
                return "我没有找到相关的历史记录。"
        messages = [
            {
                "role": "system",
                "content": "你是一个具有记忆能力的 AI 助手。以下是用户的历史记忆摘要，请根据记忆回答问题。",
            },
            {
                "role": "user",
                "content": f"历史记忆：{memory}\n\n当前问题：{question}",
            },
        ]
        return await _call_model(model, messages)

    def _score_response(self, response: str, expected_keywords: list[str]) -> float:
        """keyword 匹配：所有 expected_keywords 都出现在回答中（大小写不敏感）。"""
        resp_lower = response.lower()
        matched = sum(1 for kw in expected_keywords if kw.lower() in resp_lower)
        return matched / len(expected_keywords) if expected_keywords else 0.0

    async def run(self, model: str, mock: bool = False) -> float:
        """返回 0-1 分。10 个 case，测跨 session 记忆召回。"""
        scores = []
        for idx, (memory, question, keywords) in enumerate(_MEMORY_CASES):
            resp = await self._call_llm(model, memory, question, mock=mock, case_idx=idx)
            score = self._score_response(resp, keywords)
            scores.append(score)
            logger.debug("long_memory case=%d score=%.2f", idx, score)
        result = sum(scores) / len(scores) if scores else 0.0
        logger.info("long_memory final=%.3f", result)
        return result


# ---------------------------------------------------------------------------
# 维度 3：工具调用 (tool_use)
# ---------------------------------------------------------------------------

# 10 个 case：(tools, task, expected_tool_name, expected_param_keys, expected_param_values)
_TOOL_CASES = [
    (
        [{"name": "web_search", "description": "搜索网络", "parameters": {"query": "string"}}],
        "搜索最新的 Python 3.12 发布说明",
        "web_search",
        ["query"],
        {"query": "python 3.12"},  # keyword in value
    ),
    (
        [{"name": "calculator", "description": "数学计算", "parameters": {"expression": "string"}}],
        "计算 1234 * 5678 的结果",
        "calculator",
        ["expression"],
        {"expression": "1234"},
    ),
    (
        [
            {
                "name": "get_weather",
                "description": "查询天气",
                "parameters": {"city": "string", "date": "string"},
            }
        ],
        "查询北京明天的天气",
        "get_weather",
        ["city"],
        {"city": "北京"},
    ),
    (
        [
            {
                "name": "send_email",
                "description": "发送邮件",
                "parameters": {"to": "string", "subject": "string", "body": "string"},
            }
        ],
        "给 dev@example.com 发送主题为'会议通知'的邮件",
        "send_email",
        ["to", "subject"],
        {"to": "dev@example.com", "subject": "会议"},
    ),
    (
        [
            {
                "name": "database_query",
                "description": "执行 SQL 查询",
                "parameters": {"sql": "string"},
            }
        ],
        "查询 users 表中所有活跃用户",
        "database_query",
        ["sql"],
        {"sql": "users"},
    ),
    (
        [{"name": "file_read", "description": "读取文件内容", "parameters": {"path": "string"}}],
        "读取 /tmp/config.yaml 文件的内容",
        "file_read",
        ["path"],
        {"path": "/tmp/config.yaml"},
    ),
    (
        [
            {
                "name": "translate",
                "description": "翻译文本",
                "parameters": {"text": "string", "target_lang": "string"},
            }
        ],
        "将'Hello World'翻译成中文",
        "translate",
        ["text", "target_lang"],
        {"text": "Hello", "target_lang": "zh"},
    ),
    (
        [
            {
                "name": "code_execute",
                "description": "执行代码",
                "parameters": {"code": "string", "language": "string"},
            }
        ],
        "运行一段 Python 代码：print('test')",
        "code_execute",
        ["code", "language"],
        {"code": "print", "language": "python"},
    ),
    (
        [
            {
                "name": "image_generate",
                "description": "生成图片",
                "parameters": {"prompt": "string", "size": "string"},
            }
        ],
        "生成一张 512x512 的猫咪图片",
        "image_generate",
        ["prompt", "size"],
        {"prompt": "猫咪", "size": "512"},
    ),
    (
        [
            {
                "name": "calendar_create",
                "description": "创建日历事件",
                "parameters": {"title": "string", "datetime": "string"},
            }
        ],
        "在明天下午 3 点创建一个'代码审查'的日历事件",
        "calendar_create",
        ["title"],
        {"title": "代码审查"},
    ),
]

_TOOL_MOCK_CORRECT = [True, True, True, False, True, True, True, True, False, True]


class ToolUseBenchmark:
    """工具调用成功率 + 参数 schema 准确率。"""

    def _build_tool_prompt(self, tools: list[dict], task: str) -> str:
        tools_json = json.dumps(tools, ensure_ascii=False, indent=2)
        return (
            f"你是工具调用助手。可用工具列表（JSON）：\n{tools_json}\n\n"
            f"任务：{task}\n\n"
            "请输出 JSON 格式的工具调用，格式：\n"
            '{"tool_name": "...", "parameters": {"param1": "value1", ...}}\n'
            "只输出 JSON，不要其他内容。"
        )

    async def _call_llm(
        self,
        model: str,
        tools: list[dict],
        task: str,
        mock: bool = False,
        case_idx: int = 0,
    ) -> str:
        if mock:
            case = _TOOL_CASES[case_idx % len(_TOOL_CASES)]
            tools_c, task_c, expected_tool, expected_keys, expected_vals = case
            if _TOOL_MOCK_CORRECT[case_idx % len(_TOOL_MOCK_CORRECT)]:
                # 构造包含所有预期参数的 mock 响应
                params = {k: expected_vals.get(k, "mock_value") for k in expected_keys}
                return json.dumps(
                    {"tool_name": expected_tool, "parameters": params}, ensure_ascii=False
                )
            else:
                return '{"tool_name": "wrong_tool", "parameters": {}}'
        messages = [{"role": "user", "content": self._build_tool_prompt(tools, task)}]
        return await _call_model(model, messages)

    def _score_response(
        self,
        response: str,
        expected_tool: str,
        expected_param_keys: list[str],
        expected_param_values: dict[str, str],
    ) -> float:
        """schema 校验：工具名正确 0.4 分 + 参数键正确 0.3 分 + 参数值包含关键词 0.3 分。"""
        try:
            # 尝试从响应中提取 JSON
            json_match = re.search(r"\{.*\}", response, re.DOTALL)
            if not json_match:
                return 0.0
            call = json.loads(json_match.group())
        except (json.JSONDecodeError, AttributeError):
            return 0.0

        score = 0.0
        # 工具名称匹配 (0.4)
        tool_name = call.get("tool_name", "")
        if tool_name == expected_tool:
            score += 0.4
        elif expected_tool in tool_name:
            score += 0.2

        params = call.get("parameters", {})
        if not isinstance(params, dict):
            return score

        # 参数键存在 (0.3)
        if expected_param_keys:
            present = sum(1 for k in expected_param_keys if k in params)
            score += 0.3 * (present / len(expected_param_keys))

        # 参数值包含预期关键词 (0.3)
        if expected_param_values:
            value_matches = 0
            for param_key, expected_substr in expected_param_values.items():
                actual_val = str(params.get(param_key, "")).lower()
                if expected_substr.lower() in actual_val:
                    value_matches += 1
            score += 0.3 * (value_matches / len(expected_param_values))

        return min(1.0, score)

    async def run(self, model: str, mock: bool = False) -> float:
        """返回 0-1 分。10 个 case，测工具调用准确率。"""
        scores = []
        for idx, (tools, task, exp_tool, exp_keys, exp_vals) in enumerate(_TOOL_CASES):
            resp = await self._call_llm(model, tools, task, mock=mock, case_idx=idx)
            score = self._score_response(resp, exp_tool, exp_keys, exp_vals)
            scores.append(score)
            logger.debug("tool_use case=%d score=%.2f", idx, score)
        result = sum(scores) / len(scores) if scores else 0.0
        logger.info("tool_use final=%.3f", result)
        return result


# ---------------------------------------------------------------------------
# 维度 4：规划能力 (planning)
# ---------------------------------------------------------------------------

# 10 个 case：(goal, min_steps, max_steps, required_deps)
# required_deps: list of (step_idx, must_depend_on_earlier) — 简化为要求步骤有依赖
_PLANNING_CASES = [
    ("构建一个用户注册登录系统", 3, 8, True),
    ("分析销售数据并生成月度报告", 3, 7, True),
    ("部署一个微服务到 Kubernetes", 4, 10, True),
    ("开发移动端 APP 的推送通知功能", 3, 8, True),
    ("实现一个 RAG 问答系统", 4, 10, True),
    ("优化数据库查询性能", 3, 7, True),
    ("设计并实现 API 限流机制", 3, 8, True),
    ("完成一次代码重构：将单体应用拆分为微服务", 5, 12, True),
    ("建立 CI/CD 自动化流水线", 4, 10, True),
    ("实现多语言国际化支持（i18n）", 3, 8, True),
]

_PLANNING_MOCK_PLANS = [
    {
        "goal": "构建一个用户注册登录系统",
        "steps": [
            {"id": "s1", "description": "设计用户数据库表结构", "depends_on": []},
            {"id": "s2", "description": "实现注册接口", "depends_on": ["s1"]},
            {"id": "s3", "description": "实现登录接口与 JWT 鉴权", "depends_on": ["s1"]},
            {"id": "s4", "description": "编写单元测试", "depends_on": ["s2", "s3"]},
        ],
    },
    {
        "goal": "分析销售数据并生成月度报告",
        "steps": [
            {"id": "s1", "description": "连接数据库，查询原始销售数据", "depends_on": []},
            {"id": "s2", "description": "数据清洗与聚合计算", "depends_on": ["s1"]},
            {"id": "s3", "description": "生成可视化图表", "depends_on": ["s2"]},
            {"id": "s4", "description": "撰写报告文本并导出 PDF", "depends_on": ["s2", "s3"]},
        ],
    },
    {
        "goal": "部署一个微服务到 Kubernetes",
        "steps": [
            {"id": "s1", "description": "编写 Dockerfile", "depends_on": []},
            {"id": "s2", "description": "构建并推送容器镜像", "depends_on": ["s1"]},
            {"id": "s3", "description": "编写 Kubernetes Deployment YAML", "depends_on": []},
            {"id": "s4", "description": "配置 Service 和 Ingress", "depends_on": ["s3"]},
            {"id": "s5", "description": "执行 kubectl apply 部署", "depends_on": ["s2", "s4"]},
        ],
    },
    {
        "goal": "开发移动端 APP 的推送通知功能",
        "steps": [
            {"id": "s1", "description": "集成 FCM/APNs SDK", "depends_on": []},
            {"id": "s2", "description": "实现后端推送服务", "depends_on": []},
            {"id": "s3", "description": "实现前端通知权限申请与接收", "depends_on": ["s1"]},
            {"id": "s4", "description": "联调测试推送链路", "depends_on": ["s2", "s3"]},
        ],
    },
    {
        "goal": "实现一个 RAG 问答系统",
        "steps": [
            {"id": "s1", "description": "文档摄入与切块", "depends_on": []},
            {"id": "s2", "description": "向量化并存入向量数据库", "depends_on": ["s1"]},
            {"id": "s3", "description": "实现检索接口", "depends_on": ["s2"]},
            {"id": "s4", "description": "接入 LLM 生成回答", "depends_on": ["s3"]},
            {"id": "s5", "description": "评估召回率与答案质量", "depends_on": ["s4"]},
        ],
    },
    {
        "goal": "优化数据库查询性能",
        "steps": [
            {"id": "s1", "description": "分析慢查询日志", "depends_on": []},
            {"id": "s2", "description": "添加缺失索引", "depends_on": ["s1"]},
            {"id": "s3", "description": "重写低效 SQL", "depends_on": ["s1"]},
            {"id": "s4", "description": "压测验证性能提升", "depends_on": ["s2", "s3"]},
        ],
    },
    {
        "goal": "设计并实现 API 限流机制",
        "steps": [
            {"id": "s1", "description": "确定限流策略（滑动窗口 / 令牌桶）", "depends_on": []},
            {"id": "s2", "description": "实现 Redis 计数器", "depends_on": ["s1"]},
            {"id": "s3", "description": "编写限流中间件", "depends_on": ["s2"]},
            {"id": "s4", "description": "集成到 API 网关并测试", "depends_on": ["s3"]},
        ],
    },
    {
        "goal": "完成一次代码重构：将单体应用拆分为微服务",
        "steps": [
            {"id": "s1", "description": "识别领域边界，划分服务", "depends_on": []},
            {"id": "s2", "description": "抽取用户服务", "depends_on": ["s1"]},
            {"id": "s3", "description": "抽取订单服务", "depends_on": ["s1"]},
            {
                "id": "s4",
                "description": "实现服务间通信（gRPC/消息队列）",
                "depends_on": ["s2", "s3"],
            },
            {"id": "s5", "description": "数据库拆分与数据迁移", "depends_on": ["s2", "s3"]},
            {"id": "s6", "description": "端到端集成测试", "depends_on": ["s4", "s5"]},
        ],
    },
    {
        "goal": "建立 CI/CD 自动化流水线",
        "steps": [
            {
                "id": "s1",
                "description": "选择 CI 平台（GitHub Actions / Jenkins）",
                "depends_on": [],
            },
            {"id": "s2", "description": "编写构建脚本", "depends_on": ["s1"]},
            {"id": "s3", "description": "配置自动化测试", "depends_on": ["s2"]},
            {"id": "s4", "description": "配置部署流程（staging → prod）", "depends_on": ["s3"]},
            {"id": "s5", "description": "配置通知与监控告警", "depends_on": ["s4"]},
        ],
    },
    {
        "goal": "实现多语言国际化支持（i18n）",
        "steps": [
            {"id": "s1", "description": "提取所有硬编码字符串", "depends_on": []},
            {"id": "s2", "description": "创建语言包（zh/en/ja）", "depends_on": ["s1"]},
            {"id": "s3", "description": "集成 i18n 框架并替换字符串", "depends_on": ["s1", "s2"]},
            {"id": "s4", "description": "测试多语言切换效果", "depends_on": ["s3"]},
        ],
    },
]


class PlanningBenchmark:
    """Plan 结构合理性 + 步骤数 / 依赖正确率（DAG 校验）。"""

    def _build_plan_prompt(self, goal: str) -> str:
        return (
            f"你是任务规划助手。根据以下目标输出仅 JSON 的任务计划，格式：\n"
            '{"goal":"<目标>","steps":[{"id":"s1","description":"...","depends_on":[]}]}\n'
            f"规则：steps 至少 2 步，id 唯一（s1,s2,...），depends_on 引用已存在 step id，不得成环。\n\n"
            f"目标：{goal}"
        )

    async def _call_llm(
        self,
        model: str,
        goal: str,
        mock: bool = False,
        case_idx: int = 0,
    ) -> str:
        if mock:
            plan = _PLANNING_MOCK_PLANS[case_idx % len(_PLANNING_MOCK_PLANS)]
            return json.dumps(plan, ensure_ascii=False)
        messages = [{"role": "user", "content": self._build_plan_prompt(goal)}]
        return await _call_model(model, messages)

    def _parse_plan(self, response: str) -> dict[str, Any] | None:
        """从响应中提取 Plan JSON。"""
        try:
            json_match = re.search(r"\{.*\}", response, re.DOTALL)
            if not json_match:
                return None
            return json.loads(json_match.group())
        except (json.JSONDecodeError, AttributeError):
            return None

    def _is_valid_dag(self, steps: list[dict[str, Any]]) -> bool:
        """校验依赖图是否为 DAG（无环）。"""
        step_ids = {s.get("id") for s in steps if s.get("id")}
        # 拓扑排序
        graph: dict[str, list[str]] = {s["id"]: [] for s in steps if s.get("id")}
        for step in steps:
            sid = step.get("id")
            if not sid:
                continue
            for dep in step.get("depends_on", []):
                if dep in step_ids and dep != sid:
                    graph[sid].append(dep)

        # DFS 检测环
        visited: set[str] = set()
        rec_stack: set[str] = set()

        def has_cycle(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)
            for neighbor in graph.get(node, []):
                if neighbor not in visited:
                    if has_cycle(neighbor):
                        return True
                elif neighbor in rec_stack:
                    return True
            rec_stack.discard(node)
            return False

        return not any(has_cycle(node) for node in list(graph.keys()) if node not in visited)

    def _score_response(
        self,
        response: str,
        min_steps: int,
        max_steps: int,
        require_deps: bool,
    ) -> float:
        """DAG 校验 + 步骤数范围 + 依赖存在性。"""
        plan = self._parse_plan(response)
        if plan is None:
            return 0.0

        steps = plan.get("steps", [])
        if not isinstance(steps, list) or len(steps) == 0:
            return 0.0

        score = 0.0

        # 步骤数合理 (0.35)
        n_steps = len(steps)
        if min_steps <= n_steps <= max_steps:
            score += 0.35
        elif n_steps >= min_steps:
            score += 0.15  # 超出上限但满足下限

        # DAG 无环校验 (0.35)
        if self._is_valid_dag(steps):
            score += 0.35

        # 依赖关系存在（非全部独立）(0.30)
        if require_deps:
            has_any_dep = any(len(s.get("depends_on", [])) > 0 for s in steps)
            if has_any_dep:
                score += 0.30
        else:
            score += 0.30

        return min(1.0, score)

    async def run(self, model: str, mock: bool = False) -> float:
        """返回 0-1 分。10 个 case，测 Plan 结构合理性。"""
        scores = []
        for idx, (goal, min_steps, max_steps, require_deps) in enumerate(_PLANNING_CASES):
            resp = await self._call_llm(model, goal, mock=mock, case_idx=idx)
            score = self._score_response(resp, min_steps, max_steps, require_deps)
            scores.append(score)
            logger.debug("planning case=%d score=%.2f", idx, score)
        result = sum(scores) / len(scores) if scores else 0.0
        logger.info("planning final=%.3f", result)
        return result


# ---------------------------------------------------------------------------
# 串联 4 维度
# ---------------------------------------------------------------------------


async def run_all_benchmarks(model: str, mock: bool = False) -> CapabilityScores:
    """串联跑 4 个维度，返回 CapabilityScores。"""
    logger.info("Starting capability benchmark model=%s mock=%s", model, mock)
    t0 = time.time()

    context_score = await ContextMgmtBenchmark().run(model, mock=mock)
    memory_score = await LongMemoryBenchmark().run(model, mock=mock)
    tool_score = await ToolUseBenchmark().run(model, mock=mock)
    planning_score = await PlanningBenchmark().run(model, mock=mock)

    elapsed = time.time() - t0
    scores = CapabilityScores(
        context_mgmt=context_score,
        long_memory=memory_score,
        tool_use=tool_score,
        planning=planning_score,
    )
    logger.info(
        "Benchmark done model=%s elapsed=%.1fs overall=%.3f",
        model,
        elapsed,
        scores.overall(),
    )
    return scores


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


def _cli() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="模型能力 4 维度 benchmark")
    sub = parser.add_subparsers(dest="cmd")

    run_p = sub.add_parser("run", help="运行 benchmark")
    run_p.add_argument("--model", default="chat-fast", help="模型名称")
    run_p.add_argument("--mock", action="store_true", help="使用 mock 模式")

    args = parser.parse_args()
    if args.cmd == "run":
        scores = asyncio.run(run_all_benchmarks(args.model, mock=args.mock))
        print(f"\n{'=' * 50}")
        print(f"模型: {args.model}  (mock={args.mock})")
        print(f"{'=' * 50}")
        print(f"  context_mgmt : {scores.context_mgmt:.3f}")
        print(f"  long_memory  : {scores.long_memory:.3f}")
        print(f"  tool_use     : {scores.tool_use:.3f}")
        print(f"  planning     : {scores.planning:.3f}")
        print(f"  overall      : {scores.overall():.3f}")
        print(f"{'=' * 50}")
    else:
        parser.print_help()


if __name__ == "__main__":
    _cli()
