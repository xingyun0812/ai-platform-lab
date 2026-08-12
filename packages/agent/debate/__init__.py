"""packages/agent/debate — Multi-Agent Debate 推理模式。

多个 Agent 围绕同一问题独立推理、互相评议、收敛答案。

用法：
    result = await run_debate(
        question="23 × 45 = ?",
        config=DebateConfig(num_proposers=3, num_rounds=2),
    )
    print(result.verdict)
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any

from packages.agent.debate.models import (
    DebateConfig,
    DebateCritique,
    DebateProposal,
    DebateResult,
)

logger = logging.getLogger("ai_platform.agent.debate")

__all__ = [
    "run_debate",
    "DebateConfig",
    "DebateResult",
    "DebateProposal",
    "DebateCritique",
]

_PROPOSER_AGENT_ID_PREFIX = "debate_proposer_"
_CRITIC_AGENT_ID_PREFIX = "debate_critic_"
_JUDGE_AGENT_ID = "debate_judge"


async def run_debate(
    question: str,
    context: str | None = None,
    config: DebateConfig | None = None,
    model: str | None = None,
    tenant_id: str = "admin",
    session_id: str | None = None,
    allowed_tools: tuple[str, ...] | None = None,
    allowed_models: tuple[str, ...] | None = None,
) -> DebateResult:
    """运行 Multi-Agent Debate 的主入口。"""
    cfg = config or DebateConfig()
    resolved_session = session_id or f"debate:{uuid.uuid4().hex[:8]}"
    start = time.time()
    trace: list[dict[str, Any]] = []

    proposals: list[DebateProposal] = []
    critiques: list[DebateCritique] = []

    try:
        trace.append({"event": "debate_start", "question": question[:60], "config": cfg.to_dict()})

        # Round 1: 并行提案
        logger.info("debate round 1: spawning %d proposers", cfg.num_proposers)
        round1_proposals = await _round_proposals(
            question=question, context=context, round_number=1, cfg=cfg,
            tenant_id=tenant_id, session_id=resolved_session,
            allowed_tools=allowed_tools, allowed_models=allowed_models,
        )
        proposals.extend(round1_proposals)
        trace.append({"event": "round1_complete", "num_proposals": len(round1_proposals)})

        # Round 2: 交叉评议
        if cfg.num_rounds >= 2:
            logger.info("debate round 2: spawning critics")
            round2_critiques = await _round_critiques(
                question=question, proposals=round1_proposals, round_number=2, cfg=cfg,
                tenant_id=tenant_id, session_id=resolved_session,
                allowed_tools=allowed_tools, allowed_models=allowed_models,
            )
            critiques.extend(round2_critiques)
            trace.append({"event": "round2_complete", "num_critiques": len(round2_critiques)})

        # Round 3: 反驳/修订（可选）
        if cfg.num_rounds >= 3 and critiques:
            logger.info("debate round 3: proposers revising")
            round3_proposals = await _round_rebuttal(
                question=question, prior_proposals=proposals, critiques=critiques,
                round_number=3, cfg=cfg,
                tenant_id=tenant_id, session_id=resolved_session,
                allowed_tools=allowed_tools, allowed_models=allowed_models,
            )
            proposals.extend(round3_proposals)
            trace.append({"event": "round3_complete", "num_revisions": len(round3_proposals)})

        # Final: 裁定
        logger.info("debate final: judge producing verdict")
        verdict, verdict_confidence = await _final_verdict(
            question=question, proposals=proposals, critiques=critiques, cfg=cfg,
            tenant_id=tenant_id, session_id=resolved_session,
            allowed_tools=allowed_tools, allowed_models=allowed_models,
        )

        elapsed = (time.time() - start) * 1000
        trace.append({"event": "debate_complete", "elapsed_ms": elapsed})

        logger.info("debate completed: q=%.40s p=%d c=%d %.0fms",
                     question, len(proposals), len(critiques), elapsed)

        return DebateResult(
            question=question, verdict=verdict, verdict_confidence=verdict_confidence,
            verdict_agent=_JUDGE_AGENT_ID, proposals=proposals, critiques=critiques,
            num_rounds_completed=cfg.num_rounds, execution_time_ms=elapsed, trace=trace,
        )
    except Exception as exc:
        elapsed = (time.time() - start) * 1000
        logger.error("debate failed: %s", exc)
        trace.append({"event": "debate_error", "error": str(exc)})
        return DebateResult(
            question=question, verdict="", verdict_confidence=0.0,
            proposals=proposals, critiques=critiques,
            num_rounds_completed=cfg.num_rounds, execution_time_ms=elapsed,
            trace=trace, error=str(exc),
        )


async def _round_proposals(
    question: str, round_number: int, cfg: DebateConfig,
    tenant_id: str, session_id: str,
    context: str | None = None,
    allowed_tools=None, allowed_models=None,
) -> list[DebateProposal]:
    from packages.agent.multi_agent.delegation import parallel_delegate
    task = context + "\n\n" + question if context else question
    delegations = [
        {"agent_id": f"{_PROPOSER_AGENT_ID_PREFIX}{i + 1}", "task": task, "inputs": {"round": round_number}}
        for i in range(cfg.num_proposers)
    ]
    results = await parallel_delegate(
        delegations=delegations, tenant_id=tenant_id, session_id=session_id,
        timeout_seconds=cfg.timeout_seconds, allowed_tools=allowed_tools, allowed_models=allowed_models,
    )
    return [DebateProposal(agent_id=r.agent_id, proposal=r.output, round_number=round_number,
                           execution_time_ms=r.execution_time_ms, error=r.error) for r in results]


async def _round_critiques(
    question: str, proposals: list[DebateProposal], round_number: int, cfg: DebateConfig,
    tenant_id: str, session_id: str,
    allowed_tools=None, allowed_models=None,
) -> list[DebateCritique]:
    from packages.agent.multi_agent.delegation import parallel_delegate
    delegations: list[dict[str, Any]] = []
    for i, proposer in enumerate(proposals):
        if proposer.error:
            continue
        others = [p for j, p in enumerate(proposals) if j != i and p.error is None]
        other_text = "\n\n".join(
            f"【提案 by {p.agent_id}】\n{p.proposal}" for p in others)
        critic_task = (
            f"问题：{question}\n\n"
            f"目标提案（by {proposer.agent_id}）：\n{proposer.proposal}\n\n"
            f"其他提案供参考：\n{other_text}\n\n"
            "请评审目标提案：指出优点、缺点、漏洞，并给出改进建议。")
        delegations.append({
            "agent_id": f"{_CRITIC_AGENT_ID_PREFIX}{i + 1}",
            "task": critic_task,
            "inputs": {"round": round_number, "target": proposer.agent_id},
        })
    if not delegations:
        return []
    results = await parallel_delegate(
        delegations=delegations, tenant_id=tenant_id, session_id=session_id,
        timeout_seconds=cfg.timeout_seconds, allowed_tools=allowed_tools, allowed_models=allowed_models,
    )
    critiques: list[DebateCritique] = []
    for r in results:
        target = (r.agent_id or "").replace(_CRITIC_AGENT_ID_PREFIX, _PROPOSER_AGENT_ID_PREFIX)
        critiques.append(DebateCritique(
            critic_agent_id=r.agent_id, target_agent_id=target,
            critique=r.output, round_number=round_number, error=r.error))
    return critiques


async def _round_rebuttal(
    question: str, round_number: int, cfg: DebateConfig,
    tenant_id: str, session_id: str,
    prior_proposals: list[DebateProposal], critiques: list[DebateCritique],
    allowed_tools=None, allowed_models=None,
) -> list[DebateProposal]:
    from packages.agent.multi_agent.delegation import parallel_delegate
    delegations: list[dict[str, Any]] = []
    for p in prior_proposals:
        if p.error:
            continue
        relevant = [c for c in critiques if c.target_agent_id == p.agent_id and c.error is None]
        if not relevant:
            continue
        feedback = "\n\n".join(f"【评议 by {c.critic_agent_id}】\n{c.critique}" for c in relevant)
        delegations.append({
            "agent_id": p.agent_id,
            "task": (
                f"问题：{question}\n\n"
                f"你之前的提案：\n{p.proposal}\n\n"
                f"收到的评议：\n{feedback}\n\n"
                "请基于评议修订你的提案，输出改进后的版本。"),
            "inputs": {"round": round_number, "revision": True},
        })
    if not delegations:
        return []
    results = await parallel_delegate(
        delegations=delegations, tenant_id=tenant_id, session_id=session_id,
        timeout_seconds=cfg.timeout_seconds, allowed_tools=allowed_tools, allowed_models=allowed_models,
    )
    return [DebateProposal(agent_id=r.agent_id, proposal=r.output, round_number=round_number,
                           execution_time_ms=r.execution_time_ms, error=r.error) for r in results]


async def _final_verdict(
    question: str, proposals: list[DebateProposal], critiques: list[DebateCritique],
    cfg: DebateConfig, tenant_id: str, session_id: str,
    allowed_tools=None, allowed_models=None,
) -> tuple[str, float]:
    from packages.agent.multi_agent.delegation import delegate_to_agent
    valid = [p for p in proposals if p.error is None]
    proposals_text = "\n\n".join(
        f"【提案 by {p.agent_id} (第{p.round_number}轮)】\n{p.proposal}" for p in valid)
    critiques_text = "\n\n".join(
        f"【{c.critic_agent_id} 对 {c.target_agent_id} 的评议】\n{c.critique}"
        for c in critiques if c.error is None)
    judge_task = (
        f"问题：{question}\n\n"
        f"=== 全部提案 ===\n{proposals_text}\n\n"
        f"=== 全部评议 ===\n{critiques_text}\n\n"
        "请基于以上提案和评议，给出最终答案。\n"
        "输出格式：\n"
        "最终答案：<你的答案>\n"
        "置信度：<0-1 的数值>\n"
        "理由：<简要说明>")
    result = await delegate_to_agent(
        agent_id=_JUDGE_AGENT_ID, task=judge_task,
        tenant_id=tenant_id, session_id=session_id,
        timeout_seconds=cfg.timeout_seconds,
        allowed_tools=allowed_tools, allowed_models=allowed_models,
    )
    verdict = result.output or ""
    confidence = _extract_confidence(verdict)
    return verdict, confidence


def _extract_confidence(text: str) -> float:
    m = re.search(r"置信度[：:]", text)
    if not m:
        return 0.0
    # grab the number after the colon
    rest = text[m.end():].strip().split()[0] if text[m.end():].strip() else "0"
    rest = rest.rstrip(",.。")
    try:
        val = float(rest)
        return max(0.0, min(1.0, val))
    except ValueError:
        return 0.0
