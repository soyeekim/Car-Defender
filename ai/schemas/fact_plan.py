from typing import Literal, Optional

from pydantic import BaseModel, Field


class AccidentHypothesis(BaseModel):
    family: str
    subtype: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class RequiredFactor(BaseModel):
    factor_id: str = Field(description="사고 내에서 안정적으로 유지할 snake_case ID")
    description: str
    category: Literal[
        "road_context",
        "actor_behavior",
        "traffic_control",
        "spatial_relationship",
        "temporal_sequence",
        "collision_geometry",
        "post_accident",
        "external_claim",
        "other",
    ] = "other"
    importance: Literal["critical", "high", "medium", "low"]
    reason: str = Field(description="과실 판단에 이 사실이 필요한 이유")
    preferred_source: Literal[
        "vision", "user", "vision_then_user", "external"
    ] = "vision_then_user"
    related_fact_keys: list[str] = Field(default_factory=list)
    video_recheck_instruction: Optional[str] = None
    question_hint: Optional[str] = None
    status: Literal[
        "confirmed_by_vision",
        "corroborated",
        "user_claimed",
        "disputed_vision_preferred",
        "inferred_by_vision",
        "not_visible",
        "disputed_unresolved",
        "unresolved",
        "external_required",
    ] = "unresolved"


class FactPlan(BaseModel):
    plan_summary: str
    accident_hypotheses: list[AccidentHypothesis] = Field(default_factory=list)
    required_factors: list[RequiredFactor] = Field(default_factory=list)


class FactorAnswer(BaseModel):
    factor_id: str
    value: str
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    related_fact_updates: dict[str, str] = Field(default_factory=dict)
