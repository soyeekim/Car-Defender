"""Master Agent → Document Agent 전달 패키지 (가이드 11.6 / 23절).

원본 대화 전체를 넘기지 않고 검증·정리된 구조화 데이터만 전달한다.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from state.case_state import CaseState, Slot


class VerifiedCasePackage(BaseModel):
    case_id: str
    video_source: dict[str, Any] = Field(default_factory=dict)
    accident_datetime: dict[str, Any] = Field(default_factory=dict)
    location: Optional[str] = None
    road: dict[str, Any] = Field(default_factory=dict)
    user_vehicle: dict[str, Any] = Field(default_factory=dict)
    opponent_vehicle: dict[str, Any] = Field(default_factory=dict)
    collision: dict[str, Any] = Field(default_factory=dict)
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    verified_facts: list[str] = Field(default_factory=list, description="영상에서 확인된 사실")
    video_inferred_facts: list[str] = Field(default_factory=list)
    user_confirmed_facts: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    video_summary: str = ""
    video_detailed_description: str = Field(default="", description="영상 분석(1·2차)의 상세 서술 — 영상에서 관찰된 내용")
    retrieved_cases: list[dict[str, Any]] = Field(default_factory=list)
    fault_assessment: Optional[dict[str, Any]] = None
    adjustment_factors: list[dict[str, Any]] = Field(default_factory=list)
    opponent_claim: Optional[str] = None

    def allowed_case_ids(self) -> set[str]:
        return {str(item.get("case_id")) for item in self.retrieved_cases if item.get("case_id")}

    def fact_blob(self) -> str:
        parts = list(self.verified_facts) + list(self.user_confirmed_facts) + list(self.video_inferred_facts)
        parts.append(self.video_summary)
        parts.append(self.video_detailed_description)
        for block in (self.road, self.user_vehicle, self.opponent_vehicle, self.collision):
            parts.extend(str(value) for value in block.values() if value)
        return " ".join(parts)


def _slots(model) -> dict[str, Any]:
    block: dict[str, Any] = {}
    for name, value in model:
        if isinstance(value, Slot):
            if value.is_known():
                block[name] = f"{value.value} [{value.source}/{value.status}]"
            else:
                block[name] = "UNKNOWN"
        elif isinstance(value, (str, int, float)) or value is None:
            block[name] = value
    return block


def build_verified_package(state: CaseState) -> VerifiedCasePackage:
    cases = []
    for case in state.retrieved_cases:
        cases.append(
            {
                "case_id": case.case_id,
                "title": case.title,
                "source_type": case.source_type,
                "basic_ratio": case.basic_ratio,
                "decision_ratio": case.decision_ratio,
                "accident_description": (case.accident_description or case.excerpt)[:600],
                "key_issues": case.key_issues[:4],
                "decision_reasons": case.decision_reasons[:4],
                "modification_factors": case.modification_factors[:6],
                "matched_factors": case.relevance.matched_factors if case.relevance else [],
                "different_factors": case.relevance.different_factors if case.relevance else [],
                "usable_as_primary_reference": case.relevance.usable_as_primary_reference if case.relevance else False,
            }
        )
    assessment = state.fault_assessment
    return VerifiedCasePackage(
        case_id=state.case_id,
        video_source=_slots(state.video_source),
        accident_datetime=_slots(state.accident_datetime),
        location=state.road.location_name.value if state.road.location_name.is_known() else None,
        road=_slots(state.road),
        user_vehicle=_slots(state.ego_vehicle),
        opponent_vehicle=_slots(state.other_vehicle),
        collision=_slots(state.collision),
        timeline=[
            {"time": item.time, "end_time": item.end_time, "event": item.event, "source": item.source, "status": item.status}
            for item in state.timeline
            if item.status != "UNKNOWN"
        ],
        verified_facts=[fact.fact for fact in state.video_confirmed_facts()],
        video_inferred_facts=[fact.fact for fact in state.video_inferred_facts()],
        user_confirmed_facts=[
            f"{fact.fact} (영상 확인: {fact.verification})" for fact in state.user_confirmed_facts if fact.field != "opponent_claim"
        ],
        conflicts=[item.description for item in state.unresolved_conflicts()],
        uncertainties=list(state.uncertain_facts),
        video_summary=state.video_analysis.short_summary if state.video_analysis else "",
        video_detailed_description=(state.video_analysis.detailed_description[:3000] if state.video_analysis else ""),
        retrieved_cases=cases,
        fault_assessment=(
            {
                "fault_ratio": assessment.fault_ratio.model_dump(),
                "assessment_type": assessment.assessment_type,
                "possible_range": assessment.possible_range,
                "confidence": assessment.confidence,
                "primary_case_ids": assessment.primary_case_ids,
                "core_facts": assessment.core_facts,
                "reasoning_summary": assessment.reasoning_summary,
                "uncertainties": assessment.uncertainties,
                "explanation": assessment.explanation,
            }
            if assessment
            else None
        ),
        adjustment_factors=[item.model_dump() for item in assessment.adjustment_factors] if assessment else [],
        opponent_claim=state.opponent_claim,
    )
