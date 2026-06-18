"""tool: diagnose_idea — 접수 데이터로 균열점 1개 진단 (진단 에이전트, docs/05)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ..llm import diagnose as _diagnose
from ..schemas import Diagnosis, IntakeData


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="diagnose_idea",
        annotations=ToolAnnotations(
            title="아이디어 균열점 진단",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def diagnose_idea(intake: IntakeData) -> Diagnosis:
        """접수 데이터를 받아 가장 먼저 깨질 전제(균열점) 1개를 진단한다.

        Args:
            intake: prepare_intake가 만든 접수 데이터 (stateless 입력 체이닝).
        """
        return _diagnose(intake)
