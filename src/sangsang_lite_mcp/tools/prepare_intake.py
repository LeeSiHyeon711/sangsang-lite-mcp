"""tool: prepare_intake — 자유 서술을 접수 데이터로 구조화 (소통 에이전트, docs/03)."""

from __future__ import annotations

from typing import Annotated, Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from ..llm import prepare_intake as _prepare
from ..schemas import IntakeData


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="prepare_intake",
        annotations=ToolAnnotations(
            title="공방 접수 — 아이디어 구조화",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def prepare_intake(
        idea_text: str,
        time_budget: Annotated[
            str,
            Field(description="검증 시간. 미명시면 UNKNOWN. 48시간·2일→TWO_DAYS, 오늘→TODAY, 30분→30_MIN, 1주→ONE_WEEK, 2주+→TWO_WEEKS_PLUS"),
        ] = "UNKNOWN",
        clarification_answer: Annotated[
            Optional[str], Field(description="도구가 돌려준 clarification_questions에 대한 사용자 답변")
        ] = None,
    ) -> IntakeData:
        """사용자의 아이디어를 가장 먼저 접수해 검증에 필요한 기본 정보로 정리합니다. 아이디어가 짧거나 불완전해도 먼저 이 도구를 호출하고, 도구 호출 전에는 임의로 질문하지 않습니다. 부족한 정보가 있으면 needs_clarification과 clarification_questions로 알려줍니다."""
        return _prepare(idea_text, time_budget, clarification_answer)
