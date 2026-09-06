from fakes import FakeVideoAnalyzer, quiet_logger, sample_observation
from video.merge import merge_video_results
from video.schemas import VideoResult
from video.validation import (
    accident_profile,
    build_focus_targets,
    evaluate_video_completion,
    factor_coverage,
    should_use_cv_tracking,
    user_confirmation_question,
    validate_video_result,
)


def _result(**kwargs) -> VideoResult:
    return VideoResult.from_observation(sample_observation(**kwargs), video_backend="fake", duration_sec=10.0)


def test_valid_result_passes_fast_path_and_completion_gate():
    result = _result(pair_confidence=0.92)
    valid, reasons = validate_video_result(result, 0.8)
    assert valid and reasons == []
    completion = evaluate_video_completion(result, threshold=0.8, score_threshold=80)
    assert completion.status == "VIDEO_ANALYSIS_COMPLETE"
    assert completion.score >= 80
    assert completion.gate["collision_participant_identification"]
    assert accident_profile(result) == "signalized_intersection"


def test_low_pair_confidence_needs_recheck_and_builds_collision_focus():
    result = _result(pair_confidence=0.6)
    valid, reasons = validate_video_result(result, 0.8)
    assert not valid and "collision_pair_low_confidence" in reasons
    completion = evaluate_video_completion(result, threshold=0.8)
    assert completion.status == "VIDEO_NEEDS_RECHECK"
    targets = build_focus_targets(result, 0.8)
    assert targets and targets[0].kind == "collision_pair"
    assert "vehicle_1 ↔ vehicle_3" in targets[0].question
    assert targets[0].start_sec is not None and targets[0].end_sec is not None
    assert should_use_cv_tracking(result, 0.8)


def test_participant_not_in_inventory_is_rejected():
    result = _result()
    result.collision_pair.participants = ["vehicle_1", "vehicle_9"]
    valid, reasons = validate_video_result(result, 0.8)
    assert not valid
    assert "collision_participants_not_in_inventory" in reasons


def test_factor_coverage_checks_signal_checklist():
    result = _result()
    coverage, missing, profile = factor_coverage(result)
    assert profile == "signalized_intersection"
    assert coverage >= 0.6
    result.fault_relevant_factors = []
    result.road_environment.signal_observations = []
    result.confirmed_facts = []
    result.inferred_facts = []
    result.unknown_or_unobservable = []
    coverage2, missing2, _ = factor_coverage(result)
    assert coverage2 < coverage
    assert "신호 상태" in missing2
    # 구조화 필드(signal_present/stop_line 등)가 남아 있으면 해당 항목은 여전히 '점검됨'으로 본다
    assert "신호등 유무" not in missing2 and "정지선" not in missing2


def test_merge_prefers_higher_confidence_pair_and_keeps_vehicle_ids():
    first = _result(pair_confidence=0.6)
    second = _result(pair_confidence=0.93)
    second.changes_from_previous = ["collision pair 재검증"]
    second.vehicles = second.vehicles[:2]  # focus 결과가 일부 차량만 언급해도 inventory는 유지
    merged = merge_video_results(first, second)
    assert merged.collision_pair.confidence == 0.93
    assert set(merged.vehicle_ids()) == {"vehicle_1", "vehicle_2", "vehicle_3"}
    assert merged.changes_from_previous == ["collision pair 재검증"]
    assert len(merged.analysis_passes) == 0  # from_observation without passes


def test_merge_records_pair_change_and_new_vehicle_note():
    first = _result(pair_confidence=0.7)
    second = _result(pair_confidence=0.9)
    second.collision_pair.participants = ["vehicle_1", "vehicle_2"]
    second.vehicles.append(second.vehicles[0].model_copy(update={"id": "vehicle_4", "is_ego": False, "description": "새 차량"}))
    merged = merge_video_results(first, second)
    assert merged.collision_pair.participants == ["vehicle_1", "vehicle_2"]
    assert any("collision pair 변경" in item for item in merged.changes_from_previous)
    assert any("vehicle_4" in note for note in merged.uncertain_facts)


def test_reconcile_registers_vehicle_written_only_in_summary():
    from video.schemas import VehicleEntry, VideoResult
    from video.validation import reconcile_vehicle_inventory

    video = VideoResult(
        short_summary="블랙박스 차량(vehicle_1)이 직진 중 대향 차로에서 좌회전하던 흰색 세단(vehicle_2)과 충돌한 사고입니다.",
        vehicles=[VehicleEntry(id="vehicle_1", description="블랙박스 촬영 차량", is_ego=True)],
        ego_vehicle_id="vehicle_1",
    )
    assert reconcile_vehicle_inventory(video) == ["vehicle_2"]
    assert [vehicle.id for vehicle in video.vehicles] == ["vehicle_1", "vehicle_2"] and video.vehicles[1].description == "흰색 세단"
    assert video.collision_pair.participants == ["vehicle_1", "vehicle_2"] and video.collision_pair.confidence >= 0.8
    assert reconcile_vehicle_inventory(video) == []  # 두 번 부르면 아무것도 더하지 않는다
    # ID 없이 언급된 차량은 보정할 수 없다 → 그대로
    plain = VideoResult(short_summary="블랙박스 차량이 흰색 세단과 충돌", vehicles=[VehicleEntry(id="vehicle_1", is_ego=True)], ego_vehicle_id="vehicle_1")
    assert reconcile_vehicle_inventory(plain) == [] and len(plain.vehicles) == 1


def test_user_confirmation_question_is_objective(tmp_path):
    result = _result(pair_confidence=0.5)
    question = user_confirmation_question(result)
    assert "3대" in question
    assert "맞나요" in question
    assert "잘못" not in question and "생각하시나요" not in question


def test_fake_analyzer_finalize_sets_completion(tmp_path):
    analyzer = FakeVideoAnalyzer(run_logger=quiet_logger(tmp_path))
    result = analyzer.analyze(tmp_path / "video.mp4")
    assert result.video_backend == "fake"
    assert result.analysis_completion.status == "VIDEO_ANALYSIS_COMPLETE"
    assert result.analysis_passes[0].pass_type == "full"
    assert result.ego_vehicle_id == "vehicle_1"
