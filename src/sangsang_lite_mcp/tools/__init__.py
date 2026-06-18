"""도구 등록 묶음. server.py가 register_all(mcp)로 3개 도구를 한 번에 등록한다."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import design_first_experiment, diagnose_idea, prepare_intake


def register_all(mcp: FastMCP) -> None:
    prepare_intake.register(mcp)
    diagnose_idea.register(mcp)
    design_first_experiment.register(mcp)
