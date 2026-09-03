import json
from pathlib import Path
from unittest.mock import patch

from agent.accident_classifier import classify_accident
from agent.intake_agent import IntakeAgent
from agent.question_generator import pick_target_slot
from agent.slot_extractor import extract_slots
from agent.state_manager import (
    activate_slots,
    check_intake_complete,
    get_missing_slots,
    update_state,
)
from schemas.accident_state import AccidentState, AccidentType


ROOT = Path(__file__).resolve().parent.parent


def test_taxonomy_matches_spec_targets():
    taxonomy = json.loads((ROOT / "config/accident_taxonomy.json").read_text("utf-8"))
    assert set(taxonomy) == {"차대차", "차대보행자", "차대자전거", "차대이륜차"}


def test_pydantic_defaults_are_not_shared():
    first = AccidentState()
    second = AccidentState()
    first.active_slots.append("ego_signal")
    first.accident_type.candidates.append("rear_end")
    assert second.active_slots == []
    assert second.accident_type.candidates == []


def test_state_merge_tracks_confidence_conflict_and_derived_signal_presence():
    state = AccidentState()
    update_state(
        state,
        {
            "accident_place": {
                "value": "사거리교차로(신호등 있음)",
                "confidence": 1.2,
            },
            "pre_collision_sudden_braking": False,
            "ego_signal": {"value": "녹색", "confidence": 0.98},
        },
    )
    assert state.place_signal_presence.value == "있음"
    assert state.place_signal_presence.source == "derived"
    assert state.pre_collision_sudden_braking.value == "아니오"
    assert state.accident_place.confidence == 1.0

    update_state(state, {"place_signal_presence": "없음"})
    assert state.place_signal_presence.value == "없음"
    assert state.place_signal_presence.conflict is True
    assert state.place_signal_presence.previous_value == "있음"

    update_state(state, {"ego_signal": {"value": "황색", "confidence": 0.6}})
    assert state.ego_signal.conflict is True
    assert state.ego_signal.previous_value == "녹색"
    assert state.ego_signal.value == "황색"


def test_intersection_slots_change_after_signal_presence_is_known():
    state = AccidentState(
        accident_type=AccidentType(
            family="intersection_collision", status="partially_confirmed"
        )
    )
    update_state(
        state,
        {
            "accident_target": "차대차",
            "accident_place": "사거리교차로",
            "ego_maneuver": "직진",
            "opponent_maneuver": "좌회전",
            "ego_collision_area": "우측 측면",
            "intersection_entry_order": "자차",
        },
    )
    active = activate_slots(state)
    assert "place_signal_presence" in active
    assert "ego_signal" not in active
    assert "left_turn_type" not in active

    update_state(state, {"place_signal_presence": "있음"})
    active = activate_slots(state)
    assert {"ego_signal", "opponent_signal", "left_turn_type"} <= set(active)
    assert check_intake_complete(state, get_missing_slots(state, active)) is False

    update_state(
        state,
        {"ego_signal": "녹색", "opponent_signal": "녹색", "left_turn_type": "비보호"},
    )
    active = activate_slots(state)
    assert check_intake_complete(state, get_missing_slots(state, active)) is True


def test_pedestrian_uses_pedestrian_state_not_opponent_vehicle_maneuver():
    state = AccidentState(
        accident_type=AccidentType(
            family="vehicle_pedestrian", status="partially_confirmed"
        )
    )
    update_state(state, {"accident_target": "차대보행자"})
    active = activate_slots(state)
    assert "pedestrian_crossing_state" in active
    assert "opponent_maneuver" not in active


def test_classifier_has_safe_fallback_for_clear_lane_change():
    state = AccidentState()
    update_state(state, {"accident_target": "차대차", "opponent_maneuver": "차선변경"})
    with patch(
        "agent.accident_classifier.call_json",
        return_value={"family": None, "status": "uncertain", "candidates": []},
    ):
        accident_type = classify_accident(state)
    assert accident_type.family == "lane_change"
    assert accident_type.status == "partially_confirmed"


def test_extractor_preserves_generic_place_and_maps_unknown_short_answer():
    state = AccidentState()
    model_response = {
        "slots": {
            "accident_target": "차대차",
            "accident_place": "사거리교차로(신호등 없음)",
        },
        "confidences": {"accident_place": 0.7},
    }
    with patch("agent.slot_extractor.call_json", return_value=model_response):
        extracted = extract_slots(state, "교차로에서 차랑 부딪혔어요")
    assert extracted["accident_place"]["value"] == "사거리교차로"

    with patch("agent.slot_extractor.call_json", return_value={"slots": {}}):
        extracted = extract_slots(state, "모르겠어요", target_slot="opponent_signal")
    assert extracted["opponent_signal"]["value"] == "모름"


def test_extractor_normalizes_explicit_lane_change_and_rear_end_language():
    with patch("agent.slot_extractor.call_json", return_value={"slots": {}}):
        lane_change = extract_slots(
            AccidentState(), "옆차가 갑자기 제 차선으로 들어오면서 부딪혔습니다."
        )
        rear_end = extract_slots(AccidentState(), "정차했는데 뒤차가 박았습니다.")
    assert lane_change["opponent_maneuver"]["value"] == "차선변경"
    assert rear_end["opponent_maneuver"]["value"] == "후방에서 추돌"


def test_empty_missing_slots_gets_classification_question_instead_of_crashing():
    state = AccidentState()
    assert pick_target_slot(state, []) == "accident_type_clarification"


def test_intake_passes_previous_question_target_to_next_extraction():
    observed_targets = []

    def fake_extract(state, user_input, target_slot=None):
        observed_targets.append(target_slot)
        if len(observed_targets) == 1:
            return {
                "accident_target": "차대차",
                "accident_place": "직선 도로",
                "ego_maneuver": "직진",
                "opponent_maneuver": "차선변경",
                "ego_collision_area": "우측 측면",
            }
        return {"turn_signal": "예"}

    fixed_type = AccidentType(family="lane_change", status="partially_confirmed")
    with (
        patch("agent.intake_agent.extract_slots", side_effect=fake_extract),
        patch("agent.intake_agent.classify_accident", return_value=fixed_type),
        patch("agent.intake_agent.generate_next_question", return_value="깜빡이를 켰나요?"),
    ):
        agent = IntakeAgent()
        first = agent.process("옆차가 들어왔어요")
        agent.process("네")

    assert first["debug"]["next_question_target"] == "turn_signal"
    assert observed_targets == [None, "turn_signal"]


def test_unknown_value_resolves_a_required_slot_for_completion():
    state = AccidentState(
        accident_type=AccidentType(family="road_entry", status="partially_confirmed")
    )
    update_state(
        state,
        {
            "accident_target": "차대차",
            "accident_place": "차도와 차도가 아닌 장소",
            "ego_maneuver": "직진",
            "opponent_maneuver": "모름",
            "ego_collision_area": "전면",
        },
    )
    active = activate_slots(state)
    assert check_intake_complete(state, get_missing_slots(state, active)) is True
