import json
from unittest.mock import patch

import pytest

from agent.evidence_fusion import fuse_evidence, resolved_state_from_fusion
from agent.fact_planner import refresh_factor_statuses
from agent.state_manager import update_state
from agent.vision_first_intake_agent import VisionFirstIntakeAgent
from models.gemini_video import merge_video_analyses
from schemas.accident_state import AccidentState, AccidentType
from schemas.fact_plan import (
    AccidentHypothesis,
    FactPlan,
    FactorAnswer,
    RequiredFactor,
)
from schemas.video_analysis import (
    AdditionalVisionFact,
    ObservedEvent,
    VideoAnalysisResult,
    VisionSlotValue,
)
from storage.evidence_store import EvidenceSessionStore


def vision_value(
    value,
    *,
    confidence=0.9,
    observation_type="direct_visual",
    temporal_scope="general",
):
    return VisionSlotValue(
        value=value,
        confidence=confidence,
        evidence=f"{value}가 영상에서 확인됨" if value is not None else None,
        timestamp="00:03" if value is not None else None,
        observation_type=observation_type,
        temporal_scope=temporal_scope,
    )


def test_strong_vision_evidence_wins_but_user_claim_is_preserved():
    video = VideoAnalysisResult(
        summary="충돌 전 센서에서 급감 확인",
        pre_collision_sudden_braking=vision_value(
            "예",
            confidence=0.94,
            observation_type="sensor_readout",
            temporal_scope="pre_collision",
        ),
    )
    user_state = AccidentState()
    update_state(user_state, {"pre_collision_sudden_braking": "아니오"})

    fact = fuse_evidence(video, user_state).facts[
        "pre_collision_sudden_braking"
    ]

    assert fact.status == "disputed_vision_preferred"
    assert fact.resolved_value == "예"
    assert fact.selected_source == "vision"
    assert fact.user.value == "아니오"
    assert fact.vision.temporal_scope == "pre_collision"


@pytest.mark.parametrize(
    ("slot_name", "vision_result", "user_value"),
    [
        ("accident_place", "회전교차로", "직선 도로"),
        ("ego_signal", "녹색", "적색"),
        ("ego_lane", "외측 차로", "내측 차로"),
        ("turn_signal", "예", "아니오"),
        ("ego_collision_area", "좌측 측면", "우측 측면"),
        ("collision_timestamp", "00:04.600", "00:05.000"),
    ],
)
def test_same_evidence_policy_applies_to_all_accident_facts(
    slot_name, vision_result, user_value
):
    video = VideoAnalysisResult(
        summary="범용 증거 정책 테스트",
        **{slot_name: vision_value(vision_result)},
    )
    user_state = AccidentState()
    update_state(user_state, {slot_name: user_value})

    fact = fuse_evidence(video, user_state).facts[slot_name]

    assert fact.status == "disputed_vision_preferred"
    assert fact.resolved_value == vision_result
    assert fact.user.value == user_value


def test_inferred_video_conflict_stays_unresolved_without_reasking_user():
    video = VideoAnalysisResult(
        summary="영상 정황 추론",
        turn_signal=vision_value(
            "예", confidence=0.95, observation_type="inferred"
        ),
    )
    user_state = AccidentState()
    update_state(user_state, {"turn_signal": "아니오"})

    fusion = fuse_evidence(video, user_state)
    fact = fusion.facts["turn_signal"]
    working_state = resolved_state_from_fusion(fusion)

    assert fact.status == "disputed_unresolved"
    assert fact.resolved_value is None
    assert working_state.turn_signal.value == "아니오"
    assert working_state.turn_signal.source == "user"


def test_vision_first_agent_seeds_only_verified_vision_values():
    video = VideoAnalysisResult(
        summary="회전교차로 사고",
        accident_target=vision_value("차대차"),
        accident_place=vision_value("회전교차로"),
        ego_maneuver=vision_value("외측 차로 주행"),
        opponent_maneuver=vision_value(
            "내측 차로 주행", observation_type="inferred"
        ),
    )
    fixed_type = AccidentType(
        family="intersection_collision", status="partially_confirmed"
    )
    fixed_plan = FactPlan(
        plan_summary="회전교차로 위치 확인",
        accident_hypotheses=[
            AccidentHypothesis(
                family="roundabout_collision",
                confidence=0.8,
                reason="회전교차로가 직접 확인됨",
            )
        ],
        required_factors=[
            RequiredFactor(
                factor_id="accident_context",
                description="사고 장소와 대상",
                importance="critical",
                reason="사고 유형 판단",
                preferred_source="vision",
                related_fact_keys=["accident_target", "accident_place"],
            )
        ],
    )

    with (
        patch(
            "agent.vision_first_intake_agent.classify_accident",
            return_value=fixed_type,
        ),
        patch(
            "agent.vision_first_intake_agent.plan_required_factors",
            return_value=fixed_plan,
        ),
    ):
        agent = VisionFirstIntakeAgent(video)

    assert agent.state.accident_target.value == "차대차"
    assert agent.state.accident_target.source == "vision"
    assert agent.state.opponent_maneuver.value is None
    assert agent.user_state.accident_target.value is None
    assert agent._is_complete() is True


def test_dynamic_factor_answer_drives_question_and_completion():
    video = VideoAnalysisResult(summary="차로 경계는 영상에서 불명확")
    fixed_type = AccidentType(family="lane_change", status="partially_confirmed")
    fixed_plan = FactPlan(
        plan_summary="경계 침범 주체 확인 필요",
        accident_hypotheses=[
            AccidentHypothesis(
                family="lane_change",
                confidence=0.7,
                reason="측면 접근",
            )
        ],
        required_factors=[
            RequiredFactor(
                factor_id="lane_boundary_crossing_vehicle",
                description="차로 경계를 넘은 차량",
                importance="critical",
                reason="진로 변경 주체 판단",
                related_fact_keys=["lane_change_direction"],
                question_hint="어느 차량이 차선을 넘어왔는지 질문",
            )
        ],
    )
    answer = FactorAnswer(
        factor_id="lane_boundary_crossing_vehicle",
        value="상대 차량",
        confidence=0.95,
        related_fact_updates={
            "lane_change_direction": "상대 차량이 자차 차로로 이동"
        },
    )

    with (
        patch(
            "agent.vision_first_intake_agent.classify_accident",
            return_value=fixed_type,
        ),
        patch(
            "agent.vision_first_intake_agent.plan_required_factors",
            return_value=fixed_plan,
        ),
        patch(
            "agent.vision_first_intake_agent.generate_factor_question",
            return_value="어느 차량이 차로 경계를 넘어왔나요?",
        ),
        patch(
            "agent.vision_first_intake_agent.extract_factor_answer",
            return_value=answer,
        ),
        patch("agent.vision_first_intake_agent.extract_slots", return_value={}),
    ):
        agent = VisionFirstIntakeAgent(video)
        first = agent.start()
        second = agent.process("상대 차량이 넘어왔습니다.")

    assert first["pending_factor_id"] == "lane_boundary_crossing_vehicle"
    assert second["intake_complete"] is True
    assert second["factor_answers"]["lane_boundary_crossing_vehicle"]["value"] == "상대 차량"


def test_targeted_recheck_prefers_direct_observation_and_preserves_event_timeline():
    initial = VideoAnalysisResult(
        summary="초기 분석",
        ego_lane=vision_value("외측 추정", observation_type="inferred"),
        timeline_events=[
            ObservedEvent(event_id="event_1", description="회전교차로 진입")
        ],
    )
    targeted = VideoAnalysisResult(
        summary="재확인 분석",
        ego_lane=vision_value("외측 차로", confidence=0.85),
        timeline_events=[
            ObservedEvent(event_id="event_2", description="차로 유도선 확인")
        ],
    )

    merged = merge_video_analyses(initial, targeted)

    assert merged.ego_lane.value == "외측 차로"
    assert {event.event_id for event in merged.timeline_events} == {
        "event_1",
        "event_2",
    }


def test_agent_replans_after_targeted_video_before_user_answers():
    initial = VideoAnalysisResult(summary="차로 변경 주체 불명확")
    targeted = VideoAnalysisResult(
        summary="상대 차량의 경계 침범 확인",
        lane_change_direction=vision_value("상대 차량이 자차 차로로 이동"),
    )
    first_plan = FactPlan(
        plan_summary="영상 재확인 필요",
        required_factors=[
            RequiredFactor(
                factor_id="boundary_crossing",
                description="경계 침범 차량",
                importance="critical",
                reason="진로 변경 주체 판단",
                preferred_source="vision_then_user",
                related_fact_keys=["lane_change_direction"],
                video_recheck_instruction="차로선과 바퀴 궤적 확인",
            )
        ],
    )
    second_plan = FactPlan(
        plan_summary="재확인으로 핵심 사실 확인",
        required_factors=[
            RequiredFactor(
                factor_id="boundary_crossing",
                description="경계 침범 차량",
                importance="critical",
                reason="진로 변경 주체 판단",
                preferred_source="vision_then_user",
                related_fact_keys=["lane_change_direction"],
            )
        ],
    )

    with (
        patch(
            "agent.vision_first_intake_agent.classify_accident",
            return_value=AccidentType(family="lane_change", status="partially_confirmed"),
        ),
        patch(
            "agent.vision_first_intake_agent.plan_required_factors",
            side_effect=[first_plan, second_plan],
        ) as planner,
    ):
        agent = VisionFirstIntakeAgent(initial)
        agent.apply_targeted_video_analysis(targeted)

    assert planner.call_count == 2
    assert agent.fact_plan.plan_summary == "재확인으로 핵심 사실 확인"
    assert agent.fact_plan.required_factors[0].status == "confirmed_by_vision"
    assert agent._is_complete() is True


def test_unmodeled_video_fact_is_kept_without_adding_a_new_slot():
    video = VideoAnalysisResult(
        summary="고정 Slot 밖 관찰",
        additional_observations=[
            AdditionalVisionFact(
                fact_key="temporary_lane_obstacle",
                fact_label="임시 차로 장애물",
                value="라바콘",
                confidence=0.91,
                evidence="외측 차로에 라바콘이 보임",
                timestamp="00:02",
                observation_type="direct_visual",
                temporal_scope="pre_collision",
            )
        ],
    )

    fact = fuse_evidence(video, AccidentState()).facts["temporary_lane_obstacle"]

    assert fact.status == "verified_by_vision"
    assert fact.resolved_value == "라바콘"


def test_evidence_store_writes_three_independent_json_files(tmp_path):
    store = EvidenceSessionStore(base_dir=tmp_path, session_id="session_test")
    video = VideoAnalysisResult(
        summary="테스트 영상",
        accident_target=vision_value("차대차"),
    )
    user_state = AccidentState()
    update_state(user_state, {"accident_target": "차대차"})
    fusion = fuse_evidence(video, user_state)
    accident_type = AccidentType(family="rear_end", status="partially_confirmed")

    store.save_vision(
        video_path="sample.mp4",
        video_model="test-model",
        video_fps=5,
        analysis=video,
        analysis_passes=[
            {"pass": "broad_observation", "result": video.model_dump()}
        ],
    )
    factor_answer = FactorAnswer(factor_id="driver_memory", value="모름")
    store.save_user_answers(
        conversation=[{"role": "user", "content": "뒤에서 박았습니다."}],
        extracted_answers=user_state,
        pending_question_slot=None,
        factor_answers={"driver_memory": factor_answer},
        pending_factor_id=None,
    )
    fact_plan = FactPlan(
        plan_summary="후방추돌 사실 확인",
        required_factors=[
            RequiredFactor(
                factor_id="driver_memory",
                description="운전자 기억",
                importance="high",
                reason="영상 사각지대 보완",
                preferred_source="user",
            )
        ],
    )
    store.save_final_facts(
        fusion=fusion,
        accident_type=accident_type,
        active_slots=["accident_target"],
        missing_slots=[],
        intake_complete=True,
        completion_reason="required_slots_sufficient",
        fact_plan=fact_plan,
    )

    paths = {
        name: store.session_dir / name
        for name in (
            "vision_analysis.json",
            "user_answers.json",
            "final_facts.json",
        )
    }
    assert all(path.is_file() for path in paths.values())
    assert json.loads(paths["vision_analysis.json"].read_text("utf-8"))[
        "analysis"
    ]["summary"] == "테스트 영상"
    assert json.loads(paths["vision_analysis.json"].read_text("utf-8"))[
        "analysis_passes"
    ][0]["pass"] == "broad_observation"
    assert json.loads(paths["user_answers.json"].read_text("utf-8"))[
        "extracted_answers"
    ]["accident_target"]["value"] == "차대차"
    assert json.loads(paths["user_answers.json"].read_text("utf-8"))[
        "factor_answers"
    ]["driver_memory"]["value"] == "모름"
    assert json.loads(paths["final_facts.json"].read_text("utf-8"))["facts"][
        "accident_target"
    ]["status"] == "corroborated"
    assert json.loads(paths["final_facts.json"].read_text("utf-8"))[
        "fact_plan"
    ]["plan_summary"] == "후방추돌 사실 확인"
