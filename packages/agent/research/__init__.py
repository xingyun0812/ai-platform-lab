"""packages/agent/research — Deep Research 推理模式。

给定一个研究问题，自动进行：问题分解 → 并行搜索 → 阅读 → 综合 → 迭代深入。

用法：
    result = await run_research(
        question="量子计算的最新进展",
        config=ResearchConfig(max_sub_questions=5, max_depth=2),
    )
    print(result.report)
"""

from __future__ import annotations

import logging
import time
from typing import Any

from packages.agent.research.decomposer import QuestionDecomposer
from packages.agent.research.models import (
    ResearchConfig,
    ResearchNote,
    ResearchResult,
)
from packages.agent.research.searcher import ResearchSearcher
from packages.agent.research.synthesizer import ResearchSynthesizer

logger = logging.getLogger("ai_platform.agent.research")

__all__ = [
    "run_research",
    "ResearchConfig",
    "ResearchResult",
    "ResearchNote",
]


async def run_research(
    question: str,
    config: ResearchConfig | None = None,
    model: str | None = None,
) -> ResearchResult:
    """运行 Deep Research 的主入口。"""
    cfg = config or ResearchConfig()
    start = time.time()
    trace: list[dict[str, Any]] = []
    all_notes: list[ResearchNote] = []
    sub_questions: list[str] = []

    try:
        trace.append({"event": "research_start", "question": question[:60], "config": cfg.to_dict()})

        # Step 1: 问题分解
        decomposer = QuestionDecomposer(model=model)
        sub_questions = await decomposer.decompose(question, cfg)
        trace.append({"event": "decompose_complete", "sub_questions": sub_questions})
        logger.info(
            "research: decomposed into %d sub-questions",
            len(sub_questions),
        )

        # Step 2: 对每个子问题并行搜索+阅读
        searcher = ResearchSearcher(model=model)
        for sq in sub_questions:
            notes = await searcher.search_and_read(
                sub_question=sq,
                config=cfg,
                top_k=cfg.results_per_query,
            )
            all_notes.extend(notes)
            logger.info(
                "research: sub-question=%.40s → %d notes",
                sq,
                len(notes),
            )

        trace.append({
            "event": "search_complete",
            "total_notes": len(all_notes),
        })

        # Step 3: 信息综合
        synthesizer = ResearchSynthesizer(model=model)
        report, findings = await synthesizer.synthesize(question, all_notes, cfg)

        trace.append({"event": "synthesize_complete", "report_length": len(report)})

        # Step 4: (可选) 迭代深入
        depth_completed = 1
        if cfg.max_depth >= 2 and report:
            logger.info("research: identifying gaps for iterative deepening")
            gaps = await synthesizer.identify_gaps(question, report, cfg)
            if gaps:
                trace.append({"event": "gaps_identified", "gaps": gaps})
                logger.info("research: %d gaps identified for second pass", len(gaps))

                from packages.agent.research.gap_filler import ResearchGapFiller

                gap_filler = ResearchGapFiller(model=model)
                gap_notes = await gap_filler.fill_gaps(gaps, cfg)

                # 将补充笔记转为 ResearchNote
                from packages.agent.research.models import ResearchNote as RN

                for gn in gap_notes:
                    all_notes.append(RN(
                        sub_question=gn["sub_question"],
                        source_url=gn["source_url"],
                        source_title=gn["source_title"],
                        summary=gn["summary"],
                        key_points=gn["key_points"],
                    ))

                trace.append({
                    "event": "gap_fill_complete",
                    "gap_notes": len(gap_notes),
                    "total_notes": len(all_notes),
                })

                # 重新综合
                report, findings = await synthesizer.synthesize(question, all_notes, cfg)
                depth_completed = 2
                trace.append({"event": "re_synthesize_complete", "report_length": len(report)})

        elapsed = (time.time() - start) * 1000
        logger.info(
            "research completed: q=%.40s notes=%d depth=%d elapsed=%.0fms",
            question,
            len(all_notes),
            depth_completed,
            elapsed,
        )

        return ResearchResult(
            question=question,
            report=report,
            notes=all_notes,
            sub_questions=sub_questions,
            num_sources_consulted=len(all_notes),
            depth_completed=depth_completed,
            execution_time_ms=elapsed,
            trace=trace,
        )

    except Exception as exc:
        elapsed = (time.time() - start) * 1000
        logger.error("research failed: %s", exc)
        trace.append({"event": "research_error", "error": str(exc)})
        return ResearchResult(
            question=question,
            report="",
            notes=all_notes,
            sub_questions=sub_questions,
            num_sources_consulted=len(all_notes),
            depth_completed=1,
            execution_time_ms=elapsed,
            trace=trace,
            error=str(exc),
        )
