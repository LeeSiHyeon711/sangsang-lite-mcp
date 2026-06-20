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

# 시간 예산별 실험 규모. method/subject의 {a}에 actor(target_user 또는 '협조자')가 주입된다.
# ※ 출력 문자열에 틸드(~)·'+' 금지 — 마크다운 취소선/특수문자로 깨짐. 범위는 자연어로.
_BUDGET = {
    "30_MIN":        {"method": "{a} 본인이 점검하거나 아는 {a} 1명에게 카톡으로 질문", "subject": "{a} 본인 또는 1명이", "n": "1회 이상", "hours": "30분"},
    "TODAY":         {"method": "아는 {a} 1명 또는 2명에게 카톡이나 DM으로 짧게 질문", "subject": "{a} 1명 또는 2명 중 1명 이상이", "n": "2회 이상", "hours": "오늘 안에"},
    "TWO_DAYS":      {"method": "아는 {a} 최대 3명에게 카톡·구글시트·종이 메모로 직접 기록 요청", "subject": "{a} 3명 중 2명 이상이", "n": "3회 이상", "hours": "48시간"},
    "ONE_WEEK":      {"method": "{a} 3명에서 5명과 1주간 작은 파일럿 운영", "subject": "{a} 3명에서 5명 중 절반 이상이", "n": "주 3회 이상", "hours": "1주일"},
    "TWO_WEEKS_PLUS":{"method": "{a} 5명 이상과 노코드·수동 운영 파일럿", "subject": "{a} 5명 중 3명 이상이", "n": "반복적으로", "hours": "2주"},
    "UNKNOWN":       {"method": "아는 {a} 1명 또는 2명에게 카톡으로 질문(가장 가볍게)", "subject": "{a} 1명 또는 2명 중 1명 이상이", "n": "2회 이상", "hours": "오늘 또는 내일"},
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
    # 1인칭 직접 경험 → SELF
    if any(k in text for k in ("내가", "제가", "나는", "내 ", "나도", "직접 겪", "겪었", "해봤", "경험했")):
        return "SELF"
    # 실제 대상/현장/이유 표현이 있으면 단순 상상이 아님 → OBSERVED
    actor = any(k in text for k in ("라이더", "기사", "사장", "직원", "사람들", "주변", "친구", "동료", "고객", "사용자들"))
    grounded = any(k in text for k in ("때문", "현장", "직접", "겪", "봤", "들었", "한다고", "힘들어", "불편해"))
    if actor and grounded:
        return "OBSERVED"
    if any(k in text for k in ("있을 것", "많을 것", "수요", "니즈")):
        return "ASSUMED"
    if actor:
        return "OBSERVED"
    return "IMAGINED"


def _guess_maturity(text: str) -> str:
    # 처음 MVP / 검증하려고 → 해결책까지 있는 단계(SOLUTION)
    if any(k in text for k in ("처음 MVP", "검증하려", "검증하고", "MVP", "만들", "앱", "서비스", "기능", "방식으로", "구현", "솔루션")):
        return "SOLUTION"
    if any(k in text for k in ("문제", "불편", "번거", "어렵", "힘들")):
        return "PROBLEM"
    return "RAW"


# idea_text에서 대상 사용자 추출 (라이더 우선)
_USER_RULES = [
    (("라이더", "배달"), "배달 라이더"),
    (("학생",), "학생"),
    (("직장인", "회사원"), "직장인"),
    (("자영업", "사장", "소상공", "가게 주인"), "자영업자"),
    (("개발자",), "개발자"),
]


def _guess_target_user(text: str) -> str:
    for kws, label in _USER_RULES:
        if any(k in text for k in kws):
            return label
    return ""


def _rich_target_user(text: str, answer: str | None) -> str:
    """추가 답변이 대상을 설명하면 그 구절을 최대한 보존(과도 축약 금지).

    예: '미팅이나 일정이 많은 직장인 대상이야' → '미팅이나 일정이 많은 직장인'
    """
    ans = (answer or "").strip()
    if ans:
        m = re.search(r"(.+?)\s*(?:대상|사용자|쓸\s*사람|이\s*쓸|을\s*위한|를\s*위한)", ans)
        if m:
            phrase = re.sub(r"^(주\s*사용자는|대상은|사용자는)\s*", "", m.group(1)).strip(" ,.")
            if 2 <= len(phrase) <= 40:
                return phrase
        # '대상' 표현은 없지만 사용자 키워드가 답변에 있고 짧으면 답변 자체를 대상 구절로
        if any(any(k in ans for k in kws) for kws, _ in _USER_RULES) and len(ans) <= 40:
            return ans.rstrip(" .야요다이").strip()
    return _guess_target_user(text + " " + ans)


_PROBLEM_TRIGGERS = ("기억하기 어렵", "기억이 안", "까먹", "관리하기 어렵", "관리가 어렵",
                     "헷갈", "번거롭", "잊어", "외우기 어렵", "매번")


def _extract_problem(text: str) -> str:
    """문제를 가리키는 표현이 든 문장을 찾아 요약 반영."""
    for s in re.split(r"[.\n]", text):
        if any(t in s for t in _PROBLEM_TRIGGERS):
            s = s.strip()
            return (s[:120] + "…") if len(s) > 120 else s
    return ""


def _constraints_from_text(text: str) -> list[str]:
    """idea_text의 범위/방식 표현을 제약으로 정규화(결정적)."""
    out: list[str] = []
    if any(k in text for k in ("연동하지 않", "연동 없이", "연동 안", "연동은")):
        out.append("기존 앱과 직접 연동하지 않음(MVP)")
    if ("직접 입력" in text and "관리" in text) or "직접 관리" in text:
        out.append("사용자가 직접 입력·관리")
    elif "직접 입력" in text:
        out.append("사용자가 직접 입력")
    if "수동" in text:
        out.append("수동 방식으로 운영")
    if "개인 메모" in text:
        out.append("개인 메모 방식")
    return out


def _split_constraints(answer: str | None) -> list[str]:
    """추가 답변을 제약 후보로 분해(결정적). 자연어 정제는 PlayMCP 챗 몫."""
    if not answer or not answer.strip():
        return []
    parts = re.split(r"[.\n]|\s그리고\s|,\s", answer)
    out = [p.strip(" .") for p in parts if len(p.strip()) >= 5]
    return out[:5]


def _default_assumptions(service_type: str, target_user: str) -> list[str]:
    u = target_user or "대상 사용자"
    st = service_type if service_type != "기타" else "이 방식"
    return [
        f"{u}가 {st}을(를) 실제로 사용할 의향이 있다",
        f"{u}의 그 문제가 반복적으로 발생한다",
        f"{st} 형태가 그 문제 해결에 적합하다",
    ]


def _extract_context(text: str) -> str:
    """사용 순간/상황 구절 추출(있으면 보존). 앱 이름만으로는 안 잡힘."""
    m = re.search(r"([^.\n]{0,30}(?:받았을\s*때|할\s*때|쓸\s*때|일\s*때|순간|상황|배차|콜|도중|중에))", text)
    return m.group(1).strip(" ,.")[:50] if m else ""


def _extract_behavior(text: str) -> str:
    """핵심 행동 구절 추출(동사부터 시작해 깔끔히). '메모'·'앱' 같은 명사(앱 종류)는 행동으로 보지 않음."""
    m = re.search(
        r"((?:직접\s*|자동\s*)?(?:입력|기록|작성|표시|띄우|뜨게|뜨는|알림|추천|정리|저장|검색|공유|관리)[^.\n]{0,12})", text
    )
    return m.group(1).strip(" ,.")[:40] if m else ""


# 부족 필드별 질문 (이미 말한 건 묻지 않음)
_CLARIFY_Q = {
    "target_user": "이 서비스를 가장 먼저 쓸 사람은 누구인가요? (예: 배달 라이더, 직장인)",
    "context_of_use": "그 사람이 이게 필요해지는 순간은 언제인가요? (예: 배차 콜 받았을 때)",
    "desired_behavior": "그때 사용자가 직접 하길 기대하는 행동은 무엇인가요? (예: 메모 입력, 자동 표시)",
}


# --------------------------------------------------------------------------- #
# 1) prepare_intake — 룰 기반 구조화
# --------------------------------------------------------------------------- #
def prepare_intake(
    idea_text: str, time_budget: str = "UNKNOWN", clarification_answer: str | None = None
) -> IntakeData:
    text = (idea_text or "").strip()
    combined = text + " " + (clarification_answer or "")
    summary = (text[:140] + "…") if len(text) > 140 else (text or "(입력 없음)")
    service_type = _guess_service_type(text)
    target_user = _rich_target_user(text, clarification_answer)
    # 제약: idea_text 정규화 + 추가 답변 분해 (중복 제거, 순서 보존)
    constraints: list[str] = []
    for c in _constraints_from_text(combined) + _split_constraints(clarification_answer):
        if c not in constraints:
            constraints.append(c)
    context = _extract_context(combined)
    behavior = _extract_behavior(combined)

    # 필수 확인 3필드: target_user / context_of_use / desired_behavior
    missing = []
    if not target_user:
        missing.append("target_user")
    if not context:
        missing.append("context_of_use")
    if not behavior:
        missing.append("desired_behavior")
    needs = bool(missing)
    # 질문 수: 기본 최대 2개. 매우 부실(3개 모두 부족)이면 최대 3개. 4개 이상 없음.
    cap = 3 if len(missing) == 3 else 2
    questions = [_CLARIFY_Q[m] for m in missing][:cap] if needs else []

    # 3개 질문으로도 못 채울 정보는 가정으로 남긴다
    gaps = []
    if not target_user:
        gaps.append("대상 사용자는 가장 흔한 사용자층으로 가정한다")
    if not context:
        gaps.append("사용 순간은 반복적으로 생기는 일상 상황으로 가정한다")
    if not behavior:
        gaps.append("핵심 행동은 사용자가 직접 입력·기록하는 것으로 가정한다")
    assume_if_continue = (gaps + _default_assumptions(service_type, target_user))[:3]

    return IntakeData(
        input_summary=summary,
        service_type=service_type,  # type: ignore[arg-type]
        problem=_extract_problem(combined),
        target_user=target_user,
        context_of_use=context,
        desired_behavior=behavior,
        pain_source=_guess_pain_source(combined),  # type: ignore[arg-type]
        maturity=_guess_maturity(combined),  # type: ignore[arg-type]
        validation_time_budget=_coerce_budget(time_budget),  # type: ignore[arg-type]
        needs_clarification=needs,
        clarifying_question=(questions[0] if questions else None),
        clarification_questions=questions,
        can_continue_with_assumptions=True,
        assumptions_if_continue=assume_if_continue,
        assumptions=[],  # 사용자가 확정한 것만(현재 없음)
        constraints=constraints[:5],
    )


# --------------------------------------------------------------------------- #
# 입력 방어 — 카카오 AI가 정규화 전 {idea_text, time_budget}를 intake로 넘긴 경우 복구
# --------------------------------------------------------------------------- #
def _ensure_normalized(intake: IntakeData) -> IntakeData:
    if not intake.input_summary and intake.idea_text:
        return prepare_intake(intake.idea_text, intake.time_budget or "TWO_DAYS")
    return intake


# --------------------------------------------------------------------------- #
# 2) diagnose_idea — 포커스 기반 균열점 템플릿
# --------------------------------------------------------------------------- #
def diagnose(intake: IntakeData) -> Diagnosis:
    intake = _ensure_normalized(intake)
    focus = _FOCUS_BY_SOURCE.get(intake.pain_source, _DEFAULT_FOCUS)
    user = intake.target_user or "사용자"
    # 해결책(SOLUTION)이 있고 방식·주체가 constraints로 정해졌으면, 핵심 위험은 '실제로 할 의지'(WILLINGNESS)
    if intake.constraints and (
        intake.maturity == "SOLUTION" or focus in ("PROBLEM_EXISTENCE", "CONTEXT_OF_USE")
    ):
        focus = "WILLINGNESS"
    prof = _FOCUS.get(focus, _FOCUS[_DEFAULT_FOCUS])
    crack = prof["crack"].format(u=user)
    return Diagnosis(
        problem_statement=intake.problem or "",
        target_user_assumption=f"'{user}'이(가) 이 방식을 실제로 쓸 것이다",
        context_of_use=intake.context_of_use,
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
    intake = _ensure_normalized(intake)
    budget = intake.validation_time_budget if intake.validation_time_budget in _BUDGET else "UNKNOWN"
    b = _BUDGET[budget]
    focus = diagnosis.diagnosis_focus if diagnosis.diagnosis_focus in _FOCUS else _DEFAULT_FOCUS
    f = _FOCUS[focus]

    actor = intake.target_user or "협조자"
    method = b["method"].format(a=actor)
    subject = b["subject"].format(a=actor)
    steps = [
        method,
        f"{b['hours']} 동안 {f['act']}하는지와 횟수를 기록",
        "끝나고 한 줄 피드백(긍정/부정 표현) 받기",
    ]
    success = (
        f"{b['hours']} 안에 {subject} {b['n']} {f['act']}하고, "
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
