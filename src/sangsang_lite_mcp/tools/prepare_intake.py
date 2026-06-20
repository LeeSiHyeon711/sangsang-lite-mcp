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
    def prepare_intake(
        idea_text: str,
        time_budget: str = "UNKNOWN",
        clarification_answer: str | None = None,
    ) -> IntakeData:
        """Call this tool FIRST whenever the user mentions an idea, even if the idea is short or
        incomplete. Do NOT ask clarifying questions before calling this tool. This tool will
        determine whether clarification is needed and return clarification_questions if necessary.

        사용자가 아이디어를 말하면 먼저 이 도구를 호출하세요. 도구 호출 전에 임의로 질문하지 마세요.
        부족한 정보는 이 도구가 needs_clarification + clarification_questions로 알려줍니다.
        사용자에게는 이 도구가 돌려준 clarification_questions만 물어보세요(이미 말한 건 다시 묻지 않음).

        Args:
            idea_text: 사용자가 자유롭게 적은 아이디어/불편함 원문(짧아도 그대로 전달).
            time_budget: 검증 투자 가능 시간. 사용자가 명시하지 않으면 묻지 말고 UNKNOWN으로 전달.
                "48시간"·"2일"→TWO_DAYS, "오늘"→TODAY, "30분"→30_MIN, "1주"→ONE_WEEK, "2주+"→TWO_WEEKS_PLUS.
                값: 30_MIN | TODAY | TWO_DAYS | ONE_WEEK | TWO_WEEKS_PLUS | UNKNOWN.
            clarification_answer: 도구가 돌려준 clarification_questions에 대한 사용자의 답변(선택).
                제공되면 target_user/context/desired_behavior/constraints를 갱신하고 질문을 정리한다.
        """
        return _prepare(idea_text, time_budget, clarification_answer)
