"""프론트/백엔드 병합용 AI Interface (가이드 99~101절).

    create_case(video_path, initial_description) -> AgentResponse
    chat(case_id, message)                        -> AgentResponse
    assess_fault(case_id)                         -> FaultAssessment
    generate_incident_report(case_id)             -> DocumentResult
    generate_rebuttal(case_id)                    -> DocumentResult

Case State는 JSON 파일(`data/cases/<case_id>.json`)에 저장한다. 백엔드 병합 시 DB로 교체한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from agents.master_agent import AgentResponse, MasterAccidentAgent
from settings import Settings, get_settings
from state.case_state import CaseState, DocumentResult, FaultAssessment


class CaseStore:
    def __init__(self, base_dir: Optional[str | Path] = None):
        self.base_dir = Path(base_dir) if base_dir else get_settings().cases_dir
        self._memory: dict[str, CaseState] = {}

    def _path(self, case_id: str) -> Path:
        return self.base_dir / f"{case_id}.json"

    def save(self, state: CaseState) -> Path:
        self._memory[state.case_id] = state
        self.base_dir.mkdir(parents=True, exist_ok=True)
        path = self._path(state.case_id)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state.model_dump(), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        temporary.replace(path)
        return path

    def load(self, case_id: str) -> CaseState:
        if case_id in self._memory:
            return self._memory[case_id]
        path = self._path(case_id)
        if not path.is_file():
            raise KeyError(f"존재하지 않는 case_id: {case_id}")
        state = CaseState.model_validate_json(path.read_text(encoding="utf-8"))
        self._memory[case_id] = state
        return state

    def exists(self, case_id: str) -> bool:
        return case_id in self._memory or self._path(case_id).is_file()

    def list_cases(self) -> list[str]:
        if not self.base_dir.is_dir():
            return sorted(self._memory)
        return sorted({path.stem for path in self.base_dir.glob("*.json")} | set(self._memory))


class CarDefenderAI:
    def __init__(
        self,
        *,
        agent: Optional[MasterAccidentAgent] = None,
        store: Optional[CaseStore] = None,
        settings: Optional[Settings] = None,
    ):
        self.settings = settings or get_settings()
        self.agent = agent or MasterAccidentAgent(settings=self.settings)
        self.store = store or CaseStore(self.settings.cases_dir)

    # -- interface -------------------------------------------------------
    def create_case(self, video_path: Optional[str], initial_description: str = "", *, case_id: Optional[str] = None, progress=None) -> AgentResponse:
        state, response = self.agent.create_case(video_path=video_path, initial_description=initial_description, case_id=case_id, progress=progress)
        self.store.save(state)
        return response

    def chat(self, case_id: str, message: str, *, progress=None) -> AgentResponse:
        state = self.store.load(case_id)
        state, response = self.agent.chat(state, message, progress=progress)
        self.store.save(state)
        return response

    def assess_fault(self, case_id: str, *, progress=None) -> FaultAssessment:
        state = self.store.load(case_id)
        state, response = self.agent.chat(state, "예상 과실비율을 판정해줘. 몇 대 몇이야?", progress=progress)
        self.store.save(state)
        if state.fault_assessment is None:
            raise RuntimeError(response.message)
        return state.fault_assessment

    def generate_incident_report(self, case_id: str) -> DocumentResult:
        state = self.store.load(case_id)
        if state.fault_assessment is None or state.assessment_invalidated:
            raise RuntimeError("사건경위서를 작성하려면 먼저 예상 과실비율 판정이 완료되어야 합니다.")
        document = self.agent.document_agent.generate_incident_report(state)
        state.incident_report = document
        state.set_stage("REPORT_COMPLETE")
        state.add_message("assistant", "사건경위서 초안을 작성했습니다.", action="SHOW_DOCUMENT")
        self.store.save(state)
        return document

    def generate_rebuttal(self, case_id: str, *, opponent_claim: Optional[str] = None) -> DocumentResult:
        state = self.store.load(case_id)
        if state.fault_assessment is None or state.assessment_invalidated:
            raise RuntimeError("반박의견서를 작성하려면 먼저 예상 과실비율 판정이 완료되어야 합니다.")
        document = self.agent.document_agent.generate_rebuttal_opinion(state, opponent_claim=opponent_claim)
        state.rebuttal_opinion = document
        state.set_stage("REBUTTAL_COMPLETE")
        state.add_message("assistant", "반박의견서 초안을 작성했습니다.", action="SHOW_DOCUMENT")
        self.store.save(state)
        return document

    def get_state(self, case_id: str) -> CaseState:
        return self.store.load(case_id)


_default: Optional[CarDefenderAI] = None


def get_service() -> CarDefenderAI:
    global _default
    if _default is None:
        _default = CarDefenderAI()
    return _default


def create_case(video_path: Optional[str], initial_description: str = "") -> AgentResponse:
    return get_service().create_case(video_path, initial_description)


def chat(case_id: str, message: str) -> AgentResponse:
    return get_service().chat(case_id, message)


def assess_fault(case_id: str) -> FaultAssessment:
    return get_service().assess_fault(case_id)


def generate_incident_report(case_id: str) -> DocumentResult:
    return get_service().generate_incident_report(case_id)


def generate_rebuttal(case_id: str, opponent_claim: Optional[str] = None) -> DocumentResult:
    return get_service().generate_rebuttal(case_id, opponent_claim=opponent_claim)
