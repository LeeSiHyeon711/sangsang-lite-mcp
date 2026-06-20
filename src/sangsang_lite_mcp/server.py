"""상상공방 Lite MCP 서버 — Streamable HTTP, stateless.

실행: python -m sangsang_lite_mcp.server   (PYTHONPATH=src 필요)
endpoint: /mcp  (FastMCP Streamable HTTP 기본 경로)
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from .tools import register_all


_INSTRUCTIONS = """\
아이디어 건강검진 — '만들기 전에 먼저 확인하자'는 철학의 아이디어 검증 도구.
앱·웹·서비스 아이디어를 바로 개발 범위로 넘기지 않고, 먼저 깨질 수 있는 위험 가설(균열점)과
48시간 안에 실행 가능한 첫 검증 미션으로 바꾼다. 결과는 짧고 행동 가능한 결과카드다.

오케스트레이션 원칙 (반드시 지킬 것):
1. 사용자가 아이디어를 말하면, 짧거나 불완전해도 **먼저 prepare_intake를 호출**한다.
2. prepare_intake 호출 전에 **자체적으로 질문하지 않는다**(주요 기능/문제/사용자/시간 등을 임의로 묻지 말 것).
3. 부족한 정보는 prepare_intake가 needs_clarification + clarification_questions로 알려준다.
4. 사용자에게는 **prepare_intake가 돌려준 clarification_questions만** 묻는다.
5. 사용자가 이미 말한 정보는 다시 묻지 않는다.
6. time_budget을 사용자가 말하지 않으면 묻지 말고 UNKNOWN으로 전달한다("48시간"·"2일"→TWO_DAYS).

권장 호출 순서: prepare_intake → (필요 시 질문/답변 반영해 prepare_intake 재호출) → diagnose_idea → design_first_experiment.
도구는 빠르게 '검증 미션 재료'를 반환한다. 최종 자연어 카드는 AI채팅이 다듬어 사용자에게 보여준다.

문진(clarification) 규칙:
- prepare_intake가 needs_clarification=true면, 질문을 새로 만들지 말고 **one_shot_clarification_prompt를 그대로** 사용자에게 보여준다(이해 요약 + 추출 필드 확인 + 부족 정보 요청이 한 메시지로 들어 있다).
- 되묻기는 **1회만** 한다. 사용자가 답하면 그 답을 clarification_answer로 넘겨 prepare_intake를 한 번 더 호출한다(has_clarified=true가 되며 부족분은 기본값으로 채워진다).
- 두 번째 prepare_intake 이후에는 부족해도 다시 묻지 말고 diagnose_idea로 진행한다.

"""


def build_server() -> FastMCP:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))

    # PlayMCP: Streamable HTTP + stateless 권장 + result 최소화(application/json 단일 응답).
    # ⚠ name에 'kakao' 포함 금지(PlayMCP 규칙) — 'sangsang-lite' 사용.
    mcp = FastMCP(
        "sangsang-lite",
        instructions=_INSTRUCTIONS,
        stateless_http=True,
        json_response=True,
        host=host,
        port=port,
    )
    register_all(mcp)
    return mcp


mcp = build_server()


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
