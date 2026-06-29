"""Phase A：独立 worker 消费 Redis 索引队列。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from apps.gateway.platform_adapter import wire_platform
from apps.gateway.rag.pipeline import run_index_task
from apps.gateway.settings import get_settings
from packages.tasks.queue import get_index_task_queue

logger = logging.getLogger("ai_platform.worker")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="ai-platform-lab index worker")
    parser.add_argument(
        "--info",
        action="store_true",
        help="打印 worker 模式说明",
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=5,
        help="BLPOP 阻塞超时（秒）",
    )
    args = parser.parse_args()

    if args.info:
        print(
            "Phase A：索引任务由 gateway 入 Redis 队列，本进程 BLPOP 后执行 run_index_task。\n"
            "环境变量：REDIS_URL、USE_INDEX_WORKER=true、QDRANT_URL、LLM_API_KEY\n"
            "Compose：docker compose up -d --build\n"
            "详见 docs/phase-a-internal-beta.md"
        )
        return

    stop = False

    def _handle_sig(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    async def _loop() -> None:
        settings = get_settings()
        wire_platform()
        queue = get_index_task_queue()
        if queue is None:
            raise SystemExit("索引任务队列未初始化")
        logger.info("worker started queue=%s", settings.index_queue_name)
        while not stop:
            task_id = queue.dequeue_blocking(timeout_seconds=args.poll_seconds)
            if not task_id:
                continue
            logger.info("dequeued task_id=%s", task_id)
            try:
                await run_index_task(task_id)
            except Exception:
                logger.exception("task failed task_id=%s", task_id)

    asyncio.run(_loop())


if __name__ == "__main__":
    main()
