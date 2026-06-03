#!/usr/bin/env python3
"""第 5 周：本机短压测（默认 50 并发 healthz，可选 rag）。"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from typing import Any

import httpx


async def _one_request(
    client: httpx.AsyncClient,
    *,
    path: str,
    headers: dict[str, str],
    json_body: dict[str, Any] | None,
) -> tuple[int, float]:
    start = time.perf_counter()
    if json_body is None:
        r = await client.get(path, headers=headers)
    else:
        r = await client.post(path, json=json_body, headers=headers)
    elapsed_ms = (time.perf_counter() - start) * 1000
    return r.status_code, elapsed_ms


async def run_load(
    *,
    base_url: str,
    concurrency: int,
    path: str,
    headers: dict[str, str],
    json_body: dict[str, Any] | None,
) -> None:
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=120.0) as client:
        tasks = [
            _one_request(client, path=path, headers=headers, json_body=json_body)
            for _ in range(concurrency)
        ]
        start = time.perf_counter()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        total_s = time.perf_counter() - start

    ok = 0
    errors = 0
    latencies: list[float] = []
    status_hist: dict[int, int] = {}
    for item in results:
        if isinstance(item, Exception):
            errors += 1
            continue
        status, ms = item
        latencies.append(ms)
        status_hist[status] = status_hist.get(status, 0) + 1
        if 200 <= status < 300:
            ok += 1
        else:
            errors += 1

    print(f"并发数: {concurrency}")
    print(f"路径: {path}")
    print(f"总耗时: {total_s:.2f}s")
    print(f"成功(2xx): {ok}  失败/异常: {errors}")
    print(f"状态码分布: {status_hist}")
    if latencies:
        print(f"延迟 ms — min={min(latencies):.1f} p50={statistics.median(latencies):.1f} p95={sorted(latencies)[int(0.95 * len(latencies)) - 1]:.1f} max={max(latencies):.1f}")
    print(
        "\n结论提示: healthz 压测主要验证进程不崩溃；"
        "rag/query 压测可观察检索+LLM 瓶颈（需已配置 Key 与索引）。"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--target", choices=["healthz", "rag"], default="healthz")
    parser.add_argument("--tenant-id", default="admin")
    parser.add_argument("--bearer-token", default="sk-tenant-admin-change-me")
    args = parser.parse_args()

    headers: dict[str, str] = {}
    json_body: dict[str, Any] | None = None
    path = "/healthz"
    if args.target == "rag":
        path = "/v1/rag/query"
        headers = {
            "Content-Type": "application/json",
            "X-Tenant-Id": args.tenant_id,
            "Authorization": f"Bearer {args.bearer_token}",
        }
        json_body = {
            "tenant_id": args.tenant_id,
            "kb_id": "lab-demo",
            "version": 1,
            "query": "RAG 数据管道",
        }

    asyncio.run(
        run_load(
            base_url=args.base_url,
            concurrency=args.concurrency,
            path=path,
            headers=headers,
            json_body=json_body,
        )
    )


if __name__ == "__main__":
    main()
