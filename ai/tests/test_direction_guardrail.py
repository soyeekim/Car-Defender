"""판정 방향(누가 더 책임이 큰가)과 숫자의 일관성 guardrail + 판정 카드 문구 정리."""

from assessment.fault_ratio import assess_fault_ratio
from fakes import FakeTextClient, quiet_logger, sample_cases, sample_observation
from state.case_state import CaseState, FaultAssessment, FaultRatio
from state.updater import ExtractedFact, UserFactExtraction, apply_user_extraction, merge_video_facts
from video.schemas import VideoResult


def _state() -> CaseState:
    state = CaseState(video_path="x.mp4", video_uploaded=True)
    merge_video_facts(state, VideoResult.from_observation(sample_observation(), video_backend="fake"))
    apply_user_extraction(state, UserFactExtraction(new_facts=[ExtractedFact(field="video_source.vehicle_owner", value="user", fact="사용자 차량 블랙박스", verification="not_visible_in_video")]))
    state.retrieved_cases = sample_cases()
    return state


class FlippedClient(FakeTextClient):
    """'상대 책임이 더 크다'고 판단하고도 숫자를 청구:피청구 순서로 옮겨 나 80 : 상대 20이라고 적는 모델을 흉내 낸다."""

    def _master_fault_assessment(self, user):
        base = super()._master_fault_assessment(user)
        base["more_at_fault"] = "opponent"
        base["fault_ratio"] = {"user": 80, "opponent": 20}
        base["possible_range"] = ["80:20", "70:30"]
        base["adjustment_factors"] = [{"factor": "상대 차량 양보 의무 위반 (VIDEO_CONFIRMED)", "direction": "user_down", "percentage": 10, "source": "video", "applies": True, "note": "영상 확인"}]
        base["explanation"] = "가장 비슷한 심의사례의 결정비율 30:70을 기준으로 보면 나(사용자 차량) 80 : 상대 20 정도예요. 상대가 양보하지 않았기 때문이에요. 궁금한 점이 있으면 알려주세요."
        return base


def test_ratio_is_flipped_to_match_declared_direction(tmp_path):
    state = _state()
    assessment = assess_fault_ratio(FlippedClient(), state, state.retrieved_cases, run_logger=quiet_logger(tmp_path))
    assert assessment.more_at_fault == "opponent"
    assert (assessment.fault_ratio.user, assessment.fault_ratio.opponent) == (20, 80)
    assert assessment.reasoning_summary[0].startswith("[방향 보정]")
    assert assessment.possible_range and assessment.possible_range[0] == "20:80"
    # 설명문의 표기도 최종 비율로 맞춰진다
    assert "나 20 : 상대 80" in assessment.explanation and "80 : 상대 20" not in assessment.explanation


def test_consistent_direction_is_left_alone(tmp_path):
    state = _state()
    client = FakeTextClient(ratio=(30, 70))
    assessment = assess_fault_ratio(client, state, state.retrieved_cases, run_logger=quiet_logger(tmp_path))
    assert (assessment.fault_ratio.user, assessment.fault_ratio.opponent) == (30, 70)
    assert not any(item.startswith("[방향 보정]") for item in assessment.reasoning_summary)


def test_judge_summary_normalizes_ratio_and_drops_cta():
    from agent.presenters import change_reason_text, judge_summary

    state = CaseState()
    assessment = FaultAssessment(
        fault_ratio=FaultRatio(user=20, opponent=80),
        explanation="가장 비슷한 심의사례 2018-071745의 결정비율 50:50을 기준으로 보면 나(사용자 차량) 20 : 상대 80 정도예요. 상대가 양보하지 않았기 때문이에요. 다음으로 궁금한 점이 있으면 알려주세요.",
        uncertainties=["상대 차량의 제동 시점은 확인 불가임.", "방향지시등 여부 미확인."],
    )
    summary = judge_summary(assessment, state)
    assert summary.startswith("가장 비슷한 심의사례")  # 이미 비율이 들어 있으면 머리말을 덧붙이지 않는다
    assert summary.count("나 20 : 상대 80") == 1
    assert "알려주세요" not in summary
    assert "확인 불가임; 방향지시등 여부 미확인." in summary  # 항목 끝 마침표 중복 없음

    class Prev:
        ratio_mine, ratio_other = 30, 70

    reason = change_reason_text(Prev(), assessment, ["새로운 사용자 사실: other_vehicle.signal_state=yellow"])
    assert "나 30 : 상대 70에서 나 20 : 상대 80" in reason and "상대 차 신호 상태: 황색" in reason
