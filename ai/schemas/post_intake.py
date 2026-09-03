from typing import Literal, Optional

from pydantic import BaseModel, Field


class IncidentReport(BaseModel):
    title: str
    incident_datetime: Optional[str] = None
    location: Optional[str] = None
    parties: list[str] = Field(default_factory=list)
    objective_narrative: str
    timeline: list[str] = Field(default_factory=list)
    confirmed_facts: list[str] = Field(default_factory=list)
    user_claims: list[str] = Field(default_factory=list)
    disputed_facts: list[str] = Field(default_factory=list)
    unknown_facts: list[str] = Field(default_factory=list)
    accident_hypotheses: list[str] = Field(default_factory=list)
    retrieval_factors: dict[str, list[str]] = Field(default_factory=dict)
    retrieval_query: str
    generation_method: Literal["llm", "deterministic_fallback"] = "llm"


class RetrievedSource(BaseModel):
    source_id: str
    parent_id: Optional[str] = None
    source_type: Literal[
        "deliberation_case",
        "fault_standard",
        "roundabout_special_standard",
    ]
    source_file: str
    page_number: int = Field(ge=1)
    page_start: Optional[int] = Field(default=None, ge=1)
    page_end: Optional[int] = Field(default=None, ge=1)
    chunk_index: int = Field(ge=0)
    similarity_score: float
    semantic_score: Optional[float] = None
    lexical_score: Optional[float] = None
    case_number: Optional[str] = None
    chart_number: Optional[str] = None
    decision_ratio: Optional[str] = None
    matched_child_ids: list[str] = Field(default_factory=list)
    matched_sections: list[str] = Field(default_factory=list)
    excerpt: str


class SimilarCaseAssessment(BaseModel):
    source: RetrievedSource
    relevance_reason: str
    matching_factors: list[str] = Field(default_factory=list)
    differing_factors: list[str] = Field(default_factory=list)
    applicability_caution: str


class RagResult(BaseModel):
    retrieval_query: str
    embedding_model: str
    candidates: list[RetrievedSource] = Field(default_factory=list)
    similar_cases: list[SimilarCaseAssessment] = Field(default_factory=list)
    disclaimer: str = (
        "검색 결과는 참고용이며 사실관계와 적용 기준의 차이에 따라 과실 판단이 "
        "달라질 수 있습니다. 최종 과실비율은 보험사·분쟁심의위원회 등의 판단에 따릅니다."
    )


class PostIntakeResult(BaseModel):
    incident_report: IncidentReport
    rag: RagResult
