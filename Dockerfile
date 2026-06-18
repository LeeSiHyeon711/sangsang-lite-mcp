# 상상공방 Lite MCP 서버 — Streamable HTTP (stateless)
# 빌드: docker build -t sangsang-lite-mcp .
# 실행: docker run -p 8000:8000 sangsang-lite-mcp   → http://localhost:8000/mcp
FROM python:3.12-slim

WORKDIR /app

# 의존성 먼저(레이어 캐시)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스
COPY src/ ./src/

# src 레이아웃 → 패키지 임포트 경로
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1
# 배포 환경에서 PORT 주입 가능(미주입 시 8000). 0.0.0.0 바인딩은 server.py가 처리.
ENV PORT=8000
ENV HOST=0.0.0.0

EXPOSE 8000

CMD ["python", "-m", "sangsang_lite_mcp.server"]
