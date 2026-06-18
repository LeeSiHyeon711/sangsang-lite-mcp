"""tool: prepare_intake — 자유 서술을 접수 데이터로 구조화 (소통 에이전트, docs/03)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

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
    def prepare_intake(idea_text: str, time_budget: str = "UNKNOWN") -> IntakeData:
        """자유롭게 적은 아이디어를 접수 데이터로 구조화한다.

        Args:
            idea_text: 사용자가 자유롭게 적은 아이디어/불편함 원문.
            time_budget: 검증 투자 가능 시간. 30_MIN | TODAY | TWO_DAYS | ONE_WEEK | TWO_WEEKS_PLUS | UNKNOWN.
        """
        return _prepare(idea_text, time_budget)
