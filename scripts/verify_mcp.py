#!/usr/bin/env python3
"""상상공방 Lite MCP 서버 검증 스크립트.

실행 중인 Streamable HTTP MCP 서버(/mcp)에 접속해 다음을 순차 검증한다:
  1) initialize + tools/list (도구 3개)
  2) annotations 5종(title/readOnly/destructive/idempotent/openWorld) 노출
  3) tools/call 체이닝: prepare_intake → diagnose_idea → design_first_experiment (stateless)

사용:
  python scripts/verify_mcp.py                          # 기본 http://127.0.0.1:8000/mcp
  python scripts/verify_mcp.py http://127.0.0.1:8791/mcp
  MCP_URL=http://host:port/mcp python scripts/verify_mcp.py

종료코드: 0=통과, 1=실패. (의존성: mcp 클라이언트 SDK)
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

EXPECTED_TOOLS = ["prepare_intake", "diagnose_idea", "design_first_experiment"]
ANNOTATION_FIELDS = ["title", "readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"]


def _default_url() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    return os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp")


async def _verify(url: str) -> bool:
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print(f"[OK] initialize — {url}")

            # 1) tools/list
            listed = await session.list_tools()
            names = [t.name for t in listed.tools]
            print(f"[OK] tools/list — {names}")
            assert sorted(names) == sorted(EXPECTED_TOOLS), f"도구 목록 불일치: {names}"

            # 2) annotations 5종
            for t in listed.tools:
                a = t.annotations
                missing = [f for f in ANNOTATION_FIELDS if getattr(a, f, None) is None]
                assert not missing, f"{t.name} annotations 누락: {missing}"
            print("[OK] annotations 5종 노출 (title + readOnly/destructive/idempotent/openWorld)")

            # 3) tools/call 체이닝 (stateless: 앞 출력 → 다음 입력)
            r1 = await session.call_tool(
                "prepare_intake",
                {"idea_text": "제가 배달하며 식당 메모를 자주 까먹어요. 메모 앱.", "time_budget": "TWO_DAYS"},
            )
            assert not r1.isError, f"prepare_intake 오류: {r1.content}"
            intake = r1.structuredContent
            print(f"[OK] tools/call prepare_intake → pain_source={intake.get('pain_source')}, "
                  f"service_type={intake.get('service_type')}")

            r2 = await session.call_tool("diagnose_idea", {"intake": intake})
            assert not r2.isError, f"diagnose_idea 오류: {r2.content}"
            diagnosis = r2.structuredContent
            print(f"[OK] tools/call diagnose_idea → focus={diagnosis.get('diagnosis_focus')}")

            r3 = await session.call_tool(
                "design_first_experiment", {"intake": intake, "diagnosis": diagnosis}
            )
            assert not r3.isError, f"design_first_experiment 오류: {r3.content}"
            experiment = r3.structuredContent
            print(f"[OK] tools/call design_first_experiment → time_budget={experiment.get('time_budget')}, "
                  f"steps={len(experiment.get('mission_steps', []))}")

            return True


async def _run(url: str, retries: int, delay: float) -> int:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            ok = await _verify(url)
            if ok:
                print("\n=== ✅ MCP 검증 통과 (tools/list 3개 · annotations 5종 · tools/call 체이닝) ===")
                return 0
        except Exception as exc:  # noqa: BLE001 (검증 스크립트라 광범위 캐치)
            last = exc
            if attempt < retries - 1:
                time.sleep(delay)
    print(f"\n=== ❌ MCP 검증 실패: {last!r} ===")
    return 1


def main() -> None:
    url = _default_url()
    retries = int(os.environ.get("VERIFY_RETRIES", "30"))
    delay = float(os.environ.get("VERIFY_DELAY", "0.5"))
    print(f"대상 URL: {url} (재시도 {retries}회, 간격 {delay}s)\n")
    sys.exit(asyncio.run(_run(url, retries, delay)))


if __name__ == "__main__":
    main()
