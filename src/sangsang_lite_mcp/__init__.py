"""상상공방 Lite — PlayMCP 제출용 Streamable HTTP MCP 서버 (골격).

목표(이 골격의 범위):
  서버 실행 → /mcp endpoint → tools/list 3개 → annotations 5종 → tools/call → Docker build → MCP Inspector 통과.
LLM 호출은 stub(규칙기반)으로 둔다. 고품질 진단은 후속 단계.
"""

__version__ = "0.1.0"
