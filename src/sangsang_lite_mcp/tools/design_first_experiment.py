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
        """균열점을 사용자의 시간 예산 안에서 확인할 가장 작은 검증 미션을 설계한다.

        Args:
            intake: prepare_intake 결과 (시간 예산 포함).
            diagnosis: diagnose_idea 결과 (균열점). stateless 입력 체이닝.
        """
        return _design(intake, diagnosis)
