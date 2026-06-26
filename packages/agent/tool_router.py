from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from packages.agent.registry import ToolRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROUTING_PATH = REPO_ROOT / "config" / "agent_tool_routing.yaml"

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


@dataclass(frozen=True)
class ToolRoutingResult:
    tool_names: tuple[str, ...]
    strategy: str
    intent: str | None
    total_registered: int
    filtered_count: int
    scores: dict[str, float]


def _tokenize(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text.lower()) if t}


def _last_user_query(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user" and isinstance(msg.get("content"), str):
            return msg["content"]
    return ""


@lru_cache(maxsize=1)
def load_routing_config(path: str | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else DEFAULT_ROUTING_PATH
    if not cfg_path.is_file():
        return {"enabled": False}
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {"enabled": False}


def _allowed_name_set(registry: ToolRegistry, allowed_tools: tuple[str, ...]) -> set[str]:
    return {t.name for t in registry.list_for_tenant(allowed_tools)}


def _match_intent(query: str, config: dict[str, Any]) -> tuple[str | None, list[str]]:
    q = query.lower()
    intents = config.get("intents")
    if not isinstance(intents, dict):
        return None, []
    best_name: str | None = None
    best_score = 0
    best_tools: list[str] = []
    for name, spec in intents.items():
        if not isinstance(spec, dict):
            continue
        keywords = spec.get("keywords") or []
        if not isinstance(keywords, list):
            continue
        score = sum(1 for kw in keywords if isinstance(kw, str) and kw.lower() in q)
        if score > best_score:
            best_score = score
            best_name = name
            tools = spec.get("tools") or []
            best_tools = [t for t in tools if isinstance(t, str)]
    if best_score == 0:
        return None, []
    return best_name, best_tools


def _rag_scores(
    query: str,
    registry: ToolRegistry,
    allowed: set[str],
) -> dict[str, float]:
    q_tokens = _tokenize(query)
    if not q_tokens:
        return {}
    scores: dict[str, float] = {}
    for name in allowed:
        tool = registry.get(name)
        if not tool:
            continue
        corpus = f"{tool.name} {tool.description}"
        t_tokens = _tokenize(corpus)
        if not t_tokens:
            continue
        overlap = len(q_tokens & t_tokens)
        denom = math.sqrt(len(q_tokens) * len(t_tokens))
        scores[name] = overlap / denom if denom else 0.0
    return scores


def select_tools_for_query(
    query: str,
    *,
    registry: ToolRegistry,
    allowed_tools: tuple[str, ...],
    routing_enabled: bool = True,
    rag_enabled: bool = False,
    config_path: str | None = None,
) -> ToolRoutingResult:
    """根据用户 query 缩小暴露给 LLM 的工具子集（与白名单取交集）。"""
    allowed = _allowed_name_set(registry, allowed_tools)
    total = len(allowed)
    if not routing_enabled or not query.strip() or total <= 1:
        names = tuple(sorted(allowed))
        return ToolRoutingResult(
            tool_names=names,
            strategy="none",
            intent=None,
            total_registered=total,
            filtered_count=0,
            scores={},
        )

    config = load_routing_config(config_path)
    if not config.get("enabled", True):
        names = tuple(sorted(allowed))
        return ToolRoutingResult(
            tool_names=names,
            strategy="disabled",
            intent=None,
            total_registered=total,
            filtered_count=0,
            scores={},
        )

    strategy = str(config.get("strategy") or "intent")
    top_k = int(config.get("top_k") or 5)
    candidates: set[str] = set()

    intent_name: str | None = None
    if strategy in ("intent", "both"):
        intent_name, intent_tools = _match_intent(query, config)
        for t in intent_tools:
            if t in allowed:
                candidates.add(t)

    rag_scores: dict[str, float] = {}
    if strategy in ("rag", "both") or rag_enabled:
        rag_scores = _rag_scores(query, registry, allowed)
        ranked = sorted(rag_scores.items(), key=lambda x: x[1], reverse=True)
        for name, score in ranked[:top_k]:
            if score > 0:
                candidates.add(name)

    if not candidates:
        default_tools = config.get("default_tools") or []
        if isinstance(default_tools, list):
            for t in default_tools:
                if isinstance(t, str) and t in allowed:
                    candidates.add(t)
        if not candidates:
            candidates = allowed

    if len(candidates) > top_k:
        if rag_scores:
            candidates = {n for n, _ in sorted(
                ((n, rag_scores.get(n, 0.0)) for n in candidates),
                key=lambda x: x[1],
                reverse=True,
            )[:top_k]}
        else:
            candidates = set(sorted(candidates)[:top_k])

    names = tuple(sorted(candidates))
    return ToolRoutingResult(
        tool_names=names,
        strategy=strategy if not rag_enabled else f"{strategy}+rag",
        intent=intent_name,
        total_registered=total,
        filtered_count=max(0, total - len(names)),
        scores=rag_scores,
    )


def routing_meta(result: ToolRoutingResult) -> dict[str, Any]:
    return {
        "strategy": result.strategy,
        "intent": result.intent,
        "candidate_tools": list(result.tool_names),
        "total_allowed": result.total_registered,
        "filtered_count": result.filtered_count,
    }


def merge_pinned_tools(
    routing: ToolRoutingResult,
    *,
    registry: ToolRegistry,
    allowed_tools: tuple[str, ...],
    pinned_tools: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """将 Plan step 的 tool_hint 等强制并入候选工具（与白名单取交）。"""
    names = set(routing.tool_names)
    if pinned_tools:
        allowed = _allowed_name_set(registry, allowed_tools)
        for tool in pinned_tools:
            if isinstance(tool, str) and tool.strip() and tool.strip() in allowed:
                names.add(tool.strip())
    return tuple(sorted(names))


def select_tools_from_messages(
    messages: list[dict[str, Any]],
    *,
    registry: ToolRegistry,
    allowed_tools: tuple[str, ...],
    routing_enabled: bool = True,
    rag_enabled: bool = False,
) -> ToolRoutingResult:
    return select_tools_for_query(
        _last_user_query(messages),
        registry=registry,
        allowed_tools=allowed_tools,
        routing_enabled=routing_enabled,
        rag_enabled=rag_enabled,
    )
