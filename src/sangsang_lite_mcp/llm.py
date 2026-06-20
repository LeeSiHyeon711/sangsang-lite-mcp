"""LLM 호출 껍데기 + 규칙기반 stub fallback.

설계 원칙(이번 단계 범위):
  - tool은 `prepare_intake` / `diagnose` / `design`만 호출한다(시그니처 불변).
  - 이 함수들이 내부에서 LLM↔stub을 분기한다. tool은 Anthropic SDK를 직접 부르지 않는다.
  - API 키 없음 / LLM_ENABLED off / 호출·파싱 오류 / 타임아웃 → **stub fallback** (tool call은 항상 성공).
  - `anthropic` import는 call_anthropic 내부로 지연 → 키/패키지 없어도 모듈 로드·stub 동작 보장.

환경변수:
  ANTHROPIC_API_KEY     없으면 stub
  LLM_ENABLED           true/1/yes 만 활성 (그 외 stub)
  MODEL_NAME            없으면 가벼운 Claude 기본값
  LLM_TIMEOUT_SECONDS   없으면 2.5초
"""

from __future__ import annotations

import json
import os

from .schemas import Diagnosis, FirstExperiment, IntakeData, TimeBudget, ToolMeta

DEFAULT_MODEL = "claude-haiku-4-5"  # 가벼운 기본값 (MODEL_NAME 으로 override). 구버전 3-5-haiku-latest는 EOL 404.
DEFAULT_TIMEOUT_SECONDS = 2.5

_VALID_BUDGETS = {"30_MIN", "TODAY", "TWO_DAYS", "ONE_WEEK", "TWO_WEEKS_PLUS", "UNKNOWN"}
# TWO_DAYS 이하 = 48시간 안에 즉시 수행 가능한 경량 실험만 (상상공방 Lite 철학)
_LIGHT_BUDGETS = {"30_MIN", "TODAY", "TWO_DAYS"}

_BUDGET_LABEL = {
    "30_MIN": "30분 이내",
    "TODAY": "오늘 안에",
    "TWO_DAYS": "2일 이내",
    "ONE_WEEK": "1주일 이내",
    "TWO_WEEKS_PLUS": "2주 이상",
    "UNKNOWN": "미정",
}

_FOCUS_BY_SOURCE = {
    "SELF": "SOLUTION_FIT",
    "OBSERVED": "PAIN_INTENSITY",
    "ASSUMED": "PROBLEM_EXISTENCE",
    "IMAGINED": "CONTEXT_OF_USE",
}

_CRACK_BY_SOURCE = {
    "SELF": "문제 존재가 아니라 '이 해결 방식이 빈도·강도에 맞는지'가 먼저 확인할 지점이다.",
    "OBSERVED": "관찰자의 해석과 당사자의 실제 불편이 일치하는지가 먼저 확인할 지점이다.",
    "ASSUMED": "그 문제를 실제로 겪는 사람이 존재하는지가 먼저 확인할 지점이다.",
    "IMAGINED": "사용 순간과 첫 사용자가 구체적으로 성립하는지가 먼저 확인할 지점이다.",
}


# --------------------------------------------------------------------------- #
# LLM 활성 판정 / 호출
# --------------------------------------------------------------------------- #
class LLMTimeout(Exception):
    """LLM 호출 타임아웃."""


class LLMError(Exception):
    """LLM 호출/파싱 일반 오류."""


def _llm_flag_on() -> bool:
    return os.environ.get("LLM_ENABLED", "").strip().lower() in {"true", "1", "yes"}


def _model_name() -> str:
    return os.environ.get("MODEL_NAME", "").strip() or DEFAULT_MODEL


def _timeout_seconds() -> float:
    raw = os.environ.get("LLM_TIMEOUT_SECONDS", "").strip()
    try:
        return float(raw) if raw else DEFAULT_TIMEOUT_SECONDS
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def _block_reason() -> str | None:
    """LLM을 못 쓰는 사유. None이면 사용 가능."""
    if not _llm_flag_on():
        return "disabled"
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return "missing_api_key"
    return None


def is_llm_enabled() -> bool:
    return _block_reason() is None


def call_anthropic(prompt: str, *, max_tokens: int = 800, prefill: str | None = None) -> str:
    """Anthropic 호출(지연 import). 오류는 LLMTimeout/LLMError로 정규화해 던진다.

    prefill: assistant 응답을 이 문자열로 시작하도록 강제(예: '{' → JSON만 출력 보장).
    """
    try:
        import anthropic  # 지연 import — 키/패키지 없어도 모듈 로드 안 깨지게
    except Exception as exc:  # noqa: BLE001
        raise LLMError(f"anthropic import 실패: {exc}") from exc

    messages = [{"role": "user", "content": prompt}]
    if prefill:
        messages.append({"role": "assistant", "content": prefill})

    # max_retries=0: 재시도로 타임아웃이 누적(5s×3≈15s)되는 것을 막아 지연 상한을 timeout으로 고정
    client = anthropic.Anthropic(timeout=_timeout_seconds(), max_retries=0)  # api_key는 env에서 자동
    try:
        msg = client.messages.create(
            model=_model_name(),
            max_tokens=max_tokens,
            messages=messages,
        )
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__.lower()
        if "timeout" in name:
            raise LLMTimeout(str(exc)) from exc
        raise LLMError(str(exc)) from exc

    parts = [getattr(b, "text", "") for b in msg.content if getattr(b, "type", None) == "text"]
    text = "\n".join(p for p in parts if p).strip()
    if not text:
        raise LLMError("빈 응답")
    return (prefill + text) if prefill else text  # 프리필 사용 시 시작 토큰 복원


def _parse_json(text: str) -> dict:
    """LLM 응답에서 JSON 1개를 추출. 실패 시 LLMError."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t[:4].lower() == "json":
            t = t[4:]
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1:
        raise LLMError("응답에서 JSON을 찾지 못함")
    try:
        return json.loads(t[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"JSON 파싱 실패: {exc}") from exc


# --------------------------------------------------------------------------- #
# 공통 헬퍼
# --------------------------------------------------------------------------- #
def _guess_service_type(text: str) -> str:
    lowered = text.lower()
    if "앱" in text or "app" in lowered:
        return "앱"
    if "웹" in text or "web" in lowered or "사이트" in text:
        return "웹"
    if "자동화" in text or "automat" in lowered:
        return "자동화 도구"
    return "기타"


def _coerce_budget(value: str | None) -> str:
    return value if value in _VALID_BUDGETS else "UNKNOWN"


# --------------------------------------------------------------------------- #
# STUB (규칙기반, 결정적) — fallback의 기준
# --------------------------------------------------------------------------- #
def _prepare_intake_stub(
    idea_text: str, time_budget: str, clarification_answer: str | None = None
) -> IntakeData:
    text = (idea_text or "").strip()
    summary = (text[:120] + "…") if len(text) > 120 else (text or "(입력 없음)")
    pain_source = "SELF" if ("내가" in text or "제가" in text or "나는" in text) else "IMAGINED"
    answered = bool(clarification_answer and clarification_answer.strip())
    return IntakeData(
        input_summary=summary,
        service_type=_guess_service_type(text),  # type: ignore[arg-type]
        problem="(stub) 자유 서술에서 추출 예정",
        target_user="(stub) 자유 서술에서 추출 예정",
        pain_source=pain_source,  # type: ignore[arg-type]
        maturity="RAW",
        validation_time_budget=_coerce_budget(time_budget),  # type: ignore[arg-type]
        needs_clarification=False,
        clarifying_question=None,  # 추가 답변 수신 시에도 null 유지(req 1)
        constraints=([clarification_answer.strip()] if answered else []),  # type: ignore[union-attr]
    )


def _diagnose_stub(intake: IntakeData) -> Diagnosis:
    source = intake.pain_source
    return Diagnosis(
        problem_statement=intake.problem or "(stub) 문제 정의 예정",
        target_user_assumption=f"'{intake.target_user or '미정'}'이(가) 이 도구를 실제로 쓸 것이다",
        context_of_use="(stub) 실제 사용 순간 정의 예정",
        crack_point=_CRACK_BY_SOURCE.get(source, _CRACK_BY_SOURCE["IMAGINED"]),
        misread_risks=["(stub) 착각 가능성 1", "(stub) 착각 가능성 2"],
        positive_signals=["(stub) 좋은 신호 1"],
        diagnosis_focus=_FOCUS_BY_SOURCE.get(source, "CONTEXT_OF_USE"),  # type: ignore[arg-type]
    )


def _design_stub(intake: IntakeData, diagnosis: Diagnosis) -> FirstExperiment:
    budget: TimeBudget = intake.validation_time_budget
    label = _BUDGET_LABEL.get(budget, "미정")
    if budget in ("30_MIN", "UNKNOWN"):
        steps = ["자가 점검 1가지", "주변 1~2명에게 질문", "기존 사례 1건 확인"]
    elif budget == "TODAY":
        steps = ["짧은 메시지로 3명에게 질문", "응답 소규모 수집", "수동 정리"]
    elif budget == "TWO_DAYS":
        steps = ["아는 대상 1~3명에게 카톡/구글시트로 직접 기록 요청", "48h 내 자발적 기록 여부 확인", "한 줄 피드백 수집"]
    elif budget == "ONE_WEEK":
        steps = ["작은 파일럿 운영", "반복 사용 확인", "결과 기록"]
    else:  # TWO_WEEKS_PLUS
        steps = ["노코드/수동 운영 파일럿", "반복 행동 확인", "지불 의향 확인"]
    return FirstExperiment(
        time_budget=label,
        mission_title=f"[STUB] '{diagnosis.crack_point[:24]}…'을(를) 가장 작게 확인하기",
        mission_steps=steps,
        why_this_experiment="가장 먼저 깨질 전제를 돈·시간 거의 없이 확인하려고 일부러 작게 줄인 미션이다.",
        success_criteria=["48시간 안에 협조자 1~3명 중 2명 이상이 요청한 행동을 3회 이상 하고, 1명 이상이 '도움이 된다'고 답하면 통과"],
        failure_signals=["참여자가 행동을 1인당 1회 이하로 하거나 '필요 없다/귀찮다'고 답하면 실패"],
        do_not_build_yet=["로그인/회원", "서버·DB·앱 개발"],
        next_step_if_passed="통과해도 바로 개발하지 말고, 더 작은 다음 미션 또는 화면 없는 수동/노코드 프로토타입으로",
    )


# --------------------------------------------------------------------------- #
# LLM 경로 (최소 프롬프트 — 고도화 금지) — 실패 시 예외 던져 orchestrator가 fallback
# --------------------------------------------------------------------------- #
def prepare_intake_llm(
    idea_text: str, time_budget: str, clarification_answer: str | None = None
) -> IntakeData:
    answered = bool(clarification_answer and clarification_answer.strip())
    prompt = (
        "다음 아이디어 서술(과 추가 답변)을 공방 접수용 JSON으로만 정리해. 코드블록·설명 금지, JSON만.\n"
        "필드: idea_summary(str), problem(str), target_user(str), "
        "pain_source(SELF|OBSERVED|ASSUMED|IMAGINED), maturity(RAW|SITUATION|PROBLEM|SOLUTION), "
        "validation_time_budget(30_MIN|TODAY|TWO_DAYS|ONE_WEEK|TWO_WEEKS_PLUS|UNKNOWN), "
        "needs_clarification(bool), clarifying_question(str|null), "
        "assumptions(확정 가정 str배열), constraints(명시 제약·MVP 제외·입력 주체 확정 str배열).\n"
        "추가 답변에서 드러난 제약/범위/입력 주체는 반드시 constraints에, 확정 가정은 assumptions에 넣어라. "
        "추가 답변으로 해소됐으면 needs_clarification=false, clarifying_question=null.\n"
        f"검증 가능 시간 힌트: {time_budget}\n"
        f"서술: {idea_text}\n"
        + (f"추가 답변: {clarification_answer}\n" if answered else "")
    )
    data = _parse_json(call_anthropic(prompt, max_tokens=600, prefill="{"))
    # 추가 답변을 받았으면 clarifying_question은 무조건 정리(null) — req 1
    needs = False if answered else bool(data.get("needs_clarification", False))
    question = None if answered else (data.get("clarifying_question") or None)
    return IntakeData(
        input_summary=str(data.get("idea_summary") or idea_text)[:200],
        service_type=_guess_service_type(idea_text),  # type: ignore[arg-type]
        problem=str(data.get("problem") or ""),
        target_user=str(data.get("target_user") or ""),
        pain_source=data.get("pain_source", "IMAGINED"),
        maturity=data.get("maturity", "RAW"),
        validation_time_budget=_coerce_budget(data.get("validation_time_budget") or time_budget),  # type: ignore[arg-type]
        needs_clarification=needs,
        clarifying_question=question,
        assumptions=[str(x) for x in (data.get("assumptions") or [])][:5],
        constraints=[str(x) for x in (data.get("constraints") or [])][:5],
    )


def diagnose_idea_llm(intake: IntakeData) -> Diagnosis:
    prompt = (
        "공방 접수 데이터를 보고 '가장 먼저 확인해야 할 균열점 1개'를 찾아 JSON만 반환해. JSON만.\n"
        "필드: crack_point(str), misread_risks(최대2 str배열), positive_signals(최대2 str배열), "
        "diagnosis_focus(PROBLEM_EXISTENCE|PAIN_INTENSITY|SOLUTION_FIT|WILLINGNESS|FEASIBILITY|"
        "CONTEXT_OF_USE|OPERATION_FIT|PROBLEM_CAUSE_FIT).\n"
        "★ constraints/assumptions는 '확정 사실'이다. 이미 정해진 입력 주체·범위·제외 항목을 균열점으로 삼지 말 것"
        "(예: 입력 주체가 정해졌으면 '주체 미정'이 아니라 '그 주체가 그 일을 할 만큼 효용/지속 의지가 큰가'를 본다). "
        "clarifying_question은 참고만 — 이미 답이 있으면 무시. 비난 말고 '먼저 확인할 지점'으로. SELF면 문제 존재를 묻지 말 것.\n"
        f"constraints(반드시 준수): {intake.constraints}\n"
        f"assumptions(확정): {intake.assumptions}\n"
        f"접수: {intake.model_dump_json()}"
    )
    data = _parse_json(call_anthropic(prompt, max_tokens=500, prefill="{"))
    return Diagnosis(
        problem_statement=intake.problem or "",
        target_user_assumption=f"'{intake.target_user or '미정'}'이(가) 이 도구를 실제로 쓸 것이다",
        context_of_use=str(data.get("context_of_use") or ""),
        crack_point=str(data.get("crack_point") or ""),
        misread_risks=[str(x) for x in (data.get("misread_risks") or [])][:2],
        positive_signals=[str(x) for x in (data.get("positive_signals") or [])][:2],
        diagnosis_focus=data.get("diagnosis_focus", _FOCUS_BY_SOURCE.get(intake.pain_source, "CONTEXT_OF_USE")),
    )


def design_first_experiment_llm(intake: IntakeData, diagnosis: Diagnosis) -> FirstExperiment:
    light_rule = ""
    if intake.validation_time_budget in _LIGHT_BUDGETS:
        light_rule = (
            "★ 시간 예산이 TWO_DAYS 이하다. 실험은 **혼자 또는 1~3명 협조자로 48시간 안에 실제로 수행 가능한** 수준으로만 설계한다. "
            "5명 이상 모집·30분 이상 정식 인터뷰·복잡한 템플릿 제작·정식 프로토타입 개발은 금지. "
            "카카오톡(나에게 보내기)·구글시트·종이 메모·짧은 DM 질문처럼 **즉시 가능한** 방식으로. 균열점 1개만 직접 겨냥.\n"
        )
    prompt = (
        "균열점과 시간 예산으로 '첫 검증 미션'을 설계해 JSON만 반환해. 개발 말고 수동/노코드/질문 우선. 짧게. JSON만.\n"
        "필드: mission_title(str), mission_steps(최대3 str배열), why_this_experiment(1~2문장 str), "
        "success_criteria(1개 str배열), failure_signals(최대2 str배열), do_not_build_yet(최대2 str배열), next_step_if_passed(str).\n"
        "★ success_criteria·failure_signals는 추상 표현 금지 — 사용자가 48시간 뒤 직접 통과/실패를 판정할 수 있게 "
        "숫자·행동·시간 기준을 반드시 포함한다(몇 명 중 몇 명, 몇 시간 안에, 몇 회 이상 행동, 어떤 말을 하면 통과/실패). "
        "'신호가 보인다'·'반응 없음'처럼 모호한 문장 금지. 단 각 항목은 **1문장으로 간결하게**(장황한 수식·괄호 남발 금지).\n"
        "★ next_step_if_passed는 바로 본격 개발이 아니라, 더 작은 다음 미션 또는 최소 수동/노코드 프로토타입으로 이어지게 한다.\n"
        "★ constraints를 반드시 지킨다 — 위반하는 실험 금지(예: 제외된 연동·정해진 입력 주체를 바꾸는 실험 금지). "
        "constraints에 정해진 범위 안에서 균열점을 검증하라.\n"
        + light_rule
        + f"constraints(반드시 준수): {intake.constraints}\n"
        + f"시간예산: {intake.validation_time_budget} / 균열점: {diagnosis.crack_point}"
    )
    data = _parse_json(call_anthropic(prompt, max_tokens=900, prefill="{"))  # 잘림 방지(간결 제약과 병행)
    label = _BUDGET_LABEL.get(intake.validation_time_budget, "미정")
    return FirstExperiment(
        time_budget=label,
        mission_title=str(data.get("mission_title") or ""),
        mission_steps=[str(x) for x in (data.get("mission_steps") or [])][:3],
        why_this_experiment=str(data.get("why_this_experiment") or ""),
        success_criteria=[str(x) for x in (data.get("success_criteria") or [])][:1],
        failure_signals=[str(x) for x in (data.get("failure_signals") or [])][:2] or ["48시간 내 참여자 입력 0건"],
        do_not_build_yet=[str(x) for x in (data.get("do_not_build_yet") or [])][:2],
        next_step_if_passed=str(data.get("next_step_if_passed") or ""),
    )


# --------------------------------------------------------------------------- #
# orchestrator — tool이 호출하는 진입점 (LLM 시도 → 실패 시 stub). 항상 성공.
# --------------------------------------------------------------------------- #
def prepare_intake(
    idea_text: str, time_budget: str = "UNKNOWN", clarification_answer: str | None = None
) -> IntakeData:
    reason = _block_reason()
    if reason is None:
        try:
            res = prepare_intake_llm(idea_text, time_budget, clarification_answer)
            res.meta = ToolMeta(source="llm")
            return res
        except LLMTimeout:
            reason = "timeout"
        except Exception:  # noqa: BLE001 (LLMError·검증오류 등 → fallback)
            reason = "api_error"
    res = _prepare_intake_stub(idea_text, time_budget, clarification_answer)
    res.meta = ToolMeta(source="stub", fallback_reason=reason)  # type: ignore[arg-type]
    return res


def diagnose(intake: IntakeData) -> Diagnosis:
    reason = _block_reason()
    if reason is None:
        try:
            res = diagnose_idea_llm(intake)
            res.meta = ToolMeta(source="llm")
            return res
        except LLMTimeout:
            reason = "timeout"
        except Exception:  # noqa: BLE001
            reason = "api_error"
    res = _diagnose_stub(intake)
    res.meta = ToolMeta(source="stub", fallback_reason=reason)  # type: ignore[arg-type]
    return res


def design(intake: IntakeData, diagnosis: Diagnosis) -> FirstExperiment:
    reason = _block_reason()
    if reason is None:
        try:
            res = design_first_experiment_llm(intake, diagnosis)
            res.meta = ToolMeta(source="llm")
            return res
        except LLMTimeout:
            reason = "timeout"
        except Exception:  # noqa: BLE001
            reason = "api_error"
    res = _design_stub(intake, diagnosis)
    res.meta = ToolMeta(source="stub", fallback_reason=reason)  # type: ignore[arg-type]
    return res
