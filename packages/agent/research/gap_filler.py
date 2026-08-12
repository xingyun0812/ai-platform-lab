from __future__ import annotations

import asyncio
import logging
from typing import Any

from packages.agent.research.models import ResearchConfig

logger = logging.getLogger("ai_platform.agent.research.gap_filler")


class ResearchGapFiller:
    """信息缺口补充搜索器。

    接收信息缺口列表，对每个缺口执行搜索→阅读→摘要，返回新的 ResearchNote。
    支持并发的子问题搜索。
    """

    def __init__(self, model: str | None = None):
        self._model = model

    async def fill_gaps(
        self,
        gaps: list[str],
        config: ResearchConfig,
    ) -> list[dict[str, Any]]:
        """对每个缺口执行搜索，返回补充笔记。

        对多个 gap 并行搜索（但非无限并发，用 asyncio.Semaphore 控制）。
        """
        from packages.agent.research.searcher import ResearchSearcher

        searcher = ResearchSearcher(model=self._model)
        seen_urls: set[str] = set()
        all_notes: list[dict[str, Any]] = []
        sem = asyncio.Semaphore(3)  # 最多 3 个并发

        async def _search_one(gap: str) -> None:
            async with sem:
                notes = await searcher.search_and_read(
                    sub_question=gap,
                    config=config,
                    top_k=config.results_per_query,
                )
                for n in notes:
                    if n.source_url in seen_urls:
                        continue
                    seen_urls.add(n.source_url)
                    all_notes.append({
                        "sub_question": gap,
                        "source_url": n.source_url,
                        "source_title": n.source_title,
                        "summary": n.summary,
                        "key_points": n.key_points,
                    })
                logger.info("gap_filler: %s → %d notes (%d new)", gap[:40], len(notes), len([n for n in notes if n.source_url not in seen_urls]))

        tasks = [_search_one(g) for g in gaps]
        await asyncio.gather(*tasks)

        return all_notes
