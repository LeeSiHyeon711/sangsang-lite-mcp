"""상상공방 Lite MCP 서버 — Streamable HTTP, stateless.

실행: python -m sangsang_lite_mcp.server   (PYTHONPATH=src 필요)
endpoint: /mcp  (FastMCP Streamable HTTP 기본 경로)
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from .tools import register_all


def build_server() -> FastMCP:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))

    # PlayMCP: Streamable HTTP + stateless 권장 + result 최소화(application/json 단일 응답).
    # ⚠ name에 'kakao' 포함 금지(PlayMCP 규칙) — 'sangsang-lite' 사용.
    mcp = FastMCP(
        "sangsang-lite",
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
