"""평가 데이터셋 annotation 스키마 (가이드 42, 49.7, 102~103절)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class VideoGroundTruth(BaseModel):
    vehicle_count: Optional[int] = None
    collision_pair: list[str] = Field(default_factory=list, description="예: ['vehicle_1', 'vehicle_3']")
    non_participants: list[str] = Field(default_factory=list)
    collision_timestamp_sec: Optional[float] = None
    road_type: Optional[str] = None
    signal_present: Optional[bool] = None
    signal_state: Optional[str] = None
    ego_direction: Optional[str] = None
    other_direction: Optional[str] = None
    collision: Optional[str] = None
    ego_entered_first: Optional[bool] = None
    fault_relevant_facts: list[str] = Field(default_factory=list)


class VideoAnnotation(BaseModel):
    video_id: str
    video_path: str
    description: str = ""
    ground_truth: VideoGroundTruth = Field(default_factory=VideoGroundTruth)


class RagAnnotation(BaseModel):
    case_id: str
    query_state_path: Optional[str] = None
    structured_query: str = ""
    detailed_query: str = ""
    relevant_case_ids: list[str] = Field(default_factory=list, description="사람이 찾아둔 정답 심의번호")


class AssessmentAnnotation(BaseModel):
    case_id: str
    expected_ratio: str = Field(description="예: '20:80' (user:opponent)")
    state_path: Optional[str] = None
    facts: list[str] = Field(default_factory=list)


class VideoEvalDataset(BaseModel):
    items: list[VideoAnnotation] = Field(default_factory=list)


class RagEvalDataset(BaseModel):
    items: list[RagAnnotation] = Field(default_factory=list)


class AssessmentEvalDataset(BaseModel):
    items: list[AssessmentAnnotation] = Field(default_factory=list)
