"""인정기준 도표 계산기: agent 가 고른 도표·역할·행을 코드가 더하고 빼서 비율을 낸다."""

from pathlib import Path

from assessment.chart_calculator import ChartSelection, build_chart_assessment, compute_chart_ratio
from assessment.fault_ratio import NO_REFERENCE_PREFIX, assess_fault_ratio
from fakes import FakeTextClient, quiet_logger
from state.case_state import CaseRelevance, CaseState, ChartModifier, ChartVariant, RetrievedCase
from state.updater import set_slot


def _chart() -> RetrievedCase:
    return RetrievedCase(
        case_id="차12-1",
        source_type="fault_standard",
        title="우측도로 직진 대 좌측도로 직진(동일폭)",
        basic_ratio="40:60",
        role_a="우측도로에서 직진",
        role_b="좌측도로에서 직진",
        accident_description="신호기에 의해 교통정리가 이루어지고 있지 않는 동일 폭의 교차로에서 오른쪽 도로에서 진입하여 직진하는 A차량과 왼쪽 도로에서 진입하여 직진하는 B차량이 충돌한 사고이다.",
        chart_variants=[
            ChartVariant(label="(가)", description="A 동시 / B 동시", basic_ratio="40:60"),
            ChartVariant(label="(나)", description="A 선진입 / B 후진입", basic_ratio="30:70"),
        ],
        chart_modifiers=[
            ChartModifier(side="A", label="현저한 과실", delta=10),
            ChartModifier(side="A", label="서행(일시정지 포함)", delta=-10),
            ChartModifier(side="B", label="현저한 과실", delta=10),
            ChartModifier(side="B", label="중대한 과실", delta=20),
        ],
        relevance=CaseRelevance(case_id="차12-1", relevance=0.8, usable_as_primary_reference=True),
    )


def _state() -> CaseState:
    state = CaseState()
    set_slot(state, "road.road_type", "intersection", source="video")
    set_slot(state, "ego_vehicle.movement", "straight", source="video")
    set_slot(state, "other_vehicle.movement", "straight", source="video")
    set_slot(state, "collision.type", "side", source="video")
    state.ego_vehicle.vehicle_id = "vehicle_1"
    state.other_vehicle.vehicle_id = "vehicle_2"
    return state


def test_compute_chart_ratio_applies_rows_to_the_named_side_and_maps_to_user():
    chart = _chart()
    calc = compute_chart_ratio(chart, user_is="B", variant_label="(나)", applied=[chart.chart_modifiers[3]])
    assert (calc.basic_a, calc.basic_b) == (30, 70)
    assert (calc.final_a, calc.final_b) == (10, 90)  # B 중대한 과실 +20 → B 가 올라간다
    assert (calc.user, calc.opponent) == (90, 10)
    assert calc.formula == "기본 (나) A 30 : B 70 → B 중대한 과실 +20 → A 10 : B 90 → 나(B) 90 : 상대(A) 10"
    # 변형을 못 정하면 첫 변형, 0~100 을 넘지 않는다
    calc = compute_chart_ratio(chart, user_is="A", applied=[ChartModifier(side="A", label="신호위반", delta=80)])
    assert calc.variant == "(가)" and (calc.final_a, calc.final_b) == (100, 0) and calc.user == 100


def test_build_chart_assessment_uses_only_confirmed_rows_from_the_chart():
    chart = _chart()
    selection = ChartSelection(
        chart_id="차12-1",
        variant="(나)",
        user_is="B",
        orientation_reason="사용자 차량이 왼쪽 도로에서 직진했어요.",
        applied_modifiers=[
            {"id": "m4", "source": "video", "evidence": "상대 차량이 정지 없이 교차로에 진입"},
            {"id": "m3", "source": "rag", "evidence": "추정"},  # 확인된 출처가 아님 → 미적용
            {"id": "m9", "source": "video", "evidence": "없는 행"},  # 도표에 없는 id → 무시
        ],
        rejected_modifiers=[{"id": "m1", "reason": "영상에서 확인되지 않음"}],
        confidence=0.9,
        uncertainties=["상대 차량 서행 여부 확인 불가"],
        ratio_dependencies=["상대 차량이 서행했다면 비율이 달라짐"],
        explanation_facts=["영상에서 사용자 차량이 먼저 교차로에 들어선 것이 확인됐어요."],
    )
    assessment = build_chart_assessment(_state(), [chart], selection)
    assert (assessment.fault_ratio.user, assessment.fault_ratio.opponent) == (90, 10)
    assert assessment.more_at_fault == "user"
    assert assessment.anchor_case_id == "차12-1" and assessment.anchor_ratio == "70:30"  # 기본비율을 나(B) 기준으로
    assert assessment.calculation.startswith("기본 (나) A 30 : B 70 → B 중대한 과실 +20")
    assert assessment.confidence == 0.8  # 도표 경로 상한
    applied = [item for item in assessment.adjustment_factors if item.applies]
    assert [(item.factor, item.direction, item.percentage, item.source) for item in applied] == [("B 중대한 과실 +20", "user_up", 20, "video")]
    assert {item.factor for item in assessment.adjustment_factors if not item.applies} == {"B 현저한 과실 +10", "A 현저한 과실 +10"}
    assert assessment.primary_case_ids == ["차12-1"]
    assert "나 90 : 상대 10" in assessment.explanation and "도표 차12-1" in assessment.explanation
    assert any(line.startswith("계산: ") for line in assessment.reasoning_summary)
    assert build_chart_assessment(_state(), [chart], ChartSelection(chart_id="차12-1", user_is=None)) is None


def test_assess_fault_ratio_routes_chart_anchor_through_the_calculator(tmp_path: Path):
    client = FakeTextClient()
    client.chart_selection = {
        "chart_id": "차12-1", "variant": "(가)", "user_is": "A", "orientation_reason": "사용자 차량이 오른쪽 도로에서 직진",
        "applied_modifiers": [{"id": "m2", "source": "video", "evidence": "교차로 진입 전 서행"}], "rejected_modifiers": [],
        "confidence": 0.7, "uncertainties": [], "ratio_dependencies": [], "explanation_facts": [],
    }
    assessment = assess_fault_ratio(client, _state(), [_chart()], run_logger=quiet_logger(tmp_path))
    assert (assessment.fault_ratio.user, assessment.fault_ratio.opponent) == (30, 70)  # 40:60 에 A 서행 -10
    assert assessment.calculation and assessment.anchor_case_id == "차12-1" and not assessment.anchor_enforced


def test_assess_fault_ratio_without_references_is_provisional_and_cites_nothing(tmp_path: Path):
    assessment = assess_fault_ratio(FakeTextClient(), _state(), [], run_logger=quiet_logger(tmp_path))
    assert assessment.assessment_type == "provisional" and assessment.confidence <= 0.4
    assert assessment.primary_case_ids == [] and assessment.matched_cases == [] and assessment.anchor_case_id is None
    assert assessment.explanation.startswith(NO_REFERENCE_PREFIX)
