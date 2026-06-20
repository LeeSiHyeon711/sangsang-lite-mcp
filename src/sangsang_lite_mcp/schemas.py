"""상상공방 Lite 도구 입출력 모델 (pydantic).

FastMCP는 이 타입에서 tools/list의 inputSchema / outputSchema를 자동 생성한다.
기획 근거: docs/03(소통)·05(진단)·06(첫실험).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

FallbackReason = Literal["missing_api_key", "disabled", "timeout", "api_error"]


class ToolMeta(BaseModel):
    """결과 출처 메타(optional). LLM/stub 중 무엇이 응답했는지, fallback이면 사유."""

    source: Literal["llm", "stub"] = "stub"
    fallback_reason: Optional[FallbackReason] = None

# --- enum 류 (Literal로 두면 schema에 enum으로 노출) ---
ServiceType = Literal["웹", "앱", "자동화 도구", "업무 개선 도구", "기타"]
PainSource = Literal["SELF", "OBSERVED", "ASSUMED", "IMAGINED"]
Maturity = Literal["RAW", "SITUATION", "PROBLEM", "SOLUTION"]
TimeBudget = Literal["30_MIN", "TODAY", "TWO_DAYS", "ONE_WEEK", "TWO_WEEKS_PLUS", "UNKNOWN"]
DiagnosisFocus = Literal[
    "PROBLEM_EXISTENCE",
    "PAIN_INTENSITY",
    "SOLUTION_FIT",
    "WILLINGNESS",
    "FEASIBILITY",
    "CONTEXT_OF_USE",
    "OPERATION_FIT",
    "PROBLEM_CAUSE_FIT",
]


class IntakeData(BaseModel):
    """소통 에이전트 접수 결과 (docs/03)."""

    input_summary: str = Field(default="", description="아이디어를 한두 문장으로 요약")
    # 방어용: 카카오 AI가 정규화 전 {idea_text, time_budget}를 intake로 넘긴 경우 수용 (diagnose/design이 재정규화)
    idea_text: Optional[str] = Field(default=None, description="(방어) 정규화 전 원문이 잘못 전달된 경우")
    time_budget: Optional[str] = Field(default=None, description="(방어) 정규화 전 time_budget이 전달된 경우")
    service_type: ServiceType = Field(default="기타", description="결과물 형태 분류")
    problem: str = Field(default="", description="해결하려는 문제")
    target_user: str = Field(default="", description="처음 쓸 사람(clarification_answer의 구절 보존)")
    context_of_use: str = Field(default="", description="사용 순간/상황(있으면 보존, 없으면 질문)")
    desired_behavior: str = Field(default="", description="사용자가 하길 기대하는 핵심 행동(있으면 보존, 없으면 질문)")
    pain_source: PainSource = Field(default="IMAGINED", description="불편함의 출처")
    maturity: Maturity = Field(default="RAW", description="아이디어 성숙도")
    validation_time_budget: TimeBudget = Field(default="UNKNOWN", description="검증 투자 가능 시간")
    needs_clarification: bool = Field(default=False, description="target_user/context_of_use/desired_behavior 중 빈 값 있으면 true")
    clarifying_question: Optional[str] = Field(default=None, description="(레거시) 단일 질문. clarification_questions 사용 권장")
    clarification_questions: list[str] = Field(default_factory=list, description="부족한 필드에 대한 질문 최대 2개(이미 말한 건 묻지 않음)")
    can_continue_with_assumptions: bool = Field(default=True, description="사용자가 답 안 해도 가정 기반으로 다음 tool 진행 가능한지")
    assumptions_if_continue: list[str] = Field(default_factory=list, description="질문에 답 없이 진행할 때 사용할 가정")
    assumptions: list[str] = Field(default_factory=list, description="사용자가 확정한 가정(없으면 빈 값)")
    constraints: list[str] = Field(default_factory=list, description="명시된 제약(MVP 범위/제외·입력 주체 확정 등). 진단·실험이 반드시 따른다")
    meta: Optional[ToolMeta] = Field(default=None, description="결과 출처 메타(룰 기반은 비움)")


class Diagnosis(BaseModel):
    """아이디어 진단 결과 — 균열점 1개 (docs/05)."""

    problem_statement: str
    target_user_assumption: str
    context_of_use: str
    crack_point: str = Field(description="가장 먼저 깨질 전제 1개")
    misread_risks: list[str] = Field(default_factory=list, description="착각 가능성 (최대 2개)")
    positive_signals: list[str] = Field(default_factory=list, description="좋은 신호 (최대 2개)")
    diagnosis_focus: DiagnosisFocus
    meta: Optional[ToolMeta] = Field(default=None, description="결과 출처(llm/stub) 메타")


class ValidationReadiness(BaseModel):
    """검증 준비도 점수(100) — '아이디어가 검증 가능한 형태로 얼마나 정리됐는가'.

    ※ 사업성/성공 가능성 점수가 아니다. 사용자 평가가 아니라 '정리도' 피드백.
    """

    total: int = Field(description="검증 준비도 점수(0~100)")
    target_user_clarity: int = Field(description="대상 사용자 명확도 /25")
    problem_intensity: int = Field(description="문제·불편 강도 /25")
    context_specificity: int = Field(description="사용 상황 구체성 /25")
    verifiable_in_48h: int = Field(description="48시간 검증 가능성 /25")
    one_line: str = Field(description="한 줄 평(단정 금지, '아직 확인 필요'를 균열점과 연동)")


class FirstExperiment(BaseModel):
    """첫 검증 미션 (docs/06)."""

    time_budget: str = Field(description="사용자가 선택한 시간(표시용)")
    readiness: Optional[ValidationReadiness] = Field(default=None, description="검증 준비도 점수(결과카드용)")
    mission_title: str
    mission_steps: list[str]
    why_this_experiment: str = Field(default="", description="이 미션이 어떤 전제를 싸게 확인하는지")
    success_criteria: list[str]
    failure_signals: list[str]
    do_not_build_yet: list[str] = Field(description="지금 만들지 않아도 되는 것")
    next_step_if_passed: str
    meta: Optional[ToolMeta] = Field(default=None, description="결과 출처(llm/stub) 메타")
