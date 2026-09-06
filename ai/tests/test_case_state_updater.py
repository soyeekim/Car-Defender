from fakes import sample_observation
from state.case_state import CaseState, FaultAssessment, FaultRatio
from state.updater import (
    ExtractedConflict,
    ExtractedFact,
    UserFactExtraction,
    apply_user_extraction,
    get_slot,
    merge_video_facts,
    set_slot,
)
from video.schemas import VideoResult


def _state_with_video(**kwargs) -> CaseState:
    state = CaseState(video_path="x.mp4", video_uploaded=True)
    result = VideoResult.from_observation(sample_observation(**kwargs), video_backend="fake", duration_sec=10.0)
    merge_video_facts(state, result)
    return state


def test_merge_video_facts_populates_slots_with_video_source():
    state = _state_with_video()
    assert state.video_analyzed
    assert state.current_stage == "FACT_COLLECTING"
    assert state.road.road_type.value == "intersection"
    assert state.road.road_type.source == "video"
    assert state.road.signal_present.value == "true"
    assert state.ego_vehicle.vehicle_id == "vehicle_1"
    assert state.other_vehicle.vehicle_id == "vehicle_3"
    assert state.other_vehicle.entry_direction.value == "right_side_road"
    assert state.collision.type.value == "front_to_left_side"
    assert state.collision.ego_collision_part.value == "front"
    assert state.collision.other_collision_part.value == "left_side"
    assert state.collision.participants_confirmed.value == "true"
    assert state.ego_vehicle.signal.value.startswith("green")
    assert len(state.video_confirmed_facts()) >= 3
    assert any(fact.status == "INFERRED" for fact in state.facts)
    assert any("신호등" in item for item in state.uncertain_facts)
    assert state.timeline and state.timeline[0].source == "video"


def test_user_answer_updates_state_and_owner_opponent_remaps_vehicles():
    state = _state_with_video()
    extraction = UserFactExtraction(
        new_facts=[
            ExtractedFact(field="video_source.vehicle_owner", value="상대방 블랙박스", fact="상대 차량 블랙박스 영상", verification="not_visible_in_video", answers_field="video_source.vehicle_owner"),
        ]
    )
    added, conflicts = apply_user_extraction(state, extraction, turn=1)
    assert len(added) == 1 and not conflicts
    assert state.video_source.vehicle_owner.value == "opponent"
    assert state.video_source.vehicle_owner.source == "user"
    # 블랙박스 차량(vehicle_1)이 상대 차량이 되고 사용자 차량은 vehicle_3
    assert state.other_vehicle.vehicle_id == "vehicle_1"
    assert state.ego_vehicle.vehicle_id == "vehicle_3"
    assert state.collision.ego_collision_part.value == "left_side"
    assert "video_source.vehicle_owner" in state.asked_fields
    assert state.user_confirmed_facts[0].fact == "상대 차량 블랙박스 영상"


def test_conflicting_user_statement_is_recorded_not_overwritten():
    state = _state_with_video()
    extraction = UserFactExtraction(
        new_facts=[ExtractedFact(field="ego_vehicle.movement", value="left_turn", fact="사용자는 좌회전 중이었다고 진술", verification="unverified")]
    )
    added, conflicts = apply_user_extraction(state, extraction, turn=2)
    assert state.ego_vehicle.movement.value == "straight"
    assert state.ego_vehicle.movement.source == "video"
    assert len(conflicts) == 1
    assert conflicts[0].field == "ego_vehicle.movement"
    assert added[0].verification == "contradicts_video"
    assert state.unresolved_conflicts()


def test_video_inferred_slot_can_be_confirmed_by_user():
    state = _state_with_video()
    assert state.ego_vehicle.entered_first.status == "INFERRED"
    extraction = UserFactExtraction(new_facts=[ExtractedFact(field="ego_vehicle.entered_first", value="true", fact="교차로에 먼저 진입했다")])
    apply_user_extraction(state, extraction)
    slot = state.ego_vehicle.entered_first
    assert slot.value == "true" and slot.status == "CONFIRMED"


def test_unknown_answer_marks_field_as_asked_and_uncertain():
    state = _state_with_video()
    state.pending_questions = []
    extraction = UserFactExtraction(new_facts=[ExtractedFact(field="accident_datetime.date", value="모름", fact="날짜 기억 안남", answers_field="accident_datetime.date")])
    added, _ = apply_user_extraction(state, extraction)
    assert not added
    assert "accident_datetime.date" in state.asked_fields
    assert any("accident_datetime.date" in item for item in state.uncertain_facts)
    assert not state.accident_datetime.date.is_known()


def test_important_new_fact_invalidates_assessment_and_clears_cases():
    state = _state_with_video()
    state.fault_assessment = FaultAssessment(fault_ratio=FaultRatio(user=30, opponent=70))
    state.set_stage("ASSESSMENT_COMPLETE")
    extraction = UserFactExtraction(new_facts=[ExtractedFact(field="other_vehicle.turn_signal", value="false", fact="상대 방향지시등 미점등 진술", verification="not_visible_in_video")])
    apply_user_extraction(state, extraction)
    assert state.assessment_invalidated
    assert state.current_stage == "FACT_COLLECTING"
    assert state.assessment_invalidation_reasons


def test_opponent_claim_and_explicit_conflict_are_stored():
    state = _state_with_video()
    extraction = UserFactExtraction(
        conflicts=[ExtractedConflict(field="road.signal_present", existing_value="true", new_value="false", description="사용자는 신호등이 없었다고 진술")],
        opponent_claim="상대 보험사는 50:50을 주장",
    )
    apply_user_extraction(state, extraction)
    assert state.opponent_claim == "상대 보험사는 50:50을 주장"
    assert state.conflicts[-1].field == "road.signal_present"
    assert state.conflicts[-1].existing_source == "video"


def test_user_can_assign_vehicle_id_directly():
    state = _state_with_video()
    extraction = UserFactExtraction(new_facts=[ExtractedFact(field="ego_vehicle.vehicle_id", value="vehicle_3", fact="vehicle_3가 내 차", answers_field="ego_vehicle.vehicle_id")])
    apply_user_extraction(state, extraction)
    assert state.ego_vehicle.vehicle_id == "vehicle_3"
    assert state.other_vehicle.vehicle_id == "vehicle_1"
    assert state.video_source.vehicle_owner.value == "opponent"


def test_set_slot_same_value_from_user_keeps_video_provenance():
    state = _state_with_video()
    updated, conflict = set_slot(state, "road.road_type", "intersection", source="user")
    assert updated and conflict is None
    slot = get_slot(state, "road.road_type")
    assert slot.source == "video" and "일치" in (slot.note or "")


def test_compact_state_labels_sources():
    state = _state_with_video()
    compact = state.compact()
    assert compact["road"]["road_type"].endswith("[VIDEO_CONFIRMED]")
    assert compact["user_vehicle"]["vehicle_id"] == "vehicle_1"
    assert compact["video_collision_pair"]["participants"] == ["vehicle_1", "vehicle_3"]


def test_unknown_answer_in_natural_korean_closes_pending_question():
    """실서버 대화: "모릅니다."에 LLM 이 항목을 냈지만 value 가 원문이고 answers_field 가 질문 필드와 달라 질문이 안 닫혔다."""
    from state.case_state import Question
    from state.updater import is_unknown_answer

    for text in ("모릅니다", "몰라요", "몰랐어요", "기억이 안 나요", "기억나지 않아요", "못 봤어요", "확인 못 했어요", "알 수 없어요", "글쎄요", "unknown"):
        assert is_unknown_answer(text), text
    for text in ("켰어요", "안 켰어요", "초록불이었어요", "2차로였어요"):
        assert not is_unknown_answer(text), text

    state = CaseState(video_path="x.mp4", video_uploaded=True)
    merge_video_facts(state, VideoResult.from_observation(sample_observation(), video_backend="fake"))
    state.pending_questions = [Question(field="review.opponent_signal_compliance", question="상대 차량이 교차로 신호를 준수했나요?", importance="high")]
    extraction = UserFactExtraction(
        new_facts=[ExtractedFact(field=None, answers_field="other_vehicle.signal", value="모릅니다", fact="상대 신호 준수 여부는 모른다고 답함", verification="unverified")]
    )
    apply_user_extraction(state, extraction, turn=3)
    assert state.pending_questions == []
    assert "review.opponent_signal_compliance" in state.asked_fields
    assert any("모른다고 답함" in note for note in state.uncertain_facts)


def test_answers_field_is_matched_to_the_single_pending_question():
    """LLM 이 answers_field 를 다른 이름으로 적어도 대기 질문이 하나뿐이면 그 질문의 답으로 본다."""
    from state.case_state import Question

    state = CaseState(video_path="x.mp4", video_uploaded=True)
    merge_video_facts(state, VideoResult.from_observation(sample_observation(), video_backend="fake"))
    state.pending_questions = [Question(field="review.entered_first", question="본인 차량이 먼저 진입했나요?", importance="high")]
    extraction = UserFactExtraction(
        new_facts=[ExtractedFact(field=None, answers_field="ego_vehicle.entered_first", value="true", fact="본인 차량이 먼저 진입", verification="unverified")]
    )
    apply_user_extraction(state, extraction, turn=4)
    assert state.pending_questions == []
    assert state.review_answers.get("review.entered_first") == "true"
