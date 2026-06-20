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
    # 망각/리마인더: 'B 까먹지/잊지 않게' → 'B를 잊는 문제'
    m = re.search(r"([가-힣 ]{2,20}?)\s*(?:까먹지\s*않게|까먹지\s*않도록|잊지\s*않게|잊지\s*않도록|놓치지\s*않게)", t)
    if m:
        b = _strip_subject(m.group(1))
        if b:
            return f"{b}{_josa(b, ('을', '를'))} 잊는 문제"
    # 낭비: 'B 버리기 전에/버리게' → 'B를 버리게 되는 문제'
    m = re.search(r"([가-힣 ]{2,20}?)\s*버리(?:기|게|는)", t)
    if m:
        b = _strip_subject(m.group(1))
        if b:
            return f"{b}{_josa(b, ('을', '를'))} 버리게 되는 문제"
    # 기록 누락: '... 기록 남기는' → '... 기록을 남기지 못하거나 잊는 문제'
    if "기록" in t:
        m = re.search(r"([가-힣]{2,10})\s*(?:후|직후|뒤)?\s*기록", t)
        if m:
            return f"{m.group(1)} 기록을 남기지 못하거나 잊는 문제"
    # 일반 동사형: object + (즉시성) + '확인/찾기 어려운 문제'
    obj, verb, adverb = _object_before(t)
    if obj:
        pv = "찾기" if verb == "찾" else "확인하기"
        speed = "바로 " if (adverb or verb in ("표시", "뜨는", "뜨게", "팝업")) else ""
        return f"{obj}{_josa(obj, ('을', '를'))} {speed}{pv} 어려운 문제"
    return _extract_problem(t)


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


def _object_before(action_text: str) -> tuple[str, str, str]:
    """동사 앞의 목적어 명사구를 추출. 맥락절·부사·조사를 벗겨내 (object, verb, adverb) 반환."""
    s = re.sub(r"\s*(?:" + _SERVICE_NOUN + r")\s*$", "", (action_text or "").strip()).strip()
    m = _VERB_RE.search(s)
    if not m:
        return "", "", ""
    verb = m.group(1)
    before = re.split(_CTX_TAIL, s[:m.start()])[-1].strip()  # 맥락절 뒤 부분만
    adverb = ""
    am = re.search(r"(" + "|".join(_ADVERBS) + r")\s*$", before)
    if am:
        adverb = am.group(1)
        before = before[:am.start()].strip()
    obj = re.sub(r"\s*(?:을|를)\s*$", "", before).strip()  # 목적격 조사 제거
    return obj, verb, adverb


# 행동 동사(서비스 명사 아님)
_SERVICE_NOUN = r"(?:앱|어플|애플리케이션|도구|서비스|웹|사이트|시스템|플랫폼|프로그램)"


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
    # 일반 동사(확인/찾기/보기/정리 등) → object + (속도부사) + 동사
    obj, verb, adverb = _object_before(t)
    if obj:
        obj = re.sub(r"^(자신의|내|나의|본인의|제)\s*", "", obj).strip()  # 소유격은 행동에서 생략
        speed = (_SPEED_NORM.get(adverb, "") + " ") if adverb else ""
        return f"{obj}{_josa(obj, ('을', '를'))} {speed}{_BEH_VERB.get(verb, '확인한다')}"
    return ""


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
    raw = (idea_text or "").strip()
    text = _strip_intent_tail(raw)  # '만들고싶어 먼저 확인해줘' 등 의도 꼬리 제거
    answer = _strip_intent_tail((clarification_answer or "").strip())
    combined = (text + " " + answer).strip()
    summary = (text[:140] + "…") if len(text) > 140 else (text or "(입력 없음)")
    service_type = _guess_service_type(text)
    # target: 본문의 일반 주어(A가/이) 우선 → 역할 사전 → 답변 힌트(문장 통째로 넣지 않음).
    #         본문에서 잡힌 target은 answer가 덮어쓰지 않는다(req4: 더 구체적인 기존 값 보존).
    target_user = _subject_target(text) or _guess_target_user(text) or _target_from_answer(answer)
    # 제약: 키워드 기반만(답변을 통째로 쪼개지 않음 — req5 방어). 중복 제거·순서 보존
    constraints: list[str] = []
    for c in _constraints_from_text(combined):
        if c not in constraints:
            constraints.append(c)
    # context: 답변의 절을 우선(있으면) → 없으면 본문. 주어만 떼고 원문 구절 보존(req4)
    context = (_extract_context(answer, target_user) if answer else "") or _extract_context(text, target_user)
    # behavior: 본문에서 자연스러운 행동 문장 합성 → 없으면 combined로 보강
    behavior = _behavior_sentence(text, context, target_user) or _behavior_sentence(combined, context, target_user)
    # problem: 패턴 기반 한 문장 합성(빈 값/원문통째/부사목적어 방지)
    problem = _problem_sentence(text, context, target_user) or _problem_sentence(combined, context, target_user)

    # 필수 확인 3필드: target_user / context_of_use / desired_behavior
    missing = []
    if not target_user:
        missing.append("target_user")
    if not context:
        missing.append("context_of_use")
    if not behavior:
        missing.append("desired_behavior")
    # req3: 사용자가 이미 답(clarification_answer)을 줬다면 같은 라운드를 반복하지 않는다.
    #       남은 빈 필드는 assumptions_if_continue로 메우고 다음 단계로 진행.
    needs = bool(missing) and not answer
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
        problem=problem,
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
