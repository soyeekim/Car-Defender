import json
from pathlib import Path

from agent.llm_client import call_json
from schemas.accident_state import UNKNOWN_MARKERS, AccidentState
from schemas.slot_schema import ALL_SLOTS, SLOT_DESCRIPTIONS

_ROOT = Path(__file__).resolve().parent.parent
_PROMPT_PATH = _ROOT / "prompts" / "extract_slots.txt"
_TAXONOMY_PATH = _ROOT / "config" / "accident_taxonomy.json"

with open(_PROMPT_PATH, "r", encoding="utf-8") as f:
    _PROMPT_TEMPLATE = f.read()

with open(_TAXONOMY_PATH, "r", encoding="utf-8") as f:
    _TAXONOMY = json.load(f)

_BOOLEAN_SLOTS = {
    "place_signal_presence",
    "pre_collision_sudden_braking",
    "post_collision_stop",
    "turn_signal",
}


def _state_summary(state: AccidentState) -> dict:
    summary = {}
    for slot_name in ALL_SLOTS:
        summary[slot_name] = getattr(state, slot_name).value
    return summary


def _explicit_target(user_input: str):
    if any(word in user_input for word in ("오토바이", "이륜차", "스쿠터")):
        return "차대이륜차"
    if "자전거" in user_input:
        return "차대자전거"
    if any(word in user_input for word in ("보행자", "사람을", "사람과", "사람이랑")):
        return "차대보행자"
    if any(word in user_input for word in ("상대차", "뒤차", "옆차", "차량", "차랑")):
        return "차대차"
    return None


def _explicit_generic_place(user_input: str):
    has_signal_fact = any(
        word in user_input
        for word in (
            "신호등이 있",
            "신호등 있",
            "신호등이 없",
            "신호등 없",
            "빨간불",
            "녹색불",
            "적색 신호",
            "녹색 신호",
            "황색 신호",
        )
    )
    if "횡단보도" in user_input and not has_signal_fact:
        return "횡단보도"
    if (
        "교차로" in user_input
        and "회전교차로" not in user_input
        and "T자" not in user_input
        and not has_signal_fact
    ):
        return "사거리교차로"
    return None


def _explicit_maneuvers(user_input: str) -> dict:
    maneuvers = {}
    collision_words = ("박", "받", "부딪", "추돌")
    if "뒤차" in user_input and any(word in user_input for word in collision_words):
        maneuvers["opponent_maneuver"] = "후방에서 추돌"
    if (
        any(word in user_input for word in ("상대차", "옆차", "상대 차량"))
        and any(word in user_input for word in ("제 차선", "내 차선"))
        and any(word in user_input for word in ("들어", "차선변경", "차선 변경"))
    ):
        maneuvers["opponent_maneuver"] = "차선변경"
    return maneuvers


def _normalize_response(raw: dict) -> dict:
    raw_slots = raw.get("slots", raw)
    confidences = raw.get("confidences", {})
    if not isinstance(raw_slots, dict):
        return {}
    if not isinstance(confidences, dict):
        confidences = {}

    extracted = {}
    for slot_name, raw_value in raw_slots.items():
        if slot_name not in ALL_SLOTS or raw_value is None:
            continue
        if isinstance(raw_value, dict):
            value = raw_value.get("value")
            confidence = raw_value.get("confidence")
        else:
            value = raw_value
            confidence = confidences.get(slot_name)
        if value is not None:
            extracted[slot_name] = {"value": value, "confidence": confidence}
    return extracted


def _validate_taxonomy(extracted: dict, state: AccidentState) -> None:
    target_item = extracted.get("accident_target")
    target = target_item.get("value") if target_item else state.accident_target.value
    if target_item and target not in _TAXONOMY and target not in UNKNOWN_MARKERS:
        extracted.pop("accident_target")
        target = state.accident_target.value

    place_item = extracted.get("accident_place")
    if not place_item:
        return
    place = place_item.get("value")
    valid_places = set(_TAXONOMY.get(target, [])) if target else {
        item for places in _TAXONOMY.values() for item in places
    }
    if place not in valid_places and place not in UNKNOWN_MARKERS:
        extracted.pop("accident_place")


def extract_slots(
    state: AccidentState, user_input: str, target_slot: str | None = None
) -> dict:
    """Extract explicitly-stated accident slots from the user's latest message.

    Returns a dict of {slot_name: value} for slots the LLM was confident about.
    Slots absent from the response, or explicitly null, are left untouched by
    the caller (state_manager.update_state skips None values).
    """
    prompt = _PROMPT_TEMPLATE.format(
        slot_descriptions=json.dumps(SLOT_DESCRIPTIONS, ensure_ascii=False, indent=2),
        taxonomy=json.dumps(_TAXONOMY, ensure_ascii=False, indent=2),
        current_state=json.dumps(_state_summary(state), ensure_ascii=False),
        target_slot=target_slot or "없음 (첫 발화)",
        user_input=json.dumps(user_input, ensure_ascii=False),
    )

    raw = call_json(prompt, model_env_var="INTAKE_MODEL")
    if not isinstance(raw, dict):
        return {}

    extracted = _normalize_response(raw)

    # Deterministic guards prevent the model from changing an explicitly
    # generic place ("교차로") into an unsupported signal-presence guess.
    explicit_target = _explicit_target(user_input)
    if explicit_target:
        extracted["accident_target"] = {"value": explicit_target, "confidence": 1.0}
    explicit_place = _explicit_generic_place(user_input)
    if explicit_place:
        extracted["accident_place"] = {"value": explicit_place, "confidence": 1.0}
    for slot_name, value in _explicit_maneuvers(user_input).items():
        extracted[slot_name] = {"value": value, "confidence": 1.0}

    stripped = user_input.strip().lower().rstrip(".!?")
    if target_slot in ALL_SLOTS and stripped in UNKNOWN_MARKERS:
        extracted[target_slot] = {"value": "모름", "confidence": 1.0}
    elif target_slot in _BOOLEAN_SLOTS and target_slot not in extracted:
        if stripped in {"네", "예", "맞아요", "있어요", "있음"}:
            value = "있음" if target_slot == "place_signal_presence" else "예"
            extracted[target_slot] = {"value": value, "confidence": 1.0}
        elif stripped in {"아니요", "아뇨", "없어요", "없음"}:
            value = "없음" if target_slot == "place_signal_presence" else "아니오"
            extracted[target_slot] = {"value": value, "confidence": 1.0}

    _validate_taxonomy(extracted, state)
    return extracted
