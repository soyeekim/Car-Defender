"""Video Analysis 공통 출력 스키마 (가이드 11.3 / 13.3 / 47 / 52절 통합).

Gemini Native 경로와 GPT Frames 경로는 서로 다른 모델을 쓰더라도
동일한 `VideoResult`를 반환한다. LLM이 직접 생성하는 부분은
`VideoObservation`이고, backend 메타데이터·검증 결과는 코드가 채운다.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

ObservationStatus = Literal["CONFIRMED", "INFERRED", "UNKNOWN"]


class LenientModel(BaseModel):
    """LLM이 필드에 null을 넣어 보내면(예: 비당사 차량의 lane_change: null) 키를 버려 기본값을 쓴다.

    이 한 줄이 없으면 pydantic 검증 실패 → repair 재호출(수십 초)이 발생한다.
    """

    @model_validator(mode="before")
    @classmethod
    def _drop_nulls(cls, data):
        if isinstance(data, dict):
            return {key: value for key, value in data.items() if value is not None}
        return data


VideoBackend = Literal["gemini_native", "gpt_frames", "merged", "unavailable", "fake"]
CompletionStatus = Literal["VIDEO_ANALYSIS_COMPLETE", "VIDEO_NEEDS_RECHECK"]


class Observation(LenientModel):
    value: Optional[str] = Field(default=None, description="관찰 값. 확인 불가 시 null")
    status: ObservationStatus = Field(default="UNKNOWN")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: Optional[str] = Field(default=None, description="화면상 근거")
    timestamp: Optional[str] = Field(default=None, description="근거 시각 MM:SS.s")

    def is_known(self) -> bool:
        return self.value is not None and self.status != "UNKNOWN"


class SignalObservation(LenientModel):
    time: Optional[str] = Field(default=None, description="관찰 시각 MM:SS.s")
    applies_to: str = Field(default="unknown", description="어느 차량/방향용 신호인지 (vehicle ID 또는 방향)")
    color: Optional[str] = Field(default=None, description="green | yellow | red | green_arrow | unknown")
    status: ObservationStatus = "UNKNOWN"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    note: Optional[str] = None


class RoadEnvironment(LenientModel):
    road_type: Observation = Field(default_factory=Observation, description="straight | intersection | t_intersection | roundabout | parking_lot | highway | alley | ramp | other")
    intersection_type: Observation = Field(default_factory=Observation, description="four_way | three_way | roundabout | none | unknown")
    lane_count: Observation = Field(default_factory=Observation)
    lane_description: str = Field(default="", description="차선 구성, 방향, 전용차로 설명")
    center_line: Observation = Field(default_factory=Observation)
    stop_line: Observation = Field(default_factory=Observation)
    crosswalk: Observation = Field(default_factory=Observation)
    signal_present: Observation = Field(default_factory=Observation, description="true | false")
    signal_observations: list[SignalObservation] = Field(default_factory=list)
    lane_marking_at_lane_change: Observation = Field(default_factory=Observation, description="차로 변경/접촉 지점의 차선 종류: solid | dashed | double | none | unknown")
    turn_only_lanes: Observation = Field(default_factory=Observation)
    merge_or_split: Observation = Field(default_factory=Observation)
    road_surface: Observation = Field(default_factory=Observation)
    weather: Observation = Field(default_factory=Observation)
    lighting: Observation = Field(default_factory=Observation, description="day | night | dusk")


class VehicleEntry(LenientModel):
    id: str = Field(description="vehicle_1, vehicle_2 ... 형식의 고정 ID")
    description: str = Field(default="", description="색상, 차종, 최초 등장 위치 등 식별 특징")
    vehicle_type: Optional[str] = None
    color: Optional[str] = None
    is_ego: bool = Field(default=False, description="블랙박스 촬영 차량 여부")
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    first_seen_position: Optional[str] = None
    movement: Optional[str] = Field(default=None, description="straight | left_turn | right_turn | u_turn | lane_change_left | lane_change_right | reversing | stopped | parking | unknown")
    entry_direction: Optional[str] = Field(default=None, description="상대 차량 기준 등장/진입 방향 (예: right_side_road, opposite, same_direction_front)")
    lane: Optional[str] = None
    lane_change: Observation = Field(default_factory=Observation)
    turn_signal: Observation = Field(default_factory=Observation)
    braking: Observation = Field(default_factory=Observation)
    stopped_before_collision: Observation = Field(default_factory=Observation)
    entered_intersection_first: Observation = Field(default_factory=Observation)
    speed_qualitative: Observation = Field(default_factory=Observation, description="stopped | slow | normal | fast | 급가속 | 급감속")
    overtaking: Observation = Field(default_factory=Observation)


class VehiclePosition(LenientModel):
    vehicle_id: str
    position: str


class TrackingSnapshot(LenientModel):
    time: str
    positions: list[VehiclePosition] = Field(default_factory=list)


class CollisionWindow(LenientModel):
    start: Optional[str] = None
    end: Optional[str] = None
    most_likely_timestamp: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)

    def detected(self) -> bool:
        return bool(self.most_likely_timestamp or (self.start and self.end))


class CollisionPair(LenientModel):
    participants: list[str] = Field(default_factory=list)
    non_participants: list[str] = Field(default_factory=list)
    timestamp: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasoning: list[str] = Field(default_factory=list)
    alternative_pairs: list[list[str]] = Field(default_factory=list)


class PairScore(LenientModel):
    pair: list[str] = Field(default_factory=list)
    collision_probability: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""


class ParticipantPart(LenientModel):
    vehicle_id: str
    part: str = Field(description="front | front_left | front_right | left_side | right_side | rear | rear_left | rear_right | unknown")


class CollisionDetail(LenientModel):
    timestamp: Optional[str] = None
    collision_type: Optional[str] = Field(default=None, description="예: front_to_left_side, rear_end, side_swipe, head_on")
    relative_direction: Optional[str] = Field(default=None, description="상대 차량이 자차 기준 어느 방향에서 접근했는지")
    participant_parts: list[ParticipantPart] = Field(default_factory=list)
    evasive_action: Observation = Field(default_factory=Observation)
    braking_before_collision: Observation = Field(default_factory=Observation)
    post_collision_movement: Optional[str] = None
    secondary_collision: Observation = Field(default_factory=Observation)


class TimelineEvent(LenientModel):
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    event: str = ""
    vehicles: list[str] = Field(default_factory=list)
    status: ObservationStatus = "CONFIRMED"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class FaultRelevantFactor(LenientModel):
    factor: str
    observability: ObservationStatus = "UNKNOWN"
    note: str = ""
    vehicle_ids: list[str] = Field(default_factory=list)


class DenseSamplingRecommendation(LenientModel):
    start_sec: float = Field(ge=0.0)
    end_sec: float = Field(ge=0.0)
    reason: str = ""


class VideoObservation(LenientModel):
    """LLM(Video Agent)이 직접 생성하는 관찰 결과."""

    short_summary: str = Field(default="", description="웹 표시용 2~4문장 요약")
    detailed_description: str = Field(default="", description="RAG·판정용 상세 사고 설명")
    road_environment: RoadEnvironment = Field(default_factory=RoadEnvironment)
    vehicles: list[VehicleEntry] = Field(default_factory=list)
    ego_vehicle_id: Optional[str] = Field(default=None, description="블랙박스 촬영 차량의 vehicle ID")
    vehicle_identity_consistent: bool = True
    tracking_timeline: list[TrackingSnapshot] = Field(default_factory=list)
    collision_window: CollisionWindow = Field(default_factory=CollisionWindow)
    collision_pair: CollisionPair = Field(default_factory=CollisionPair)
    pair_scores: list[PairScore] = Field(default_factory=list)
    collision: CollisionDetail = Field(default_factory=CollisionDetail)
    timeline: list[TimelineEvent] = Field(default_factory=list)
    confirmed_facts: list[str] = Field(default_factory=list)
    inferred_facts: list[str] = Field(default_factory=list)
    unknown_or_unobservable: list[str] = Field(default_factory=list)
    fault_relevant_factors: list[FaultRelevantFactor] = Field(default_factory=list)
    uncertain_facts: list[str] = Field(default_factory=list)
    recommended_reanalysis_targets: list[str] = Field(default_factory=list)
    recommended_dense_sampling: Optional[DenseSamplingRecommendation] = None
    changes_from_previous: list[str] = Field(default_factory=list, description="focus 재분석 시 기존 결론 대비 변경점")


class AnalysisPassRecord(LenientModel):
    pass_type: Literal["full", "focus", "tracking", "cache"] = "full"
    backend: VideoBackend = "gemini_native"
    model: str = ""
    prompt_version: str = ""
    focus: Optional[str] = None
    latency_sec: float = 0.0
    frame_count: int = 0
    token_usage: dict[str, int] = Field(default_factory=dict)


class AnalysisCompletion(LenientModel):
    status: CompletionStatus = "VIDEO_NEEDS_RECHECK"
    score: int = 0
    reasons: list[str] = Field(default_factory=list)
    gate: dict[str, bool] = Field(default_factory=dict)


class VideoResult(VideoObservation):
    """backend 무관 공통 Video Result."""

    video_backend: VideoBackend = "gemini_native"
    model: str = ""
    prompt_version: str = ""
    video_hash: str = ""
    video_path: Optional[str] = None
    duration_sec: Optional[float] = None
    analysis_passes: list[AnalysisPassRecord] = Field(default_factory=list)
    analysis_completion: AnalysisCompletion = Field(default_factory=AnalysisCompletion)
    fault_relevant_factors_checked: bool = False
    cached: bool = False

    @classmethod
    def from_observation(cls, observation: VideoObservation, **metadata) -> "VideoResult":
        return cls(**observation.model_dump(), **metadata)

    def vehicle_ids(self) -> list[str]:
        return [vehicle.id for vehicle in self.vehicles]

    def get_vehicle(self, vehicle_id: Optional[str]) -> Optional[VehicleEntry]:
        for vehicle in self.vehicles:
            if vehicle.id == vehicle_id:
                return vehicle
        return None

    def dashcam_vehicle_id(self) -> Optional[str]:
        if self.ego_vehicle_id:
            return self.ego_vehicle_id
        for vehicle in self.vehicles:
            if vehicle.is_ego:
                return vehicle.id
        return None

    def other_participant(self, vehicle_id: Optional[str]) -> Optional[str]:
        for participant in self.collision_pair.participants:
            if participant != vehicle_id:
                return participant
        return None


def observation_schema_for_llm() -> dict:
    """LLM에게 전달하는 출력 스키마 (메타데이터 필드 제외)."""
    return VideoObservation.model_json_schema()
