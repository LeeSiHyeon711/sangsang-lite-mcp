#!/usr/bin/env python3
"""MCP 도구 호출 지연 측정 (p50/p95/p99/max).

실행 중인 서버에 prepare_intake → diagnose_idea → design_first_experiment를 N회 호출해
각 도구 + 3단 합산의 지연을 측정한다. PlayMCP p99 3000ms 기준 점검용.

사용:
  python scripts/measure_latency.py [URL] [N]
  python scripts/measure_latency.py http://127.0.0.1:8080/mcp 20

주의:
  - LLM_ENABLED + 유효 크레딧이면 LLM 경로(meta.source=llm) 지연을, 아니면 stub 경로 지연을 측정한다.
  - 결과의 meta.source를 함께 출력하므로 무엇을 측정 중인지 명확하다.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import sys
import time

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100) * (len(s) - 1)))))
    return s[k]


def _report(name: str, samples: list[float]) -> None:
    ms = [v * 1000 for v in samples]
    print(
        f"  {name:<26} n={len(ms):>2} "
        f"p50={_pct(ms,50):7.1f}ms p95={_pct(ms,95):7.1f}ms "
        f"p99={_pct(ms,99):7.1f}ms max={max(ms):7.1f}ms"
    )


async def _run(url: str, n: int) -> int:
    per: dict[str, list[float]] = {"prepare_intake": [], "diagnose_idea": [], "design_first_experiment": [], "total(3-chain)": []}
    source_seen = set()

    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for _ in range(n):
                t_all = time.perf_counter()

                t = time.perf_counter()
                r1 = await session.call_tool("prepare_intake", {"idea_text": "배달 라이더용 식당 메모 앱", "time_budget": "TWO_DAYS"})
                per["prepare_intake"].append(time.perf_counter() - t)
                intake = r1.structuredContent
                source_seen.add((intake.get("meta") or {}).get("source"))

                t = time.perf_counter()
                r2 = await session.call_tool("diagnose_idea", {"intake": intake})
                per["diagnose_idea"].append(time.perf_counter() - t)
                diagnosis = r2.structuredContent

                t = time.perf_counter()
                await session.call_tool("design_first_experiment", {"intake": intake, "diagnosis": diagnosis})
                per["design_first_experiment"].append(time.perf_counter() - t)

                per["total(3-chain)"].append(time.perf_counter() - t_all)

    print(f"\n대상: {url} | 반복 {n}회 | 측정된 source: {sorted(s for s in source_seen if s)}")
    print("-" * 78)
    for name, samples in per.items():
        _report(name, samples)
    print("-" * 78)
    worst_p99 = _pct([v * 1000 for v in per["total(3-chain)"]], 99)
    print(f"  3단 합산 p99 = {worst_p99:.1f}ms  (PlayMCP 도구당 p99 3000ms 기준은 개별 도구 기준)")
    return 0


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp")
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    sys.exit(asyncio.run(_run(url, n)))


if __name__ == "__main__":
    main()
