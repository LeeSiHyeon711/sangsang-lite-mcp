"""검증 미션 '재료' 생성 — 룰/템플릿 기반 (등록용: 서버 내부 LLM 호출 없음).

설계 원칙(PlayMCP 가이드: 평균 100ms·p99 3000ms 필수):
  - MCP 도구는 **빠르고 결정적인** 변환기. 자연어 품질은 PlayMCP AI채팅 본체가 담당한다.
  - 서버는 diagnosis_focus / time_budget / service_type / constraints 기반으로
    구조화된 '검증 미션 재료'(균열점·미션·성공/실패 기준·만들지 말 것)를 룰·템플릿으로 반환한다.
  - LLM/anthropic 호출 없음 → 지연은 사실상 전송 오버헤드(~ms)뿐.

상상공방 Lite 철학은 유지: 균열점 1개, TWO_DAYS 이하는 즉시 가능한 최소 행동,
성공/실패 기준은 숫자·행동·시간 포함, do_not_build_yet 유지, "만들기 전 먼저 확인".
"""

from __future__ import annotations

import re

from .schemas import Diagnosis, FirstExperiment, IntakeData

_VALID_BUDGETS = {"30_MIN", "TODAY", "TWO_DAYS", "ONE_WEEK", "TWO_WEEKS_PLUS", "UNKNOWN"}
_LIGHT_BUDGETS = {"30_MIN", "TODAY", "TWO_DAYS", "UNKNOWN"}

_BUDGET_LABEL = {
    "30_MIN": "30분 이내", "TODAY": "오늘 안에", "TWO_DAYS": "2일 이내",
    "ONE_WEEK": "1주일 이내", "TWO_WEEKS_PLUS": "2주 이상", "UNKNOWN": "미정",
}

# 시간 예산별 실험 규모 (숫자·행동·시간 — 성공/실패 기준에 그대로 들어감)
_BUDGET = {
    "30_MIN":        {"method": "본인 자가 점검 또는 아는 1명에게 카톡으로 질문", "who": "본인이", "n": "1회 이상", "hours": "30분", "scale": "light"},
    "TODAY":         {"method": "아는 1~2명에게 카톡/DM으로 짧게 질문", "who": "물어본 1~2명 중 1명 이상이", "n": "2회 이상", "hours": "오늘 안(몇 시간)에", "scale": "light"},
    "TWO_DAYS":      {"method": "아는 1~3명에게 카톡·구글시트·종이 메모로 직접 기록 요청", "who": "협조자 3명 중 2명 이상이", "n": "3회 이상", "hours": "48시간", "scale": "light"},
    "ONE_WEEK":      {"method": "협조자 3~5명에게 1주간 작은 파일럿 운영", "who": "참여자 3~5명 중 절반 이상이", "n": "주 3회 이상", "hours": "1주일", "scale": "heavy"},
    "TWO_WEEKS_PLUS":{"method": "노코드/수동 운영 파일럿(협조자 5명+)", "who": "참여자 5명 중 3명 이상이", "n": "반복적으로", "hours": "2주", "scale": "heavy"},
    "UNKNOWN":       {"method": "아는 1~2명에게 카톡으로 질문(가장 가볍게)", "who": "물어본 1~2명 중 1명 이상이", "n": "2회 이상", "hours": "오늘~내일", "scale": "light"},
}

# 진단 포커스별 — 균열점·확인 행동(verb 어간)·통과/실패 표현. act는 "{n} {act}하고"에 들어간다.
_FOCUS = {
    "WILLINGNESS":       {"crack": "{u}가 그 행동(입력·관리 등)을 지속할 동기·의지가 충분한가", "act": "자발적으로 직접 실행", "yes": "계속 쓸 것 같다/도움 된다", "no": "번거롭다/필요 없다"},
    "PROBLEM_EXISTENCE": {"crack": "그 문제를 실제로 겪는 {u}가 존재하는가", "act": "겪는 상황을 구체적으로 언급", "yes": "맞아, 나도 그래서 불편하다", "no": "그건 별 문제 아니다"},
    "PAIN_INTENSITY":    {"crack": "그 불편이 {u}의 행동을 바꿀 만큼 강한가", "act": "그 불편을 강하게 호소", "yes": "자주 겪어서 짜증난다", "no": "가끔 있지만 참을 만하다"},
    "SOLUTION_FIT":      {"crack": "제안한 방식이 {u}의 문제에 실제로 맞는가", "act": "그 방식이 자기 상황에 맞다고 확인", "yes": "이런 방식이면 쓰겠다", "no": "이 방식은 내 상황엔 안 맞다"},
    "FEASIBILITY":       {"crack": "이 방식이 {u}의 환경에서 실제로 작동·실행 가능한가", "act": "실제 환경에서 시도", "yes": "되네, 쓸 만하다", "no": "환경 때문에 안 된다"},
    "CONTEXT_OF_USE":    {"crack": "{u}의 실제 사용 순간과 첫 사용자가 구체적으로 성립하는가", "act": "쓸 순간을 구체적으로 지목", "yes": "이럴 때(구체 상황) 쓰겠다", "no": "딱히 쓸 순간이 안 떠오른다"},
    "OPERATION_FIT":     {"crack": "{u}가 이 방식을 지속적으로 운영·유지할 수 있는가", "act": "끊기지 않고 반복 수행", "yes": "계속 유지할 수 있겠다", "no": "며칠 만에 흐지부지된다"},
    "PROBLEM_CAUSE_FIT": {"crack": "이 해결책이 진짜 원인을 겨냥하는가", "act": "진짜 원인을 짚어 말", "yes": "그게 진짜 원인 맞다", "no": "원인은 다른 데 있다"},
}
_DEFAULT_FOCUS = "WILLINGNESS"

_FOCUS_BY_SOURCE = {
    "SELF": "SOLUTION_FIT", "OBSERVED": "PAIN_INTENSITY",
    "ASSUMED": "PROBLEM_EXISTENCE", "IMAGINED": "CONTEXT_OF_USE",
}

# service_type별 '지금 만들지 말 것'
_SERVICE_DNB = {
    "앱": ["앱·백엔드 개발", "정식 UI·디자인"],
    "웹": ["서버·DB·로그인", "정식 디자인"],
    "자동화 도구": ["완전 자동화 구축", "외부 시스템 연동"],
    "업무 개선 도구": ["전용 도구 개발", "정식 시스템화"],
    "기타": ["본격 개발·서버", "정식 디자인"],
}


# --------------------------------------------------------------------------- #
# 공통 헬퍼 (룰 기반)
# --------------------------------------------------------------------------- #
def _coerce_budget(value: str | None) -> str:
    return value if value in _VALID_BUDGETS else "UNKNOWN"


def _guess_service_type(text: str) -> str:
    low = text.lower()
    if "앱" in text or "app" in low:
        return "앱"
    if "웹" in text or "web" in low or "사이트" in text:
        return "웹"
    if "자동화" in text or "automat" in low:
        return "자동화 도구"
    if "업무" in text or "사내" in text:
        return "업무 개선 도구"
    return "기타"


def _guess_pain_source(text: str) -> str:
    if any(k in text for k in ("내가", "제가", "나는", "내 ", "나도")):
        return "SELF"
    if any(k in text for k in ("봤", "들었", "주변", "친구", "동료", "들이", "사람들")):
        return "OBSERVED"
    if any(k in text for k in ("있을 것", "많을 것", "수요", "니즈")):
        return "ASSUMED"
    return "IMAGINED"


def _guess_maturity(text: str) -> str:
    if any(k in text for k in ("만들", "앱", "서비스", "기능", "방식으로", "구현", "솔루션")):
        return "SOLUTION"
    if any(k in text for k in ("문제", "불편", "번거", "어렵", "힘들")):
        return "PROBLEM"
    return "RAW"


def _split_constraints(answer: str | None) -> list[str]:
    """추가 답변을 제약 후보로 분해(결정적). 자연어 정제는 PlayMCP 챗 몫."""
    if not answer or not answer.strip():
        return []
    parts = re.split(r"[.\n]|\s그리고\s|,\s", answer)
    out = [p.strip(" .") for p in parts if len(p.strip()) >= 5]
    return out[:5]


# --------------------------------------------------------------------------- #
# 1) prepare_intake — 룰 기반 구조화
# --------------------------------------------------------------------------- #
def prepare_intake(
    idea_text: str, time_budget: str = "UNKNOWN", clarification_answer: str | None = None
) -> IntakeData:
    text = (idea_text or "").strip()
    summary = (text[:140] + "…") if len(text) > 140 else (text or "(입력 없음)")
    constraints = _split_constraints(clarification_answer)
    return IntakeData(
        input_summary=summary,
        service_type=_guess_service_type(text),  # type: ignore[arg-type]
        problem="",  # 자연어 정제는 PlayMCP 챗이 담당(서버는 재료만)
        target_user="",
        pain_source=_guess_pain_source(text),  # type: ignore[arg-type]
        maturity=_guess_maturity(text),  # type: ignore[arg-type]
        validation_time_budget=_coerce_budget(time_budget),  # type: ignore[arg-type]
        needs_clarification=False,
        clarifying_question=None,
        assumptions=[],
        constraints=constraints,
    )


# --------------------------------------------------------------------------- #
# 2) diagnose_idea — 포커스 기반 균열점 템플릿
# --------------------------------------------------------------------------- #
def diagnose(intake: IntakeData) -> Diagnosis:
    focus = _FOCUS_BY_SOURCE.get(intake.pain_source, _DEFAULT_FOCUS)
    prof = _FOCUS.get(focus, _FOCUS[_DEFAULT_FOCUS])
    user = intake.target_user or "사용자"
    # constraints로 입력 주체/범위가 정해졌으면 '주체 미정'이 아니라 효용/지속을 본다
    if intake.constraints and focus in ("PROBLEM_EXISTENCE", "CONTEXT_OF_USE"):
        focus = "WILLINGNESS"
        prof = _FOCUS[focus]
    crack = prof["crack"].format(u=user)
    return Diagnosis(
        problem_statement=intake.problem or "",
        target_user_assumption=f"'{user}'이(가) 이 방식을 실제로 쓸 것이다",
        context_of_use="",
        crack_point=crack,
        misread_risks=[
            "'좋아 보인다'(관심)와 '실제로 한다'(행동)를 혼동",
            "한두 명의 호의적 반응을 전체 수요로 일반화",
        ],
        positive_signals=["검증할 대상·행동이 구체적임", "48시간 내 직접 확인 가능한 범위"],
        diagnosis_focus=focus,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- #
# 3) design_first_experiment — (focus, time_budget, service_type) 기반 미션 템플릿
# --------------------------------------------------------------------------- #
def design(intake: IntakeData, diagnosis: Diagnosis) -> FirstExperiment:
    budget = intake.validation_time_budget if intake.validation_time_budget in _BUDGET else "UNKNOWN"
    b = _BUDGET[budget]
    focus = diagnosis.diagnosis_focus if diagnosis.diagnosis_focus in _FOCUS else _DEFAULT_FOCUS
    f = _FOCUS[focus]

    steps = [
        b["method"],
        f"{b['hours']} 동안 {f['act']}하는지와 횟수를 기록",
        "끝나고 한 줄 피드백(긍정/부정 표현) 받기",
    ]
    success = (
        f"{b['hours']} 안에 {b['who']} {b['n']} {f['act']}하고, "
        f"1명 이상이 '{f['yes']}'고 말하면 통과"
    )
    failure = [
        f"{f['act']}한 횟수가 1회 이하이거나 시간이 지날수록 줄어듦",
        f"참여자가 '{f['no']}'고 말함",
    ]

    # 지금 만들지 말 것: service_type 기본 + constraints에서 제외/연동 언급 반영
    dnb = list(_SERVICE_DNB.get(intake.service_type, _SERVICE_DNB["기타"]))
    for c in intake.constraints:
        if ("연동" in c or "제외" in c or "안 함" in c or "하지 않" in c) and len(dnb) < 3:
            dnb.append("constraints에서 제외한 것(외부 연동 등)")
            break

    return FirstExperiment(
        time_budget=_BUDGET_LABEL.get(budget, "미정"),
        mission_title=f"균열점 확인: {diagnosis.crack_point}",
        mission_steps=steps[:3],
        why_this_experiment=(
            "이 균열점은 말이 아니라 '실제 행동'으로만 확인되므로, 개발 전에 "
            f"{b['hours']} 안에 가장 싸게 검증하려고 일부러 작게 줄인 미션이다."
        ),
        success_criteria=[success],
        failure_signals=failure[:2],
        do_not_build_yet=dnb[:3],
        next_step_if_passed="통과해도 바로 개발하지 말고, 더 작은 다음 미션 또는 화면 없는 수동/노코드 프로토타입으로",
    )
