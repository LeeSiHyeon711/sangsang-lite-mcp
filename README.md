# 상상공방 Lite — MCP 서버

PlayMCP 제출용 **Streamable HTTP / stateless** MCP 서버.
아이디어를 '만들 것'이 아니라 '먼저 확인할 것'으로 바꾸는 검증 도구.

> **등록용 버전은 룰/템플릿 기반(서버 내부 LLM 호출 없음).** 자연어 품질은 PlayMCP AI채팅 본체가 담당하고,
> 서버는 빠른 '검증 미션 재료'만 반환한다(도구당 p99 < 20ms, 평균 100ms 이하 — 가이드 충족).

---

## ⚠️ 오케스트레이션 원칙 (AI채팅이 지킬 것 — 등록 설명에 그대로 사용)

> PlayMCP 등록 시 "서버 설명" 칸에 아래를 넣으세요. server `instructions` + `prepare_intake` description에도 동일하게 박혀 있습니다.

1. 사용자가 아이디어를 말하면, **짧거나 불완전해도 먼저 `prepare_intake`를 호출**한다.
2. `prepare_intake` 호출 **전에 AI가 임의로 질문하지 않는다**(주요 기능/문제/사용자/시간 등을 먼저 묻지 말 것).
3. 부족한 정보는 `prepare_intake`가 `needs_clarification` + `clarification_questions`로 반환한다.
4. AI는 **`prepare_intake`가 돌려준 `clarification_questions`만** 사용자에게 묻는다.
5. 사용자가 이미 말한 정보는 다시 묻지 않는다.
6. `time_budget`을 사용자가 말하지 않으면 묻지 말고 `UNKNOWN`으로 전달한다("48시간"·"2일"→`TWO_DAYS`).

> 호출 순서: `prepare_intake` → (필요 시 질문/답변 반영해 재호출) → `diagnose_idea` → `design_first_experiment`.

```
Call prepare_intake FIRST whenever the user mentions an idea, even if short or incomplete.
Do NOT ask clarifying questions before calling it — the tool returns clarification_questions if needed.
Ask the user ONLY the clarification_questions returned by the tool. Never re-ask what the user already said.
If time_budget is unspecified, pass UNKNOWN (don't ask); "48h"/"2 days" → TWO_DAYS.
```

---

## 도구 3개

| tool | 입력 | 출력 | 대응 에이전트 |
|------|------|------|----------------|
| `prepare_intake` | `idea_text`, `time_budget?` | `IntakeData` | 소통 (docs/03) |
| `diagnose_idea` | `intake` | `Diagnosis` | 진단 (docs/05) |
| `design_first_experiment` | `intake`, `diagnosis` | `FirstExperiment` | 첫실험 (docs/06) |

- 모두 **읽기 전용 분석** → annotations: `readOnlyHint=true, destructiveHint=false, idempotentHint=true, openWorldHint=false` (+ 한글 `title`).
- **stateless 입력 체이닝**: 서버에 세션 없음. 앞 도구 출력을 다음 도구 입력으로 클라이언트가 전달한다.

---

## 로컬 실행

```bash
cd projects/sangsang-lite/prototype/mcp-server
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=src PORT=8000 python -m sangsang_lite_mcp.server
# → Streamable HTTP endpoint: http://localhost:8000/mcp
# 포트가 점유돼 있으면 PORT=8791 등으로 바꿔 실행 (server.py가 PORT/HOST env를 읽음)
```

---

## 검증 (Verification)

> 순서대로 따라 하면 7개 목표(서버 실행 · /mcp · tools/list 3개 · annotations 5종 · tools/call · Docker build · Inspector)를 모두 확인할 수 있다.

### 1) 로컬 서버 실행

```bash
cd projects/sangsang-lite/prototype/mcp-server
pip install -r requirements.txt
PYTHONPATH=src PORT=8000 python -m sangsang_lite_mcp.server   # http://localhost:8000/mcp
```

### 2) 내부 verify 스크립트 (가장 빠른 전체 점검)

서버가 떠 있는 상태에서 **다른 터미널**에서 실행한다. initialize → tools/list(3개) → annotations(5종) → tools/call 체이닝까지 한 번에 검증.

```bash
# 같은 venv(mcp 설치됨)에서
python scripts/verify_mcp.py                          # 기본 http://127.0.0.1:8000/mcp
python scripts/verify_mcp.py http://127.0.0.1:8791/mcp   # 다른 포트
MCP_URL=http://127.0.0.1:8000/mcp python scripts/verify_mcp.py
# 종료코드 0=통과 / 1=실패
```

### 3) MCP Inspector — tools/list (공식 CLI)

```bash
npx @modelcontextprotocol/inspector --cli http://localhost:8000/mcp --transport http --method tools/list
```
- `--transport http` = Streamable HTTP(기본 SSE 아님). 도구 3개 + 각 annotations 노출 확인.

### 4) MCP Inspector — tools/call (공식 CLI)

```bash
# 스칼라 인자라 CLI로 가장 쉬운 스모크 테스트
npx @modelcontextprotocol/inspector --cli http://localhost:8000/mcp --transport http \
  --method tools/call --tool-name prepare_intake \
  --tool-arg idea_text="배달 라이더용 식당 메모 앱" --tool-arg time_budget=TWO_DAYS

# object 입력(diagnose_idea/design_first_experiment)은 UI 모드가 편함
npx @modelcontextprotocol/inspector   # http://localhost:6274 → transport=Streamable HTTP, URL=…/mcp
```

### 5) Docker build & run (linux/amd64 — 배포 타깃 기준)

```bash
cd projects/sangsang-lite/prototype/mcp-server

# PlayMCP/배포 환경은 보통 linux/amd64. Apple Silicon 등에서도 동일 타깃으로 빌드.
docker build --platform linux/amd64 -t sangsang-lite-mcp:latest .

# 컨테이너 실행 (PORT 주입 가능, 컨테이너 내부 0.0.0.0 바인딩은 server.py가 처리)
docker run --rm -p 8000:8000 -e PORT=8000 sangsang-lite-mcp:latest
# → http://localhost:8000/mcp

# 다른 터미널에서 동일하게 검증
python scripts/verify_mcp.py http://127.0.0.1:8000/mcp
```

> 로컬에 Docker가 없으면 build/run은 **미검증 상태로 남긴다.** 위 명령은 linux/amd64 기준이며, 실행 CMD(`python -m sangsang_lite_mcp.server`)는 로컬에서 검증됨.

---

## LLM 사용 (선택 — 없으면 stub fallback)

도구 3개는 기본적으로 **결정적 stub**으로 동작한다. 아래 환경변수를 모두 갖추면 Anthropic 호출로 전환되고,
**키가 없거나 비활성/오류/타임아웃이면 자동으로 stub으로 fallback**한다(도구 호출은 항상 성공).

| 환경변수 | 기본값 | 동작 |
|----------|--------|------|
| `ANTHROPIC_API_KEY` | (없음) | 없으면 stub fallback (`missing_api_key`) |
| `LLM_ENABLED` | `false` | `true`/`1`/`yes`만 LLM 사용. 그 외 stub (`disabled`) |
| `MODEL_NAME` | `claude-3-5-haiku-latest` | 가벼운 Claude 기본값. 필요 시 override |
| `LLM_TIMEOUT_SECONDS` | `2.5` | 호출 타임아웃(초). 초과 시 stub (`timeout`) |

- 결과에는 optional `meta`가 붙는다: `{"source": "llm"|"stub", "fallback_reason": null|"missing_api_key"|"disabled"|"timeout"|"api_error"}`.
- LLM 응답은 JSON으로 받아 **기존 pydantic schema에 맞춰 정제**해 반환한다(원문 그대로 반환하지 않음). 파싱 실패 시 stub fallback.

### 로컬 (LLM on)

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export LLM_ENABLED=true
export MODEL_NAME=claude-3-5-haiku-latest    # 생략 가능(기본값)
PYTHONPATH=src PORT=8000 python -m sangsang_lite_mcp.server
```

### Docker (LLM on)

```bash
docker run -d -p 8080:8000 \
  -e PORT=8000 \
  -e LLM_ENABLED=true \
  -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  -e MODEL_NAME=$MODEL_NAME \
  sangsang-lite-mcp:latest
```

> 키를 안 주거나 `LLM_ENABLED`를 빼면 동일 이미지가 **stub fallback**으로 동작한다(검증·Inspector는 키 없이도 통과).

---

## 구조

```
mcp-server/
├─ Dockerfile
├─ requirements.txt        # mcp>=1.9.0
├─ .env.example
├─ .dockerignore
├─ README.md
├─ scripts/
│  └─ verify_mcp.py        # 실행 중 서버 검증(tools/list+call 체이닝)
└─ src/sangsang_lite_mcp/
   ├─ server.py            # FastMCP(stateless_http, json_response) + run(streamable-http)
   ├─ schemas.py           # IntakeData / Diagnosis / FirstExperiment (→ inputSchema 자동)
   ├─ llm.py               # ★ STUB (규칙기반). 후속에 Anthropic으로 본문만 교체
   └─ tools/
      ├─ prepare_intake.py
      ├─ diagnose_idea.py
      └─ design_first_experiment.py
```

## 확인 필요 (구현 메모)

- `FastMCP(..., host=, port=)` 인자 표면은 설치된 `mcp` 버전에 따라 다를 수 있다(연구문서 §5.1). 동작 안 하면 `mcp.settings.host/port` 또는 env `FASTMCP_HOST/FASTMCP_PORT`로 대체.
- endpoint가 `/mcp`인지 `/mcp/`인지 Inspector로 확정 후 PlayMCP 등록 URL과 일치시킬 것.
- `result 최소화`: stub 출력은 짧게 유지. LLM 결선 시 p99 3000ms 예산 주의.
