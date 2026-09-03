import json
from pathlib import Path
from typing import List

from agent.llm_client import call_text
from schemas.accident_state import AccidentState
from schemas.slot_schema import ALL_SLOTS, slot_description, slot_priority

_ROOT = Path(__file__).resolve().parent.parent
_PROMPT_PATH = _ROOT / "prompts" / "generate_question.txt"

with open(_PROMPT_PATH, "r", encoding="utf-8") as f:
    _PROMPT_TEMPLATE = f.read()

def _state_summary(state: AccidentState) -> dict:
    summary = {}
    for slot_name in ALL_SLOTS:
        summary[slot_name] = getattr(state, slot_name).value
    return summary


def pick_target_slot(state: AccidentState, missing_slots: List[str]) -> str:
    """Pick which missing slot to ask about next.

    Per spec section 12/17: when the accident type is still ambiguous with
    multiple candidates, prefer a slot that helps disambiguate the type over
    a plain priority sort. Otherwise fall back to the fixed priority table.
    """
    accident_type = state.accident_type

    if accident_type.status == "uncertain" or (
        not accident_type.family and len(accident_type.candidates) > 1
    ):
        # accident_place / accident_target / maneuvers most directly narrow
        # down the family; prefer those among the missing slots if present.
        disambiguating_order = [
            "accident_place",
            "accident_target",
            "ego_maneuver",
            "opponent_maneuver",
        ]
        for slot_name in disambiguating_order:
            if slot_name in missing_slots:
                return slot_name

    if missing_slots:
        return max(missing_slots, key=slot_priority)

    # This can happen when all known slots are filled but classification is
    # still ambiguous. Ask a classification question instead of crashing on
    # max([]).
    return "accident_type_clarification"


def generate_next_question(
    state: AccidentState,
    missing_slots: List[str],
    history: list,
    target_slot: str | None = None,
) -> str:
    target_slot = target_slot or pick_target_slot(state, missing_slots)

    recent_history = history[-6:]

    prompt = _PROMPT_TEMPLATE.format(
        current_state=json.dumps(_state_summary(state), ensure_ascii=False),
        accident_type=json.dumps(state.accident_type.model_dump(), ensure_ascii=False),
        active_slots=json.dumps(state.active_slots, ensure_ascii=False),
        missing_slots=json.dumps(missing_slots, ensure_ascii=False),
        target_slot=target_slot,
        target_slot_description=slot_description(target_slot)
        or "현재 후보 중 사고 유형을 구분할 수 있는 구체적인 발생 방식",
        candidates=json.dumps(state.accident_type.candidates, ensure_ascii=False),
        history=json.dumps(recent_history, ensure_ascii=False),
    )

    return call_text(prompt, model_env_var="INTAKE_MODEL")
