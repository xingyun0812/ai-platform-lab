from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ResearchConfig:
    """Deep Research 策略配置。"""

    enabled: bool = True
    max_sub_questions: int = 5
    results_per_query: int = 5
    max_depth: int = 2  # 1=不迭代, 2=一次迭代深入
    timeout_seconds: float = 300.0
    temperature: float = 0.3

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_sub_questions": self.max_sub_questions,
            "results_per_query": self.results_per_query,
            "max_depth": self.max_depth,
            "timeout_seconds": self.timeout_seconds,
            "temperature": self.temperature,
        }


@dataclass
class ResearchNote:
    """研究笔记（一次搜索+阅读的综合产出）。"""

    sub_question: str
    source_url: str
    source_title: str
    summary: str
    key_points: list[str] = field(default_factory=list)
    error: str | None = None
    # Phase V #201: 多模态截图分析
    screenshot_analysis: str | None = None  # LLM 对截图的文本描述
    visual_data: dict[str, Any] | None = None  # 提取的结构化图表数据
    # Phase U 完善: 事实抽取 + 置信度
    facts: list[str] = field(default_factory=list)  # 提取的事实点列表
    confidence: str = "medium"  # high / medium / low
    contradictions: list[str] = field(default_factory=list)  # 与此笔记冲突的其他 note_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "sub_question": self.sub_question,
            "source_url": self.source_url,
            "source_title": self.source_title,
            "summary": self.summary,
            "key_points": self.key_points,
            "error": self.error,
            "screenshot_analysis": self.screenshot_analysis,
            "visual_data": self.visual_data,
            "facts": self.facts,
            "confidence": self.confidence,
            "contradictions": self.contradictions,
        }


@dataclass
class ResearchResult:
    """Deep Research 最终结果。"""

    question: str
    report: str
    notes: list[ResearchNote] = field(default_factory=list)
    sub_questions: list[str] = field(default_factory=list)
    num_sources_consulted: int = 0
    depth_completed: int = 0
    execution_time_ms: float = 0.0
    trace: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "report": self.report,
            "sub_questions": self.sub_questions,
            "num_sources_consulted": self.num_sources_consulted,
            "depth_completed": self.depth_completed,
            "execution_time_ms": self.execution_time_ms,
            "trace": self.trace,
            "error": self.error,
        }
