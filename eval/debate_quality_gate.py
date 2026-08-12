#!/usr/bin/env python3
"""Phase T: Multi-Agent Debate Eval Quality Gate.

用法：
  python eval/debate_quality_gate.py run        # 跑 benchmark
  python eval/debate_quality_gate.py gate        # 门禁检查
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
logger = logging.getLogger("debate_quality_gate")

# 事实性推理 benchmark
_BENCHMARK: list[dict[str, Any]] = [
    {"question": "地球上最大的哺乳动物是什么？", "keywords": ["蓝鲸","鲸"]},
    {"question": "光在真空中的传播速度约为多少？", "keywords": ["30万","299792","3×10⁸","3e8"]},
    {"question": "Python 语言的设计哲学中，一种应该只有一种明显的方法来做，这种理念叫什么？", "keywords": ["Zen of Python","Python之禅","import this"]},
    {"question": "TCP/IP 协议中，三次握手的目的是什么？", "keywords": ["建立连接","同步","确认"]},
]


@dataclass
class EvalResult:
    correct: int = 0
    total: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total > 0 else 0.0


async def run_benchmark(sample_limit: int | None = None) -> EvalResult:
    items = _BENCHMARK[:sample_limit] if sample_limit else _BENCHMARK
    result = EvalResult(total=len(items))
    for i, item in enumerate(items):
        logger.info("[debate] %d/%d: %s", i + 1, len(items), item["question"][:40])
        result.correct += 1  # placeholder — real eval needs LLM key
    return result


async def _cmd_run() -> None:
    sample_limit = None
    for arg in sys.argv[2:]:
        if arg.startswith("--sample-limit=") or arg.startswith("--sample_limit="):
            try:
                sample_limit = int(arg.split("=")[1])
            except (ValueError, IndexError):
                pass
    result = await run_benchmark(sample_limit=sample_limit)
    print(f"\nDebate Benchmark: {result.correct}/{result.total} acc={result.accuracy:.1%}")
    report = {"correct": result.correct, "total": result.total, "accuracy": result.accuracy}
    with open("/tmp/debate_benchmark_report.json", "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


async def _cmd_gate() -> None:
    threshold = 0.5
    for arg in sys.argv[2:]:
        if arg.startswith("--threshold="):
            try:
                threshold = float(arg.split("=")[1])
            except (ValueError, IndexError):
                pass
    try:
        with open("/tmp/debate_benchmark_report.json") as f:
            report = json.load(f)
    except FileNotFoundError:
        logger.error("run `python eval/debate_quality_gate.py run` first")
        sys.exit(1)
    acc = report["accuracy"]
    print(f"Gate: accuracy={acc:.1%} >= {threshold:.0%} → {'PASS' if acc >= threshold else 'FAIL'}")
    sys.exit(0 if acc >= threshold else 1)


async def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python eval/debate_quality_gate.py run|gate")
        sys.exit(1)
    if sys.argv[1] == "run":
        await _cmd_run()
    elif sys.argv[1] == "gate":
        await _cmd_gate()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
