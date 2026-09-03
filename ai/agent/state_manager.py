"""Owns AccidentState mutations: merging extraction results, tracking conflicts,
computing active/missing slots, and deciding when intake is complete.
"""

import json
from pathlib import Path
from typing import List, Optional

from schemas.accident_state import AccidentState, SlotValue
from schemas.slot_schema import ALL_SLOTS, COMMON_SLOTS

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

with open(_CONFIG_DIR / "slot_rules.json", "r", encoding="utf-8") as f:
    SLOT_RULES = json.load(f)


def initialize_state() -> AccidentState:
    return AccidentState()


def _normalize_value(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None


def _set_slot(
    state: AccidentState,
    slot_name: str,
    value,
    source: str,
    confidence: Optional[float] = None,
) -> None:
    value = _normalize_value(value)
    if value is None:
        return

    existing = getattr(state, slot_name)
    conflict = existing.value is not None and existing.value != value
    setattr(
        state,
        slot_name,
        SlotValue(
            value=value,
            source=source,
            confidence=confidence,
            conflict=conflict,
            previous_value=existing.value if conflict else None,
        ),
    )


def update_state(
    state: AccidentState, extracted: dict, source: str = "user"
) -> AccidentState:
    """Merge newly extracted slot values into the existing state.

    A new value always overwrites the old one (per spec section 19: silently
    dropping the newer statement is worse than surfacing a conflict), but if
    the old value was already filled and differs from the new one, the slot
    is flagged as a conflict and the previous value is preserved for review.
    """
    updated_slots = set()
    for slot_name, extracted_value in extracted.items():
        if slot_name not in ALL_SLOTS or extracted_value is None:
            continue

        confidence = None
        value = extracted_value
        if isinstance(extracted_value, dict):
            value = extracted_value.get("value")
            confidence = extracted_value.get("confidence")
            if confidence is not None:
                try:
                    confidence = max(0.0, min(1.0, float(confidence)))
                except (TypeError, ValueError):
                    confidence = None

        _set_slot(state, slot_name, value, source, confidence)
        if _normalize_value(value) is not None:
            updated_slots.add(slot_name)

    # Keep the independently queryable signal-presence slot consistent with
    # taxonomy values that explicitly contain that fact.
    place = state.accident_place.value or ""
    should_derive_presence = (
        "place_signal_presence" not in updated_slots
        and (
            "accident_place" in updated_slots
            or state.place_signal_presence.value is None
        )
    )
    if should_derive_presence:
        if "신호등 있음" in place:
            _set_slot(state, "place_signal_presence", "있음", "derived", 1.0)
        elif "신호등 없음" in place:
            _set_slot(state, "place_signal_presence", "없음", "derived", 1.0)

    return state


def _has_left_turn(state: AccidentState) -> bool:
    maneuvers = (state.ego_maneuver.value, state.opponent_maneuver.value)
    return any(value and "좌회전" in value for value in maneuvers)


def _signals_exist(state: AccidentState) -> bool:
    value = (state.place_signal_presence.value or "").replace(" ", "")
    return value in {"있음", "예", "네", "유", "신호등있음"}


def required_slots_for_state(state: AccidentState, family: str) -> List[str]:
    rule = SLOT_RULES.get(family)
    if not rule:
        return []

    required = list(rule["required_slots"])

    if family == "intersection_collision":
        if _signals_exist(state):
            required.extend(["ego_signal", "opponent_signal"])
        if _has_left_turn(state) and _signals_exist(state):
            required.append("left_turn_type")
        required.append("intersection_entry_order")
        if state.accident_target.value in {"차대자전거", "차대이륜차"}:
            required.extend(["ego_lane", "opponent_lane"])

    if family == "vehicle_pedestrian" and _signals_exist(state):
        required.extend(["ego_signal", "pedestrian_signal"])

    return list(dict.fromkeys(required))


def activate_slots(state: AccidentState) -> List[str]:
    """Common slots are always active. Conditional slots are activated once
    an accident family is known; if the family is still ambiguous, the union
    of required slots across all candidates is activated so that answers
    help narrow the classification down.
    """
    active = list(COMMON_SLOTS)

    # A pedestrian's behavior is captured by pedestrian_crossing_state, not
    # by the vehicle-oriented opponent_maneuver slot.
    if state.accident_target.value == "차대보행자":
        active.remove("opponent_maneuver")

    accident_type = state.accident_type
    families = []
    if accident_type.family:
        families = [accident_type.family]
    elif accident_type.candidates:
        families = accident_type.candidates

    for family in families:
        active.extend(required_slots_for_state(state, family))

    return list(dict.fromkeys(active))


def get_missing_slots(state: AccidentState, active_slots: List[str]) -> List[str]:
    missing = []
    for slot_name in active_slots:
        slot = getattr(state, slot_name)
        if slot.value is None:
            missing.append(slot_name)
    return missing


def check_intake_complete(state: AccidentState, missing_slots: List[str]) -> bool:
    accident_type = state.accident_type

    # Condition 1: accident type must be determinable.
    if not accident_type.family or accident_type.status == "uncertain":
        return False

    # Condition 2: the family's core required slots must be sufficiently filled.
    required = required_slots_for_state(state, accident_type.family)
    if not required:
        return False

    for slot_name in required:
        slot = getattr(state, slot_name)
        if slot.value is None:
            return False
        # Condition 4: user explicitly said they don't know — treat as resolved.
        # (value is kept as the unknown marker itself, not blocking completion)

    return True
