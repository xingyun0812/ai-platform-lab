"""RAG 索引任务指标（增量 new/updated/skipped）。"""

from __future__ import annotations

import threading
from collections import defaultdict


class IndexMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks_total: defaultdict[tuple[str, int], int] = defaultdict(int)
        self._new_chunks: defaultdict[tuple[str, int], int] = defaultdict(int)
        self._updated_chunks: defaultdict[tuple[str, int], int] = defaultdict(int)
        self._skipped_chunks: defaultdict[tuple[str, int], int] = defaultdict(int)
        self._purge_total: defaultdict[tuple[str, int], int] = defaultdict(int)

    def record_index_success(
        self,
        *,
        kb_id: str,
        version: int,
        new_chunks: int,
        updated_chunks: int,
        skipped_chunks: int,
    ) -> None:
        key = (kb_id or "unknown", int(version))
        with self._lock:
            self._tasks_total[key] += 1
            self._new_chunks[key] += max(0, new_chunks)
            self._updated_chunks[key] += max(0, updated_chunks)
            self._skipped_chunks[key] += max(0, skipped_chunks)

    def record_purge(self, *, kb_id: str, version: int) -> None:
        key = (kb_id or "unknown", int(version))
        with self._lock:
            self._purge_total[key] += 1

    def prometheus_text(self) -> str:
        with self._lock:
            tasks = dict(self._tasks_total)
            new_c = dict(self._new_chunks)
            updated_c = dict(self._updated_chunks)
            skipped_c = dict(self._skipped_chunks)
            purges = dict(self._purge_total)

        lines: list[str] = []
        lines.append("# HELP rag_index_tasks_total Successful index tasks by kb/version")
        lines.append("# TYPE rag_index_tasks_total counter")
        for (kb, ver), count in sorted(tasks.items()):
            lines.append(f'rag_index_tasks_total{{kb_id="{kb}",version="{ver}"}} {count}')

        lines.append("# HELP rag_index_new_chunks_total New chunks embedded")
        lines.append("# TYPE rag_index_new_chunks_total counter")
        for (kb, ver), count in sorted(new_c.items()):
            lines.append(f'rag_index_new_chunks_total{{kb_id="{kb}",version="{ver}"}} {count}')

        lines.append("# HELP rag_index_updated_chunks_total Updated chunks re-embedded")
        lines.append("# TYPE rag_index_updated_chunks_total counter")
        for (kb, ver), count in sorted(updated_c.items()):
            lines.append(f'rag_index_updated_chunks_total{{kb_id="{kb}",version="{ver}"}} {count}')

        lines.append("# HELP rag_index_skipped_chunks_total Unchanged chunks skipped")
        lines.append("# TYPE rag_index_skipped_chunks_total counter")
        for (kb, ver), count in sorted(skipped_c.items()):
            lines.append(f'rag_index_skipped_chunks_total{{kb_id="{kb}",version="{ver}"}} {count}')

        lines.append("# HELP rag_index_purge_total Source purge operations")
        lines.append("# TYPE rag_index_purge_total counter")
        for (kb, ver), count in sorted(purges.items()):
            lines.append(f'rag_index_purge_total{{kb_id="{kb}",version="{ver}"}} {count}')

        return "\n".join(lines) + "\n"


_store: IndexMetrics | None = None


def get_index_metrics() -> IndexMetrics:
    global _store
    if _store is None:
        _store = IndexMetrics()
    return _store


def reset_index_metrics_for_tests() -> None:
    global _store
    _store = None
