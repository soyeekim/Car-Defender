"""Case State — 멀티턴 Agent가 소유하는 사건의 구조화된 현재 상태 (가이드 5.2 / 6 / 7절).

- 모든 사실(Fact)에는 출처(video / user / rag / model_inference)를 붙인다.
- 서로 충돌하는 정보는 자동으로 하나를 고르지 않고 `conflicts`에 기록한다.
- 대화 전체를 매번 LLM에 넣지 않고 `compact()`로 요약된 상태를 전달한다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from video.schemas import VideoResult

FactSource = Literal["video", "user", "rag", "model_inference", "unknown"]
FactStatus = Literal["CONFIRMED", "INFERRED", "UNKNOWN"]
Verification = Literal[
    "visible_in_video",
    "not_visible_in_video",
    "contradicts_video",
    "consistent_with_video",
    "unverified",
]
Stage = Literal[
    "INITIAL",
    "VIDEO_ANALYZING",
    "FACT_COLLECTING",
    "READY_FOR_RAG",
    "RAG_SEARCHING",
    "CASE_REVIEW",
    "READY_FOR_ASSESSMENT",
    "ASSESSMENT_COMPLETE",
    "REPORT_COMPLETE",
    "REBUTTAL_COMPLETE",
]
VideoStatus = Literal[
    "VIDEO_NOT_ANALYZED",
    "VIDEO_ANALYZING",
    "VIDEO_NEEDS_RECHECK",
    "VIDEO_ANALYSIS_COMPLETE",
    "VIDEO_UNAVAILABLE",
]

STAGE_ORDER: list[str] = [
    "INITIAL",
    "VIDEO_ANALYZING",
    "FACT_COLLECTING",
    "READY_FOR_RAG",
    "RAG_SEARCHING",
    "CASE_REVIEW",
    "READY_FOR_ASSESSMENT",
    "ASSESSMENT_COMPLETE",
    "REPORT_COMPLETE",
    "REBUTTAL_COMPLETE",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class Slot(BaseModel):
    value: Optional[str] = None
    source: FactSource = "unknown"
    status: FactStatus = "UNKNOWN"
    confidence: Optional[float] = None
    note: Optional[str] = None

    def is_known(self) -> bool:
        return self.value is not None and str(self.value).strip() != "" and self.status != "UNKNOWN"

    def label(self) -> str:
        if not self.is_known():
            return "UNKNOWN"
        tag = {"video": "VIDEO", "user": "USER", "rag": "RAG", "model_inference": "INFERENCE"}.get(
            self.source, "UNKNOWN"
        )
        if self.status == "INFERRED":
            tag += "_INFERRED"
        elif self.source in {"video", "user"}:
            tag += "_CONFIRMED"
        return f"{self.value} [{tag}]"


class Fact(BaseModel):
    fact_id: str = Field(default_factory=lambda: _short_id("fact"))
    fact: str
    source: FactSource
    status: FactStatus = "CONFIRMED"
    confidence: Optional[float] = None
    field: Optional[str] = Field(default=None, description="관련 Case State 경로 (예: video_source.vehicle_owner)")
    value: Optional[str] = None
    timestamp: Optional[str] = None
    verification: Verification = "unverified"
    turn: Optional[int] = None
    created_at: str = Field(default_factory=_now)


class Conflict(BaseModel):
    conflict_id: str = Field(default_factory=lambda: _short_id("conflict"))
    field: Optional[str] = None
    description: str
    existing_value: Optional[str] = None
    existing_source: FactSource = "unknown"
    new_value: Optional[str] = None
    new_source: FactSource = "unknown"
    resolved: bool = False
    resolution: Optional[str] = None
    created_at: str = Field(default_factory=_now)


class VideoSource(BaseModel):
    type: Slot = Field(default_factory=Slot, description="dashcam | cctv | third_party | unknown")
    vehicle_owner: Slot = Field(default_factory=Slot, description="user | opponent | unknown")


class AccidentDatetime(BaseModel):
    date: Slot = Field(default_factory=Slot)
    time: Slot = Field(default_factory=Slot)


class RoadInfo(BaseModel):
    location_name: Slot = Field(default_factory=Slot)
    road_type: Slot = Field(default_factory=Slot)
    intersection_type: Slot = Field(default_factory=Slot)
    lane_count: Slot = Field(default_factory=Slot)
    signal_present: Slot = Field(default_factory=Slot)
    signal_state: Slot = Field(default_factory=Slot)
    lane_marking: Slot = Field(default_factory=Slot, description="차로 변경·접촉 지점 차선 종류 (solid | dashed | double | none)")
    road_surface: Slot = Field(default_factory=Slot)
    weather: Slot = Field(default_factory=Slot)


class VehicleInfo(BaseModel):
    vehicle_id: Optional[str] = Field(default=None, description="영상 분석의 vehicle ID")
    description: Optional[str] = None
    movement: Slot = Field(default_factory=Slot)
    entry_direction: Slot = Field(default_factory=Slot)
    lane: Slot = Field(default_factory=Slot)
    estimated_speed: Slot = Field(default_factory=Slot)
    signal: Slot = Field(default_factory=Slot)
    turn_signal: Slot = Field(default_factory=Slot)
    lane_change: Slot = Field(default_factory=Slot)
    braking: Slot = Field(default_factory=Slot)
    entered_first: Slot = Field(default_factory=Slot)


class CollisionInfo(BaseModel):
    type: Slot = Field(default_factory=Slot)
    ego_collision_part: Slot = Field(default_factory=Slot)
    other_collision_part: Slot = Field(default_factory=Slot)
    relative_direction: Slot = Field(default_factory=Slot)
    timestamp: Slot = Field(default_factory=Slot)
    participants_confirmed: Slot = Field(default_factory=Slot, description="충돌 당사 차량 확인 여부")


class TimelineItem(BaseModel):
    time: Optional[str] = None
    end_time: Optional[str] = None
    event: str
    source: FactSource = "video"
    status: FactStatus = "CONFIRMED"
    confidence: Optional[float] = None


class MissingInformation(BaseModel):
    field: str
    importance: Literal["critical", "high", "medium", "low"] = "high"
    reason: str = ""
    user_answerable: bool = False
    video_recheckable: bool = False


class Question(BaseModel):
    field: str
    question: str
    importance: Literal["critical", "high", "medium", "low"] = "high"
    asked_turn: Optional[int] = None
    ask_count: int = 1
    phase: Literal["fact", "case_review"] = "fact"
    why: Optional[str] = Field(default=None, description="이 사실이 확인되면 어떤 수정요소 적용 여부가 결정되는지")
    related_case_ids: list[str] = Field(default_factory=list)
    reasoning: list[str] = Field(default_factory=list, description="Agent가 이 질문을 고르기까지의 추론")


class Message(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str
    turn: int = 0
    action: Optional[str] = None
    created_at: str = Field(default_factory=_now)


class CaseMetadata(BaseModel):
    road_type: Optional[str] = None
    intersection_type: Optional[str] = None
    signal_present: Optional[bool] = None
    accident_target: Optional[str] = None
    movement_a: Optional[str] = None
    movement_b: Optional[str] = None
    lane_change: Optional[bool] = None
    keywords: list[str] = Field(default_factory=list)


class CaseRelevance(BaseModel):
    case_id: str
    relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    matched_factors: list[str] = Field(default_factory=list)
    different_factors: list[str] = Field(default_factory=list)
    usable_as_primary_reference: bool = False
    basic_ratio_applicable: bool = False
    adjustment_factor_notes: list[str] = Field(default_factory=list)
    note: str = ""


class RetrievedCase(BaseModel):
    case_id: str
    source_type: Literal["deliberation_case", "fault_standard", "roundabout_special_standard"] = "deliberation_case"
    title: str = ""
    accident_type: Optional[str] = None
    chart_number: Optional[str] = None
    decision_ratio: Optional[str] = None
    basic_ratio: Optional[str] = None
    accident_description: str = ""
    claimant_argument: str = ""
    respondent_argument: str = ""
    key_issues: list[str] = Field(default_factory=list)
    decision_reasons: list[str] = Field(default_factory=list)
    recognized_facts: list[str] = Field(default_factory=list)
    modification_factors: list[str] = Field(default_factory=list)
    source_file: Optional[str] = None
    source_pages: list[int] = Field(default_factory=list)
    similarity: float = 0.0
    semantic_score: Optional[float] = None
    lexical_score: Optional[float] = None
    excerpt: str = ""
    metadata: CaseMetadata = Field(default_factory=CaseMetadata)
    relevance: Optional[CaseRelevance] = None

    def ratio_summary(self) -> str:
        parts = []
        if self.basic_ratio:
            parts.append(f"기본 {self.basic_ratio}")
        if self.decision_ratio:
            parts.append(f"결정 {self.decision_ratio}")
        return ", ".join(parts) or "비율 정보 없음"


class RagQuery(BaseModel):
    structured_query: str = ""
    detailed_query: str = ""
    key_factors: list[str] = Field(default_factory=list)
    filters: CaseMetadata = Field(default_factory=CaseMetadata)
    generation_method: Literal["llm", "deterministic"] = "deterministic"


class RagResult(BaseModel):
    query: RagQuery = Field(default_factory=RagQuery)
    candidates: list[RetrievedCase] = Field(default_factory=list)
    cases: list[RetrievedCase] = Field(default_factory=list)
    embedding_model: str = ""
    tier: Literal["deliberation_case", "fault_standard", "none"] = "deliberation_case"
    fallback_reason: Optional[str] = None
    created_at: str = Field(default_factory=_now)


class FaultRatio(BaseModel):
    user: int = Field(ge=0, le=100)
    opponent: int = Field(ge=0, le=100)

    def as_text(self) -> str:
        return f"{self.user}:{self.opponent}"


class AdjustmentFactor(BaseModel):
    factor: str
    direction: Literal["user_up", "user_down", "neutral", "unknown"] = "unknown"
    percentage: Optional[int] = None
    source: FactSource = "rag"
    applies: bool = True
    note: str = ""


class MatchedCaseSummary(BaseModel):
    case_id: str
    decision_ratio: Optional[str] = None
    basic_ratio: Optional[str] = None
    relevance: Optional[float] = None
    role: Literal["primary", "supporting"] = "supporting"


class FaultAssessment(BaseModel):
    fault_ratio: FaultRatio
    more_at_fault: Optional[Literal["user", "opponent", "equal"]] = Field(default=None, description="모델이 숫자보다 먼저 정한 책임 방향 (숫자와의 일관성 검사용)")
    assessment_type: Literal["estimated", "provisional"] = "estimated"
    most_likely: str = ""
    possible_range: Optional[list[str]] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    anchor_case_id: Optional[str] = Field(default=None, description="기준값으로 삼은 가장 유사한 심의사례")
    anchor_ratio: Optional[str] = Field(default=None, description="기준값 (user:opponent)")
    anchor_enforced: bool = Field(default=False, description="확인된 수정요소가 없어 코드가 기준값으로 되돌렸는지")
    primary_case_ids: list[str] = Field(default_factory=list)
    core_facts: list[str] = Field(default_factory=list)
    matched_cases: list[MatchedCaseSummary] = Field(default_factory=list)
    adjustment_factors: list[AdjustmentFactor] = Field(default_factory=list)
    reasoning_summary: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    ratio_dependencies: list[str] = Field(default_factory=list, description="어떤 사실이 확인되면 비율이 바뀌는지")
    explanation: str = ""
    model: str = ""
    prompt_version: str = ""
    created_at: str = Field(default_factory=_now)


class GroundingReport(BaseModel):
    removed_sentences: list[str] = Field(default_factory=list)
    flagged_claims: list[str] = Field(default_factory=list)
    invalid_case_citations: list[str] = Field(default_factory=list)


class DocumentResult(BaseModel):
    document_type: Literal["incident_report", "rebuttal_opinion"]
    title: str = ""
    sections: dict[str, str] = Field(default_factory=dict)
    text: str = ""
    caveat: str = Field(default="", description="사건경위서: 확인되지 않아 본문에 쓰지 않은 사실을 사용자에게 알리는 한 문장")
    mail_body: str = Field(default="", description="반박의견서: 보험사에 보내는 메일 본문(압축본)")
    cited_case_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    grounding: GroundingReport = Field(default_factory=GroundingReport)
    generation_method: Literal["llm", "deterministic_fallback"] = "llm"
    model: str = ""
    prompt_version: str = ""
    created_at: str = Field(default_factory=_now)


class CaseState(BaseModel):
    case_id: str = Field(default_factory=lambda: _short_id("case"))
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

    video_path: Optional[str] = None
    video_uploaded: bool = False
    video_analyzed: bool = False
    video_status: VideoStatus = "VIDEO_NOT_ANALYZED"
    initial_description: Optional[str] = None

    video_source: VideoSource = Field(default_factory=VideoSource)
    accident_datetime: AccidentDatetime = Field(default_factory=AccidentDatetime)
    road: RoadInfo = Field(default_factory=RoadInfo)
    ego_vehicle: VehicleInfo = Field(default_factory=VehicleInfo, description="사용자 차량")
    other_vehicle: VehicleInfo = Field(default_factory=VehicleInfo, description="상대 차량")
    collision: CollisionInfo = Field(default_factory=CollisionInfo)

    timeline: list[TimelineItem] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    uncertain_facts: list[str] = Field(default_factory=list)
    user_confirmed_facts: list[Fact] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    opponent_claim: Optional[str] = None

    video_analysis: Optional[VideoResult] = None
    video_reanalysis_count: int = 0
    critical_recheck_count: int = Field(default=0, description="충돌 차량 식별 등 critical 공백 때문에 수행한 재분석 횟수")
    recheck_focus_history: list[str] = Field(default_factory=list, description="Agent가 요청해 수행한 영상 focus 재분석 쟁점")
    missing_information: list[MissingInformation] = Field(default_factory=list)
    pending_questions: list[Question] = Field(default_factory=list)
    asked_fields: list[str] = Field(default_factory=list)
    asked_questions: list[str] = Field(default_factory=list, description="지금까지 실제로 던진 질문 문장 (표현만 바꾼 중복 방지)")

    rag_query: Optional[RagQuery] = None
    rag_tier: Optional[str] = Field(default=None, description="deliberation_case | fault_standard (심의사례에 없어 인정기준으로 fallback)")
    retrieved_cases: list[RetrievedCase] = Field(default_factory=list)
    # 심의사례 제시 후 판정 전에 차이점·수정요소를 확인하는 대화 단계
    case_review_started: bool = False
    case_review_done: bool = False
    review_rounds: int = Field(default=0, description="검토 단계에서 Agent가 고른 질문 횟수")
    fact_question_rounds: int = Field(default=0, description="심의사례 검색 전 Agent가 스스로 고른 질문 횟수")
    review_answers: dict[str, str] = Field(default_factory=dict, description="review.* 질문에 대한 사용자 답변")
    pending_intent: Optional[str] = Field(default=None, description="질문 답변 후 이어서 수행할 사용자 요청 (예: request_incident_report)")
    fault_assessment: Optional[FaultAssessment] = None
    assessment_invalidated: bool = False
    assessment_invalidation_reasons: list[str] = Field(default_factory=list)

    incident_report: Optional[DocumentResult] = None
    rebuttal_opinion: Optional[DocumentResult] = None

    current_stage: Stage = "INITIAL"
    conversation_history: list[Message] = Field(default_factory=list)
    turn_count: int = 0
    notes: list[str] = Field(default_factory=list)

    # ------------------------------------------------------------------ helpers
    def touch(self) -> None:
        self.updated_at = _now()

    def add_message(self, role: str, content: str, action: Optional[str] = None) -> Message:
        message = Message(role=role, content=content, turn=self.turn_count, action=action)
        self.conversation_history.append(message)
        self.touch()
        return message

    def recent_messages(self, window: int = 8) -> list[dict[str, Any]]:
        return [
            {"role": item.role, "content": item.content, "turn": item.turn}
            for item in self.conversation_history[-window:]
        ]

    def set_stage(self, stage: Stage) -> None:
        self.current_stage = stage
        self.touch()

    def stage_at_least(self, stage: Stage) -> bool:
        return STAGE_ORDER.index(self.current_stage) >= STAGE_ORDER.index(stage)

    def unresolved_conflicts(self) -> list[Conflict]:
        return [item for item in self.conflicts if not item.resolved]

    def video_confirmed_facts(self) -> list[Fact]:
        return [item for item in self.facts if item.source == "video" and item.status == "CONFIRMED"]

    def video_inferred_facts(self) -> list[Fact]:
        return [item for item in self.facts if item.source == "video" and item.status == "INFERRED"]

    def user_facts(self) -> list[Fact]:
        return [item for item in self.facts if item.source == "user"]

    def has_pending_questions(self) -> bool:
        return bool(self.pending_questions)

    def compact(self, *, include_timeline: bool = True, max_facts: int = 40) -> dict[str, Any]:
        """LLM Context용 요약 상태. 출처 태그를 값에 함께 붙인다."""

        def slot_block(model: BaseModel) -> dict[str, Any]:
            block: dict[str, Any] = {}
            for name, value in model:
                if isinstance(value, Slot):
                    block[name] = value.label()
                elif isinstance(value, BaseModel):
                    block[name] = slot_block(value)
                else:
                    block[name] = value
            return block

        compact: dict[str, Any] = {
            "case_id": self.case_id,
            "stage": self.current_stage,
            "video_status": self.video_status,
            "video_source": slot_block(self.video_source),
            "accident_datetime": slot_block(self.accident_datetime),
            "road": slot_block(self.road),
            "user_vehicle": slot_block(self.ego_vehicle),
            "opponent_vehicle": slot_block(self.other_vehicle),
            "collision": slot_block(self.collision),
            "video_confirmed_facts": [f.fact for f in self.video_confirmed_facts()][:max_facts],
            "video_inferred_facts": [f.fact for f in self.video_inferred_facts()][:max_facts],
            "user_confirmed_facts": [
                f"{f.fact} (verification={f.verification})" for f in self.user_confirmed_facts
            ][:max_facts],
            "uncertain_facts": self.uncertain_facts[:max_facts],
            "conflicts": [
                {
                    "field": c.field,
                    "description": c.description,
                    "existing": f"{c.existing_value} [{c.existing_source}]",
                    "new": f"{c.new_value} [{c.new_source}]",
                    "resolved": c.resolved,
                }
                for c in self.conflicts
            ],
            "opponent_claim": self.opponent_claim,
            "missing_information": [m.model_dump() for m in self.missing_information],
            "asked_fields": self.asked_fields,
            "retrieved_case_ids": [c.case_id for c in self.retrieved_cases],
            "rag_tier": self.rag_tier,
            "case_review": {
                "started": self.case_review_started,
                "done": self.case_review_done,
                "answers": self.review_answers,
                "pending_questions": [q.field for q in self.pending_questions],
            },
            "fault_assessment": (
                {
                    "fault_ratio": self.fault_assessment.fault_ratio.model_dump(),
                    "possible_range": self.fault_assessment.possible_range,
                    "confidence": self.fault_assessment.confidence,
                    "primary_case_ids": self.fault_assessment.primary_case_ids,
                    "reasoning_summary": self.fault_assessment.reasoning_summary,
                    "uncertainties": self.fault_assessment.uncertainties,
                    "invalidated": self.assessment_invalidated,
                }
                if self.fault_assessment
                else None
            ),
        }
        if include_timeline:
            compact["timeline"] = [
                {"time": t.time, "event": t.event, "source": t.source, "status": t.status}
                for t in self.timeline[:30]
            ]
        if self.video_analysis:
            compact["video_summary"] = self.video_analysis.short_summary
            compact["video_detailed_description"] = self.video_analysis.detailed_description[:1500]
            compact["video_vehicles"] = [
                {"id": v.id, "description": v.description, "is_dashcam": v.is_ego}
                for v in self.video_analysis.vehicles
            ]
            compact["video_collision_pair"] = self.video_analysis.collision_pair.model_dump(
                include={"participants", "non_participants", "confidence"}
            )
        return compact
