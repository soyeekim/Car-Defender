from unittest.mock import patch

from agent.evidence_fusion import fuse_evidence
from agent.fact_planner import (
    factors_for_video_recheck,
    plan_required_factors,
    refresh_factor_statuses,
)
from schemas.accident_state import AccidentState
from schemas.fact_plan import FactPlan, FactorAnswer, RequiredFactor
from schemas.video_analysis import VideoAnalysisResult, VisionSlotValue


def test_empty_llm_plan_uses_active_slots_as_a_fallback():
    video = VideoAnalysisResult(summary="영상 정보 부족")
    state = AccidentState(active_slots=["accident_place", "ego_maneuver"])
    fusion = fuse_evidence(video, state)

    with patch(
        "agent.fact_planner.call_json",
        return_value={
            "plan_summary": "판단 요소를 생성하지 못함",
            "accident_hypotheses": [],
            "required_factors": [],
        },
    ):
        plan = plan_required_factors(video, fusion, state)

    assert [factor.factor_id for factor in plan.required_factors] == [
        "accident_place",
        "ego_maneuver",
    ]
    assert all(factor.importance == "high" for factor in plan.required_factors)


def test_llm_enum_aliases_are_normalized_without_losing_the_plan():
    video = VideoAnalysisResult(summary="회전교차로 측면 충돌")
    state = AccidentState(active_slots=["ego_maneuver"])
    fusion = fuse_evidence(video, state)
    raw = {
        "plan_summary": "진행 궤적 확인",
        "accident_hypotheses": [
            {
                "family": "roundabout_collision",
                "confidence": 0.8,
                "reason": "측면 접근",
            }
        ],
        "required_factors": [
            {
                "factor_id": "ego_path",
                "description": "자차 진행 궤적",
                "category": "maneuver",
                "importance": "CRITICAL",
                "reason": "진로 변경 여부 확인",
                "preferred_source": "video_then_user",
                "status": "vision_inferred",
            }
        ],
    }

    with patch("agent.fact_planner.call_json", return_value=raw):
        plan = plan_required_factors(video, fusion, state)

    factor = plan.required_factors[0]
    assert factor.category == "actor_behavior"
    assert factor.importance == "critical"
    assert factor.preferred_source == "vision_then_user"
    assert factor.status == "inferred_by_vision"


def test_only_unresolved_high_impact_vision_factors_are_rechecked():
    plan = FactPlan(
        plan_summary="차로변경 판단",
        required_factors=[
            RequiredFactor(
                factor_id="boundary_crossing",
                description="경계 침범 차량",
                importance="critical",
                reason="진로변경 주체 판단",
                preferred_source="vision_then_user",
                video_recheck_instruction="차로선과 두 차량의 궤적을 재확인",
            ),
            RequiredFactor(
                factor_id="insurance_claim",
                description="상대 보험사 주장",
                importance="high",
                reason="분쟁 범위 확인",
                preferred_source="external",
            ),
            RequiredFactor(
                factor_id="minor_detail",
                description="경미한 주변 정보",
                importance="low",
                reason="보조 정보",
                preferred_source="vision",
                video_recheck_instruction="주변을 재확인",
            ),
        ],
    )

    selected = factors_for_video_recheck(plan)

    assert [factor.factor_id for factor in selected] == ["boundary_crossing"]


def test_dynamic_factor_can_be_confirmed_by_video_or_user_answer():
    video = VideoAnalysisResult(
        summary="차로 경계 확인",
        additional_observations=[
            {
                "fact_key": "boundary_crossing",
                "fact_label": "경계 침범 차량",
                "value": "상대 차량",
                "confidence": 0.9,
                "evidence": "상대 차량 바퀴가 차로선을 넘어옴",
                "timestamp": "00:03.2",
                "observation_type": "direct_visual",
                "temporal_scope": "pre_collision",
            }
        ],
    )
    fusion = fuse_evidence(video, AccidentState())
    plan = FactPlan(
        plan_summary="차로변경 판단",
        required_factors=[
            RequiredFactor(
                factor_id="boundary_crossing",
                description="경계 침범 차량",
                importance="critical",
                reason="진로변경 주체 판단",
            )
        ],
    )

    refresh_factor_statuses(plan, fusion, {})
    assert plan.required_factors[0].status == "confirmed_by_vision"

    answer = FactorAnswer(factor_id="boundary_crossing", value="상대 차량")
    refresh_factor_statuses(plan, fusion, {"boundary_crossing": answer})
    assert plan.required_factors[0].status == "user_claimed"
