"""tool: design_first_experiment — 균열점을 시간 예산에 맞춘 첫 검증 미션으로 (첫실험 에이전트, docs/06)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ..llm import design as _design
from ..schemas import Diagnosis, FirstExperiment, IntakeData


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="design_first_experiment",
        annotations=ToolAnnotations(
            title="첫 검증 미션 설계",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def design_first_experiment(intake: IntakeData, diagnosis: Diagnosis) -> FirstExperiment:
        """진단된 균열점을 바탕으로 시간 예산 안에서 실행 가능한 첫 검증 미션을 설계합니다. 반드시 검증 준비도 점수(readiness_summary)와 결과카드에 포함할 내용을 함께 반환하며, AI는 이 점수를 최종 결과카드 맨 위에 그대로 표시해야 합니다. 결과는 컨설팅 보고서가 아니라 오늘 또는 48시간 안에 해볼 수 있는 작은 행동, 성공 기준, 실패 신호, 아직 만들지 말아야 할 것으로 정리합니다."""
        return _design(intake, diagnosis)
