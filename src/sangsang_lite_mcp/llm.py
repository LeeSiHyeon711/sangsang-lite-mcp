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

# 진단 포커스 6종 — 같은 '카톡 질문'이라도 무엇을 확인하는지가 아이디어마다 다르다.
#   crack   : 결과카드의 균열점/목표   ({u}=대상 사용자, {j}=조사)
#   act     : 미션 2단계에서 '무엇을 관찰·기록하는지'(명사구; "...를 기록"에 들어감)
#   success : 성공 기준 본문(앞에 기간+대상 주어가 붙는다)
#   failure : 실패 신호(보류) 목록
# ※ 출력 문자열에 틸드(~)·'+' 금지 — 마크다운 깨짐. 범위는 자연어로.
_FOCUS = {
    "PAIN_INTENSITY": {
        "crack": "이 문제가 {u}의 행동을 바꿀 만큼 실제로 불편한가",
        "act": "최근 겪은 실제 사례와 같은 문제가 반복되는지를",
        "success": "최근 실제 사례를 구체적으로 말하고, 같은 문제가 반복된다고 하면 통과",
        "failure": ["'가끔 있긴 하다' 수준이거나 최근 사례를 바로 떠올리지 못하면 보류",
                     "'그건 별 문제 아니다'라고 말하면 보류"],
    },
    "CONTEXT_OF_USE": {
        "crack": "이 도구가 {u}에게 실제로 쓰일 순간이 분명한가",
        "act": "언제·어디서·무엇을 하다가 쓸지를",
        "success": "'언제, 어디서, 무엇을 하다가' 쓸지 구체적으로 말하면 통과",
        "failure": ["쓸 만한 순간을 설명하지 못하면 보류",
                     "'있으면 좋긴 하다' 수준에 머무르면 보류"],
    },
    "WILLINGNESS": {
        "crack": "{u}{j} 직접 입력·기록·확인하는 귀찮은 행동을 할 의지가 있는가",
        "act": "앱 없이 직접 한 번 해봤는지와 다음에도 할 의향을",
        "success": "앱 없이도 카톡·메모장·시트로 직접 한 번 해보고, 다음에도 해볼 의향이 있다고 하면 통과",
        "failure": ["좋다고 말하지만 직접 해보지는 않으면 보류",
                     "번거롭다고 느껴 다시 안 하면 보류"],
    },
    "ALTERNATIVE_BEHAVIOR": {
        "crack": "{u}에게 이미 쓰는 대체재가 있는지, 새 방식이 기존 습관을 이길 수 있는가",
        "act": "지금 쓰는 방식과 그 불편함을",
        "success": "지금 쓰는 방식의 불편함을 말하고, 새 방식이 더 낫다고 이유를 설명하면 통과",
        "failure": ["기존 메모장·카톡·캘린더·종이 등으로 충분하다고 말하면 보류",
                     "굳이 바꿀 이유를 대지 못하면 보류"],
    },
    "DATA_INPUT_BURDEN": {
        "crack": "{u}{j} 필요한 정보를 입력하는 부담을 감수할 수 있는가",
        "act": "직접 입력해본 결과와 부담 정도를",
        "success": "필요한 정보를 1회 이상 직접 입력해보고, 입력 시간이 부담되지 않는다고 하면 통과",
        "failure": ["입력할 정보가 많거나 귀찮아서 다시 안 하겠다고 하면 보류",
                     "한 번 입력한 뒤 이어가지 않으면 보류"],
    },
    "TIMING_URGENCY": {
        "crack": "이 도구가 필요한 순간이, 놓치면 손해가 나는 타이밍인가",
        "act": "그 순간 바로 필요한지와 늦으면 손해인지를",
        "success": "'그 순간 바로 알림·확인이 필요하다'고 말하고, 늦으면 실제 불편·손해가 있다고 설명하면 통과",
        "failure": ["나중에 확인해도 괜찮다고 하면 보류",
                     "즉시성이 중요하지 않다고 하면 보류"],
    },
}
_DEFAULT_FOCUS = "CONTEXT_OF_USE"

# 아이디어 유형 → 포커스 선택(우선순위 룰, 결정적). 위에서부터 처음 맞는 것을 택한다.
_SIG_TIMING = ("알림", "알람", "팝업", "뜨는", "뜨게", "띄우", "실시간", "즉시", "바로",
               "놓치", "마감", "타이밍", "리마인", "제때", "그 순간")
_SIG_INPUT = ("입력", "기록", "장부", "가계부", "식단", "재료", "운동", "일지", "로그", "다이어리")
_SIG_HABIT = ("매일", "꾸준", "습관", "루틴", "일기", "운동", "매번")
_SIG_ALT = ("정리", "메모", "일정", "조율", "추천", "검색", "관리", "캘린더", "목록", "비교")


def _select_focus(intake: "IntakeData") -> str:
    # 원문(input_summary)·맥락·제약만 신호로 사용. 합성된 behavior/problem의 표현('바로' 등)이
    # 포커스 선택을 오염시키지 않게 제외한다.
    t = " ".join((intake.input_summary, intake.context_of_use, " ".join(intake.constraints)))
    if any(k in t for k in _SIG_TIMING):
        return "TIMING_URGENCY"
    if any(k in t for k in _SIG_INPUT):
        return "WILLINGNESS" if any(k in t for k in _SIG_HABIT) else "DATA_INPUT_BURDEN"
    if any(k in t for k in _SIG_ALT):
        return "ALTERNATIVE_BEHAVIOR"
    # 사용자가 지금 쓰는 대체 방법을 말했다면, 기존 습관을 이길 수 있는지가 핵심 위험.
    alt = intake.current_alternative or ""
    if alt and "직접 확인 필요" not in alt:
        return "ALTERNATIVE_BEHAVIOR"
    if intake.pain_source in ("SELF", "OBSERVED"):
        return "PAIN_INTENSITY"
    return _DEFAULT_FOCUS

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


def _josa(word: str, pair: tuple[str, str] = ("이", "가")) -> str:
    """종성 유무로 조사 선택(받침 있으면 pair[0], 없으면 pair[1]). 예: 사장님→이, 라이더→가."""
    if not word:
        return pair[1]
    last = word[-1]
    if "가" <= last <= "힣":
        return pair[0] if (ord(last) - 0xAC00) % 28 else pair[1]
    return pair[1]


# 의도 꼬리말 제거 — '...만들고 싶어 먼저 확인해줘' 류가 필드 추출에 섞이지 않게.
# (서비스 명사 '앱/도구'는 보존; '확인앱'처럼 명사에 붙은 '확인'은 건드리지 않음)
_TAIL_RE = re.compile(
    r"\s*(?:"
    r"먼저\s*(?:확인|검증|점검)\S*"
    r"|(?:확인|검증|점검)\s*(?:해줘|해주\S*|해|좀|부탁\S*)"
    r"|만들?고?\s*싶\S*|만들래\S*|만들고자\S*|만들\s*거\S*|만들어\S*|만들고\s*있\S*"
    r"|개발하?고?\s*싶\S*|하고\s*싶\S*|했으면\s*좋겠\S*|구현하?고?\s*싶\S*"
    r")"
)


def _strip_intent_tail(text: str) -> str:
    """'만들고 싶어', '먼저 확인해줘' 같은 의도 표현 제거. 서비스 명사는 보존."""
    return re.sub(r"\s{2,}", " ", _TAIL_RE.sub(" ", text)).strip(" ,.")


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
    (("자취생",), "자취생"),
    (("대학생",), "대학생"),
    (("학생",), "학생"),
    (("직장인", "회사원"), "직장인"),
    (("자영업", "사장", "소상공", "가게 주인"), "자영업자"),
    (("개발자",), "개발자"),
]


# 원문 표현 보존용: '치킨집 사장님' 같은 구절을 일반 라벨로 과도 축약하지 않는다
_ROLE_NOUNS = (
    "사장님", "사장", "점주", "점장", "라이더", "기사", "스터디원", "회원", "팀원",
    "대학생", "학생", "직장인", "회사원", "개발자", "디자이너", "선생님", "간호사",
    "주부", "상인", "점원", "약사", "의사", "프리랜서",
)
# 역할 명사 앞 한정어는 0~6글자(역할이 문장 맨 앞이어도 매칭되게 {0,6}). 예: '스터디원', '헬스장 회원'
_ROLE_RE = re.compile(r"([가-힣]{0,6}(?:\s[가-힣]{1,6})?\s*(?:" + "|".join(_ROLE_NOUNS) + r"))")


def _guess_target_user(text: str) -> str:
    m = _ROLE_RE.search(text)  # 원문 구절 우선 보존(예: '치킨집 사장님')
    if m:
        return m.group(1).strip()
    for kws, label in _USER_RULES:
        if any(k in text for k in kws):
            return label
    return ""


# 문장 맨 앞 주어구(A가/이) — 역할 사전에 없어도 target_user로 잡는다.
_SUBJECT_RE = re.compile(r"^\s*([가-힣]{2,7}(?:\s[가-힣]{1,7})?)(?:가|이)\s")


def _trim_modifiers(phrase: str) -> str:
    """대상 구절에서 동사·형용사 수식어를 떼고 역할 명사만 남긴다.
    예: '자꾸 까먹는 직장인' → '직장인' (단, '헬스장 회원' 같은 명사 합성은 보존)."""
    toks = (phrase or "").split()
    if len(toks) <= 1:
        return phrase
    last_mod = -1
    for idx, tok in enumerate(toks[:-1]):  # 마지막(역할 명사)은 보존
        if tok in _FREQ or re.search(r"(는|은|던|한|할|운|니|고|서|게|을|를)$", tok):
            last_mod = idx
    return " ".join(toks[last_mod + 1:]) if last_mod >= 0 else phrase


def _subject_target(text: str) -> str:
    """'편의점 알바가 …', '동호회 운영자가 …'처럼 사전에 없는 주어도 추출."""
    m = _SUBJECT_RE.match(text or "")
    if m:
        cand = m.group(1).strip()
        if 2 <= len(cand) <= 14:
            return cand
    return ""


def _strip_subject(text: str, target: str = "") -> str:
    """문장 맨 앞의 주어(역할/일반 명사구)+조사를 일반적으로 떼어낸다."""
    t = (text or "").strip()
    if target and t.startswith(target):
        return t[len(target):].lstrip(" 가이은는의에게을를")
    m = _SUBJECT_RE.match(t)
    if m:
        return t[m.end():].strip()
    return t


def _target_from_answer(answer: str) -> str:
    """clarification_answer에서 대상 '힌트'만 추출(전체 문장을 통째로 넣지 않음 — req5).

    1) '... 대상/사용자/위한' 표현이면 그 앞 구절 보존(예: '미팅 많은 직장인 대상' → '미팅 많은 직장인').
    2) 아니면 역할 표현만(예: '스터디원이 과제 마감일 잊었을때' → '스터디원').
    """
    ans = (answer or "").strip()
    if not ans:
        return ""
    m = re.search(r"(.+?)\s*(?:대상|사용자|쓸\s*사람|이\s*쓸|을\s*위한|를\s*위한)", ans)
    if m:
        phrase = re.sub(r"^(주\s*사용자는|대상은|사용자는)\s*", "", m.group(1)).strip(" ,.")
        if 2 <= len(phrase) <= 40:
            return phrase
    return _guess_target_user(ans)  # 역할만(문장 전체를 target에 넣지 않음)


_PROBLEM_TRIGGERS = ("기억하기 어렵", "기억이 안", "까먹", "관리하기 어렵", "관리가 어렵",
                     "헷갈", "번거롭", "잊어", "외우기 어렵", "매번")


def _extract_problem(text: str) -> str:
    """(레거시 폴백) 문제를 가리키는 표현이 든 문장을 찾아 요약 반영."""
    for s in re.split(r"[.\n]", text):
        if any(t in s for t in _PROBLEM_TRIGGERS):
            s = s.strip()
            return (s[:120] + "…") if len(s) > 120 else s
    return ""


def _problem_sentence(text: str, context: str = "", target: str = "") -> str:
    """문장 패턴으로 problem을 자연스러운 한 문장으로 합성(빈 값/원문통째/부사목적어 방지)."""
    t = _strip_subject(text, target)
    # 망각: 'B 까먹지/잊지 않게', 'B 자꾸 까먹는', 'B 잊는' → 'B를 잊는 문제'
    b = _problem_object(t, r"까먹|잊어버리|잊는|잊지\s*않")
    if b:
        return f"{b}{_noun_particle(b)} 잊는 문제"
    # 낭비: 'B 버리기 전에/버리게' → 'B를 버리게 되는 문제'
    b = _problem_object(t, r"버리")
    if b:
        return f"{b}{_noun_particle(b)} 버리게 되는 문제"
    # 헷갈림: 'B 헷갈리지 않게/헷갈리는' → 'B를 헷갈리는 문제'
    b = _problem_object(t, r"헷갈리|헛갈리")
    if b:
        return f"{b}{_noun_particle(b)} 헷갈리는 문제"
    # 기록 누락: '... 기록 남기는' → '... 기록을 남기지 못하거나 잊는 문제'
    if "기록" in t:
        m = re.search(r"([가-힣]{2,10})\s*(?:후|직후|뒤)?\s*기록", t)
        if m:
            return f"{m.group(1)} 기록을 남기지 못하거나 잊는 문제"
    # 일반 동사형: object + (즉시성) + '확인/찾기 어려운 문제'. object가 깨끗할 때만.
    obj, verb, adverb = _object_before(t)
    if obj:
        pv = "찾기" if verb == "찾" else "확인하기"
        speed = "바로 " if (adverb or verb in ("표시", "뜨는", "뜨게", "팝업")) else ""
        return f"{obj}{_josa(obj, ('을', '를'))} {speed}{pv} 어려운 문제"
    return ""  # 확신 낮음 → 호출부가 안전 fallback 사용


def _constraints_from_text(text: str) -> list[str]:
    """범위/방식을 가리키는 '키워드'가 있을 때만 제약으로 정규화(결정적).

    clarification 답변을 통째로 쪼개지 않는다 — 괄호·쉼표·'대학생/직장인 모두 포함'
    같은 대상 설명이 제약으로 잘못 분해되는 것을 방지(키워드 매칭만 수행).
    """
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
    if any(k in text for k in ("제외", "빼고", "은 빼", "는 빼", "만 만들", "만 우선")):
        out.append("일부 기능은 MVP 범위에서 제외")
    return out


def _default_assumptions(service_type: str, target_user: str) -> list[str]:
    u = target_user or "대상 사용자"
    st = service_type if service_type != "기타" else "이 방식"
    return [
        f"{u}{_josa(u)} {st}{_josa(st, ('을', '를'))} 실제로 사용할 의향이 있다",
        f"{u}의 그 문제가 반복적으로 발생한다",
        f"{st} 형태가 그 문제 해결에 적합하다",
    ]


# 맥락 절의 끝(연결어미). '때'는 일반화('넣을 때','필요할 때' 등 모두 포함).
_CTX_TAIL = r"(?:때|순간|상황|마친\s*직후|직후|직전|전에|끝나고|도중|중에)"
_CTX_RE = re.compile(r"(.{2,40}?" + _CTX_TAIL + r")")


def _strip_lead_target(phrase: str, target: str = "") -> str:
    """구절 맨 앞의 대상(역할)+조사만 떼어낸다. 맥락 일부인 주어(예: '유통기한이')는 보존."""
    p = phrase.strip()
    role = target or _guess_target_user(p)
    if role and p.startswith(role):
        p = p[len(role):].lstrip(" 가이은는의에게을를")
    return p.strip(" ,.")


def _norm_spacing(s: str) -> str:
    """원문 보존 원칙 하에 흔한 붙여쓰기만 자연스럽게 교정."""
    return s.replace("한번에", "한 번에").replace("잊었을때", "잊었을 때")


def _extract_context(text: str, target: str = "") -> str:
    """사용 순간/상황 절을 원문 구절 그대로 추출. '대상(target)'이 맨 앞일 때만 떼고
    그 외 주어(맥락 일부인 '모두가' 등)는 보존한다."""
    body = text.strip()
    if target and body.startswith(target):
        body = body[len(target):].lstrip(" 가이은는의에게을를")
    m = _CTX_RE.search(body)
    if not m:
        return ""
    ctx = m.group(1).strip(" ,.")
    if target and ctx.startswith(target):
        ctx = ctx[len(target):].lstrip(" 가이은는의에게을를")
    return _norm_spacing(ctx)[:60]


# 일반 동사·부사·동사구 매핑 (object 추출과 자연스러운 문장 합성에 공용)
_ADVERBS = ("빨리", "빠르게", "바로", "즉시", "금방", "얼른", "간단히", "쉽게", "한번에", "한 번에", "미리", "자동으로", "직접", "잘")
_SPEED_NORM = {"빨리": "빠르게", "빠르게": "빠르게", "바로": "바로", "즉시": "즉시", "금방": "바로", "얼른": "바로"}
_VERB_RE = re.compile(r"(확인|조회|체크|찾|보는|본다|보기|표시|뜨는|뜨게|팝업|추천|입력|기록|정리|관리|계산|비교)")
_BEH_VERB = {
    "확인": "확인한다", "조회": "확인한다", "체크": "확인한다", "보는": "확인한다", "본다": "확인한다",
    "보기": "확인한다", "표시": "확인한다", "찾": "찾는다", "추천": "추천받는다", "입력": "입력한다",
    "기록": "기록을 남긴다", "정리": "정리한다", "관리": "정리한다", "계산": "계산한다", "비교": "비교한다",
}


# 빈도 부사(목적어에서 제거 대상)
_FREQ = ("자꾸", "자주", "매번", "계속", "늘", "항상", "또")
# 행동 동사(서비스 명사 아님)
_SERVICE_NOUN = r"(?:앱|어플|애플리케이션|도구|서비스|웹|사이트|시스템|플랫폼|프로그램)"

# 확신이 낮을 때 쓰는 안전 fallback(이상한 문장 대신).
_FALLBACK_PROBLEM = "사용자가 말한 상황에서 실제 불편이 반복되는지 확인해야 하는 문제"
_FALLBACK_BEHAVIOR = "필요한 정보를 더 쉽게 확인하거나 정리한다"
_FALLBACK_TARGET = "사용자"


def _clean_clause(s: str) -> str:
    """맥락절 뒤 부분만 취하고, 빈도·속도 부사와 목적격 조사를 벗겨낸 명사구."""
    s = re.split(_CTX_TAIL, s)[-1].strip()
    for _ in range(2):
        s = re.sub(r"\s*(?:" + "|".join(_ADVERBS + _FREQ) + r")\s*$", "", s).strip()
    s = re.sub(r"\s*(?:을|를)\s*$", "", s).strip()
    return s


def _valid_object(o: str) -> bool:
    """목적어 명사구가 '깨끗한지' 검증. 부사·조사 잔여·과길이면 거부(→ fallback)."""
    if not o or not (2 <= len(o) <= 18):
        return False
    if o in _ADVERBS or o in _FREQ:
        return False
    if re.search(r"(용|위한|위해|에게|한테|을|를|은|는|이|가)$", o):  # 문법 잔여 누수
        return False
    return True


def _object_before(action_text: str) -> tuple[str, str, str]:
    """동사 앞의 목적어 명사구를 추출. 맥락절·부사·조사를 벗겨내 (object, verb, adverb) 반환.
    object가 깨끗하지 않으면 빈 object를 돌려 호출부가 fallback하게 한다."""
    s = re.sub(r"\s*(?:" + _SERVICE_NOUN + r")\s*$", "", (action_text or "").strip()).strip()
    m = _VERB_RE.search(s)
    if not m:
        return "", "", ""
    verb = m.group(1)
    before = re.split(_CTX_TAIL, s[:m.start()])[-1].strip()
    adverb = ""
    am = re.search(r"(" + "|".join(_ADVERBS) + r")\s*$", before)
    if am:
        adverb = am.group(1)
        before = before[:am.start()].strip()
    obj = _clean_clause(before)
    if not _valid_object(obj):
        return "", verb, adverb
    return obj, verb, adverb


# 'B 헷갈리지 않게 / B 까먹지 않게' 같은 문제 절에서 B(명사구)를 안전하게 뽑는다.
def _problem_object(text: str, trigger_re: str) -> str:
    m = re.search(r"([가-힣 ]{2,28}?)\s*(?:을|를)?\s*(?:" + trigger_re + r")", text)
    if not m:
        return ""
    b = _clean_clause(m.group(1))
    return b if _valid_object(b) or b.endswith(("지", "는지", "은지")) else ""


def _noun_particle(b: str, pair: tuple[str, str] = ("을", "를")) -> str:
    """명사구 뒤 목적격 조사. 절(…하는지/…나/…면)로 끝나면 조사를 붙이지 않는다."""
    return "" if b and b[-1] in "지나면음" else _josa(b, pair)


# 문장 중간 주어 'X가/이 (부사) 동사' 패턴에서 역할(X)을 잡는다(앞 주어가 없을 때 보조).
_MID_SUBJECT_RE = re.compile(
    r"([가-힣]{2,7}(?:\s[가-힣]{1,7})?)(?:가|이)\s+(?:(?:" + "|".join(_ADVERBS) + r")\s*)?"
    r"(?:확인|조회|보|찾|추천|입력|기록|정리|관리|계산|비교|쓰|사용)"
)


def _mid_subject(text: str) -> str:
    m = _MID_SUBJECT_RE.search(text or "")
    if m:
        cand = m.group(1).strip()
        if 2 <= len(cand) <= 14:
            return cand
    return ""


def _behavior_sentence(text: str, context: str = "", target: str = "") -> str:
    """핵심 행동을 자연스러운 한 문장으로 합성(토막/부사 단독 금지). 룰 기반·결정적."""
    t = _strip_subject(text, target)
    # 알림/리마인더 → '... 전에 알림을 받는다'
    if re.search(r"알림|알람|리마인|푸시|알려", t):
        m = re.search(r"([가-힣 ]{2,20}?)\s*(?:까먹지\s*않게|까먹지\s*않도록|잊지\s*않게|잊지\s*않도록|놓치지\s*않게|전에)", t)
        thing = _strip_subject(m.group(1)) if m else ""
        return f"{thing or '필요한 순간'} 전에 알림을 받는다"
    # 뜨다/팝업/표시 → '{대상}가 뜨거나 바로 확인된다'
    m = re.search(r"([가-힣]{2,12})\s*(?:가|이|을|를)?\s*(?:뜨는|뜨게|뜬다|팝업|띄우)", t)
    if m or "팝업" in t:
        obj = m.group(1) if m else "필요한 정보"
        return f"{obj}{_josa(obj)} 뜨거나 바로 확인된다"
    # 추천 → '남은 재료로 메뉴를 추천받는다' / '{X}를 추천받는다'
    if "추천" in t:
        if "재료" in (t + " " + context):
            return "남은 재료로 메뉴를 추천받는다"
        m = re.search(r"([가-힣]{2,10})\s*추천", t)
        x = m.group(1) if m else "결과"
        return f"{x}{_josa(x, ('을', '를'))} 추천받는다"
    # 기록 → '{활동(후/직후 포함)} 기록을 남긴다'
    if "기록" in t:
        m = re.search(r"([가-힣]{2,10}(?:\s*(?:후|직후|뒤))?)\s*기록", t)
        act = m.group(1).strip() if m else (context or "활동")
        return f"{act} 기록을 남긴다"
    # 헷갈림 → '{object}를 바로 확인한다'
    b = _problem_object(t, r"헷갈리|헛갈리")
    if b:
        return f"{b}{_noun_particle(b)} 바로 확인한다"
    # 일반 동사(확인/찾기/보기/정리 등) → object + (속도부사) + 동사. object가 깨끗할 때만.
    obj, verb, adverb = _object_before(t)
    if obj:
        obj = re.sub(r"^(자신의|내|나의|본인의|제)\s*", "", obj).strip()  # 소유격은 행동에서 생략
        speed = (_SPEED_NORM.get(adverb, "") + " ") if adverb else ""
        return f"{obj}{_josa(obj, ('을', '를'))} {speed}{_BEH_VERB.get(verb, '확인한다')}"
    return ""  # 확신 낮음 → 호출부가 안전 fallback 사용


# --------------------------------------------------------------------------- #
# 문진 보조: 시간/대상 수/대체 방법 파싱 + 1회 문진 UX 빌더
# --------------------------------------------------------------------------- #
_TIME_LABEL = {
    "30_MIN": "오늘 30분", "TODAY": "오늘 안에", "TWO_DAYS": "오늘 또는 내일",
    "ONE_WEEK": "이번 주", "TWO_WEEKS_PLUS": "2주 이상", "UNKNOWN": "오늘 또는 내일",
}


def _parse_time_budget(text: str) -> str:
    t = text or ""
    if not t:
        return "UNKNOWN"
    if "30분" in t or "30 분" in t or "반시간" in t:
        return "30_MIN"
    if "주말" in t:
        return "TWO_DAYS"
    if any(k in t for k in ("2주", "이 주", "한 달", "이주", "한달")):
        return "TWO_WEEKS_PLUS"
    if any(k in t for k in ("1주", "한 주", "일주일", "1 주", "이번 주", "한주")):
        return "ONE_WEEK"
    if "오늘" in t and "내일" in t:
        return "TWO_DAYS"
    if "내일" in t:
        return "TWO_DAYS"
    if "오늘" in t:
        return "TODAY"
    return "UNKNOWN"


def _parse_testers(text: str) -> str:
    """답변에서 '바로 물어볼 수 있는 사람' 구절 보존(없으면 빈 값)."""
    t = (text or "").strip()
    if not t:
        return ""
    for seg in re.split(r"[\n,/]", t):
        s = seg.strip()
        if any(k in s for k in ("없어", "없음", "없다", "아무도", "혼자")):
            return "없음"
        if "명" in s:
            return s.strip(" .")[:24]
    if any(k in t for k in ("여러", "많", "이상")):
        return "2명 이상"
    return ""


def _tester_category(reachable: str) -> str:
    r = reachable or ""
    if "없" in r or "혼자" in r or "아무도" in r:
        return "NONE"
    if "이상" in r or "여러" in r or "많" in r:
        return "MANY"
    m = re.search(r"(\d+)\s*명", r)
    if m:
        return "ONE" if int(m.group(1)) <= 1 else "MANY"
    return ""


_ALT_KEYS = ("메모장", "메모", "카톡", "엑셀", "노트", "다이어리", "수첩", "종이", "기억", "캘린더", "구글", "시트", "장부")


def _parse_alternative(text: str) -> str:
    for seg in re.split(r"[\n,]", (text or "").strip()):
        s = seg.strip()
        if any(k in s for k in _ALT_KEYS):
            return re.sub(r"^((?:지금은|지금|현재|그냥|보통|주로|아직)\s*)+", "", s).strip(" .")[:30]
    return ""


# 라벨형 답변('대상: …') 파싱
_LABEL_MAP = (
    ("원하는 행동", "behavior"), ("검증 가능 시간", "time"), ("검증 시간", "time"),
    ("바로 물어볼 수 있는 사람", "testers"), ("지금 쓰는 대체 방법", "alt"),
    ("대상", "target"), ("사용자", "target"), ("상황", "context"), ("순간", "context"),
    ("불편", "problem"), ("문제", "problem"), ("행동", "behavior"),
    ("시간", "time"), ("실험", "testers"), ("사람", "testers"), ("대체", "alt"),
)


def _parse_labeled_answer(answer: str) -> dict:
    out: dict = {}
    for line in re.split(r"[\n]", answer or ""):
        m = re.match(r"\s*([^:：]{1,18})\s*[:：]\s*(.+)", line)
        if not m:
            continue
        key, val = m.group(1).strip(), m.group(2).strip()
        for lab, f in _LABEL_MAP:
            if lab in key and val and f not in out:
                out[f] = val
                break
    return out


_FIELD_KO = {"target": "대상", "context": "상황", "problem": "불편한 점", "behavior": "원하는 행동",
             "time": "검증 가능 시간", "testers": "바로 물어볼 수 있는 사람", "alt": "지금 쓰는 대체 방법"}
_FIELD_EG = {
    "target": "예) 배달 라이더, 직장인, 자취생", "context": "예) 콜 받을 때, 회의 끝나고",
    "problem": "예) 자꾸 까먹는다, 매번 헷갈린다", "behavior": "예) 바로 확인한다, 체크리스트로 정리한다",
    "time": "예) 오늘 30분 / 오늘 또는 내일 / 이번 주말 / 1주일",
    "testers": "예) 없음 / 1명 / 2명 이상", "alt": "예) 메모장, 카톡, 엑셀, 기억에 의존",
}


def _build_understood(target: str, context: str, behavior: str, summary: str) -> str:
    who = target or "사용자"
    phrase = behavior + ("는" if behavior.endswith("다") else "") if behavior else ""
    if context and phrase:
        return f"제가 이해한 바로는, {who}{_josa(who)} {context} 상황에서 {phrase} 아이디어예요."
    if phrase:
        return f"제가 이해한 바로는, {who}{_josa(who)} {phrase} 아이디어예요."
    return f"제가 이해한 바로는, '{summary}' 아이디어예요."


def _build_extracted_summary(target: str, context: str, problem: str, behavior: str) -> str:
    lines = ["현재 이렇게 이해했어요."]
    if target:
        lines.append(f"- 대상: {target}")
    if context:
        lines.append(f"- 상황: {context}")
    if problem:
        lines.append(f"- 불편한 점: {problem}")
    if behavior:
        lines.append(f"- 원하는 행동: {behavior}")
    return "\n".join(lines)


def _build_oneshot(ask: list[str]) -> str:
    body = "\n".join(f"{_FIELD_KO[k]}: {_FIELD_EG[k]}" for k in ask)
    return "맞다면 아래 정보만 추가로 알려주세요. 일부는 비워도 기본값으로 진행할게요.\n\n" + body


def _build_format_hint(ask: list[str]) -> str:
    body = "\n".join(f"{_FIELD_KO[k]}:" for k in ask)
    return "아래 형식으로 답해주시면 더 정확하게 건강검진할 수 있어요.\n\n" + body


def _question_for(field: str, target: str) -> str:
    who = target or "사람"
    qmap = {
        "target": "이 아이디어를 가장 먼저 쓸 사람은 누구인가요? (예: 배달 라이더, 직장인)",
        "context": "그 사람이 이게 필요해지는 순간은 언제인가요? (예: 콜 받을 때)",
        "problem": "그때 가장 불편하거나 반복되는 문제는 무엇인가요?",
        "behavior": "사용자가 직접 하길 기대하는 행동은 무엇인가요? (예: 바로 확인, 정리)",
        "time": "검증에 쓸 수 있는 시간은 어느 정도인가요? (예: 오늘 30분, 오늘 또는 내일, 이번 주말)",
        "testers": f"바로 물어볼 수 있는 {who}{_josa(who)} 몇 명 있나요? (예: 없음, 1명, 2명 이상)",
        "alt": "지금은 이 일을 어떻게 하고 있나요? (예: 메모장, 카톡, 엑셀, 기억에 의존)",
    }
    return qmap[field]


# --------------------------------------------------------------------------- #
# 1) prepare_intake — 룰 기반 구조화
# --------------------------------------------------------------------------- #
def prepare_intake(
    idea_text: str, time_budget: str = "UNKNOWN", clarification_answer: str | None = None
) -> IntakeData:
    raw = (idea_text or "").strip()
    text = _strip_intent_tail(raw)  # '만들고싶어 먼저 확인해줘' 등 의도 꼬리 제거
    answer = _strip_intent_tail((clarification_answer or "").strip())
    has_clarified = bool(answer)
    combined = (text + " " + answer).strip()
    summary = (text[:140] + "…") if len(text) > 140 else (text or "(입력 없음)")
    service_type = _guess_service_type(text)
    labeled = _parse_labeled_answer(answer)  # '대상: …' 형식이면 우선 사용

    # --- 확신 있는 필드만 채운다(억지 생성 금지) ---
    # target: 본문 주어 → 역할 사전 → 문장 중간 주어 → 답변(라벨/힌트)
    target_user = (_subject_target(text) or _guess_target_user(text) or _mid_subject(text)
                   or labeled.get("target") or _target_from_answer(answer))
    if labeled.get("target"):  # 라벨 정정이 있으면 우선
        target_user = labeled["target"]
    target_user = _trim_modifiers(target_user)  # '자꾸 까먹는 직장인' → '직장인'
    target_conf = bool(target_user)

    constraints: list[str] = []
    for c in _constraints_from_text(combined):
        if c not in constraints:
            constraints.append(c)

    context = (labeled.get("context")
               or (_extract_context(answer, target_user) if answer else "")
               or _extract_context(text, target_user))

    behavior = (labeled.get("behavior")
                or _behavior_sentence(text, context, target_user)
                or _behavior_sentence(combined, context, target_user))
    behavior_conf = bool(behavior)
    if not behavior:
        behavior = _FALLBACK_BEHAVIOR

    problem = (labeled.get("problem")
               or _problem_sentence(text, context, target_user)
               or _problem_sentence(combined, context, target_user))
    problem_conf = bool(problem)
    if not problem:
        problem = _FALLBACK_PROBLEM

    # 시간/대상 수/대체 방법
    tb = _coerce_budget(time_budget)
    if tb == "UNKNOWN":
        tb = _parse_time_budget(labeled.get("time") or answer)
    reachable = labeled.get("testers") or _parse_testers(answer)
    alternative = labeled.get("alt") or _parse_alternative(answer)

    # --- 부족 필드 판정 + 1회 문진 제한 ---
    missing = []
    if not target_conf:
        missing.append("target")
    if not context:
        missing.append("context")
    if not problem_conf:
        missing.append("problem")
    if not behavior_conf:
        missing.append("behavior")
    if tb == "UNKNOWN":
        missing.append("time")
    if not reachable:
        missing.append("testers")
    # clarification은 최대 1회: 답변을 이미 받았으면 부족해도 false로 닫는다.
    needs = bool(missing) and not has_clarified

    # 2차(또는 진행): 기본값 fallback — 이상한 문장 대신 안전값
    if not needs:
        if tb == "UNKNOWN":
            tb = "TWO_DAYS"  # 기본 '오늘 또는 내일'
        if not reachable:
            reachable = "1명 또는 2명"
        if not alternative:
            alternative = "현재 쓰는 방식은 직접 확인 필요"

    # 1회 문진 UX 문구 (되묻을 때만)
    understood = extracted = oneshot = fmt = ""
    questions: list[str] = []
    if needs:
        core = [k for k in ("target", "context", "problem", "behavior") if k in missing]
        ask = core + ["time", "testers", "alt"]  # 한 번에: 부족한 핵심 + 시간/대상/대체
        understood = _build_understood(target_user, context, behavior if behavior_conf else "", summary)
        extracted = _build_extracted_summary(
            target_user, context, problem if problem_conf else "", behavior if behavior_conf else "")
        oneshot = _build_oneshot(ask)
        fmt = _build_format_hint(ask)
        questions = [_question_for(k, target_user) for k in ask]

    gaps = []
    if not target_conf:
        gaps.append("대상 사용자는 가장 흔한 사용자층으로 가정한다")
    if not context:
        gaps.append("사용 순간은 반복적으로 생기는 일상 상황으로 가정한다")
    assume_if_continue = (gaps + _default_assumptions(service_type, target_user))[:3]

    return IntakeData(
        input_summary=summary,
        service_type=service_type,  # type: ignore[arg-type]
        problem=problem,
        target_user=target_user,
        context_of_use=context,
        desired_behavior=behavior,
        pain_source=_guess_pain_source(combined),  # type: ignore[arg-type]
        maturity=_guess_maturity(combined),  # type: ignore[arg-type]
        validation_time_budget=tb,  # type: ignore[arg-type]
        reachable_testers=reachable,
        current_alternative=alternative,
        has_clarified=has_clarified,
        understood_summary=understood,
        extracted_fields_summary=extracted,
        one_shot_clarification_prompt=oneshot,
        answer_format_hint=fmt,
        needs_clarification=needs,
        clarifying_question=(questions[0] if questions else None),
        clarification_questions=questions,
        can_continue_with_assumptions=True,
        assumptions_if_continue=assume_if_continue,
        assumptions=[],
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
    user = intake.target_user or "사용자"
    # 아이디어 유형 신호로 균열점 포커스 선택 — 같은 카톡 질문이라도 확인 대상이 달라진다.
    focus = _select_focus(intake)
    prof = _FOCUS.get(focus, _FOCUS[_DEFAULT_FOCUS])
    crack = prof["crack"].format(u=user, j=_josa(user))
    return Diagnosis(
        problem_statement=intake.problem or "",
        target_user_assumption=f"'{user}'{_josa(user)} 이 방식을 실제로 쓸 것이다",
        context_of_use=intake.context_of_use,
        crack_point=crack,
        misread_risks=[
            "'좋아 보인다'(관심)와 '실제로 한다'(행동)를 혼동",
            "한두 명의 호의적 반응을 전체 수요로 일반화",
        ],
        positive_signals=["검증할 대상·행동이 구체적임", "48시간 내 직접 확인 가능한 범위"],
        diagnosis_focus=focus,  # type: ignore[arg-type]
    )


# WILLINGNESS 안에서도 '기록·입력형' 행동이면 더 구체적인 기록 미션을 쓴다(다른 focus는 불변).
_RECORD_HINTS = ("기록", "입력", "남긴다", "남기", "일지", "로그", "적는", "적어", "체크")


def _is_record_behavior(text: str) -> bool:
    return any(k in text for k in _RECORD_HINTS)


def _record_mission(intake: IntakeData, b: dict, subject: str) -> tuple[list[str], str, list[str]]:
    """기록형 WILLINGNESS 전용 미션. 활동·순간·기록 항목을 맥락에서 구체화."""
    blob = " ".join((intake.desired_behavior, intake.context_of_use, intake.input_summary))
    m = re.search(r"([가-힣]{2,8})\s*(?:후|직후|뒤)?\s*기록", blob) or re.search(r"([가-힣]{2,8})\s*(?:후|직후|뒤)", blob)
    activity = m.group(1) if m else ""
    moment = intake.context_of_use.strip()
    if not re.search(r"(직후|후|끝나고|뒤|때)$", moment):
        moment = f"{activity or '그 일'} 직후"
    # 활동별 기록 항목(운동/식사/지출은 구체, 그 외 일반)
    if "운동" in blob:
        activity, items = "운동", "운동명·무게·횟수"
        item_ex = "운동명, 무게, 횟수 중 가능한 것만 적는다"
        if not re.search(r"(직후|후|끝나고|뒤)", moment):
            moment = "운동 직후"
    elif any(k in blob for k in ("식단", "식사", "먹은", "음식")):
        activity, items = (activity or "식사"), "메뉴·양·시간"
        item_ex = "메뉴, 양, 시간 중 가능한 것만 적는다"
    elif any(k in blob for k in ("지출", "가계", "소비")):
        activity, items = (activity or "지출"), "금액·항목·시간"
        item_ex = "금액, 항목, 시간 중 가능한 것만 적는다"
    else:
        activity, items = (activity or "활동"), ""
        item_ex = "핵심 항목 2가지에서 3가지 중 가능한 것만 적는다"
    steps = [
        f"{moment} 1분 안에 오늘 한 {activity} 핵심을 메모장이나 카톡 '나에게 보내기'로 짧게 기록해본다",
        f"기록 항목 예: {item_ex}",
        "끝나고 다음에도 같은 방식으로 남길 수 있을지 한 줄로 확인받기",
    ]
    quota = f"{items} 중 2가지 이상" if items else "핵심 항목 2가지 이상"
    success = (
        f"{b['hours']} 안에 {subject} {moment} 5분 안에 {quota}{_josa(quota, ('을', '를'))} 기록하고, "
        f"다음 {activity} 때도 같은 방식으로 남길 수 있겠다고 말하면 통과"
    )
    failure = [
        f"{activity}{_josa(activity)} 끝난 뒤 기록을 미루거나 잊어버리면 보류",
        "기록할 항목이 많아 귀찮다고 느끼면 보류",
        f"다음 {activity} 때 다시 할 생각이 없다고 하면 보류",
    ]
    return steps, success, failure


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
    # 바로 물어볼 수 있는 사람 수에 맞춰 미션 규모 조정(없으면 budget 기본 스케일 유지).
    tcat = _tester_category(intake.reachable_testers)
    if tcat == "NONE":
        method = "오늘 직접 1회 써보거나, 관련 커뮤니티·오픈채팅에 짧게 의견 물어보기"
        subject = "본인 경험 또는 온라인 반응 1건 이상에서"
    elif tcat == "ONE":
        method = f"바로 물어볼 수 있는 {actor} 1명에게 카톡으로 질문하거나 직접 1회 실험"
        subject = f"그 {actor} 1명이"
    elif tcat == "MANY":
        method = f"바로 물어볼 수 있는 {actor} 2명에서 3명에게 카톡으로 질문"
        subject = f"{actor} 2명에서 3명 중 1명 이상이"
    # WILLINGNESS + 기록·입력형 행동이면 구체적인 '기록 미션'을 쓴다(다른 focus는 기존 그대로).
    record = focus == "WILLINGNESS" and _is_record_behavior(
        intake.desired_behavior + " " + intake.input_summary
    )
    if record:
        steps, success, failure = _record_mission(intake, b, subject)
    else:
        # step1(접촉)·기간은 시간 예산이, 무엇을 확인하는지(act/success/failure)는 포커스가 정한다.
        steps = [
            method,
            f"{b['hours']} 동안 {f['act']} 기록",
            "끝나고 한 줄 피드백(긍정/부정 표현) 받기",
        ]
        success = f"{b['hours']} 안에 {subject} {f['success']}"
        failure = list(f["failure"])

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
        failure_signals=failure[:3] if record else failure[:2],
        do_not_build_yet=dnb[:3],
        next_step_if_passed="통과해도 바로 개발하지 말고, 더 작은 다음 미션 또는 화면 없는 수동/노코드 프로토타입으로",
    )
