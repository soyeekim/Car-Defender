"""Document Agent (가이드 11.6 / 16절).

사고 사실을 새로 판정하지 않고, Master Agent가 검증한 패키지만 입력으로 문서를 작성한다.
"""

from __future__ import annotations

from typing import Optional

from document.incident_report import generate_incident_report
from document.package import VerifiedCasePackage, build_verified_package
from document.rebuttal import generate_rebuttal_opinion
from models.clients import OpenAITextClient, TextClient
from settings import Settings, get_settings
from state.case_state import CaseState, DocumentResult
from telemetry import RunLogger, get_run_logger


class DocumentAgent:
    def __init__(
        self,
        client: Optional[TextClient] = None,
        *,
        settings: Optional[Settings] = None,
        run_logger: Optional[RunLogger] = None,
        use_llm: bool = True,
    ):
        self.settings = settings or get_settings()
        self.run_logger = run_logger or get_run_logger()
        self._client = client
        self.use_llm = use_llm

    @property
    def client(self) -> Optional[TextClient]:
        if not self.use_llm:
            return None
        if self._client is None:
            if not self.settings.openai_api_key:
                return None
            self._client = OpenAITextClient(
                self.settings.agent.document_model,
                api_key=self.settings.openai_api_key,
                temperature=self.settings.agent.document_temperature,
            )
        return self._client

    def build_package(self, state: CaseState) -> VerifiedCasePackage:
        return build_verified_package(state)

    def generate_incident_report(self, state: CaseState) -> DocumentResult:
        package = self.build_package(state)
        return generate_incident_report(self.client, package, run_logger=self.run_logger, temperature=self.settings.agent.document_temperature)

    def generate_rebuttal_opinion(self, state: CaseState, *, opponent_claim: Optional[str] = None) -> DocumentResult:
        if opponent_claim:
            state.opponent_claim = opponent_claim
        package = self.build_package(state)
        return generate_rebuttal_opinion(self.client, package, run_logger=self.run_logger, temperature=self.settings.agent.document_temperature)
