from typing import Literal, Protocol

from pydantic import BaseModel, Field, model_validator


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class Ratio(BaseModel):
    mine: int = Field(ge=0, le=100)
    other: int = Field(ge=0, le=100)


class VerdictSnapshot(BaseModel):
    version: int
    ratio_mine: int
    ratio_other: int
    summary: str
    opponent_claim: dict | None = None
    basis: dict = Field(default_factory=dict)


class VideoMeta(BaseModel):
    speed_kph: int | None = None
    impact_at_sec: int | None = None


class AnalyzeInput(BaseModel):
    video_path: str
    video_mime: str
    description: str


class AnalyzeResult(BaseModel):
    summary_text: str
    facts: dict = Field(default_factory=dict)
    questions: list[str] = Field(default_factory=list)
    title: str
    video_meta: VideoMeta | None = None


NextAction = Literal["none", "verdict", "rejudge", "create_report", "create_rebuttal"]


class ChatInput(BaseModel):
    messages: list[ChatTurn]
    new_message: str
    facts: dict
    questions: list[str]
    verdict: VerdictSnapshot | None
    has_video: bool
    has_report: bool


class ChatResult(BaseModel):
    reply: str
    next_action: NextAction = "none"
    fact_updates: dict = Field(default_factory=dict)


class Chart(BaseModel):
    name: str
    note: str = ""


class Precedent(BaseModel):
    id: str
    title: str
    body_text: str = ""  # H37 팝업 본문. 빈 문자열 허용


class Basis(BaseModel):
    chart: Chart
    precedents: list[Precedent] = Field(default_factory=list)


class JudgeInput(BaseModel):
    messages: list[ChatTurn]
    facts: dict
    previous_verdict: VerdictSnapshot | None


class JudgeResult(BaseModel):
    ratio_mine: int = Field(ge=0, le=100)
    ratio_other: int = Field(ge=0, le=100)
    summary: str
    change_reason: str | None = None
    opponent_claim: Ratio | None = None
    basis: Basis

    @model_validator(mode="after")
    def _sum(self):
        if self.ratio_mine + self.ratio_other != 100:
            raise ValueError("ratio_mine + ratio_other must be 100")
        return self


class Section(BaseModel):
    index: int
    title: str
    body: str


class WriteInput(BaseModel):
    kind: Literal["report", "rebuttal"]
    messages: list[ChatTurn]
    facts: dict
    verdict: VerdictSnapshot
    revision_request: str | None = None
    previous_sections: list[Section] | None = None
    report_sections: list[Section] | None = None


class WriteResult(BaseModel):
    sections: list[Section] | None = None
    caveat: str | None = None
    page_count: int | None = None
    body: str | None = None


class ExplainInput(BaseModel):
    precedent_id: str
    facts: dict


class ExplainResult(BaseModel):
    body_text: str


class Agent(Protocol):
    async def analyze(self, inp: AnalyzeInput) -> AnalyzeResult: ...
    async def chat(self, inp: ChatInput) -> ChatResult: ...
    async def judge(self, inp: JudgeInput) -> JudgeResult: ...
    async def write(self, inp: WriteInput) -> WriteResult: ...
    async def explain(self, inp: ExplainInput) -> ExplainResult: ...
