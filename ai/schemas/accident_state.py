from typing import List, Optional

from pydantic import BaseModel, Field


class SlotValue(BaseModel):
    value: Optional[str] = None
    source: Optional[str] = None
    confidence: Optional[float] = None
    conflict: bool = False
    previous_value: Optional[str] = None


class AccidentType(BaseModel):
    family: Optional[str] = None
    subtype: Optional[str] = None
    status: str = "uncertain"
    candidates: List[str] = Field(default_factory=list)


class AccidentState(BaseModel):
    accident_target: SlotValue = Field(default_factory=SlotValue)
    accident_place: SlotValue = Field(default_factory=SlotValue)
    place_signal_presence: SlotValue = Field(default_factory=SlotValue)

    ego_maneuver: SlotValue = Field(default_factory=SlotValue)
    opponent_maneuver: SlotValue = Field(default_factory=SlotValue)

    ego_lane: SlotValue = Field(default_factory=SlotValue)
    opponent_lane: SlotValue = Field(default_factory=SlotValue)

    ego_signal: SlotValue = Field(default_factory=SlotValue)
    opponent_signal: SlotValue = Field(default_factory=SlotValue)
    pedestrian_signal: SlotValue = Field(default_factory=SlotValue)

    ego_speed: SlotValue = Field(default_factory=SlotValue)
    opponent_speed: SlotValue = Field(default_factory=SlotValue)

    ego_collision_area: SlotValue = Field(default_factory=SlotValue)
    opponent_collision_area: SlotValue = Field(default_factory=SlotValue)

    pre_collision_sudden_braking: SlotValue = Field(default_factory=SlotValue)
    post_collision_stop: SlotValue = Field(default_factory=SlotValue)
    speed_change_before_collision: SlotValue = Field(default_factory=SlotValue)
    braking_evidence: SlotValue = Field(default_factory=SlotValue)
    braking_reason: SlotValue = Field(default_factory=SlotValue)
    turn_signal: SlotValue = Field(default_factory=SlotValue)
    lane_change_direction: SlotValue = Field(default_factory=SlotValue)

    left_turn_type: SlotValue = Field(default_factory=SlotValue)
    intersection_entry_order: SlotValue = Field(default_factory=SlotValue)
    pedestrian_crossing_state: SlotValue = Field(default_factory=SlotValue)

    collision_type: SlotValue = Field(default_factory=SlotValue)
    collision_timestamp: SlotValue = Field(default_factory=SlotValue)

    opponent_claimed_fault_ratio: SlotValue = Field(default_factory=SlotValue)

    accident_type: AccidentType = Field(default_factory=AccidentType)

    active_slots: List[str] = Field(default_factory=list)
    missing_slots: List[str] = Field(default_factory=list)


# Slots the user can legitimately not know. Any other value is a taxonomy string.
UNKNOWN_MARKERS = {
    "모름",
    "unknown",
    "모르겠음",
    "모르겠어요",
    "모릅니다",
    "잘 모르겠어요",
}


def is_filled(slot: SlotValue) -> bool:
    return slot.value is not None
