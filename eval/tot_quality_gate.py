#!/usr/bin/env python3
"""Phase S: ToT Eval Quality Gate.

比较 ToT vs CoT 在数学推理 benchmark 上的效果。
用法：
  python eval/tot_quality_gate.py run        # 跑 benchmark
  python eval/tot_quality_gate.py gate        # 门禁检查（通过阈值）
"""

from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import dataclass, field
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("tot_quality_gate")

# 简单的 math word problem benchmark（GSM8K 风格的简化测试集）
_BENCHMARK: list[dict[str, Any]] = [
    {"question": "小明有 5 个苹果，妈妈又给了他 3 个，他一共吃了 2 个，还剩几个？", "answer": "6"},
    {"question": "一个长方形的长是 8 米，宽是 5 米，面积是多少平方米？", "answer": "40"},
    {"question": "一辆车每小时行驶 60 公里，行驶 180 公里需要多少小时？", "answer": "3"},
    {"question": "商店有 120 个气球，上午卖出了 45 个，下午卖出了 38 个，还剩几个？", "answer": "37"},
    {"question": "一本书有 200 页，小红每天读 25 页，需要几天读完？", "answer": "8"},
    {"question": "一个三角形底是 12 厘米，高是 8 厘米，面积是多少平方厘米？", "answer": "48"},
    {"question": "甲乙两地相距 240 公里，两车同时从两地相对开出，甲车每小时行 55 公里，乙车每小时行 65 公里，几小时后相遇？", "answer": "2"},
    {"question": "小明买了 3 支钢笔，每支 12 元，又买了 2 个笔记本，每个 8 元，一共花了多少元？", "answer": "52"},
    {"question": "一个水池有进水管和出水管，单开进水管 6 小时注满，单开出水管 8 小时放完。同时打开两管，几小时可以注满？", "answer": "24"},
    {"question": "某商品原价 200 元，打八五折出售，售价是多少元？", "answer": "170"},
]


@dataclass
class EvalResult:
    strategy: str  # "tot" | "cot"
    correct: int = 0
    total: int = 0
    total_time_ms: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total > 0 else 0.0

    @property
    def avg_time_ms(self) -> float:
        return self.total_time_ms / self.total if self.total > 0 else 0.0


async def _run_cot(
    question: str,
    model: str | None = None,
) -> str:
    """使用 CoT 模式的 LLM 回答问题。"""
    from packages.platform import forward_with_model_router

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是数学解题助手。先在 <thinking> 中写出推理过程，"
                    "再给出最终答案。答案格式：最终答案：<数字>"
                ),
            },
            {"role": "user", "content": question},
        ],
        "temperature": 0.1,
    }
    try:
        route = await forward_with_model_router(payload)
        if route.status != 200 or not route.body:
            return ""
        choices = route.body.get("choices") or []
        if not choices:
            return ""
        return (choices[0].get("message") or {}).get("content") or ""
    except Exception as exc:
        logger.warning("CoT LLM call failed: %s", exc)
        return ""


async def _run_tot(
    question: str,
    model: str | None = None,
) -> str:
    """使用 ToT 模式回答问题。"""
    from packages.agent.tot import TotConfig, run_tot

    cfg = TotConfig(
        search_algorithm="bfs",
        branching_factor=2,
        beam_width=2,
        max_depth=3,
    )
    try:
        result = await run_tot(
            goal=question,
            config=cfg,
            model=model,
        )
        return result.best_answer or ""
    except Exception as exc:
        logger.warning("ToT call failed: %s", exc)
        return ""


def _extract_answer(text: str) -> str:
    """从 LLM 输出中提取最终数字答案。"""
    if not text:
        return ""
    import re

    # 尝试提取「最终答案：」后面的内容
    m = re.search(r"最终答案[：:]\s*(\d+\.?\d*)", text)
    if m:
        return m.group(1)
    # 回退：找最后一个出现的数字
    numbers = re.findall(r"(\d+\.?\d*)", text)
    return numbers[-1] if numbers else ""


def _is_correct(predicted: str, expected: str) -> bool:
    """判断答案是否匹配。"""
    try:
        return abs(float(predicted) - float(expected)) < 0.01
    except (ValueError, TypeError):
        return predicted.strip() == expected.strip()


async def run_benchmark(
    strategy: str,
    sample_limit: int | None = None,
    model: str | None = None,
) -> EvalResult:
    """运行 benchmark，返回评估结果。"""
    items = _BENCHMARK[:sample_limit] if sample_limit else _BENCHMARK
    result = EvalResult(strategy=strategy, total=len(items))
    runner = _run_tot if strategy == "tot" else _run_cot

    for i, item in enumerate(items):
        question = item["question"]
        expected = item["answer"]
        logger.info("[%s] %d/%d: %s", strategy, i + 1, len(items), question[:40])

        start = time.time()
        output = await runner(question, model=model)
        elapsed = (time.time() - start) * 1000
        result.total_time_ms += elapsed

        predicted = _extract_answer(output)
        if not predicted:
            result.errors.append(f"Q{i + 1}: no answer extracted")
            continue

        if _is_correct(predicted, expected):
            result.correct += 1
        else:
            result.errors.append(
                f"Q{i + 1}: expected={expected}, got={predicted}"
            )

    return result


async def _cmd_run() -> None:
    sample_limit = None
    model = None
    for arg in sys.argv[2:]:
        if arg.startswith("--sample-limit=") or arg.startswith("--sample_limit="):
            try:
                sample_limit = int(arg.split("=")[1])
            except (ValueError, IndexError):
                pass
        elif arg.startswith("--model="):
            model = arg.split("=")[1]

    logger.info(
        "running tot vs cot benchmark: sample_limit=%s model=%s",
        sample_limit or "all",
        model or "default",
    )

    tot_result = await run_benchmark("tot", sample_limit=sample_limit, model=model)
    cot_result = await run_benchmark("cot", sample_limit=sample_limit, model=model)

    print("\n" + "=" * 50)
    print("Benchmark Results")
    print("=" * 50)
    print(f"  ToT:  {tot_result.correct}/{tot_result.total} "
          f"acc={tot_result.accuracy:.1%} "
          f"avg={tot_result.avg_time_ms:.0f}ms")
    print(f"  CoT:  {cot_result.correct}/{cot_result.total} "
          f"acc={cot_result.accuracy:.1%} "
          f"avg={cot_result.avg_time_ms:.0f}ms")
    if tot_result.errors:
        print(f"\nToT Errors ({len(tot_result.errors)}):")
        for e in tot_result.errors[:5]:
            print(f"  - {e}")
    if cot_result.errors:
        print(f"\nCoT Errors ({len(cot_result.errors)}):")
        for e in cot_result.errors[:5]:
            print(f"  - {e}")
    print("=" * 50)

    # 保存结果到 JSON（供 gate 命令使用）
    report = {
        "tot": {
            "correct": tot_result.correct,
            "total": tot_result.total,
            "accuracy": tot_result.accuracy,
            "avg_time_ms": round(tot_result.avg_time_ms, 1),
        },
        "cot": {
            "correct": cot_result.correct,
            "total": cot_result.total,
            "accuracy": cot_result.accuracy,
            "avg_time_ms": round(cot_result.avg_time_ms, 1),
        },
    }
    with open("/tmp/tot_benchmark_report.json", "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info("report saved to /tmp/tot_benchmark_report.json")


async def _cmd_gate() -> None:
    threshold = 5.0  # ToT accuracy - CoT accuracy 的绝对值差值
    for arg in sys.argv[2:]:
        if arg.startswith("--threshold="):
            try:
                threshold = float(arg.split("=")[1])
            except (ValueError, IndexError):
                pass

    try:
        with open("/tmp/tot_benchmark_report.json") as f:
            report = json.load(f)
    except FileNotFoundError:
        logger.error("no benchmark report found, run `python eval/tot_quality_gate.py run` first")
        sys.exit(1)

    tot_acc = report["tot"]["accuracy"]
    cot_acc = report["cot"]["accuracy"]
    diff = (tot_acc - cot_acc) * 100

    print(f"\nGate Check (threshold={threshold}%)")
    print(f"  ToT accuracy:  {tot_acc:.1%}")
    print(f"  CoT accuracy:  {cot_acc:.1%}")
    print(f"  Difference:    {diff:+.1f}%")

    if diff >= -threshold:  # ToT 不低于 CoT 超过 threshold
        print(f"  ✅ PASS (diff={diff:+.1f}% >= -{threshold}%)")
        sys.exit(0)
    else:
        print(f"  ❌ FAIL (diff={diff:+.1f}% < -{threshold}%)")
        sys.exit(1)


async def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python eval/tot_quality_gate.py run|gate [--sample-limit=N] [--model=M] [--threshold=N]")
        sys.exit(1)

    command = sys.argv[1]
    if command == "run":
        await _cmd_run()
    elif command == "gate":
        await _cmd_gate()
    else:
        print(f"未知命令: {command}")
        sys.exit(1)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
