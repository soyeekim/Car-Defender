from typing import Literal, Optional

from pydantic import BaseModel, Field

from schemas.video_analysis import ObservedEvent, VisionSlotValue


class UserEvidence(BaseModel):
    value: str
    source: str = "user"
    confidence: Optional[float] = None


class FinalFact(BaseModel):
    slot: str
    resolved_value: Optional[str] = None
    status: Literal[
        "unresolved",
        "corroborated",
        "verified_by_vision",
        "vision_inferred",
        "user_claimed",
        "disputed_vision_preferred",
        "disputed_unresolved",
    ] = "unresolved"
    selected_source: Optional[Literal["vision", "user", "both"]] = None
    vision: Optional[VisionSlotValue] = None
    user: Optional[UserEvidence] = None
    resolution_reason: str


class EvidenceFusionResult(BaseModel):
    facts: dict[str, FinalFact] = Field(default_factory=dict)
    observed_events: list[ObservedEvent] = Field(default_factory=list)
