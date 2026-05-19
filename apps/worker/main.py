"""第 2 周：索引任务默认在 gateway 进程内通过 BackgroundTasks 执行。

独立 worker 入口保留，便于后续拆分为队列消费进程。
"""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="ai-platform-lab worker")
    parser.add_argument(
        "--info",
        action="store_true",
        help="打印当前 worker 模式说明",
    )
    args = parser.parse_args()
    if args.info:
        print(
            "当前索引任务由 gateway 的 POST /internal/index 触发（FastAPI BackgroundTasks）。\n"
            "启动网关: uvicorn apps.gateway.main:app --reload\n"
            "向量库: docker compose --profile vectors up -d\n"
            "详见 docs/week2-rag-pipeline.md"
        )
    else:
        print("使用 --info 查看 worker 模式说明。")


if __name__ == "__main__":
    main()
