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
from case.questions import format_questions, generate_followup_questions, mark_pending_unknown, unregister_questions, video_gap_candidates
from case.review import (
    ADDITIONAL_FACTS_FIELD,
    generate_case_review_questions,
    mark_review_done,
    review_questions_remaining,
)
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
from video.validation import FocusTarget, collision_window_seconds

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
    ):
        self.settings = settings or get_settings()
        self.run_logger = run_logger or get_run_logger()
        self.use_llm = use_llm
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
        if state.pending_questions or len(message.strip()) > 30 or re.search(r"\d", message):
            return False
        if intent.contains_new_facts or intent.contains_opponent_claim:
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
                if decision.status == "NEEDS_USER_CONFIRMATION" and decision.confirmation_question:
                    question = Question(field="collision.participants_confirmed", question=decision.confirmation_question, importance="critical", asked_turn=state.turn_count)
                    state.pending_questions = [question]
                    if question.field not in state.asked_fields:
                        state.asked_fields.append(question.field)
                    state.set_stage("FACT_COLLECTING")
                    return self._respond(state, message=self._video_intro(state) + "\n\n" + decision.confirmation_question, action="ASK_USER",
                                         data={"questions": [question.model_dump()], "video_summary": self._video_summary_data(state)}, warnings=warnings)

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

        # 5. 사실 수집 단계 — 한 턴에 한 질문.
        #    (a) 코드 필수 항목(영상 소유 관계·사용자 차량 식별 등 critical)은 반드시 먼저 묻는다.
        #    (b) 그 외에는 Agent가 사건 상태를 보고 "판정에 영향을 주는데 미확인인 사실"을 스스로 추론해 고른다.
        #        코드 후보(video_gap_candidates)는 LLM 호출 실패 시 fallback으로만 쓴다.
        askable = [item for item in sufficiency.sorted_missing() if item.user_answerable and item.importance in {"critical", "high", "medium"}]
        known_fields = {item.field for item in askable}
        askable += [item for item in video_gap_candidates(state) if item.field not in known_fields]
        critical = [item for item in askable if item.importance == "critical"]
        agent_turn = (
            state.video_analyzed
            and not state.retrieved_cases
            and not explicit_conclusion
            and not wants_cases
            and state.fact_question_rounds < self.settings.agent.max_fact_question_rounds
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
            )
            # Agent가 "영상으로 확인 가능"하다고 판단한 쟁점은 사용자에게 묻지 않고 Video Agent 재분석으로 해결한다
            if rechecks and not critical and self._apply_agent_rechecks(state, rechecks, events, progress):
                unregister_questions(state, questions)
                sufficiency = self._sufficiency(state)
                intro, questions, _ = generate_followup_questions(
                    self.text_client, state, [item for item in video_gap_candidates(state) if item.importance == "high"],
                    max_questions=self.settings.agent.max_questions_per_turn, run_logger=self.run_logger,
                    intro_default="", use_llm_intro=False, allow_agent_choice=True,
                )
            if questions and not critical:
                state.fact_question_rounds += 1
            if questions:
                state.set_stage("FACT_COLLECTING")
                text = format_questions(intro if intro else self._ack(events, is_initial, state), questions)
                if first_turn:
                    text = self._with_second_pass_note(state, text)
                recheck_note = self._recheck_note(events)
                if recheck_note:
                    text = recheck_note + "\n" + text
                if explicit_conclusion:
                    state.pending_intent = intent.primary_intent
                    text = ("예상 과실비율을 판정하기 전에" if wants_assessment else "문서를 작성하기 전에") + " 확인이 필요한 사항이 있습니다.\n" + text
                return self._respond(state, message=text, action="ASK_USER", data={"questions": [q.model_dump() for q in questions], "agent_reasoning": questions[0].reasoning, "missing_information": [m.model_dump() for m in sufficiency.missing_information], "video_summary": self._video_summary_data(state) if is_initial else None}, warnings=warnings)

        # 5-1. 사건경위서에는 사고 일시가 필요하므로, 아직 모르면 한 번 묻고 답변 후 이어서 작성한다
        if intent.primary_intent == "request_incident_report" and not state.accident_datetime.date.is_known() and "accident_datetime.date" not in state.asked_fields:
            date_item = next((item for item in sufficiency.missing_information if item.field == "accident_datetime.date"), None)
            if date_item is not None:
                intro, questions, _ = generate_followup_questions(self.text_client, state, [date_item], max_questions=1, run_logger=self.run_logger, allow_agent_choice=False)
                if questions:
                    state.pending_intent = intent.primary_intent
                    text = "사건경위서에 사고 일시가 들어가야 합니다. " + format_questions("", questions)
                    return self._respond(state, message=text, action="ASK_USER", data={"questions": [q.model_dump() for q in questions]}, warnings=warnings)

        # 6. 유사 심의사례 검색 → 제시 → 검토 대화 시작 (판정은 아직 하지 않는다)
        needs_assessment = state.fault_assessment is None or state.assessment_invalidated
        if (sufficiency.ready_for_rag or explicit_conclusion or wants_cases) and (not state.retrieved_cases or state.assessment_invalidated):
            cases = self._run_rag(state, events, progress=progress)
            if cases and not explicit_conclusion and not state.case_review_done:
                return self._present_cases_and_review(state, events, warnings, is_initial=is_initial, progress=progress)
            if wants_cases and not explicit_conclusion:
                return self._similar_cases_response(state, warnings)

        # 7. 심의사례 검토 대화 진행 → 준비되면 종합 판정
        if state.case_review_started and not state.case_review_done and not explicit_conclusion and not wants_cases:
            review_response = self._continue_case_review(state, message=message, intent=intent, events=events, answered_fields=answered_fields, sufficiency=sufficiency, warnings=warnings, progress=progress)
            if review_response is not None:
                return review_response

        # 8. 명시적 판정/문서 요청 또는 재평가
        if (explicit_conclusion or state.assessment_invalidated) and needs_assessment:
            if state.case_review_started and not state.case_review_done:
                mark_review_done(state, "사용자 요청으로 판정 진행")
            assessment = self._run_assessment(state, events, progress=progress, force_provisional=not sufficiency.ready_for_assessment)
            if assessment is not None and not wants_document and not wants_cases:
                intro = "중요한 사실이 바뀌어 다시 판정했습니다." if "재평가" in " ".join(events) else None
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
        merge_video_facts(state, merged, threshold=self.video_agent.threshold)
        events.append("영상 focus 재분석 완료 [" + focus.rstrip(".").strip()[:70] + "]: " + (", ".join(merged.changes_from_previous[:3]) or "기존 결론 유지"))
        return True

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
    def _recheck_note(events: list[str]) -> str:
        done = [event for event in events if event.startswith("영상 focus 재분석 완료")]
        if not done:
            return ""
        topics = [item.split("[", 1)[1].split("]", 1)[0] for item in done[:2] if "[" in item and "]" in item]
        return "영상에서 확인할 수 있는 부분은 다시 분석했습니다 (" + "; ".join(topics) + ")."

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
            note = f"유사 심의사례 없음 → 과실비율 인정기준으로 대체 ({result.fallback_reason})"
            events.append(note)
            if note not in state.notes:
                state.notes.append(note)
        events.append("유사 심의사례 검색: " + (", ".join(item.case_id for item in result.cases) or "없음"))
        state.set_stage("CASE_REVIEW" if not state.case_review_done else "READY_FOR_ASSESSMENT")
        return result.cases

    def _run_assessment(self, state: CaseState, events: list[str], *, progress=None, force_provisional: bool = False) -> Optional[FaultAssessment]:
        if not state.retrieved_cases or state.assessment_invalidated:
            self._run_rag(state, events, progress=progress)
        if not state.retrieved_cases:
            events.append("유사 사례가 없어 판정을 보류")
            return None
        state.set_stage("READY_FOR_ASSESSMENT")
        assessment = assess_fault_ratio(self.text_client, state, state.retrieved_cases, run_logger=self.run_logger, force_provisional=force_provisional)
        state.fault_assessment = assessment
        state.assessment_invalidated = False
        state.assessment_invalidation_reasons = []
        state.set_stage("ASSESSMENT_COMPLETE")
        events.append(f"예상 과실비율 {assessment.fault_ratio.as_text()} ({assessment.assessment_type}, conf {assessment.confidence:.2f})")
        return assessment

    # ------------------------------------------------------------------ case review phase
    def _review_step(self, state: CaseState, events: list[str], progress=None):
        """검토 질문 생성 + Agent가 요청한 영상 재분석 수행(있으면 재생성)."""
        summary, questions, rechecks = generate_case_review_questions(
            self.text_client, state, state.retrieved_cases, max_questions=self.settings.agent.max_questions_per_turn, run_logger=self.run_logger
        )
        if rechecks and self._apply_agent_rechecks(state, rechecks, events, progress):
            # 재분석으로 상태가 바뀌었으니 Agent가 다시 판단한다 (같은 쟁점은 recheck_history로 반복 방지)
            unregister_questions(state, questions, review=True)
            summary, questions, _ = generate_case_review_questions(
                self.text_client, state, state.retrieved_cases, max_questions=self.settings.agent.max_questions_per_turn, run_logger=self.run_logger
            )
        return summary, questions

    def _present_cases_and_review(self, state: CaseState, events: list[str], warnings: list[str], *, is_initial: bool, progress=None) -> AgentResponse:
        summary, questions = self._review_step(state, events, progress)
        state.set_stage("CASE_REVIEW")
        if not questions:
            # Agent가 더 확인할 것이 없다고 판단 → 사례를 보여주고 바로 종합 판정으로 이어간다
            mark_review_done(state, "검토할 미확인 쟁점 없음")
            assessment = self._run_assessment(state, events, progress=progress)
            if assessment is not None:
                intro = (self._video_intro(state) + "\n" if is_initial else "") + self._cases_block(state) + ("\n" + summary if summary else "") + "\n확인이 더 필요한 사항이 없어 심의사례와 사고 사실을 종합하여 판정했습니다."
                return self._assessment_response(state, assessment, events, warnings, intro=intro)
        lines = []
        if is_initial:
            lines.append(self._video_intro(state))
        lines.append(self._cases_block(state))
        if summary:
            lines.append(summary)
        recheck_note = self._recheck_note(events)
        if recheck_note:
            lines.append(recheck_note)
        lines.append("아직 과실비율을 판정하지 않았습니다. 판정 전에 한 가지 확인하겠습니다." if len(questions) == 1 else "아직 과실비율을 판정하지 않았습니다. 판정 전에 몇 가지만 더 확인하겠습니다.")
        lines.append(format_questions("", questions))
        data = {
            "similar_cases": self._similar_cases_data(state),
            "tier": state.rag_tier,
            "review_summary": summary,
            "questions": [q.model_dump() for q in questions],
            "agent_reasoning": questions[0].reasoning if questions else [],
            "video_summary": self._video_summary_data(state) if is_initial else None,
        }
        return self._respond(state, message="\n".join(line for line in lines if line), action="SHOW_SIMILAR_CASES", data=data, warnings=warnings)

    def _continue_case_review(
        self,
        state: CaseState,
        *,
        message: str,
        intent: IntentResult,
        events: list[str],
        answered_fields: set[str],
        sufficiency: SufficiencyResult,
        warnings: list[str],
        progress=None,
    ) -> Optional[AgentResponse]:
        remaining = review_questions_remaining(state)
        answered_now = bool(answered_fields) or any(event.startswith("새 사실 반영") for event in events)
        # '추가 정황 있나요?' 같은 열린 질문은 새 사실이 있거나 부정 답변이면 답한 것으로 본다
        if remaining and all(item.field == ADDITIONAL_FACTS_FIELD for item in remaining):
            if answered_now or _NEGATIVE_ANSWER.search(message):
                state.review_answers[ADDITIONAL_FACTS_FIELD] = "provided" if answered_now else "none"
                state.pending_questions = [item for item in state.pending_questions if item.field != ADDITIONAL_FACTS_FIELD]
                remaining = []

        if remaining and intent.primary_intent in {"general_question", "ask_explanation", "provide_opponent_claim"}:
            # 사용자의 질문에 먼저 답하고, 남은 확인 질문을 다시 붙인다
            answer = self._answer(state, message=message, intent=intent, events=events, warnings=warnings, is_initial=False, respond=False)
            for item in remaining:
                item.ask_count += 1
                item.asked_turn = state.turn_count
            text = answer + "\n\n확인이 필요한 사항이 남아 있습니다.\n" + format_questions("", remaining)
            return self._respond(state, message=text, action="ASK_USER", data={"questions": [q.model_dump() for q in remaining], "review": True}, warnings=warnings)

        if remaining:
            for item in remaining:
                item.ask_count += 1
                item.asked_turn = state.turn_count
            prefix = "확인했습니다. " if answered_now else "답변이 확인되지 않아 다시 여쭤봅니다. 모르시면 '모름'이라고 답해 주셔도 됩니다. "
            text = prefix + "\n" + format_questions("", remaining)
            return self._respond(state, message=text, action="ASK_USER", data={"questions": [q.model_dump() for q in remaining], "review": True}, warnings=warnings)

        # 답을 받았으면 Agent가 다음으로 확인할 사실이 더 있는지 스스로 판단한다 (한 턴에 한 질문, 최대 max_review_rounds회)
        if state.review_rounds < self.settings.agent.max_review_rounds:
            summary, questions = self._review_step(state, events, progress)
            if questions:
                prefix = "확인했습니다. " if answered_now else ""
                recheck_note = self._recheck_note(events)
                text = prefix + (recheck_note + "\n" if recheck_note else "") + "판정 전에 한 가지 더 확인하겠습니다.\n" + format_questions("", questions)
                return self._respond(state, message=text, action="ASK_USER", data={"questions": [q.model_dump() for q in questions], "agent_reasoning": questions[0].reasoning, "review": True, "review_summary": summary}, warnings=warnings)

        # 검토 완료 → Agent가 판정 준비 완료를 판단하고 종합 판정
        mark_review_done(state, "검토 질문 확인 완료")
        events.append("심의사례 검토 완료 → 종합 판정")
        assessment = self._run_assessment(state, events, progress=progress, force_provisional=not sufficiency.ready_for_assessment)
        if assessment is None:
            return None
        recheck_note = self._recheck_note(events)
        intro = (recheck_note + "\n" if recheck_note else "") + "확인해 주신 내용과 유사 심의사례를 종합하여 예상 과실비율을 판정했습니다."
        return self._assessment_response(state, assessment, events, warnings, intro=intro)

    # ------------------------------------------------------------------ responses
    def _respond(self, state: CaseState, *, message: str, action: Action, data: Optional[dict] = None, warnings: Optional[list[str]] = None) -> AgentResponse:
        state.add_message("assistant", message, action=action)
        return AgentResponse(case_id=state.case_id, message=message, stage=state.current_stage, action=action, data=data or {}, warnings=warnings or [])

    def _video_intro(self, state: CaseState) -> str:
        video = state.video_analysis
        if video is None:
            return "영상 분석 결과가 없어 말씀해주신 내용을 기준으로 진행합니다."
        summary = video.short_summary.strip() or "영상 분석을 완료했습니다."
        vehicles = ", ".join(f"{v.id}({v.description or '설명 없음'}{', 블랙박스 차량' if v.is_ego else ''})" for v in video.vehicles)
        pair = video.collision_pair
        pair_text = ""
        if len(pair.participants) == 2:
            pair_text = f" 충돌 당사 차량은 {pair.participants[0]}과(와) {pair.participants[1]}로 분석됩니다(신뢰도 {pair.confidence:.2f})."
        second_pass = ""
        changes = [item for item in video.changes_from_previous if item != "재분석 상세 서술 보완"]
        if len(video.analysis_passes) >= 2 and changes:
            second_pass = " 2차 분석에서 보완된 내용: " + "; ".join(changes[:3]) + "."
        return f"영상 분석 결과: {summary} 식별된 차량: {vehicles or '없음'}.{pair_text}{second_pass}"

    @staticmethod
    def _with_second_pass_note(state: CaseState, text: str) -> str:
        """첫 턴 메시지에 2차 영상 분석이 보완한 내용을 한 줄 덧붙인다 (LLM이 intro를 썼을 때도)."""
        video = state.video_analysis
        if video is None or len(video.analysis_passes) < 2 or "2차 분석에서 보완" in text:
            return text
        changes = [item for item in video.changes_from_previous if item != "재분석 상세 서술 보완"]
        if not changes:
            return text
        note = "2차 분석에서 보완된 내용: " + "; ".join(changes[:3]) + "."
        lines = text.split("\n", 1)
        if len(lines) == 2:
            return lines[0] + "\n" + note + "\n" + lines[1]
        return note + "\n" + text

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

    def _cases_block(self, state: CaseState) -> str:
        top_k = self.settings.rag.final_top_k
        if state.rag_tier == "fault_standard":
            lines = ["현재 사고 구조와 맞는 심의사례가 없어 과실비율 인정기준 도표를 대신 찾았습니다."]
        else:
            lines = [f"현재 사건과 유사한 심의사례입니다 (최대 {top_k}개)."]
        for case in state.retrieved_cases[:top_k]:
            rel = case.relevance
            lines.append(
                f"- {case.case_id} {case.title or ''} | {case.ratio_summary()}"
                + (f" | 공통점: {', '.join(rel.matched_factors[:3])}" if rel and rel.matched_factors else "")
                + (f" | 차이점: {', '.join(rel.different_factors[:2])}" if rel and rel.different_factors else "")
            )
        lines.append("심의사례의 사실관계는 현재 사건과 다를 수 있으며, 차이점을 함께 검토해야 합니다.")
        return "\n".join(lines)

    def _ack(self, events: list[str], is_initial: bool, state: CaseState) -> str:
        if is_initial:
            return self._video_intro(state)
        facts = [event for event in events if event.startswith("새 사실 반영")]
        conflicts = [event for event in events if event.startswith("영상과 충돌")]
        parts = []
        if facts:
            parts.append("확인했습니다.")
        if conflicts:
            parts.append("일부 진술은 영상에서 직접 확인되지 않아 사용자 진술로만 기록했습니다.")
        parts.append("한 가지 더 확인하겠습니다.")
        return " ".join(parts)

    def _not_ready_message(self, sufficiency: SufficiencyResult) -> str:
        missing = ", ".join(item.reason or item.field for item in sufficiency.sorted_missing()[:3])
        return f"아직 예상 과실비율을 판정하기에 정보가 부족합니다. 부족한 항목: {missing}. 영상 재분석 또는 추가 확인 후 판정하겠습니다."

    def _assessment_response(self, state: CaseState, assessment: FaultAssessment, events: list[str], warnings: list[str], *, intro: Optional[str] = None) -> AgentResponse:
        lines = []
        if intro:
            lines.append(intro)
        label = "임시 예상" if assessment.assessment_type == "provisional" else "예상"
        lines.append(f"현재 영상과 확인된 사실, 유사 심의사례를 기준으로 {label} 과실비율은 사용자 {assessment.fault_ratio.user} : 상대 {assessment.fault_ratio.opponent} 수준입니다. (신뢰도 {assessment.confidence:.2f})")
        if assessment.anchor_case_id:
            anchor_line = f"기준 심의사례: {assessment.anchor_case_id} (결정비율 {assessment.anchor_ratio})"
            if assessment.anchor_enforced:
                anchor_line += " — 확인된 수정요소가 없어 기준값을 그대로 적용"
            lines.append(anchor_line)
        if assessment.possible_range:
            lines.append(f"예상 범위: {' ~ '.join(assessment.possible_range)}")
        primary = [item for item in assessment.matched_cases if item.case_id in assessment.primary_case_ids] or assessment.matched_cases[:1]
        if primary:
            lines.append("참고 심의사례: " + ", ".join(f"{item.case_id}({item.decision_ratio or item.basic_ratio or '비율 미상'})" for item in primary))
        if state.rag_tier == "fault_standard":
            lines.append("참고: 현재 사고 구조와 맞는 심의사례가 없어 과실비율 인정기준 도표를 근거로 산정했습니다.")
        if assessment.reasoning_summary:
            lines.append("근거:\n" + "\n".join(f"- {item}" for item in assessment.reasoning_summary[:6]))
        if assessment.adjustment_factors:
            applied = [f"{item.factor}({'적용' if item.applies else '확인 불가'})" for item in assessment.adjustment_factors[:4]]
            lines.append("수정요소: " + ", ".join(applied))
        if assessment.uncertainties:
            lines.append("불확실한 사항:\n" + "\n".join(f"- {item}" for item in assessment.uncertainties[:4]))
        if assessment.ratio_dependencies:
            lines.append("추가 확인 시 변동 가능: " + "; ".join(assessment.ratio_dependencies[:3]))
        lines.append("이 비율은 예상치이며 법률상 확정 판단이 아닙니다. 궁금한 점을 물어보시거나 사건경위서/반박의견서 작성을 요청하실 수 있습니다.")
        return self._respond(state, message="\n".join(lines), action="SHOW_FAULT_ASSESSMENT",
                             data={"fault_assessment": assessment.model_dump(), "similar_cases": self._similar_cases_data(state), "events": events}, warnings=warnings)

    def _similar_cases_response(self, state: CaseState, warnings: list[str]) -> AgentResponse:
        if not state.retrieved_cases:
            return self._respond(state, message="현재 사건 구조로 검색된 유사 심의사례가 없습니다. 사고 장소와 진행 방향이 더 확인되면 다시 검색하겠습니다.", action="INFO", warnings=warnings)
        message = self._cases_block(state)
        remaining = review_questions_remaining(state) if not state.case_review_done else []
        if remaining:
            message += "\n\n판정 전에 확인이 필요한 사항입니다.\n" + format_questions("", remaining)
        data = {"similar_cases": [case.model_dump(exclude={"excerpt"}) for case in state.retrieved_cases[: self.settings.rag.final_top_k]], "tier": state.rag_tier, "query": state.rag_query.model_dump() if state.rag_query else None}
        return self._respond(state, message=message, action="SHOW_SIMILAR_CASES", data=data, warnings=warnings)

    def _document_response(self, state: CaseState, intent: str, warnings: list[str], events: list[str]) -> AgentResponse:
        if state.fault_assessment is None or state.assessment_invalidated:
            return self._respond(state, message="문서를 작성하려면 먼저 예상 과실비율 판정이 완료되어야 합니다. 부족한 정보를 확인한 뒤 판정하고 문서를 작성하겠습니다.", action="INFO", warnings=warnings)
        if intent == "request_incident_report":
            document = self.document_agent.generate_incident_report(state)
            state.incident_report = document
            state.set_stage("REPORT_COMPLETE")
            label = "사건경위서"
        else:
            document = self.document_agent.generate_rebuttal_opinion(state)
            state.rebuttal_opinion = document
            state.set_stage("REBUTTAL_COMPLETE")
            label = "반박의견서"
        warnings.extend(document.warnings)
        message = f"{label} 초안을 작성했습니다.\n\n{document.text}"
        if not state.opponent_claim and intent == "request_rebuttal":
            message += "\n\n상대방 주장이 아직 입력되지 않았습니다. 상대방(또는 보험사) 주장을 알려주시면 반박 논리를 보완하겠습니다."
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
            parts.append(f"예상 과실비율 {assessment.fault_ratio.as_text()}의 근거는 다음과 같습니다: " + "; ".join(assessment.reasoning_summary[:4]))
            if assessment.uncertainties:
                parts.append("불확실한 사항: " + "; ".join(assessment.uncertainties[:3]))
        facts = [event for event in events if event.startswith("새 사실 반영")]
        if facts:
            parts.append("말씀하신 내용을 사건 정보에 반영했습니다.")
        conflicts = [event for event in events if event.startswith("영상과 충돌")]
        if conflicts:
            parts.append("해당 내용은 사용자 진술로 기록하되, 현재 영상에서는 직접 확인되지 않습니다.")
        if state.assessment_invalidated:
            parts.append("중요한 사실이 바뀌어 기존 판정을 다시 평가해야 합니다. 과실비율을 요청하시면 재판정하겠습니다.")
        if intent.primary_intent == "general_question" and state.fault_assessment and not state.assessment_invalidated:
            parts.append(
                "보험사가 제시한 과실비율에 동의하지 않는 경우, 영상에서 확인된 사실과 유사 심의사례를 근거로 반박의견서를 작성해 보험사에 제출하고, "
                "합의가 되지 않으면 과실비율분쟁심의위원회 심의 청구를 검토할 수 있습니다. 반박의견서 작성을 요청하시면 현재 사건 근거로 초안을 만들어 드리겠습니다."
            )
        if not parts:
            parts.append("확인했습니다. 사고 사실을 추가로 알려주시거나 과실비율 판정, 유사 심의사례, 사건경위서/반박의견서 작성을 요청하실 수 있습니다.")
        return " ".join(parts)
