"""심의사례 검토(Case Review) 단계 — 판정 전 대화 (가이드 84·85·111절 보강).

유사 심의사례를 제시한 뒤, 현재 사건과 사례의 차이·수정요소 적용 여부를 결정할 사실을
Agent가 스스로 추론한다. 화면에 찍히는 것은 영상 재분석으로, 사용자만 아는 것은 질문으로 낸다.
확인이 끝나면 Master Agent가 판정 준비 완료를 판단한다.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from case.questions import (
    DEFAULT_QUESTIONS,
    MAX_ASK_COUNT,
    VIDEO_GAP_RULES,
    RecheckTarget,
    asked_fields_text,
    field_is_askable,
    is_objective_question,
    recheck_history_text,
    split_video_rechecks,
)
from common.jsonutil import compact_json
from models.clients import TextClient
from prompts.loader import load_prompt
from rag.reranker import compact_case_for_prompt
from state.case_state import CaseState, Question, RetrievedCase
from state.updater import get_slot
from telemetry import RunLogger, get_run_logger

ADDITIONAL_FACTS_FIELD = "review.additional_facts"
_ADDITIONAL_FACTS_QUESTION = (
    "심의사례와 비교해 추가로 알려주실 사고 정황이 있나요? (예: 상대 차량 방향지시등, 진입 순서, 영상 시작 전 상황) "
    "없으면 '없어요' 또는 '판정해줘'라고 말씀해 주세요."
)

# LLM 실패 시 fallback: 첫 턴 질문(questions.VIDEO_GAP_RULES)과 같은 표를 쓴다. 화면에 찍히는 항목은 재분석으로 간다.
_FALLBACK_RULES: list[tuple[tuple[str, ...], str, str, str]] = [
    (keywords, field, DEFAULT_QUESTIONS[field], reason) for keywords, field, _importance, reason in VIDEO_GAP_RULES
]
_FALLBACK_RECHECKS: list[tuple[tuple[str, ...], str, str]] = [
    (("실선", "점선", "solid"), "충돌 직전 상대 차량이 차로를 변경한 지점의 노면 차선이 실선인지 점선인지 확인하라.", "실선구간 진로변경 수정요소"),
    (("신호", "signal"), "충돌 직전 블랙박스 차량 진행 방향 신호등 색과 정지선 통과 시점을 확인하라.", "신호 조건"),
    (("제동", "브레이크", "brak"), "충돌 직전 블랙박스 차량의 제동·감속 여부를 확인하라.", "회피 조치"),
]


class _ReviewOutput(BaseModel):
    reasoning: list[str] = Field(default_factory=list, description="심의사례와 현재 사건을 대조한 추론")
    summary: str = ""
    video_recheck_targets: list[RecheckTarget] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)


def _already_known(state: CaseState, field: str) -> bool:
    if field in state.asked_fields:
        return True
    if field.startswith("review."):
        return field in state.review_answers
    slot = get_slot(state, field)
    return bool(slot and slot.is_known() and slot.status == "CONFIRMED")


def deterministic_review_questions(
    state: CaseState, cases: list[RetrievedCase], *, max_questions: int
) -> tuple[str, list[Question], list[RecheckTarget]]:
    blob_parts = list(state.uncertain_facts)
    for case in cases:
        if case.relevance:
            blob_parts.extend(case.relevance.different_factors)
            blob_parts.extend(case.relevance.adjustment_factor_notes)
        blob_parts.extend(case.modification_factors[:4])
    blob = " ".join(blob_parts).lower()
    questions: list[Question] = []
    for keywords, field, text, why in _FALLBACK_RULES:
        if len(questions) >= max_questions:
            break
        if not any(keyword.lower() in blob for keyword in keywords):
            continue
        if _already_known(state, field):
            continue
        questions.append(Question(field=field, question=text, importance="high", phase="case_review", why=why))
    rechecks: list[RecheckTarget] = []
    for keywords, focus, why in _FALLBACK_RECHECKS:
        if any(keyword.lower() in blob for keyword in keywords):
            rechecks.append(RecheckTarget(focus=focus, why=why))
    primary = next((case for case in cases if case.relevance and case.relevance.usable_as_primary_reference), cases[0] if cases else None)
    summary = ""
    if primary:
        summary = f"가장 유사한 사례는 {primary.case_id}({primary.ratio_summary()})입니다."
        if primary.relevance and primary.relevance.different_factors:
            summary += " 현재 사건에서 아직 확인되지 않은 차이: " + ", ".join(primary.relevance.different_factors[:3]) + "."
    return summary, questions, rechecks


def generate_case_review_questions(
    client: Optional[TextClient],
    state: CaseState,
    cases: list[RetrievedCase],
    *,
    max_questions: int = 1,
    run_logger: Optional[RunLogger] = None,
) -> tuple[str, list[Question], list[RecheckTarget]]:
    """심의사례 검토: (요약, 사용자 질문 ≤ max, 영상 재분석 요청). 질문은 pending으로 등록한다."""
    logger = run_logger or get_run_logger()
    summary, questions, rechecks = deterministic_review_questions(state, cases, max_questions=max_questions)
    llm_decided = False

    if client is not None and cases:
        system = load_prompt("master_agent", "system")
        task = load_prompt("master_agent", "case_review_questions")
        user = task.render(
            case_state=compact_json(state.compact(include_timeline=False), max_chars=8000),
            retrieved_cases=compact_json(
                [compact_case_for_prompt(item) | {"validation": item.relevance.model_dump() if item.relevance else None} for item in cases],
                max_chars=14000,
            ),
            uncertain_facts="\n".join(f"- {item}" for item in state.uncertain_facts[:12]) or "(없음)",
            recheck_history=recheck_history_text(state),
            asked_fields=asked_fields_text(state),
            max_questions=max_questions,
        )
        try:
            response = client.generate_json(system=system.text, user=user, schema=_ReviewOutput, task=task.task)
            output = _ReviewOutput.model_validate(response.data)
            logger.log(
                agent="master_agent",
                task=task.task,
                case_id=state.case_id,
                model=response.metrics.model,
                prompt_version=f"{system.version_id}+{task.version_id}",
                metrics=response.metrics,
                extra={"reasoning": output.reasoning, "questions": [q.field for q in output.questions], "rechecks": [t.focus for t in output.video_recheck_targets]},
            )
            llm_questions = []
            for item in output.questions:
                if not item.field or not is_objective_question(item.question) or _already_known(state, item.field):
                    continue
                if not field_is_askable(state, item.field, item.question):
                    continue
                if item.field.split(".")[-1] in {"date", "time", "location_name"} or "날짜" in item.question or "장소명" in item.question:
                    continue  # 판정과 무관한 정보는 검토 단계에서 묻지 않는다
                if item.field in {q.field for q in llm_questions}:
                    continue
                item.phase = "case_review"
                item.reasoning = list(output.reasoning)
                llm_questions.append(item)
            # Agent가 "더 물을 것이 없다"고 판단했으면 코드 후보로 억지로 채우지 않는다
            questions = llm_questions[:max_questions]
            rechecks = [target for target in output.video_recheck_targets if target.focus.strip()]
            llm_decided = True
            if output.summary.strip():
                summary = output.summary.strip()
        except Exception as exc:  # noqa: BLE001
            logger.log(agent="master_agent", task=task.task, case_id=state.case_id, extra={"error": str(exc)[:300], "fallback": "deterministic_review_questions"})

    # guardrail: 화면에 찍히는 사실은 사용자에게 묻지 않고 재분석으로 보낸다
    questions, rechecks = split_video_rechecks(state, questions, rechecks)
    questions = questions[:max_questions]
    if llm_decided and not questions and state.review_rounds > 0:
        # 이미 구체적 검토 질문을 했고 Agent도 더 물을 것이 없다고 판단 → 판정 단계로
        state.touch()
        return summary, [], rechecks
    # 구체적인 검토 질문을 한 번도 못 냈고 재분석 요청도 없을 때만 '추가 정황' 열린 질문을 한 번 둔다
    if not questions and not rechecks and state.review_rounds == 0 and ADDITIONAL_FACTS_FIELD not in state.asked_fields:
        questions = [Question(field=ADDITIONAL_FACTS_FIELD, question=_ADDITIONAL_FACTS_QUESTION, importance="medium", phase="case_review", why="판정 전 추가 정황 확인")]

    previous = {item.field: item for item in state.pending_questions}
    for question in questions:
        question.asked_turn = state.turn_count
        if question.field in previous:
            question.ask_count = previous[question.field].ask_count + 1
        if question.field not in state.asked_fields:
            state.asked_fields.append(question.field)
        if question.question not in state.asked_questions:
            state.asked_questions.append(question.question)
    state.pending_questions = questions
    state.case_review_started = True
    if questions:
        state.review_rounds += 1
    state.touch()
    return summary, questions, rechecks


def review_questions_remaining(state: CaseState) -> list[Question]:
    return [item for item in state.pending_questions if item.phase == "case_review" and item.ask_count < MAX_ASK_COUNT]


def mark_review_done(state: CaseState, reason: str) -> None:
    for item in state.pending_questions:
        if item.phase == "case_review" and item.field not in state.review_answers and item.field != ADDITIONAL_FACTS_FIELD:
            note = f"사용자가 {item.field} 질문에 답하지 않아 미확인으로 처리"
            if note not in state.uncertain_facts:
                state.uncertain_facts.append(note)
    state.pending_questions = [item for item in state.pending_questions if item.phase != "case_review"]
    state.case_review_done = True
    state.notes.append(f"심의사례 검토 완료: {reason}")
    state.touch()
