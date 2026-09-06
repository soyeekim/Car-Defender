"""Master Accident Agent (가이드 5 / 14 / 25 / 96절).

사용자의 모든 대화 진입점. Case State를 소유하고 매 턴 다음을 판단한다.
1. 사용자가 무엇을 원하는가? (intent)
2. 새로운 사실이 입력되었는가? (fact extraction → state)
3. 영상 추가 분석이 필요한가? (video agent)
4. 질문이 필요한가? (sufficiency → objective questions)
5. RAG를 수행할 수 있는가? → 유사 심의사례 제시 → 차이·수정요소 확인 대화 (CASE_REVIEW)
6. 판정할 준비가 되었는가? (검토 완료 또는 사용자 명시 요청) → 심의사례 + 사고 사실 종합 판정
7. 문서를 작성해야 하는가? (document agent)

LLM 판단 + Deterministic Guardrail을 함께 사용한다.
"""

from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from agents.document_agent import DocumentAgent
from agents.video_agent import VideoAnalysisAgent, VideoDecision
from assessment.fault_ratio import assess_fault_ratio
from case.extractor import extract_case_facts
from case.fact_labels import generate_fact_labels
from case.questions import MAX_ASK_COUNT, format_questions, generate_followup_questions, mark_pending_unknown, unregister_questions, video_gap_candidates
from case.review import ADDITIONAL_FACTS_FIELD
from case.sufficiency import SufficiencyResult, check_information_sufficiency
from common.jsonutil import compact_json
from models.clients import OpenAITextClient, TextClient
from prompts.loader import load_prompt, wrap_user_text
from rag.tool import SimilarCaseRagTool
from settings import Settings, get_settings
from state.case_state import CaseState, FaultAssessment, Question, RetrievedCase
from state.updater import UserFactExtraction, apply_user_extraction, merge_video_facts
from telemetry import RunLogger, get_run_logger, input_hash
from video.base import compact_previous_result
from video.schemas import Observation, VideoResult
from video.validation import FocusTarget, _opponent_candidate_from_text, collision_window_seconds

Intent = Literal[
    "provide_facts",
    "answer_question",
    "request_fault_assessment",
    "request_similar_cases",
    "request_incident_report",
    "request_rebuttal",
    "request_video_recheck",
    "ask_explanation",
    "provide_opponent_claim",
    "general_question",
    "other",
]
Action = Literal[
    "ASK_USER",
    "SHOW_VIDEO_SUMMARY",
    "SHOW_FAULT_ASSESSMENT",
    "SHOW_SIMILAR_CASES",
    "SHOW_DOCUMENT",
    "REQUEST_ASSESSMENT",  # defer_conclusions: 판정 준비 완료 → 호출자(서버 Job)가 judge를 수행
    "REQUEST_DOCUMENT",  # defer_conclusions: 문서 작성 요청 → 호출자(서버 Job)가 write를 수행
    "ANSWER",
    "INFO",
    "ERROR",
]


class IntentResult(BaseModel):
    primary_intent: Intent = "other"
    secondary_intents: list[str] = Field(default_factory=list)
    wants_ratio_now: bool = False
    mentions_video_scene: bool = False
    contains_new_facts: bool = False
    contains_opponent_claim: bool = False
    video_recheck_focus: Optional[str] = None
    confidence: float = 0.0


class AgentResponse(BaseModel):
    case_id: str
    message: str
    stage: str
    action: Action
    data: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class _RespondOutput(BaseModel):
    message: str = ""
    follow_up_needed: bool = False
    next_action: Optional[str] = None


class _GapItem(BaseModel):
    item: str
    time_window: str = ""
    target_field: str = ""
    why: str = ""


class _SkippedItem(BaseModel):
    item: str
    reason: str = ""


class _VideoGapPlan(BaseModel):
    reasoning: list[str] = Field(default_factory=list)
    recheck_items: list[_GapItem] = Field(default_factory=list)
    enrichment: str = ""
    skipped: list[_SkippedItem] = Field(default_factory=list)


def video_already_confirmed_topic_text(confirmed_blob: str, result: "VideoResult", text: str) -> bool:
    """지시서 항목이 1차 결과에서 이미 CONFIRMED인지 (필드 값 + 확정 사실 문장 기준)."""
    lowered = text.lower()
    env = result.road_environment
    checks = [
        (("실선", "점선", "차선 종류", "노면", "lane_marking"), env.lane_marking_at_lane_change.status == "CONFIRMED"),
        (("정지선", "stop_line"), env.stop_line.status == "CONFIRMED"),
        (("신호등", "신호 색", "signal_observations", "신호 변경"), any(item.status == "CONFIRMED" for item in env.signal_observations)),
        (("충돌 부위", "participant_parts"), bool(result.collision.participant_parts)),
    ]
    for keywords, confirmed in checks:
        if any(keyword in lowered for keyword in keywords) and confirmed:
            return True
    for vehicle in result.vehicles:
        vid = vehicle.id.lower()
        if vid in lowered:
            if ("방향지시등" in lowered or "turn_signal" in lowered) and vehicle.turn_signal.status == "CONFIRMED":
                return True
            if ("제동" in lowered or "braking" in lowered) and vehicle.braking.status == "CONFIRMED":
                return True
            if ("선진입" in lowered or "entered" in lowered or "먼저 진입" in lowered) and vehicle.entered_intersection_first.status == "CONFIRMED":
                return True
    return False


# 키워드 규칙은 "명시적 요청"에만 강하게 반응한다. 단어가 언급되기만 한 경우(예: "보험사가 주장한
# 과실비율에 동의할 수 없으면 어떻게 하나요?")는 약한 힌트로만 남기고 LLM 의도 판단을 따른다.
_TOPIC_RULES: list[tuple[Intent, re.Pattern]] = [
    ("request_incident_report", re.compile(r"경위서")),
    ("request_rebuttal", re.compile(r"반박\s*의견서|반박서|반박\s*문|반박\s*(해|써|작성|만들)")),
    ("request_similar_cases", re.compile(r"(유사|비슷한)\s*(사례|판례|심의)|심의\s*사례")),
    (
        "request_fault_assessment",
        re.compile(
            r"몇\s*대\s*몇|몇\s*[:：]\s*몇|몇\s*퍼센트|몇\s*%"
            r"|과실\s*(비율|은|이)?\s*(얼마|몇|판정|산정|계산|평가|알려|추정|예상|나와|나올|어떻게\s*(돼|되))"
            r"|비율\s*(알려|판정|계산|산정|얼마|몇|나와)"
            r"|판정\s*(해|부탁|다시|진행|시작)"
        ),
    ),
    ("request_video_recheck", re.compile(r"(영상|장면|구간|화면).{0,14}(다시|재)\s*(봐|확인|분석)|재분석|다시\s*분석")),
]
_REQUEST_VERB = re.compile(r"(작성|써\s*줘|써줘|써\s*주|만들|생성|뽑|준비|부탁|줘|주세요|해줘|해\s*주|필요해|보여|해봐|출력|받고\s*싶|싶어|싶습)")
_PROCEDURE_QUESTION = re.compile(r"(뭐야|뭔가요|무엇|어떤\s*건|어떻게\s*해야|어떻게\s*하나|어떻게\s*하면|방법|절차|필요할까|해야\s*하나|해야\s*되|할\s*수\s*있나|가능한가|가능해)")
_NEGATIVE_ANSWER = re.compile(r"^\s*(없|아니|아뇨|아니요|아니오|그게\s*다|더\s*없|딱히|모르|기억\s*안|괜찮|판정|이제\s*(판정|결론)|충분)")
# 판정 제안("판정해 드릴까요?")에 대한 짧은 수락
_AFFIRMATIVE = re.compile(r"^\s*(네|예|응|어|그래|좋아|좋습니다|해\s*줘|해\s*주세요|부탁|진행|시작|판정|ㅇㅇ|ok|okay|yes)", re.IGNORECASE)
# 사실 수집이 끝난 뒤의 열린 질문과 판정 제안 문구
OPEN_FACTS_QUESTION = (
    "지금까지 확인한 내용 외에 추가로 알려주실 사고 정황이 있나요?\n"
    "(예: 상대 차량 방향지시등, 진입 순서, 영상 시작 전 상황) 없으면 '없어요'라고 말씀해 주세요."
)
ASSESSMENT_OFFER = (
    "지금까지 확인된 내용으로 비슷한 심의사례를 찾아 예상 과실비율을 판정해 드릴까요?\n"
    "준비되셨으면 '예상 과실비율 판정해줘'라고 말씀해 주세요. 더 알려주실 내용이 있으면 먼저 말씀해 주셔도 돼요."
)


def rule_based_intent(state: CaseState, message: str) -> IntentResult:
    text = message.strip()
    compact = re.sub(r"\s+", "", text)
    for intent, pattern in _TOPIC_RULES:
        if not (pattern.search(text) or pattern.search(compact)):
            continue
        procedural = bool(_PROCEDURE_QUESTION.search(text))
        explicit = (
            intent in {"request_fault_assessment", "request_video_recheck"}
            or bool(_REQUEST_VERB.search(text))
            or len(compact) <= 12
        ) and not procedural
        if explicit:
            result = IntentResult(primary_intent=intent, confidence=0.9)
            if intent == "request_fault_assessment":
                result.wants_ratio_now = True
            if intent == "request_video_recheck":
                result.mentions_video_scene = True
                result.video_recheck_focus = message
            return result
        # 주제만 언급된 경우: 일반 질문으로 두고 LLM이 최종 판단
        return IntentResult(primary_intent="general_question", secondary_intents=[intent], confidence=0.5)
    if ("보험사" in text or "상대" in text) and re.search(r"주장|우긴|그러는데|그러던데|말하는데", text) and re.search(r"\d", text):
        return IntentResult(primary_intent="provide_opponent_claim", contains_opponent_claim=True, confidence=0.6)
    if state.fault_assessment and re.search(r"왜|이유|근거|어째서", text):
        return IntentResult(primary_intent="ask_explanation", confidence=0.6)
    if state.pending_questions:
        return IntentResult(primary_intent="answer_question", contains_new_facts=True, confidence=0.6)
    if re.search(r"\?|까요|나요|인가요|무엇|어떻게|어떤", text):
        return IntentResult(primary_intent="general_question", confidence=0.5)
    return IntentResult(primary_intent="provide_facts", contains_new_facts=True, confidence=0.5)


class MasterAccidentAgent:
    def __init__(
        self,
        *,
        text_client: Optional[TextClient] = None,
        video_agent: Optional[VideoAnalysisAgent] = None,
        rag_tool: Optional[SimilarCaseRagTool] = None,
        document_agent: Optional[DocumentAgent] = None,
        settings: Optional[Settings] = None,
        run_logger: Optional[RunLogger] = None,
        use_llm: bool = True,
        defer_conclusions: bool = False,
    ):
        self.settings = settings or get_settings()
        self.run_logger = run_logger or get_run_logger()
        self.use_llm = use_llm
        # 서버 연동 모드: 판정·문서 작성을 대화 턴 안에서 수행하지 않고 REQUEST_* 응답으로 넘긴다.
        # (백엔드는 judge/write를 별도 Job으로 돌리고 결과 카드를 직접 그린다)
        self.defer_conclusions = defer_conclusions
        self._text_client = text_client
        self._video_agent = video_agent
        self._rag_tool = rag_tool
        self._document_agent = document_agent
        # 대화 중 Agent 재분석: 2차 분석이 이미 수행되므로 기본 0회. critical 공백(충돌 차량 식별 등)은 별도 1회.
        self.max_master_rechecks = self.settings.agent.max_master_rechecks
        self.max_critical_rechecks = self.settings.agent.max_critical_rechecks

    # ------------------------------------------------------------------ lazy deps
    @property
    def text_client(self) -> Optional[TextClient]:
        if not self.use_llm:
            return None
        if self._text_client is None:
            if not self.settings.openai_api_key:
                return None
            self._text_client = OpenAITextClient(
                self.settings.agent.master_model,
                api_key=self.settings.openai_api_key,
                temperature=self.settings.agent.master_temperature,
            )
        return self._text_client

    @property
    def video_agent(self) -> VideoAnalysisAgent:
        if self._video_agent is None:
            self._video_agent = VideoAnalysisAgent(settings=self.settings, run_logger=self.run_logger)
        return self._video_agent

    @property
    def rag_tool(self) -> SimilarCaseRagTool:
        if self._rag_tool is None:
            self._rag_tool = SimilarCaseRagTool(self.text_client, settings=self.settings.rag, run_logger=self.run_logger)
        return self._rag_tool

    @property
    def document_agent(self) -> DocumentAgent:
        if self._document_agent is None:
            self._document_agent = DocumentAgent(settings=self.settings, run_logger=self.run_logger, use_llm=self.use_llm)
        return self._document_agent

    # ------------------------------------------------------------------ entry points
    def create_case(
        self,
        *,
        video_path: Optional[str] = None,
        initial_description: str = "",
        case_id: Optional[str] = None,
        progress=None,
    ) -> tuple[CaseState, AgentResponse]:
        state = CaseState(
            video_path=video_path,
            video_uploaded=bool(video_path),
            initial_description=initial_description or None,
        )
        if case_id:
            state.case_id = case_id
        events: list[str] = []
        intent = IntentResult(primary_intent="provide_facts", contains_new_facts=bool(initial_description))
        if initial_description:
            state.add_message("user", initial_description)
            extraction = extract_case_facts(self.text_client, state, initial_description, run_logger=self.run_logger)
            added, conflicts = apply_user_extraction(state, extraction, turn=state.turn_count)
            if added:
                events.append(f"사용자 설명에서 사실 {len(added)}건 추출")
            if extraction.ignored_opinions:
                events.append("사용자 의견(과실 주장)은 사실로 기록하지 않음")
        response = self._run_turn(state, message=initial_description, intent=intent, events=events, answered_fields=set(), progress=progress, is_initial=True)
        return state, response

    def chat(self, state: CaseState, message: str, *, progress=None) -> tuple[CaseState, AgentResponse]:
        state.turn_count += 1
        state.add_message("user", message)
        intent = self.detect_intent(state, message)
        events: list[str] = [f"intent={intent.primary_intent}"]
        # 질문에 답하면 그 전에 사용자가 요청했던 작업(경위서 등)을 이어서 수행한다
        if state.pending_intent and intent.primary_intent in {"answer_question", "provide_facts", "other"}:
            intent = IntentResult(primary_intent=state.pending_intent, secondary_intents=[intent.primary_intent], confidence=0.9,  # type: ignore[arg-type]
                                  wants_ratio_now=state.pending_intent == "request_fault_assessment", contains_new_facts=True)
            events.append(f"이전 요청 재개: {state.pending_intent}")
        state.pending_intent = None
        # 판정 제안이 떠 있을 때 "네 / 판정해줘 / 예상 과실비율 판정해줘" → 판정 요청으로 본다
        if state.assessment_offer_pending and not intent.contains_new_facts and (
            intent.primary_intent == "request_fault_assessment" or _AFFIRMATIVE.match(message)
        ):
            intent = IntentResult(primary_intent="request_fault_assessment", secondary_intents=[intent.primary_intent], confidence=0.95, wants_ratio_now=True)  # type: ignore[arg-type]
            events.append("판정 제안 수락")
        pending_before = {item.field for item in state.pending_questions}
        if self._is_pure_command(state, message, intent):
            # "사건경위서 작성해줘" 같은 짧은 명령에는 새 사실이 없으므로 추출 호출을 생략한다
            extraction = UserFactExtraction(no_new_facts=True)
        else:
            extraction = extract_case_facts(self.text_client, state, message, run_logger=self.run_logger)
        added, conflicts = apply_user_extraction(state, extraction, turn=state.turn_count)
        # guardrail: "잘 모르겠어요" 류의 답을 추출기가 놓치면 코드가 pending 질문을 '모름'으로 닫는다
        unknown_fields = mark_pending_unknown(state, message) if state.pending_questions else []
        if unknown_fields:
            events.append("사용자가 모른다고 답함: " + ", ".join(unknown_fields))
        answered_fields = pending_before - {item.field for item in state.pending_questions}
        if added:
            events.append("새 사실 반영: " + "; ".join(fact.fact for fact in added[:4]))
        if any(fact.field == "opponent_claim" for fact in added):
            events.append("상대 보험사 주장 기록: " + (extraction.opponent_claim or "").strip()[:120])
        if conflicts:
            events.append("영상과 충돌하는 진술 기록: " + "; ".join(item.description for item in conflicts[:3]))
        if extraction.ignored_opinions:
            events.append("과실 의견은 판정 근거로 사용하지 않음")
        if state.assessment_invalidated:
            events.append("중요 사실 변경으로 기존 판정 무효화 → 재평가 필요")
        response = self._run_turn(state, message=message, intent=intent, events=events, answered_fields=answered_fields, progress=progress)
        return state, response

    # ------------------------------------------------------------------ intent
    @staticmethod
    def _is_pure_command(state: CaseState, message: str, intent: IntentResult) -> bool:
        """'사건경위서 작성해줘'처럼 새 사실이 있을 수 없는 짧은 한 문장 명령인지. 조금이라도 애매하면 추출을 돌린다."""
        text = message.strip()
        if state.pending_questions or len(text) > 24 or re.search(r"\d", text):
            return False
        if intent.contains_new_facts or intent.contains_opponent_claim:
            return False
        # 문장이 둘 이상이거나 접속어로 이어지면 앞부분에 사실이 있을 수 있다
        if re.search(r"[.!?\n,]\s*\S|그리고|근데|그런데|그러고|사실", text):
            return False
        return intent.primary_intent in {"request_incident_report", "request_rebuttal", "request_similar_cases", "request_fault_assessment"}

    def detect_intent(self, state: CaseState, message: str) -> IntentResult:
        rules = rule_based_intent(state, message)
        client = self.text_client
        if client is None:
            return rules
        system = load_prompt("master_agent", "system")
        task = load_prompt("master_agent", "intent")
        user = task.render(
            user_message=wrap_user_text(message),
            stage=state.current_stage,
            video_analyzed=state.video_analyzed,
            assessment_done=bool(state.fault_assessment and not state.assessment_invalidated),
            pending_questions="; ".join(item.question for item in state.pending_questions) or "(없음)",
        )
        try:
            response = client.generate_json(system=system.text, user=user, schema=IntentResult, task=task.task)
            detected = IntentResult.model_validate(response.data)
            self.run_logger.log(agent="master_agent", task=task.task, case_id=state.case_id, model=response.metrics.model,
                                prompt_version=f"{system.version_id}+{task.version_id}", metrics=response.metrics,
                                input_digest=input_hash(message), extra={"intent": detected.primary_intent})
        except Exception as exc:  # noqa: BLE001
            self.run_logger.log(agent="master_agent", task=task.task, case_id=state.case_id, extra={"error": str(exc)[:300]})
            return rules
        # 명시적 요청(경위서 써줘 / 몇 대 몇이야)만 코드 규칙을 우선한다 (Deterministic Guardrail).
        # 주제 언급만 있는 약한 힌트(0.5)는 LLM 판단을 따른다.
        if rules.confidence >= 0.85 and rules.primary_intent != detected.primary_intent:
            detected.secondary_intents.append(detected.primary_intent)
            detected.primary_intent = rules.primary_intent
            detected.wants_ratio_now = detected.wants_ratio_now or rules.wants_ratio_now
            detected.video_recheck_focus = detected.video_recheck_focus or rules.video_recheck_focus
        return detected

    # ------------------------------------------------------------------ orchestration
    def _run_turn(
        self,
        state: CaseState,
        *,
        message: str,
        intent: IntentResult,
        events: list[str],
        answered_fields: set[str],
        progress=None,
        is_initial: bool = False,
    ) -> AgentResponse:
        warnings: list[str] = []

        # 1. 영상 최초 분석 (deterministic guardrail)
        if state.video_uploaded and not state.video_analyzed and state.video_status != "VIDEO_UNAVAILABLE":
            decision = self._ensure_video_analyzed(state, progress=progress)
            if decision.status == "UNAVAILABLE":
                warnings.append(f"영상 분석을 수행하지 못했습니다: {decision.error}")
                events.append("영상 분석 실패 → 사용자 확인 정보로 진행")
            else:
                events.append(f"영상 분석 완료 ({decision.passes}회 호출, {decision.status})")
                # 서술에만 있고 목록에 빠진 상대 차량은 분석 결과 안에서 보정된다(video.validation.reconcile_vehicle_inventory) —
                # 첫 턴에 영상을 한 번 더 돌리지 않는다 (분석은 한 번에 제대로, 재분석으로 덮어쓰지 않는다: 사용자 요청 2026-09-06)
                if decision.status == "NEEDS_USER_CONFIRMATION" and decision.confirmation_question:
                    confirmation_question = decision.confirmation_question
                    question = Question(field="collision.participants_confirmed", question=confirmation_question, importance="critical", asked_turn=state.turn_count)
                    state.pending_questions = [question]
                    if question.field not in state.asked_fields:
                        state.asked_fields.append(question.field)
                    state.set_stage("FACT_COLLECTING")
                    return self._respond(state, message=self._video_intro(state) + "\n\n" + confirmation_question, action="ASK_USER",
                                         data={"questions": [question.model_dump()], "video_summary": self._video_summary_data(state)}, warnings=warnings)

        # 1b. 사용자가 상대 차량을 확인해 줬는데 영상 차량 목록에는 아직 없으면, 그 확인을 들고 영상 agent 가 다시 찾는다
        if "collision.participants_confirmed" in answered_fields and state.video_analysis and len(state.video_analysis.vehicles) < 2:
            self._try_register_opponent(state, events, progress=progress, user_note=message)

        # 2. 영상 재확인 요청
        if intent.primary_intent == "request_video_recheck" and state.video_analysis and state.video_path:
            focus = intent.video_recheck_focus or message
            self._recheck_video(state, focus, progress=progress, events=events)

        # 3. 정보 충분성
        sufficiency = self._sufficiency(state)
        wants_assessment = intent.primary_intent == "request_fault_assessment" or intent.wants_ratio_now
        wants_document = intent.primary_intent in {"request_incident_report", "request_rebuttal"}
        wants_cases = intent.primary_intent == "request_similar_cases"
        explicit_conclusion = wants_assessment or wants_document

        # 4. critical 영상 공백(충돌 당사 차량·장소 유형·진행 방향)이 남아 있으면 1회에 한해 focus 재분석 (사용자에게 묻기 전에)
        critical_targets = [
            item for item in sufficiency.missing_information
            if item.video_recheckable and item.importance == "critical" and item.field not in {"video_source.vehicle_owner", "ego_vehicle.vehicle_id", "other_vehicle.vehicle_id"}
        ]
        if (
            state.video_analysis
            and state.video_path
            and critical_targets
            and sufficiency.video_reanalysis_targets
            and state.critical_recheck_count < self.max_critical_rechecks
            and not sufficiency.critical_missing_user_answerable()
        ):
            if self._recheck_video(state, sufficiency.video_reanalysis_targets[0], progress=progress, events=events):
                state.critical_recheck_count += 1
            sufficiency = self._sufficiency(state)

        # 5-0. 직전 질문에 답을 못 받았으면(딴 얘기·되묻기) 새 질문을 고르기 전에 그 질문을 먼저 정리한다
        if state.fault_assessment is None and not explicit_conclusion and not wants_cases and not wants_document and state.pending_questions:
            handled = self._handle_pending_questions(state, message=message, intent=intent, events=events, answered_fields=answered_fields, warnings=warnings)
            if handled is not None:
                return handled

        # 5. 사실 수집 단계 — 한 턴에 한 질문.
        #    (a) 코드 필수 항목(영상 소유 관계·사용자 차량 식별 등 critical)은 반드시 먼저 묻는다.
        #    (b) 그 외에는 Agent가 사건 상태를 보고 "판정에 영향을 주는데 미확인인 사실"을 스스로 추론해 고른다.
        #        코드 후보(video_gap_candidates)는 LLM 호출 실패 시 fallback으로만 쓴다.
        askable = [item for item in sufficiency.sorted_missing() if item.user_answerable and item.importance in {"critical", "high", "medium"}]
        known_fields = {item.field for item in askable}
        askable += [item for item in video_gap_candidates(state) if item.field not in known_fields]
        critical = [item for item in askable if item.importance == "critical"]
        new_facts_now = bool(answered_fields) or any(event.startswith("새 사실 반영") for event in events)
        agent_turn = (
            state.video_analyzed
            and not state.retrieved_cases
            and not explicit_conclusion
            and not wants_cases
            and state.fact_question_rounds < self.settings.agent.max_fact_question_rounds
            # 열린 질문("추가로 알려주실 정황?")까지 나간 뒤에는 새 사실이 들어온 턴에만 다시 고른다 (질문 → 열린 질문 → 또 질문 순서 방지)
            and (ADDITIONAL_FACTS_FIELD not in state.asked_fields or new_facts_now)
        )
        if critical or agent_turn or (askable and not explicit_conclusion and not wants_cases and not sufficiency.ready_for_rag):
            first_turn = is_initial or not state.conversation_history[:-1]
            if critical:
                candidates = askable
            elif agent_turn:
                # Agent가 스스로 고르는 턴: 코드 후보는 LLM 실패/부재 시 fallback이며 과실 관련(high) 항목만 쓴다
                candidates = [item for item in askable if item.importance == "high"]
            else:
                candidates = askable
            intro, questions, rechecks = generate_followup_questions(
                self.text_client, state, candidates,
                max_questions=self.settings.agent.max_questions_per_turn, run_logger=self.run_logger,
                intro_default=self._video_intro(state) if first_turn else "",
                use_llm_intro=first_turn,  # 이어지는 턴에서는 영상 요약을 반복하지 않는다
                allow_agent_choice=True,
                max_rechecks=self.max_master_rechecks,
            )
            # Agent가 "영상으로 확인 가능"하다고 판단한 쟁점은 사용자에게 묻지 않고 Video Agent 재분석으로 해결한다
            rechecks_applied = bool(rechecks) and not critical and self._apply_agent_rechecks(state, rechecks, events, progress)
            if rechecks_applied:
                unregister_questions(state, questions)
                sufficiency = self._sufficiency(state)
                intro, questions, _ = generate_followup_questions(
                    self.text_client, state, [item for item in video_gap_candidates(state) if item.importance == "high"],
                    max_questions=self.settings.agent.max_questions_per_turn, run_logger=self.run_logger,
                    intro_default="", use_llm_intro=False, allow_agent_choice=True, max_rechecks=self.max_master_rechecks,
                )
            elif rechecks and not questions and not critical:
                # Agent가 재분석만 고르고 질문은 비웠는데 재분석은 할 수 없다 → "재분석 불가"를 알리고 사용자에게 물을 것을 다시 고르게 한다
                # (실제 대화에서 이 조합이 "물을 게 없음"으로 흘러 판정 제안으로 건너뛰었다)
                events.append("영상 재분석 불가 → 사용자 질문 재선택")
                intro_again, questions, _ = generate_followup_questions(
                    self.text_client, state, candidates,
                    max_questions=self.settings.agent.max_questions_per_turn, run_logger=self.run_logger,
                    intro_default=intro, use_llm_intro=first_turn and not intro, allow_agent_choice=True, max_rechecks=0, rechecks_unavailable=True,
                )
                intro = intro or intro_again
            if questions and not critical:
                state.fact_question_rounds += 1
            if questions:
                state.set_stage("FACT_COLLECTING")
                intro = self._strip_unfulfilled_recheck_promise(intro, events)
                intro = self._strip_request_sentences(intro)  # 질문은 질문 카드가 하나만 한다 — 안내문에 섞인 질문·요청 문장은 뺀다
                text = format_questions(intro if intro else self._ack(events, is_initial, state), questions)
                # 첫 턴은 분석 결과 하나로 말한다 — 재분석 안내를 덧붙여 결과를 덮어쓴 것처럼 보이게 하지 않는다
                recheck_note = "" if is_initial else self._recheck_note(events, state)
                if recheck_note:
                    text = recheck_note + "\n" + text
                if explicit_conclusion:
                    state.pending_intent = intent.primary_intent
                    text = ("예상 과실비율을 판정하기 전에" if wants_assessment else "문서를 만들기 전에") + " 확인할 게 있어요.\n" + text
                return self._respond(state, message=text, action="ASK_USER", data={"questions": [q.model_dump() for q in questions], "agent_reasoning": questions[0].reasoning, "missing_information": [m.model_dump() for m in sufficiency.missing_information], "video_summary": self._video_summary_data(state) if is_initial else None}, warnings=warnings)

        # 5-1. 사건경위서에는 사고 일시가 필요하므로, 아직 모르면 한 번 묻고 답변 후 이어서 작성한다
        if intent.primary_intent == "request_incident_report" and not state.accident_datetime.date.is_known() and "accident_datetime.date" not in state.asked_fields:
            date_item = next((item for item in sufficiency.missing_information if item.field == "accident_datetime.date"), None)
            if date_item is not None:
                intro, questions, _ = generate_followup_questions(self.text_client, state, [date_item], max_questions=1, run_logger=self.run_logger, allow_agent_choice=False)
                if questions:
                    state.pending_intent = intent.primary_intent
                    questions[0].why = None  # 안내문이 이유를 이미 말한다
                    text = "사건경위서에는 사고 일시가 들어가야 해요. " + format_questions("", questions)
                    return self._respond(state, message=text, action="ASK_USER", data={"questions": [q.model_dump() for q in questions]}, warnings=warnings)

        needs_assessment = state.fault_assessment is None or state.assessment_invalidated

        # 6. 사실 수집 마무리 (첫 판정 전): Agent가 더 물을 것이 없으면 → 추가 정황 열린 질문 → 판정 제안.
        #    사용자가 "예상 과실비율 판정해줘"라고 하면 그때 심의사례를 찾고 판정한다 (사례를 먼저 늘어놓지 않는다).
        if state.fault_assessment is None and not state.assessment_invalidated and not explicit_conclusion and not wants_cases:
            wrap_up = self._wrap_up_fact_collection(state, message=message, intent=intent, events=events, answered_fields=answered_fields, warnings=warnings, is_initial=is_initial)
            if wrap_up is not None:
                return wrap_up

        # 7. 유사 심의사례 검색 — 판정·문서·사례를 요청했을 때만
        if (explicit_conclusion or wants_cases) and (not state.retrieved_cases or state.assessment_invalidated):
            self._run_rag(state, events, progress=progress)
            if wants_cases and not explicit_conclusion:
                return self._similar_cases_response(state, warnings)

        # 8. 명시적 판정/문서 요청 또는 재평가
        if (explicit_conclusion or state.assessment_invalidated) and needs_assessment:
            state.assessment_offer_pending = False
            state.pending_questions = [item for item in state.pending_questions if item.field != ADDITIONAL_FACTS_FIELD]
            reassessing = "재평가" in " ".join(events)
            if self.defer_conclusions:
                # 서버 모드: 문서 요청이라도 판정이 먼저다 → 판정 요청으로 넘기고, 문서 요청은 pending_intent로 남겨
                # 판정 뒤 사용자의 다음 메시지(또는 판정 카드 버튼)에서 이어서 만든다
                label = "사건경위서" if intent.primary_intent == "request_incident_report" else "반박의견서"
                if wants_document:
                    state.pending_intent = intent.primary_intent
                    intro = (
                        f"중요한 사실이 바뀌어서 예상 과실비율을 먼저 다시 계산하고, 판정이 끝나면 이어서 {label}를 만들게요."
                        if reassessing
                        else f"{label}를 만들려면 먼저 예상 과실비율 판정이 필요해요. 판정이 끝나면 이어서 {label}를 만들게요."
                    ) + f" 판정 카드가 나오면 '{label} 만들어줘'라고 한 번 더 말씀해 주시거나 카드의 버튼을 눌러 주세요."
                else:
                    intro = "중요한 사실이 바뀌어서 예상 과실비율을 다시 계산할게요." if reassessing else None
                conclusion = self._conclude(state, events, warnings, intro=intro, force_provisional=not sufficiency.ready_for_assessment, progress=progress)
                if conclusion is not None:
                    return conclusion
                state.pending_intent = None
                if wants_assessment or wants_document:
                    return self._respond(state, message=self._not_ready_message(sufficiency), action="INFO", data={"missing_information": [m.model_dump() for m in sufficiency.missing_information]}, warnings=warnings)
            else:
                assessment = self._run_assessment(state, events, progress=progress, force_provisional=not sufficiency.ready_for_assessment)
                if assessment is not None and not wants_document and not wants_cases:
                    intro = "중요한 사실이 바뀌어서 다시 판정했어요." if reassessing else None
                    return self._assessment_response(state, assessment, events, warnings, intro=intro)
                if assessment is None and wants_assessment:
                    return self._respond(state, message=self._not_ready_message(sufficiency), action="INFO", data={"missing_information": [m.model_dump() for m in sufficiency.missing_information]}, warnings=warnings)

        if wants_cases:
            return self._similar_cases_response(state, warnings)

        # 9. 문서 요청
        if wants_document:
            return self._document_response(state, intent.primary_intent, warnings, events)

        # 10. 일반 답변 (기존 Case State 기반)
        if intent.primary_intent == "request_fault_assessment" and state.fault_assessment:
            return self._assessment_response(state, state.fault_assessment, events, warnings)
        return self._answer(state, message=message, intent=intent, events=events, warnings=warnings, is_initial=is_initial)

    # ------------------------------------------------------------------ fact collection wrap-up
    def _handle_pending_questions(
        self,
        state: CaseState,
        *,
        message: str,
        intent: IntentResult,
        events: list[str],
        answered_fields: set[str],
        warnings: list[str],
    ) -> Optional[AgentResponse]:
        """직전 턴의 질문에 답을 못 받았을 때: 사용자의 질문이면 답하고 다시 붙이고, 딴 얘기면 한 번 더 묻고,
        두 번째도 답이 없으면 미확인으로 두고 넘어간다. 열린 질문(추가 정황)에 '없어요'는 답으로 본다."""
        answered_now = bool(answered_fields) or any(event.startswith("새 사실 반영") for event in events)
        open_pending = [item for item in state.pending_questions if item.field == ADDITIONAL_FACTS_FIELD]
        # 열린 질문("추가로 알려주실 정황?")에는 되묻는 질문이 아닌 한 어떤 진술도 답이다 ("영상 시작 전에는 특별한 일 없었어요" 포함)
        statement = intent.primary_intent in {"provide_facts", "answer_question", "other", "provide_opponent_claim"} and "?" not in message
        if open_pending and (answered_now or _NEGATIVE_ANSWER.search(message) or statement):
            state.review_answers[ADDITIONAL_FACTS_FIELD] = "provided" if answered_now else "none"
            state.pending_questions = [item for item in state.pending_questions if item.field != ADDITIONAL_FACTS_FIELD]
            events.append("추가 정황 " + ("반영" if answered_now else "없음"))
        pending = [item for item in state.pending_questions if item.ask_count < MAX_ASK_COUNT]
        if not state.pending_questions:
            return None

        claim_ack = self._claim_ack(state, events)
        if pending and not claim_ack and intent.primary_intent in {"general_question", "ask_explanation", "provide_opponent_claim"}:
            answer = self._answer(state, message=message, intent=intent, events=events, warnings=warnings, is_initial=False, respond=False)
            for item in pending:
                item.ask_count += 1
                item.asked_turn = state.turn_count
            text = answer + "\n\n아직 확인이 필요한 게 남아 있어요.\n" + format_questions("", pending)
            return self._respond(state, message=text, action="ASK_USER", data={"questions": [q.model_dump() for q in pending]}, warnings=warnings)

        if pending:
            for item in pending:
                item.ask_count += 1
                item.asked_turn = state.turn_count
            if answered_now:
                text = (claim_ack or "확인했어요.") + "\n" + format_questions("", pending)
            elif pending[0].field == ADDITIONAL_FACTS_FIELD:
                text = "추가로 알려주실 사고 정황이 있으면 말씀해 주시고, 없으면 '없어요'라고 답해 주세요."
            else:
                text = "답변을 확인하지 못해서 다시 여쭤볼게요. 모르시면 '모름'이라고 답해 주셔도 돼요.\n" + format_questions("", pending)
            return self._respond(state, message=text, action="ASK_USER", data={"questions": [q.model_dump() for q in pending]}, warnings=warnings)

        # 두 번 물어도 답이 없는 질문 → 미확인으로 두고 진행
        for item in state.pending_questions:
            if item.field == ADDITIONAL_FACTS_FIELD:
                state.review_answers[ADDITIONAL_FACTS_FIELD] = "none"
                continue
            note = f"사용자가 {item.field} 질문에 답하지 않아 미확인으로 처리"
            if note not in state.uncertain_facts:
                state.uncertain_facts.append(note)
            events.append(f"미응답 질문 미확인 처리: {item.field}")
        state.pending_questions = []
        return None

    def _wrap_up_fact_collection(
        self,
        state: CaseState,
        *,
        message: str,
        intent: IntentResult,
        events: list[str],
        answered_fields: set[str],
        warnings: list[str],
        is_initial: bool,
    ) -> Optional[AgentResponse]:
        """Agent 질문이 끝난 뒤의 순서: 답 안 한 질문 재질문 → 추가 정황 열린 질문 → 판정 제안.

        사용자가 제안을 받아들이면(`chat` 에서 request_fault_assessment 로 바꿔 준다) 여기를 지나 검색·판정으로 간다.
        None 을 돌려주면 호출자가 다음 단계(검색·판정·문서·일반 답변)로 진행한다."""
        answered_now = bool(answered_fields) or any(event.startswith("새 사실 반영") for event in events)
        claim_ack = self._claim_ack(state, events)
        ack = (claim_ack + " ") if claim_ack else ("확인했어요. " if answered_now else "")
        intro = (self._video_intro(state) + "\n\n") if is_initial else ""

        # 추가 정황 열린 질문 (한 번만)
        if ADDITIONAL_FACTS_FIELD not in state.asked_fields:
            question = Question(field=ADDITIONAL_FACTS_FIELD, question=OPEN_FACTS_QUESTION, importance="medium", asked_turn=state.turn_count)
            state.pending_questions = [question]
            state.asked_fields.append(ADDITIONAL_FACTS_FIELD)
            state.asked_questions.append(OPEN_FACTS_QUESTION)
            state.set_stage("FACT_COLLECTING")
            return self._respond(state, message=intro + ack + OPEN_FACTS_QUESTION, action="ASK_USER",
                                 data={"questions": [question.model_dump()], "open_question": True, "video_summary": self._video_summary_data(state) if is_initial else None}, warnings=warnings)

        # 판정 제안 (새 사실이 들어오면 다시 제안한다)
        state.assessment_offer_pending = True
        state.set_stage("READY_FOR_RAG")
        events.append("사실 수집 완료 → 판정 제안")
        return self._respond(state, message=intro + ack + ASSESSMENT_OFFER, action="ASK_USER", data={"questions": [], "offer_assessment": True}, warnings=warnings)

    # ------------------------------------------------------------------ server-mode entry points (judge / write Job)
    def search_similar_cases(self, state: CaseState, events: Optional[list[str]] = None, *, progress=None) -> list[RetrievedCase]:
        return self._run_rag(state, events if events is not None else [], progress=progress)

    def assess(self, state: CaseState, events: Optional[list[str]] = None, *, force_provisional: bool = False, progress=None) -> Optional[FaultAssessment]:
        """유사 심의사례(없으면 검색) + 사고 사실을 종합해 판정하고 state에 기록한다. defer_conclusions와 무관하게 실제로 판정한다."""
        return self._run_assessment(state, events if events is not None else [], progress=progress, force_provisional=force_provisional)

    def video_intro(self, state: CaseState) -> str:
        return self._video_intro(state)

    def cases_block(self, state: CaseState) -> str:
        return self._cases_block(state)

    # ------------------------------------------------------------------ steps
    def _plan_video_gaps(self, state: CaseState, result: VideoResult) -> Optional[FocusTarget]:
        """1차 영상 결과를 읽고 Agent가 '무엇이 부족한지' 추론해 2차 분석 지시서(FocusTarget)를 만든다."""
        client = self.text_client
        if client is None:
            return None
        system = load_prompt("master_agent", "system")
        task = load_prompt("master_agent", "video_gap_plan")
        compact_result = compact_previous_result(result)
        compact_result["timeline"] = [event.model_dump(include={"start_time", "end_time", "event", "status"}) for event in result.timeline[:20]]
        compact_result["fault_relevant_factors"] = [factor.model_dump() for factor in result.fault_relevant_factors]
        compact_result["road_environment"] = {
            name: (value.value if isinstance(value, Observation) else value)
            for name, value in result.road_environment
            if not isinstance(value, list)
        }
        compact_result["vehicles"] = [
            {
                "id": vehicle.id, "description": vehicle.description, "is_ego": vehicle.is_ego, "movement": vehicle.movement,
                "lane": vehicle.lane, "turn_signal": vehicle.turn_signal.model_dump(include={"value", "status"}),
                "braking": vehicle.braking.model_dump(include={"value", "status"}),
                "entered_first": vehicle.entered_intersection_first.model_dump(include={"value", "status"}),
                "lane_change": vehicle.lane_change.model_dump(include={"value", "status"}),
            }
            for vehicle in result.vehicles
        ]
        unknowns = list(result.unknown_or_unobservable) + list(result.uncertain_facts) + [
            f"{factor.factor}: {factor.note} [{factor.observability}]" for factor in result.fault_relevant_factors if factor.observability != "CONFIRMED"
        ]
        user = task.render(
            user_description=wrap_user_text(state.initial_description or "", tag="USER_CASE_DESCRIPTION"),
            video_result=compact_json(compact_result, max_chars=9000),
            confirmed_facts="\n".join(f"- {item}" for item in result.confirmed_facts[:20]) or "(없음)",
            unknowns="\n".join(f"- {item}" for item in dict.fromkeys(unknowns)) or "(없음)",
            max_items=6,
        )
        response = client.generate_json(system=system.text, user=user, schema=_VideoGapPlan, task=task.task)
        plan = _VideoGapPlan.model_validate(response.data)
        self.run_logger.log(agent="master_agent", task=task.task, case_id=state.case_id, model=response.metrics.model,
                            prompt_version=f"{system.version_id}+{task.version_id}", metrics=response.metrics,
                            extra={"reasoning": plan.reasoning, "items": [item.item for item in plan.recheck_items], "skipped": [s.item for s in plan.skipped]})
        state.notes.append("2차 영상 분석 계획(Agent): " + " / ".join(plan.reasoning[:4]))
        # guardrail: 이미 CONFIRMED이거나 화각 밖으로 명시된 항목은 지시서에서 뺀다
        confirmed_blob = " ".join(result.confirmed_facts).lower()
        unobservable = ("화각", "보이지 않", "찍히지", "영상 시작 전", "영상 이전", "가려")
        items: list[str] = []
        for item in plan.recheck_items[:6]:
            text = f"{item.item} {item.why}"
            if any(marker in text for marker in unobservable):
                continue
            if video_already_confirmed_topic_text(confirmed_blob, result, text):
                continue
            window = f" [{item.time_window}]" if item.time_window else ""
            items.append(f"{item.item}{window} → {item.target_field or '해당 필드'} ({item.why})")
        if not items and not plan.enrichment.strip():
            return None
        question_lines = ["Master Agent가 1차 결과를 검토한 뒤 요청하는 2차 분석이다. 각 항목을 확인해 지정 필드에 CONFIRMED/INFERRED/UNKNOWN과 근거 시각을 기록하라. UNKNOWN이면 이유를 unknown_or_unobservable에 적어라."]
        question_lines += [f"{index}. {item}" for index, item in enumerate(items, start=1)]
        if plan.enrichment.strip():
            question_lines.append("추가로 사건경위서용 서술 보완: " + plan.enrichment.strip() + " 결과는 timeline(0.5초 단위)과 detailed_description에 반영하라.")
        start, end = collision_window_seconds(result, padding=2.0)
        return FocusTarget(kind="agent_gap_fill", question="\n".join(question_lines), prompt_name="focus_analysis", start_sec=start, end_sec=end,
                           reasons=[item.split(" →")[0][:40] for item in items] or ["enrichment"])

    def _ensure_video_analyzed(self, state: CaseState, *, progress=None) -> VideoDecision:
        state.video_status = "VIDEO_ANALYZING"
        state.set_stage("VIDEO_ANALYZING")
        decision = self.video_agent.run_policy(
            state.video_path, case_id=state.case_id, progress=progress, extra_context=state.initial_description or "",
            sweep_planner=lambda result: self._plan_video_gaps(state, result),
        )
        if decision.status == "UNAVAILABLE" or decision.result is None:
            state.video_status = "VIDEO_UNAVAILABLE"
            state.set_stage("FACT_COLLECTING")
            state.notes.append(f"영상 분석 실패: {decision.error}")
            return decision
        merge_video_facts(state, decision.result, threshold=self.video_agent.threshold)
        self._refresh_fact_labels(state)
        state.video_status = decision.result.analysis_completion.status
        if decision.sweep_focus:
            # 2차 분석이 다룬 쟁점은 대화 중 다시 재분석하지 않는다 (guardrail이 history를 본다)
            state.recheck_focus_history.append("[일괄 재확인] " + decision.sweep_focus)
            state.notes.append("1차 분석 직후 2차 영상 분석(부족한 내용 보완) 수행")
        state.set_stage("FACT_COLLECTING")
        self.run_logger.log(agent="master_agent", task="video_validation", case_id=state.case_id, extra={"status": decision.status, "reasons": decision.reasons, "passes": decision.passes, "sweep": bool(decision.sweep_focus)})
        return decision

    def _recheck_video(self, state: CaseState, focus: str, *, progress=None, events: list[str]) -> bool:
        if not state.video_analysis or not state.video_path:
            return False
        try:
            merged = self.video_agent.recheck(state.video_path, state.video_analysis, focus, case_id=state.case_id, progress=progress)
        except Exception as exc:  # noqa: BLE001
            events.append(f"영상 재분석 실패: {str(exc)[:80]}")
            return False
        state.video_reanalysis_count += 1
        state.recheck_focus_history.append(focus)
        before = self._confirmed_labels(state)
        merge_video_facts(state, merged, threshold=self.video_agent.threshold)
        self._refresh_fact_labels(state)
        # 로그용 이벤트(모델이 쓴 변경 기록)와 사용자 안내용 이벤트(슬롯에서 만든 라벨)를 나눈다 — 채팅에는 후자만 쓴다
        events.append("영상 focus 재분석 완료 [" + focus.rstrip(".").strip()[:70] + "]: " + (", ".join(merged.changes_from_previous[:3]) or "기존 결론 유지"))
        gained = [label for label in self._confirmed_labels(state) if label not in before]
        events.append("영상 재확인 결과: " + ("; ".join(gained[:3]) if gained else "새로 확인된 사실 없음"))
        return True

    def _refresh_fact_labels(self, state: CaseState) -> None:
        """영상 확정 사실 문장을 현황판 칩 라벨로 줄인다 (영상 결과가 바뀔 때마다). 실패하면 슬롯 칩만 남는다."""
        from agent.presenters import slot_chips

        try:
            existing = [item["label"] for item in slot_chips(state)]
            state.video_fact_labels = generate_fact_labels(self.text_client, state, existing_labels=existing, run_logger=self.run_logger)
        except Exception as exc:  # noqa: BLE001
            self.run_logger.log(agent="master_agent", task="master_fact_labels", case_id=state.case_id, extra={"error": str(exc)[:300]})

    @staticmethod
    def _confirmed_labels(state: CaseState) -> list[str]:
        """영상에서 확정된 사실의 짧은 라벨 목록 (사건 현황판 슬롯 칩과 같은 규칙). 모델이 쓴 자유 문장·LLM 라벨은 쓰지 않는다 —
        재분석 전후 비교에 LLM 라벨을 넣으면 표현만 바뀐 것이 '새로 확인된 점'으로 보인다."""
        from agent.presenters import slot_chips

        return [item["label"] for item in slot_chips(state)]

    def _try_register_opponent(self, state: CaseState, events: list[str], *, progress=None, user_note: str = "") -> bool:
        """상대 차량이 차량 목록에 없을 때 사용자에게 묻기 전에(또는 사용자 확인을 들고) 영상 agent 가 그 차량을 찾아 등록한다.

        진입 방향·진행 경로 같은 사고 정황은 영상 agent 의 몫이지 사용자에게 물을 것이 아니다. critical 재분석 예산(기본 1회)을 쓴다."""
        video = state.video_analysis
        if not video or not state.video_path or len(video.vehicles) >= 2:
            return False  # 목록에 두 대 이상 있으면 '누가 부딪혔는지'의 문제라 여기서 다루지 않는다 (사용자 확인 질문으로)
        if state.critical_recheck_count >= self.max_critical_rechecks:
            return False
        candidate = _opponent_candidate_from_text(video)
        parts: list[str] = []
        if user_note and candidate:
            parts.append(f"사용자가 상대 차량이 {candidate}가 맞다고 확인함.")
        parts.append(
            f"요약·서술에 '{candidate}'(으)로 적힌 상대 차량이 차량 목록(vehicles)에 없다." if candidate else "충돌 상대 차량이 차량 목록(vehicles)에 없다."
        )
        parts.append("충돌 직전 구간을 다시 보고 그 차량을 목록에 id·설명과 함께 등록하고 collision_pair 를 확정하라. 상대 차량의 진입 방향(좌/우/맞은편)·진행·충돌 부위도 기록하라.")
        if user_note:
            parts.append(f"사용자 진술: {user_note.strip()[:120]}")
        events.append("상대 차량 미등록 → 영상 agent 재확인")
        if not self._recheck_video(state, " ".join(parts), progress=progress, events=events):
            return False
        state.critical_recheck_count += 1
        video = state.video_analysis
        resolved = bool(video) and len(video.vehicles) >= 2 and len(video.collision_pair.participants) == 2
        events.append("상대 차량 등록 " + ("성공" if resolved else "실패 → 사용자 확인"))
        return resolved

    def _apply_agent_rechecks(self, state: CaseState, rechecks, events: list[str], progress=None) -> bool:
        """Agent가 요청한 영상 재분석 쟁점을 (예산 안에서) 수행한다. 한 턴에 하나."""
        if not rechecks or not state.video_path or not state.video_analysis:
            return False
        if state.video_reanalysis_count >= self.max_master_rechecks:
            events.append("영상 재분석 예산 소진 → 남은 쟁점은 사용자 확인으로")
            return False
        target = rechecks[0]
        if progress:
            progress(f"Agent 판단: 영상으로 확인 가능 → focus 재분석: {target.focus[:60]}")
        return self._recheck_video(state, target.focus, progress=progress, events=events)

    @staticmethod
    def _strip_unfulfilled_recheck_promise(intro: str, events: list[str]) -> str:
        """LLM이 쓴 안내문이 '영상을 다시 확인할게요'라고 약속했는데 guardrail이 재분석을 하지 않았으면 그 문장을 뺀다."""
        if not intro or any(event.startswith("영상 focus 재분석 완료") for event in events):
            return intro
        sentences = re.split(r"(?<=[.!?])\s+", intro.strip())
        kept = [item for item in sentences if not re.search(r"(다시\s*(확인|분석)|재분석|재확인)", item)]
        return " ".join(kept).strip()

    _REQUEST_SENTENCE = re.compile(r"(알려\s*주세요|말씀해\s*주세요|답해\s*주세요|확인해\s*주세요|인가요\s*[?？]|나요\s*[?？]|까요\s*[?？]|[?？]\s*$)")

    @classmethod
    def _strip_request_sentences(cls, intro: str) -> str:
        """안내문(intro) 안의 질문·요청 문장을 뺀다. 실제 대화에서 모델이 안내문 끝에 '…블랙박스인지 알려주세요'를 쓰고
        바로 아래 질문 카드가 같은 것을 다시 물어 두 번 묻는 것처럼 보였다."""
        if not intro:
            return intro
        sentences = re.split(r"(?<=[.!?？])\s+", intro.strip())
        kept = [item for item in sentences if not cls._REQUEST_SENTENCE.search(item)]
        return " ".join(kept).strip()

    def _recheck_note(self, events: list[str], state: CaseState) -> str:
        """이번 턴에 영상을 다시 봤으면 한 줄로 알린다. Video Agent에 준 지시문이나 모델이 쓴 변경 기록(내부 표현)은
        보여주지 않고, 슬롯에서 새로 확정된 사실의 라벨만 말한다."""
        if not any(event.startswith("영상 focus 재분석 완료") for event in events):
            return ""
        gained: list[str] = []
        for event in events:
            if event.startswith("영상 재확인 결과: "):
                for label in event[len("영상 재확인 결과: "):].split("; "):
                    label = label.strip()
                    if label and label != "새로 확인된 사실 없음" and label not in gained:
                        gained.append(label)
        # 사용자 답변 없이 agent 가 스스로 돌린 재확인이면 '말씀해 주신 내용을 바탕으로' 라고 하지 않는다
        agent_initiated = any(event.startswith("상대 차량 미등록") for event in events)
        lead = "영상을 다시 확인" if agent_initiated else "말씀해 주신 내용을 바탕으로 영상을 다시 확인"
        if gained:
            return f"{lead}했어요. 새로 확인된 점: " + ", ".join(gained[:3]) + "."
        return f"{lead}했는데, 결론은 그대로예요."

    def _sufficiency(self, state: CaseState) -> SufficiencyResult:
        # 판정이 이미 완료되어 유효하면 LLM 보강 없이 deterministic 체크만 수행한다 (턴당 호출 절감)
        use_llm = self.settings.agent.llm_sufficiency_refinement and (state.fault_assessment is None or state.assessment_invalidated)
        result = check_information_sufficiency(state, self.text_client, use_llm=use_llm, run_logger=self.run_logger)
        state.missing_information = result.missing_information
        if state.current_stage == "FACT_COLLECTING" and result.ready_for_rag and not state.retrieved_cases:
            state.set_stage("READY_FOR_RAG")
        return result

    def _run_rag(self, state: CaseState, events: list[str], *, progress=None) -> list[RetrievedCase]:
        state.set_stage("RAG_SEARCHING")
        try:
            result = self.rag_tool.search(state, progress=progress)
        except Exception as exc:  # noqa: BLE001
            events.append(f"유사 심의사례 검색 실패: {str(exc)[:100]}")
            state.notes.append(f"RAG 실패: {exc}")
            state.set_stage("READY_FOR_RAG")
            return []
        state.rag_query = result.query
        state.retrieved_cases = result.cases
        state.rag_tier = result.tier
        if result.fallback_reason:
            if result.tier == "none":
                note = f"참고할 심의사례·인정기준 도표 없음 → 기준 없이 임시 판정 ({result.fallback_reason})"
            else:
                note = f"유사 심의사례 없음 → 과실비율 인정기준으로 대체 ({result.fallback_reason})"
            events.append(note)
            if note not in state.notes:
                state.notes.append(note)
        events.append("유사 심의사례 검색: " + (", ".join(item.case_id for item in result.cases) or "없음"))
        state.set_stage("CASE_REVIEW" if not state.case_review_done else "READY_FOR_ASSESSMENT")
        return result.cases

    def _needs_rag(self, state: CaseState) -> bool:
        # 검색을 이미 했는데 참고 기준이 없었던 사건(rag_tier == "none")은 사실이 바뀌기 전까지 다시 찾지 않는다
        return state.assessment_invalidated or (not state.retrieved_cases and state.rag_tier != "none")

    def _run_assessment(self, state: CaseState, events: list[str], *, progress=None, force_provisional: bool = False) -> Optional[FaultAssessment]:
        if self._needs_rag(state):
            self._run_rag(state, events, progress=progress)
        if not state.retrieved_cases:
            if state.rag_tier != "none":
                events.append("유사 사례가 없어 판정을 보류")
                return None
            events.append("참고 기준 없음 → 일반 원칙으로 임시 판정")
            force_provisional = True
        state.set_stage("READY_FOR_ASSESSMENT")
        assessment = assess_fault_ratio(self.text_client, state, state.retrieved_cases, run_logger=self.run_logger, force_provisional=force_provisional)
        state.fault_assessment = assessment
        state.assessment_invalidated = False
        state.assessment_invalidation_reasons = []
        state.set_stage("ASSESSMENT_COMPLETE")
        events.append(f"예상 과실비율 {assessment.fault_ratio.as_text()} ({assessment.assessment_type}, conf {assessment.confidence:.2f})")
        return assessment

    def _conclude(
        self,
        state: CaseState,
        events: list[str],
        warnings: list[str],
        *,
        intro: Optional[str] = None,
        force_provisional: bool = False,
        progress=None,
    ) -> Optional[AgentResponse]:
        """판정 준비가 끝났을 때의 마무리. 기본은 바로 종합 판정, 서버 모드는 판정 요청(REQUEST_ASSESSMENT)만 남긴다.

        서버 모드에서도 유사 심의사례 검색은 여기서 끝내 두어 judge 단계가 검색을 반복하지 않게 한다.
        """
        if not self.defer_conclusions:
            assessment = self._run_assessment(state, events, progress=progress, force_provisional=force_provisional)
            if assessment is None:
                return None
            return self._assessment_response(state, assessment, events, warnings, intro=intro)
        if self._needs_rag(state):
            self._run_rag(state, events, progress=progress)
        if not state.retrieved_cases and state.rag_tier != "none":
            events.append("유사 사례가 없어 판정을 보류")
            return None
        rejudge = state.fault_assessment is not None
        state.set_stage("READY_FOR_ASSESSMENT")
        events.append("판정 준비 완료 → 판정 요청 (재판정)" if rejudge else "판정 준비 완료 → 판정 요청")
        if rejudge:
            body = "바뀐 내용을 반영해서 예상 과실비율을 다시 계산할게요. 잠시만 기다려 주세요."
        elif not state.retrieved_cases:
            body = (
                "지금까지 확인한 사실과 딱 맞는 심의사례나 과실비율 인정기준 도표를 찾지 못했어요.\n"
                "일반 원칙으로만 본 임시 예상치를 계산할게요. 잠시만 기다려 주세요."
            )
        elif state.rag_tier == "fault_standard":
            # 심의사례가 없어 도표가 기준인 경우: 도표를 보여주고, 기본비율 ± 수정요소 계산으로 넘어간다
            body = (
                "꼭 맞는 심의사례가 없어서 과실비율 인정기준 도표를 대신 찾았어요.\n" + self._cases_block(state, compact=True)
                + "\n이 도표의 기본 과실비율에 확인된 수정요소를 더하고 빼서 예상 과실비율을 계산할게요. 잠시만 기다려 주세요."
            )
        else:
            # 사용자가 판정을 요청한 시점에 찾은 사례를 한 줄씩 보여주고 판정으로 넘어간다 (판정 카드의 '근거'에도 같은 사례가 붙는다)
            body = (
                "지금까지 확인한 사실로 비슷한 심의사례를 찾았어요.\n" + self._cases_block(state, compact=True)
                + "\n이 사례들과 사고 사실을 종합해서 예상 과실비율을 계산할게요. 잠시만 기다려 주세요."
            )
        message = (intro.strip() + "\n" if intro else "") + body
        data = {"rejudge": rejudge, "provisional": force_provisional, "similar_cases": self._similar_cases_data(state), "tier": state.rag_tier, "events": events}
        return self._respond(state, message=message, action="REQUEST_ASSESSMENT", data=data, warnings=warnings)

    # ------------------------------------------------------------------ responses
    def _respond(self, state: CaseState, *, message: str, action: Action, data: Optional[dict] = None, warnings: Optional[list[str]] = None) -> AgentResponse:
        state.add_message("assistant", message, action=action)
        return AgentResponse(case_id=state.case_id, message=message, stage=state.current_stage, action=action, data=data or {}, warnings=warnings or [])

    def _video_intro(self, state: CaseState) -> str:
        video = state.video_analysis
        if video is None:
            return "영상 분석 결과가 없어서 말씀해 주신 내용을 기준으로 진행할게요."
        # 영상 모델이 쓴 요약에는 vehicle_1 같은 내부 ID가 섞여 있다 → 사용자에게는 차량 설명으로 바꿔 보여준다
        summary = self._humanize_vehicle_ids(video, video.short_summary.strip()) or "영상 분석을 마쳤어요."
        names = self._vehicle_names(video)
        # 촬영 차량이 사용자 차량인지(자차)는 아직 물어보기 전이므로 단정하지 않는다 → '블랙박스 촬영 차량'
        others = [names[v.id] for v in video.vehicles if not v.is_ego]
        vehicles_text = ""
        if others:
            vehicles_text = " 영상에서 확인된 차량: 블랙박스 촬영 차량, " + ", ".join(others) + "."
        pair = video.collision_pair
        pair_text = ""
        if len(pair.participants) == 2:
            first, second = (names.get(pid, pid) for pid in pair.participants)
            pair_text = f" 충돌한 두 차량은 {first}과(와) {second}로 보여요(신뢰도 {pair.confidence:.2f})."
        # 2차 분석의 변경 기록(changes_from_previous)은 모델이 쓴 내부 표현이라 채팅에 싣지 않는다 (로그·events 에만 남는다).
        # 2차 분석으로 확정된 사실은 요약·확인된 사실 칩·질문에 이미 반영돼 있다.
        return f"영상을 분석했어요. {summary}{vehicles_text}{pair_text}"

    @staticmethod
    def _vehicle_names(video: VideoResult) -> dict[str, str]:
        """vehicle_N → 사용자에게 보여줄 이름 (블랙박스 차량 / 흰색 승용차 / 상대 차량)."""
        names: dict[str, str] = {}
        for vehicle in video.vehicles:
            desc = (vehicle.description or "").strip()
            names[vehicle.id] = "블랙박스 차량" if vehicle.is_ego else (desc[:20] if desc else "상대 차량")
        return names

    @classmethod
    def _humanize_vehicle_ids(cls, video: VideoResult, text: str) -> str:
        """'흰색 승용차(vehicle_2)' → '흰색 승용차', 홀로 쓰인 'vehicle_2' → 차량 설명."""
        if not text:
            return text
        names = cls._vehicle_names(video)
        text = re.sub(r"\s*\((vehicle_\d+)\)", "", text)  # 설명 뒤에 붙은 ID 괄호는 지운다
        text = re.sub(r"(?<![A-Za-z_])(vehicle_\d+)(?![A-Za-z0-9_])", lambda m: names.get(m.group(1), "상대 차량" if m.group(1) != "vehicle_1" else "블랙박스 차량"), text)
        return text

    _CHANGE_FIELD_LABELS = {
        "turn_signal": "방향지시등", "braking": "제동", "brake": "제동", "lane_change": "차로 변경", "movement": "진행 방향",
        "signal": "신호", "speed": "속도", "estimated_speed": "속도", "position": "위치", "positions": "위치", "entered_first": "선진입",
        "lane": "차로", "trajectory": "진행 경로", "collision_part": "충돌 부위", "description": "식별 정보", "is_ego": "블랙박스 차량 여부",
    }
    # 한글은 \w 라서 \b 로는 "UNKNOWN으로" 를 못 잡는다 → 영문자 경계로만 자른다
    _CHANGE_STATUS_PATTERN = re.compile(r"\s*\(?(?<![A-Za-z])(CONFIRMED|PROBABLE|UNKNOWN|UNCERTAIN|LIKELY|POSSIBLE)(?![A-Za-z])(?:\s*,\s*(?:신뢰도|confidence)\s*[\d.]+)?\)?", re.IGNORECASE)
    _CHANGE_VALUE_LABELS = {"none": "없음", "unknown": "미확인", "true": "예", "false": "아니오", "left": "왼쪽", "right": "오른쪽", "on": "켜짐", "off": "꺼짐"}

    @classmethod
    def _second_pass_changes(cls, video: VideoResult, limit: int = 3) -> list[str]:
        """changes_from_previous 는 영상 모델이 쓴 내부 표현(vehicle_2.turn_signal, CONFIRMED 0.85)이 섞여 있다.
        사용자에게 보여줄 때는 차량 설명·한국어 필드명으로 바꾸고 상태 토큰은 지운다."""
        items: list[str] = []
        for raw in video.changes_from_previous:
            if raw == "재분석 상세 서술 보완":
                continue
            text = cls._humanize_change(video, raw)
            if text and text not in items:
                items.append(text[:160])
            if len(items) >= limit:
                break
        return items

    @classmethod
    def _humanize_change(cls, video: VideoResult, raw: str) -> str:
        """changes_from_previous 항목 하나를 사용자 문장으로 바꾼다."""
        names = cls._vehicle_names(video)

        def _field(match: "re.Match[str]") -> str:
            vehicle_name = names.get(match.group(1), match.group(1))
            field = match.group(2)
            label = cls._CHANGE_FIELD_LABELS.get(field.lower())
            return f"{vehicle_name} {label}" if label else f"{vehicle_name} {field}"

        text = re.sub(r"(?<![A-Za-z_])(vehicle_\d+)\.([A-Za-z_]+)", _field, raw)
        text = re.sub(r"(?<![A-Za-z_])(vehicle_\d+)(?![A-Za-z0-9_])", lambda m: names.get(m.group(1), m.group(1)), text)
        text = cls._CHANGE_STATUS_PATTERN.sub("", text)
        text = re.sub(r"['\"]([A-Za-z_]+)['\"]", lambda m: cls._CHANGE_VALUE_LABELS.get(m.group(1).lower(), m.group(1)), text)
        text = re.sub(r"\bcollision pair\b", "충돌 차량 조합", text)
        return re.sub(r"\s{2,}", " ", text).strip(" ;,")

    def _video_summary_data(self, state: CaseState) -> Optional[dict]:
        video = state.video_analysis
        if video is None:
            return None
        return {
            "short_summary": video.short_summary,
            "vehicles": [v.model_dump(include={"id", "description", "is_ego", "movement"}) for v in video.vehicles],
            "collision_pair": video.collision_pair.model_dump(include={"participants", "non_participants", "confidence"}),
            "completion": video.analysis_completion.model_dump(),
            "backend": video.video_backend,
        }

    def _similar_cases_data(self, state: CaseState) -> list[dict]:
        return [
            {"case_id": item.case_id, "title": item.title, "source_type": item.source_type, "decision_ratio": item.decision_ratio,
             "basic_ratio": item.basic_ratio, "relevance": item.relevance.relevance if item.relevance else None,
             "matched_factors": item.relevance.matched_factors if item.relevance else [],
             "different_factors": item.relevance.different_factors if item.relevance else []}
            for item in state.retrieved_cases[: self.settings.rag.final_top_k]
        ]

    def _cases_block(self, state: CaseState, *, compact: bool = False) -> str:
        """검색된 심의사례 목록. compact=True 면 사례마다 한 줄(판정 직전 안내용), 아니면 공통점·차이점까지."""
        top_k = self.settings.rag.final_top_k
        if state.rag_tier == "none":
            return "현재 사고 구조와 맞는 심의사례나 과실비율 인정기준 도표를 찾지 못했어요."
        if state.rag_tier == "fault_standard":
            lines = ["현재 사고 구조와 맞는 심의사례가 없어서 과실비율 인정기준 도표를 대신 찾았어요."] if not compact else []
        else:
            lines = [f"현재 사건과 비슷한 심의사례예요 (최대 {top_k}개)."] if not compact else []
        for case in state.retrieved_cases[:top_k]:
            rel = case.relevance
            if case.source_type != "deliberation_case" and (case.role_a or case.role_b):
                lines.append(f"- 도표 {case.case_id} {case.title or ''} · A {case.role_a or '?'} / B {case.role_b or '?'} · {case.ratio_summary()}".rstrip())
            else:
                lines.append(f"- {case.case_id} {case.title or ''} · {case.ratio_summary()}".rstrip())
            if compact:
                continue
            if rel and rel.matched_factors:
                lines.append(f"  공통점: {', '.join(rel.matched_factors[:3])}")
            if rel and rel.different_factors:
                lines.append(f"  차이점: {', '.join(rel.different_factors[:2])}")
        if not compact:
            lines.append("심의사례의 사실관계는 현재 사건과 다를 수 있어서, 차이점을 함께 살펴볼게요.")
        return "\n".join(lines)

    def _ack(self, events: list[str], is_initial: bool, state: CaseState) -> str:
        if is_initial:
            return self._video_intro(state)
        facts = [event for event in events if event.startswith("새 사실 반영")]
        conflicts = [event for event in events if event.startswith("영상과 충돌")]
        parts = []
        claim_ack = self._claim_ack(state, events)
        if claim_ack:
            parts.append(claim_ack)
        elif facts:
            parts.append("확인했어요.")
        if conflicts:
            parts.append("일부 말씀은 영상에서 직접 확인되지 않아서 진술로만 기록해 두었어요.")
        parts.append("한 가지만 더 확인할게요.")
        return " ".join(parts)

    @staticmethod
    def _claim_ack(state: CaseState, events: list[str]) -> str:
        """이번 턴에 상대 보험사 주장 비율이 새로 기록됐으면 그 사실을 한 문장으로 확인해 준다 (판정 전에는 비교 대상일 뿐 판정이 아니다)."""
        if not any(event.startswith("상대 보험사 주장 기록") for event in events):
            return ""
        from agent.presenters import parse_opponent_claim, ratio_text

        claim = parse_opponent_claim(state.opponent_claim)
        if claim:
            return f"상대 보험사 주장({ratio_text(claim['mine'], claim['other'])})은 기록해 두었다가 판정 카드에서 비교해 드릴게요."
        return "상대 보험사 주장은 기록해 두었어요."

    _RATIO_MENTION = re.compile(r"\d{1,3}\s*[:：대]\s*\d{1,3}|\d{1,3}\s*%|앞서\s*(?:말씀|안내|제시)")

    @classmethod
    def _scrub_unfounded_ratio(cls, state: CaseState, text: str) -> str:
        """판정이 아직 없는 사건에서 자유 답변이 비율 숫자를 말하면 그 문장을 뺀다.

        실제 웹 대화에서 respond 모델이 프롬프트 예시 문장("앞서 말씀드린 예상 과실비율 30:70")을 그대로 베껴
        존재하지 않는 판정을 언급한 일이 있었다. 사용자가 전한 상대 보험사 주장을 옮기는 문장('주장')은 남긴다."""
        if not text or state.fault_assessment is not None:
            return text
        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        kept = [item for item in sentences if not (cls._RATIO_MENTION.search(item) and "주장" not in item) and not re.search(r"앞서\s*(?:말씀|안내|제시)", item)]
        return " ".join(kept).strip()

    def _not_ready_message(self, sufficiency: SufficiencyResult) -> str:
        missing = ", ".join(item.reason or item.field for item in sufficiency.sorted_missing()[:3])
        return f"아직 예상 과실비율을 판정하기에는 정보가 부족해요. 부족한 항목: {missing}. 영상을 다시 확인하거나 추가로 확인한 뒤 판정할게요."

    def _assessment_response(self, state: CaseState, assessment: FaultAssessment, events: list[str], warnings: list[str], *, intro: Optional[str] = None) -> AgentResponse:
        lines = []
        if intro:
            lines.append(intro)
        label = "임시 예상" if assessment.assessment_type == "provisional" else "예상"
        lines.append(f"영상과 확인된 사실, 유사 심의사례를 기준으로 보면 {label} 과실비율은 나 {assessment.fault_ratio.user} : 상대 {assessment.fault_ratio.opponent} 정도예요. (신뢰도 {assessment.confidence:.2f})")
        chart_based = state.rag_tier == "fault_standard"
        if assessment.anchor_case_id:
            if chart_based:
                anchor_line = f"기준 인정기준 도표: {assessment.anchor_case_id} (기본비율 나 {assessment.anchor_ratio})"
            else:
                anchor_line = f"기준 심의사례: {assessment.anchor_case_id} (결정비율 {assessment.anchor_ratio})"
            if assessment.anchor_enforced:
                anchor_line += " — 확인된 수정요소가 없어서 기준값을 그대로 적용했어요"
            lines.append(anchor_line)
        if assessment.calculation:
            lines.append(f"계산: {assessment.calculation}")
        if assessment.possible_range:
            lines.append(f"예상 범위: {' ~ '.join(assessment.possible_range)}")
        primary = [item for item in assessment.matched_cases if item.case_id in assessment.primary_case_ids] or assessment.matched_cases[:1]
        if primary:
            lines.append(("참고 도표: " if chart_based else "참고 심의사례: ") + ", ".join(f"{item.case_id}({item.decision_ratio or item.basic_ratio or '비율 미상'})" for item in primary))
        if chart_based:
            lines.append("참고: 현재 사고 구조와 맞는 심의사례가 없어서 과실비율 인정기준 도표의 기본비율에 확인된 수정요소를 더하고 빼서 계산했어요.")
        elif state.rag_tier == "none":
            lines.append("참고: 꼭 맞는 심의사례나 인정기준 도표를 찾지 못해서 일반 원칙으로만 본 임시 예상치예요.")
        if assessment.reasoning_summary:
            lines.append("근거:\n" + "\n".join(f"- {item}" for item in assessment.reasoning_summary[:6]))
        if assessment.adjustment_factors:
            applied = [f"{item.factor}({'적용' if item.applies else '확인 불가'})" for item in assessment.adjustment_factors[:4]]
            lines.append("수정요소(비율을 더하거나 빼는 조건): " + ", ".join(applied))
        if assessment.uncertainties:
            lines.append("아직 확실하지 않은 점:\n" + "\n".join(f"- {item}" for item in assessment.uncertainties[:4]))
        if assessment.ratio_dependencies:
            lines.append("추가로 확인되면 바뀔 수 있는 부분: " + "; ".join(assessment.ratio_dependencies[:3]))
        lines.append("이 비율은 예상치라 법적으로 확정된 판단은 아니에요. 궁금한 점을 물어보시거나 사건경위서·반박의견서 작성을 요청하실 수 있어요.")
        return self._respond(state, message="\n".join(lines), action="SHOW_FAULT_ASSESSMENT",
                             data={"fault_assessment": assessment.model_dump(), "similar_cases": self._similar_cases_data(state), "events": events}, warnings=warnings)

    def _similar_cases_response(self, state: CaseState, warnings: list[str]) -> AgentResponse:
        if not state.retrieved_cases:
            return self._respond(state, message="현재 사건 구조로 찾은 유사 심의사례가 아직 없어요. 사고 장소와 진행 방향이 더 확인되면 다시 찾아볼게요.", action="INFO", warnings=warnings)
        message = self._cases_block(state)
        data = {"similar_cases": [case.model_dump(exclude={"excerpt"}) for case in state.retrieved_cases[: self.settings.rag.final_top_k]], "tier": state.rag_tier, "query": state.rag_query.model_dump() if state.rag_query else None}
        return self._respond(state, message=message, action="SHOW_SIMILAR_CASES", data=data, warnings=warnings)

    def _document_response(self, state: CaseState, intent: str, warnings: list[str], events: list[str]) -> AgentResponse:
        if state.fault_assessment is None or state.assessment_invalidated:
            return self._respond(state, message="문서를 만들려면 먼저 예상 과실비율 판정이 끝나야 해요. 부족한 정보를 확인한 뒤 판정하고 문서를 만들게요.", action="INFO", warnings=warnings)
        kind = "report" if intent == "request_incident_report" else "rebuttal"
        label = "사건경위서" if kind == "report" else "반박의견서"
        claim_note = ""
        if not state.opponent_claim and kind == "rebuttal":
            claim_note = "\n\n상대 보험사가 제시한 과실비율이나 주장을 아직 못 들었어요. 알려주시면 반박 논리를 더 정확하게 쓸 수 있어요."
        if self.defer_conclusions:
            # 서버 모드: 문서 생성은 백엔드 Job(write)이 수행한다
            events.append(f"문서 작성 요청: {kind}")
            message = f"{label} 초안을 만들게요. 잠시만 기다려 주세요." + claim_note
            return self._respond(state, message=message, action="REQUEST_DOCUMENT", data={"kind": kind, "events": events}, warnings=warnings)
        if kind == "report":
            document = self.document_agent.generate_incident_report(state)
            state.incident_report = document
            state.set_stage("REPORT_COMPLETE")
        else:
            document = self.document_agent.generate_rebuttal_opinion(state)
            state.rebuttal_opinion = document
            state.set_stage("REBUTTAL_COMPLETE")
        warnings.extend(document.warnings)
        message = f"{label} 초안을 만들었어요.\n\n{document.text}" + claim_note
        return self._respond(state, message=message, action="SHOW_DOCUMENT", data={"document": document.model_dump()}, warnings=warnings)

    def _answer(self, state: CaseState, *, message: str, intent: IntentResult, events: list[str], warnings: list[str], is_initial: bool, respond: bool = True):
        client = self.text_client
        text = ""
        if client is not None and message:
            system = load_prompt("master_agent", "system")
            task = load_prompt("master_agent", "respond")
            user = task.render(
                user_message=wrap_user_text(message),
                intent=intent.primary_intent,
                case_state=compact_json(state.compact(), max_chars=10000),
                recent_messages=compact_json(state.recent_messages(self.settings.agent.recent_message_window)),
                turn_events=compact_json(events),
            )
            try:
                response = client.generate_json(system=system.text, user=user, schema=_RespondOutput, task=task.task)
                text = _RespondOutput.model_validate(response.data).message.strip()
                self.run_logger.log(agent="master_agent", task=task.task, case_id=state.case_id, model=response.metrics.model,
                                    prompt_version=f"{system.version_id}+{task.version_id}", metrics=response.metrics)
            except Exception as exc:  # noqa: BLE001
                self.run_logger.log(agent="master_agent", task=task.task, case_id=state.case_id, extra={"error": str(exc)[:300]})
            # guardrail: 하지 않은 재분석 약속, 없는 판정의 비율 숫자는 모델이 써도 사용자에게 내보내지 않는다
            text = self._strip_unfulfilled_recheck_promise(text, events)
            text = self._scrub_unfounded_ratio(state, text)
        if not text:
            text = self._deterministic_answer(state, intent, events, is_initial)
        if not respond:
            return text
        action: Action = "SHOW_VIDEO_SUMMARY" if is_initial and state.video_analysis else "ANSWER"
        return self._respond(state, message=text, action=action, data={"events": events, "video_summary": self._video_summary_data(state) if is_initial else None}, warnings=warnings)

    def _deterministic_answer(self, state: CaseState, intent: IntentResult, events: list[str], is_initial: bool) -> str:
        parts = []
        if is_initial:
            parts.append(self._video_intro(state))
        if intent.primary_intent == "ask_explanation" and state.fault_assessment:
            assessment = state.fault_assessment
            parts.append(f"예상 과실비율 나 {assessment.fault_ratio.user} : 상대 {assessment.fault_ratio.opponent}의 근거는 이래요: " + "; ".join(assessment.reasoning_summary[:4]))
            if assessment.uncertainties:
                parts.append("아직 확실하지 않은 점: " + "; ".join(assessment.uncertainties[:3]))
        facts = [event for event in events if event.startswith("새 사실 반영")]
        if facts:
            parts.append("말씀하신 내용을 사건 정보에 반영했어요.")
        conflicts = [event for event in events if event.startswith("영상과 충돌")]
        if conflicts:
            parts.append("그 내용은 진술로 기록해 두었지만, 영상에서는 직접 확인되지 않아요.")
        if state.assessment_invalidated:
            parts.append("중요한 사실이 바뀌어서 기존 판정을 다시 봐야 해요. 과실비율을 요청하시면 다시 판정할게요.")
        if intent.primary_intent == "general_question" and state.fault_assessment and not state.assessment_invalidated:
            parts.append(
                "보험사가 제시한 과실비율에 동의하기 어려우면, 영상에서 확인된 사실과 유사 심의사례를 근거로 반박의견서를 만들어 보험사에 보낼 수 있어요. "
                "그래도 합의가 안 되면 과실비율분쟁심의위원회에 심의를 청구하는 방법도 있어요. 반박의견서 작성을 요청하시면 지금 사건 근거로 초안을 만들어 드릴게요."
            )
        if not parts:
            parts.append("확인했어요. 사고 사실을 더 알려주시거나 과실비율 판정, 유사 심의사례, 사건경위서·반박의견서 작성을 요청하실 수 있어요.")
        return " ".join(parts)
