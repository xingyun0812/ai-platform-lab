#!/usr/bin/env python3
"""Phase W: Self-Refine Eval Quality Gate.

比较 Self-Refine (1-refine / 3-refine) vs single-shot 在数学推理 benchmark 上的效果。

用法：
  python eval/self_refine_quality_gate.py run        # 跑 benchmark
  python eval/self_refine_quality_gate.py gate        # 门禁检查（无回归）
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
logger = logging.getLogger("self_refine_quality_gate")

# GSM8K 风格数学题 benchmark（~30 题，使 margin of error ~ +/-9%）
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
    {"question": "小明有 15 颗糖，给了小红 4 颗，又给了小刚 3 颗，还剩几颗？", "answer": "8"},
    {"question": "一个正方形的周长是 36 厘米，它的面积是多少平方厘米？", "answer": "81"},
    {"question": "把 120 本书平均分给 6 个班，每个班分到多少本？", "answer": "20"},
    {"question": "一件衣服原价 150 元，先涨价 10%，再打八折出售，现价是多少元？", "answer": "132"},
    {"question": "一段路长 500 米，已经修了 180 米，剩下的每天修 40 米，还需要几天修完？", "answer": "8"},
    {"question": "学校买了 8 箱矿泉水，每箱 24 瓶，分给 6 个年级，每个年级分到多少瓶？", "answer": "32"},
    {"question": "一个数加上 15，再乘以 3，结果是 90，这个数是多少？", "answer": "15"},
    {"question": "甲乙两数的和是 120，甲数是乙数的 3 倍，乙数是多少？", "answer": "30"},
    {"question": "一桶油重 50 千克，用去了 3/5，还剩多少千克？", "answer": "20"},
    {"question": "一个圆形的半径是 5 厘米，它的周长是多少厘米？（π取 3.14）", "answer": "31.4"},
    {"question": "某班有 48 人，其中 2/3 是男生，女生有多少人？", "answer": "16"},
    {"question": "一项工程，甲队单独做 10 天完成，乙队单独做 15 天完成，两队合作需要几天完成？", "answer": "6"},
    {"question": "小华从家到学校，每分钟走 60 米，15 分钟到达。如果每分钟走 75 米，需要几分钟？", "answer": "12"},
    {"question": "一种盐水有 200 克，盐和水的比是 1:4，盐有多少克？", "answer": "40"},
    {"question": "一台电脑打七五折后是 3600 元，原价是多少元？", "answer": "4800"},
    {"question": "一个圆锥的底面半径是 3 厘米，高是 4 厘米，它的体积是多少立方厘米？（π取 3.14）", "answer": "37.68"},
    {"question": "小明今年 12 岁，爸爸的年龄是小明的 3 倍，妈妈比爸爸小 3 岁，妈妈今年多少岁？", "answer": "33"},
    {"question": "一本书有 240 页，第一天看了全书的 1/4，第二天看了剩下的 1/3，还剩多少页没看？", "answer": "120"},
    {"question": "一辆货车从甲地到乙地，每小时行 50 公里，3 小时到达。返回时每小时行 60 公里，需要几小时？", "answer": "2.5"},
    {"question": "一个长方体的长是 8 厘米，宽是 6 厘米，高是 4 厘米，它的表面积是多少平方厘米？", "answer": "208"},
]


@dataclass
class EvalResult:
    strategy: str  # "single-shot" | "self-refine-1" | "self-refine-3"
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


async def _run_single_shot(
    question: str,
    model: str | None = None,
) -> str:
    """直接 LLM 回答（single-shot）。"""
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
        logger.warning("single-shot LLM call failed: %s", exc)
        return ""


async def _run_self_refine(
    question: str,
    max_iterations: int = 3,
    model: str | None = None,
) -> str:
    """使用 Self-Refine 模式回答问题。"""
    from packages.agent.self_refine import SelfRefineConfig, run_self_refine

    cfg = SelfRefineConfig(
        max_iterations=max_iterations,
        convergence_strategy="hybrid",
        max_total_llm_calls=max_iterations * 3 + 2,
        temperature=0.1,
    )
    try:
        result = await run_self_refine(
            prompt=question,
            config=cfg,
            model=model,
        )
        return result.final_output or ""
    except Exception as exc:
        logger.warning("self-refine call failed: %s", exc)
        return ""


def _extract_answer(text: str) -> str:
    """从 LLM 输出中提取最终数字答案。"""
    if not text:
        return ""
    import re

    m = re.search(r"最终答案[：: ]\s*(\d+\.?\d*)", text)
    if m:
        return m.group(1)
    numbers = re.findall(r"(\d+\.?\d*)", text)
    return numbers[-1] if numbers else ""


def _is_correct(predicted: str, expected: str) -> bool:
    try:
        return abs(float(predicted) - float(expected)) < 0.01
    except (ValueError, TypeError):
        return predicted.strip() == expected.strip()


async def run_benchmark(
    strategy: str,
    max_iterations: int = 3,
    sample_limit: int | None = None,
    model: str | None = None,
) -> EvalResult:
    """运行 benchmark。"""
    items = _BENCHMARK[:sample_limit] if sample_limit else _BENCHMARK
    result = EvalResult(strategy=strategy, total=len(items))

    for i, item in enumerate(items):
        question = item["question"]
        expected = item["answer"]
        logger.info("[%s] %d/%d: %s", strategy, i + 1, len(items), question[:40])

        start = time.time()
        if strategy == "single-shot":
            output = await _run_single_shot(question, model=model)
        else:
            output = await _run_self_refine(question, max_iterations=max_iterations, model=model)
        elapsed = (time.time() - start) * 1000
        result.total_time_ms += elapsed

        predicted = _extract_answer(output)
        if not predicted:
            result.errors.append(f"Q{i + 1}: no answer extracted")
            continue

        if _is_correct(predicted, expected):
            result.correct += 1
        else:
            result.errors.append(f"Q{i + 1}: expected={expected}, got={predicted}")

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
        "running self-refine benchmark: sample_limit=%s model=%s",
        sample_limit or "all",
        model or "default",
    )

    single = await run_benchmark("single-shot", sample_limit=sample_limit, model=model)
    refine1 = await run_benchmark("self-refine-1", max_iterations=1, sample_limit=sample_limit, model=model)
    refine3 = await run_benchmark("self-refine-3", max_iterations=3, sample_limit=sample_limit, model=model)

    print("\n" + "=" * 60)
    print("Self-Refine Benchmark Results")
    print("=" * 60)
    print(f"  Single-shot:    {single.correct}/{single.total} acc={single.accuracy:.1%} avg={single.avg_time_ms:.0f}ms")
    print(f"  Self-Refine-1:  {refine1.correct}/{refine1.total} acc={refine1.accuracy:.1%} avg={refine1.avg_time_ms:.0f}ms")
    print(f"  Self-Refine-3:  {refine3.correct}/{refine3.total} acc={refine3.accuracy:.1%} avg={refine3.avg_time_ms:.0f}ms")
    if single.errors:
        print(f"\nSingle-shot Errors ({len(single.errors)}):")
        for e in single.errors[:5]:
            print(f"  - {e}")
    if refine1.errors:
        print(f"\nSelf-Refine-1 Errors ({len(refine1.errors)}):")
        for e in refine1.errors[:5]:
            print(f"  - {e}")
    if refine3.errors:
        print(f"\nSelf-Refine-3 Errors ({len(refine3.errors)}):")
        for e in refine3.errors[:5]:
            print(f"  - {e}")
    print("=" * 60)

    report = {
        "single_shot": {
            "correct": single.correct, "total": single.total,
            "accuracy": single.accuracy, "avg_time_ms": round(single.avg_time_ms, 1),
        },
        "self_refine_1": {
            "correct": refine1.correct, "total": refine1.total,
            "accuracy": refine1.accuracy, "avg_time_ms": round(refine1.avg_time_ms, 1),
        },
        "self_refine_3": {
            "correct": refine3.correct, "total": refine3.total,
            "accuracy": refine3.accuracy, "avg_time_ms": round(refine3.avg_time_ms, 1),
        },
    }
    with open("/tmp/self_refine_benchmark_report.json", "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    logger.info("report saved to /tmp/self_refine_benchmark_report.json")


async def _cmd_gate() -> None:
    """门禁检查：self-refine(3) 准确率不低于 single-shot（无回归）。"""
    try:
        with open("/tmp/self_refine_benchmark_report.json") as f:
            report = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print("FAIL: no benchmark report found — run 'python eval/self_refine_quality_gate.py run' first")
        sys.exit(1)

    single_acc = report["single_shot"]["accuracy"]
    refine3_acc = report["self_refine_3"]["accuracy"]

    print(f"\nSingle-shot accuracy:    {single_acc:.1%}")
    print(f"Self-Refine-3 accuracy: {refine3_acc:.1%}")
    print(f"Delta:                  {refine3_acc - single_acc:+.1%}")

    # 宽松门禁：self-refine(3) >= single-shot（无回归）
    if refine3_acc >= single_acc:
        print("\nPASS: Self-Refine-3 accuracy >= single-shot (no regression)")
        sys.exit(0)
    else:
        print(f"\nFAIL: Self-Refine-3 ({refine3_acc:.1%}) < single-shot ({single_acc:.1%})")
        sys.exit(1)


async def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "run":
        await _cmd_run()
    elif cmd == "gate":
        await _cmd_gate()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    import anyio
    anyio.run(main)
