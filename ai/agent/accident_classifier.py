import json
from pathlib import Path

from agent.llm_client import call_json
from schemas.accident_state import AccidentState, AccidentType
from schemas.slot_schema import ALL_SLOTS

_ROOT = Path(__file__).resolve().parent.parent
_PROMPT_PATH = _ROOT / "prompts" / "classify_accident.txt"

with open(_PROMPT_PATH, "r", encoding="utf-8") as f:
    _PROMPT_TEMPLATE = f.read()

_VALID_FAMILIES = {
    "rear_end",
    "intersection_collision",
    "lane_change",
    "vehicle_pedestrian",
    "road_entry",
}


def _rule_based_family(state: AccidentState):
    target = state.accident_target.value or ""
    place = state.accident_place.value or ""
    maneuvers = " ".join(
        value
        for value in (state.ego_maneuver.value, state.opponent_maneuver.value)
        if value
    )

    if target == "차대보행자":
        return "vehicle_pedestrian", "vehicle_vs_pedestrian"
    if "차선변경" in maneuvers or "차선 변경" in maneuvers:
        return "lane_change", "lane_change_collision"
    if "추돌" in maneuvers or "후방" in maneuvers:
        return "rear_end", "rear_end_collision"
    if "교차로" in place:
        return "intersection_collision", None
    return None, None


def _state_summary(state: AccidentState) -> dict:
    summary = {}
    for slot_name in ALL_SLOTS:
        summary[slot_name] = getattr(state, slot_name).value
    return summary


def classify_accident(state: AccidentState) -> AccidentType:
    previous = state.accident_type

    prompt = _PROMPT_TEMPLATE.format(
        current_state=json.dumps(_state_summary(state), ensure_ascii=False),
        previous_accident_type=json.dumps(previous.model_dump(), ensure_ascii=False),
    )

    raw = call_json(prompt, model_env_var="INTAKE_MODEL")

    family = raw.get("family")
    if family not in _VALID_FAMILIES:
        family = None

    raw_candidates = raw.get("candidates", [])
    if not isinstance(raw_candidates, list):
        raw_candidates = []
    candidates = [c for c in raw_candidates if c in _VALID_FAMILIES][:3]

    status = raw.get("status", "uncertain")
    if status not in {"uncertain", "partially_confirmed", "confirmed"}:
        status = "uncertain"

    subtype = raw.get("subtype")
    if subtype is not None and not isinstance(subtype, str):
        subtype = None

    inferred_family, inferred_subtype = _rule_based_family(state)
    strong_inference = inferred_family in {
        "rear_end",
        "lane_change",
        "vehicle_pedestrian",
    }
    if inferred_family and (family is None or strong_inference):
        family = inferred_family
        subtype = subtype or inferred_subtype
        status = "partially_confirmed" if status == "uncertain" else status
        candidates = [family] + [c for c in candidates if c != family]
        candidates = candidates[:3]

    # Sticky classification safety net: if a family was already confirmed or
    # partially confirmed in a previous turn, and this turn's result dropped
    # it to None without a competing family taking its place, treat that as
    # LLM noise rather than a real reversal and keep the previous judgment.
    previously_locked_in = previous.family and previous.status in (
        "confirmed",
        "partially_confirmed",
    )
    if previously_locked_in and family is None:
        family = previous.family
        subtype = subtype or previous.subtype
        status = previous.status
        if family not in candidates:
            candidates = ([family] + candidates)[:3]

    return AccidentType(
        family=family,
        subtype=subtype,
        status=status,
        candidates=candidates,
    )
