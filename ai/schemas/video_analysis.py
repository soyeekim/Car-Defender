from typing import Literal, Optional

from pydantic import BaseModel, Field


class VisionSlotValue(BaseModel):
    value: Optional[str] = Field(
        default=None,
        description="영상에서 관찰 가능한 값. 확인할 수 없으면 null",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="해당 관찰값의 영상 근거 신뢰도. 값이 null이면 0",
    )
    evidence: Optional[str] = Field(
        default=None,
        description="값을 뒷받침하는 화면상 근거",
    )
    timestamp: Optional[str] = Field(
        default=None,
        description="근거를 가장 잘 확인할 수 있는 MM:SS 형식 시각",
    )
    observation_type: Literal[
        "direct_visual", "sensor_readout", "inferred", "not_observable"
    ] = Field(
        default="not_observable",
        description="직접 관찰, 센서 판독, 추론, 관찰 불가 중 하나",
    )
    temporal_scope: Literal[
        "pre_collision", "at_collision", "post_collision", "general", "unknown"
    ] = Field(default="unknown", description="해당 관찰값이 속하는 사고 시간 구간")


class ObservedEvent(BaseModel):
    event_id: str = Field(description="영상 안에서 고유한 사건 ID")
    description: str = Field(description="화면에서 관찰된 사건 설명")
    actors: list[str] = Field(
        default_factory=list,
        description="사건에 관여한 주체: ego/opponent/pedestrian 등",
    )
    start_timestamp: Optional[str] = None
    end_timestamp: Optional[str] = None
    temporal_scope: Literal[
        "pre_collision", "at_collision", "post_collision", "general", "unknown"
    ] = "unknown"
    observation_type: Literal[
        "direct_visual", "sensor_readout", "inferred", "not_observable"
    ] = "not_observable"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: Optional[str] = None
    attributes: dict[str, str] = Field(
        default_factory=dict,
        description="고정 Slot에 없는 차종, 도로 구조, 장애물 등 추가 관찰 속성",
    )


class AdditionalVisionFact(VisionSlotValue):
    fact_key: str = Field(description="snake_case 형식의 범용 사실 키")
    fact_label: str = Field(description="사람이 이해할 수 있는 사실 이름")


class VideoAnalysisResult(BaseModel):
    summary: str = Field(description="확인된 사실만 사용한 짧은 사고 영상 요약")

    accident_target: VisionSlotValue = Field(default_factory=VisionSlotValue)
    accident_place: VisionSlotValue = Field(default_factory=VisionSlotValue)
    place_signal_presence: VisionSlotValue = Field(default_factory=VisionSlotValue)

    ego_maneuver: VisionSlotValue = Field(default_factory=VisionSlotValue)
    opponent_maneuver: VisionSlotValue = Field(default_factory=VisionSlotValue)
    ego_lane: VisionSlotValue = Field(default_factory=VisionSlotValue)
    opponent_lane: VisionSlotValue = Field(default_factory=VisionSlotValue)

    ego_signal: VisionSlotValue = Field(default_factory=VisionSlotValue)
    opponent_signal: VisionSlotValue = Field(default_factory=VisionSlotValue)
    pedestrian_signal: VisionSlotValue = Field(default_factory=VisionSlotValue)

    ego_speed: VisionSlotValue = Field(default_factory=VisionSlotValue)
    opponent_speed: VisionSlotValue = Field(default_factory=VisionSlotValue)
    ego_collision_area: VisionSlotValue = Field(default_factory=VisionSlotValue)
    opponent_collision_area: VisionSlotValue = Field(default_factory=VisionSlotValue)

    pre_collision_sudden_braking: VisionSlotValue = Field(
        default_factory=VisionSlotValue
    )
    post_collision_stop: VisionSlotValue = Field(default_factory=VisionSlotValue)
    speed_change_before_collision: VisionSlotValue = Field(
        default_factory=VisionSlotValue
    )
    braking_evidence: VisionSlotValue = Field(default_factory=VisionSlotValue)
    braking_reason: VisionSlotValue = Field(default_factory=VisionSlotValue)
    turn_signal: VisionSlotValue = Field(default_factory=VisionSlotValue)
    lane_change_direction: VisionSlotValue = Field(default_factory=VisionSlotValue)
    left_turn_type: VisionSlotValue = Field(default_factory=VisionSlotValue)
    intersection_entry_order: VisionSlotValue = Field(default_factory=VisionSlotValue)
    pedestrian_crossing_state: VisionSlotValue = Field(default_factory=VisionSlotValue)

    collision_type: VisionSlotValue = Field(default_factory=VisionSlotValue)
    collision_timestamp: VisionSlotValue = Field(default_factory=VisionSlotValue)

    observable_events: list[str] = Field(default_factory=list)
    timeline_events: list[ObservedEvent] = Field(default_factory=list)
    additional_observations: list[AdditionalVisionFact] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


VIDEO_STATE_SLOTS = (
    "accident_target",
    "accident_place",
    "place_signal_presence",
    "ego_maneuver",
    "opponent_maneuver",
    "ego_lane",
    "opponent_lane",
    "ego_signal",
    "opponent_signal",
    "pedestrian_signal",
    "ego_speed",
    "opponent_speed",
    "ego_collision_area",
    "opponent_collision_area",
    "pre_collision_sudden_braking",
    "post_collision_stop",
    "speed_change_before_collision",
    "braking_evidence",
    "braking_reason",
    "turn_signal",
    "lane_change_direction",
    "left_turn_type",
    "intersection_entry_order",
    "pedestrian_crossing_state",
    "collision_type",
    "collision_timestamp",
)
