from agent.accident_classifier import classify_accident
from agent.evidence_fusion import fuse_evidence, resolved_state_from_fusion
from agent.fact_planner import (
    extract_factor_answer,
    factors_for_video_recheck,
    generate_factor_question,
    next_question_factor,
    plan_required_factors,
    refresh_factor_statuses,
    unresolved_factors,
)
from agent.slot_extractor import extract_slots
from agent.state_manager import (
    activate_slots,
    get_missing_slots,
    initialize_state,
    update_state,
)
from models.gemini_video import merge_video_analyses
from schemas.accident_state import AccidentType
from schemas.fact_plan import FactorAnswer, RequiredFactor
from schemas.slot_schema import ALL_SLOTS
from schemas.video_analysis import VideoAnalysisResult


class VisionFirstIntakeAgent:
    def __init__(self, video_analysis: VideoAnalysisResult):
        self.video_analysis = video_analysis
        self.user_state = initialize_state()
        self.factor_answers: dict[str, FactorAnswer] = {}
        self.history = []
        self.pending_question_slot = None
        self.pending_factor_id = None
        self.fusion = fuse_evidence(self.video_analysis, self.user_state)
        self.state = resolved_state_from_fusion(self.fusion)
        self.state.accident_type = classify_accident(self.state)
        self.state.active_slots = activate_slots(self.state)
        self.state.missing_slots = get_missing_slots(
            self.state, self.state.active_slots
        )
        self.fact_plan = plan_required_factors(
            self.video_analysis, self.fusion, self.state
        )
        self._apply_hypotheses_to_state()
        self._refresh()

    def _apply_hypotheses_to_state(self):
        if not self.fact_plan.accident_hypotheses:
            return
        top = self.fact_plan.accident_hypotheses[0]
        self.state.accident_type = AccidentType(
            family=top.family,
            subtype=top.subtype,
            status="partially_confirmed" if top.confidence >= 0.5 else "uncertain",
            candidates=[item.family for item in self.fact_plan.accident_hypotheses],
        )

    def _refresh(self):
        accident_type = self.state.accident_type
        self.fusion = fuse_evidence(
            self.video_analysis,
            self.user_state,
            factor_answers=self.factor_answers,
        )
        self.state = resolved_state_from_fusion(self.fusion)
        self.state.accident_type = accident_type
        self.state.active_slots = activate_slots(self.state)
        self.state.missing_slots = get_missing_slots(
            self.state, self.state.active_slots
        )
        refresh_factor_statuses(
            self.fact_plan, self.fusion, self.factor_answers
        )

    def factors_needing_video_recheck(self) -> list[RequiredFactor]:
        return factors_for_video_recheck(self.fact_plan)

    def apply_targeted_video_analysis(self, targeted: VideoAnalysisResult):
        self.video_analysis = merge_video_analyses(self.video_analysis, targeted)
        self._refresh()
        # CLI에서는 사용자 질문 전에만 targeted pass를 수행한다. 새 관찰로 사고
        # 가설이 달라질 수 있으므로 아직 사용자 답변이 없을 때 계획도 갱신한다.
        if not self.factor_answers:
            self.fact_plan = plan_required_factors(
                self.video_analysis, self.fusion, self.state
            )
            self._apply_hypotheses_to_state()
            self._refresh()

    def _is_complete(self) -> bool:
        return bool(self.fact_plan.required_factors) and not unresolved_factors(
            self.fact_plan
        )

    def _target_slot_for_factor(self, factor: RequiredFactor) -> str | None:
        for fact_key in factor.related_fact_keys:
            if fact_key in ALL_SLOTS and getattr(self.state, fact_key).value is None:
                return fact_key
        return None

    def _result(self, next_question: str | None = None) -> dict:
        complete = self._is_complete()
        disputed_facts = [
            name
            for name, fact in self.fusion.facts.items()
            if fact.status in {"disputed_unresolved", "disputed_vision_preferred"}
        ]
        remaining = [factor.factor_id for factor in unresolved_factors(self.fact_plan)]
        reason = None
        if complete:
            reason = (
                "required_factors_collected_with_disputes"
                if disputed_facts
                else "required_factors_sufficient"
            )
        return {
            "intake_complete": complete,
            "completion_reason": reason,
            "next_question": next_question,
            "next_question_target": self.pending_question_slot,
            "pending_factor_id": self.pending_factor_id,
            "state": self.state.model_dump(),
            "user_state": self.user_state.model_dump(),
            "factor_answers": {
                key: value.model_dump() for key, value in self.factor_answers.items()
            },
            "fact_plan": self.fact_plan.model_dump(),
            "final_facts": self.fusion.model_dump(),
            "unresolved_factors": remaining,
            "disputed_facts": disputed_facts,
        }

    def _ask_next_factor(self) -> dict:
        factor = next_question_factor(self.fact_plan)
        if factor is None:
            self.pending_factor_id = None
            self.pending_question_slot = None
            return self._result()

        question = generate_factor_question(
            factor=factor,
            plan=self.fact_plan,
            fusion=self.fusion,
            history=self.history,
        )
        self.pending_factor_id = factor.factor_id
        self.pending_question_slot = self._target_slot_for_factor(factor)
        self.history.append(
            {
                "role": "assistant",
                "content": question,
                "target_factor": factor.factor_id,
                "target_slot": self.pending_question_slot,
            }
        )
        return self._result(question)

    def start(self) -> dict:
        return self._ask_next_factor()

    def process(self, user_input: str) -> dict:
        self.history.append({"role": "user", "content": user_input})
        extracted = extract_slots(
            state=self.state,
            user_input=user_input,
            target_slot=self.pending_question_slot,
        )
        update_state(self.user_state, extracted, source="user")

        factor_answer = None
        if self.pending_factor_id:
            factor = next(
                item
                for item in self.fact_plan.required_factors
                if item.factor_id == self.pending_factor_id
            )
            last_question = next(
                (
                    item["content"]
                    for item in reversed(self.history[:-1])
                    if item["role"] == "assistant"
                ),
                "",
            )
            factor_answer = extract_factor_answer(
                factor=factor,
                last_question=last_question,
                user_input=user_input,
            )
            self.factor_answers[factor.factor_id] = factor_answer
            update_state(
                self.user_state,
                factor_answer.related_fact_updates,
                source="user",
            )
            if (
                not factor_answer.related_fact_updates
                and len(factor.related_fact_keys) == 1
                and factor.related_fact_keys[0] in ALL_SLOTS
            ):
                update_state(
                    self.user_state,
                    {factor.related_fact_keys[0]: factor_answer.value},
                    source="user",
                )

        self._refresh()
        result = self._ask_next_factor()
        result["extracted_slots"] = extracted
        result["extracted_factor_answer"] = (
            factor_answer.model_dump() if factor_answer else None
        )
        return result
