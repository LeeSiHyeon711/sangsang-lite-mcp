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

        상상공방 Lite 원칙: 결과는 컨설팅 보고서가 아니라 '오늘 할 수 있는 가장 작은 검증 행동'.
        시간 예산이 TWO_DAYS 이하면 혼자 또는 1~3명 협조자로 48시간 안에 실제 수행 가능한 수준으로만 설계하고,
        5명 이상 모집·장시간 인터뷰·복잡한 템플릿·정식 프로토타입 개발은 피한다.

        Args:
            intake: prepare_intake 결과 (시간 예산 포함).
            diagnosis: diagnose_idea 결과 (균열점). stateless 입력 체이닝.
        """
        return _design(intake, diagnosis)
