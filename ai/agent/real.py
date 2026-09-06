"""백엔드 Agent 계약 구현체 — `AGENT_IMPL=ai.agent.real:RealAgent`.

server/docs/agent-interface.md 의 계약(`app/agent/base.py` 모델)을 3-Agent 구조(agents/master_agent.py) 위에서
구현한다. 백엔드는 이 클래스를 프로세스당 하나 만들어 `analyze / chat / judge / write` 를 부른다.

설계 요점
- 상태를 갖지 않는다. Case State 는 `facts["_case_state"]` 로 직렬화되어 매 호출에 왕복한다 (agent/codec.py).
- 대화(chat)는 Master Agent 를 `defer_conclusions=True` 로 돌린다: 판정·문서 작성 시점이 되면 실제로 하지 않고
  REQUEST_ASSESSMENT / REQUEST_DOCUMENT 응답을 내고, 어댑터가 이를 `next_action` (verdict·rejudge·create_report·
  create_rebuttal) 으로 바꾼다. 백엔드는 그 다음 judge/write 를 Job 으로 부른다.
- judge 는 facts 를 갱신할 수 없으므로 판정 상세를 /tmp 에 남겨 두고(agent/cache.py) 다음 호출에서 되살린다.
  없으면 백엔드의 판정 스냅샷만으로 복원한다.
- 모든 메서드는 동기(def)다. 백엔드 AgentAdapter 가 스레드에서 실행한다.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Any, Optional

NL = chr(10)

from agent.cache import JudgeCache, agent_tmp_dir
from agent.codec import STATE_KEY, pack_facts, stored_verdict_version, sync_conversation, unpack_state
from agent.presenters import (
    NO_VIDEO_REPLY,
    assessment_from_snapshot,
    build_case_title,
    build_chart,
    build_video_meta,
    change_reason_text,
    estimate_page_count,
    judge_summary,
    readable_lines,
    ordered_cases,
    parse_opponent_claim,
    precedent_body_text,
    precedent_title,
    ratio_text,
    report_sections_for_server,
    sections_to_dict,
    split_initial_response,
)
from agents.master_agent import MasterAccidentAgent
from case.sufficiency import check_information_sufficiency
from document.rebuttal import MAIL_BODY_MAX_CHARS, compose_mail_body
from settings import Settings, get_settings
from state.case_state import CaseState, Question

log = logging.getLogger("car_defender.agent")

_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".mpg", ".mpeg", ".3gp"}
_MIME_SUFFIX = {
    "video/mp4": ".mp4", "video/quicktime": ".mov", "video/x-m4v": ".m4v", "video/x-msvideo": ".avi",
    "video/x-matroska": ".mkv", "video/webm": ".webm", "video/mpeg": ".mpg", "video/3gpp": ".3gp",
}


def prepare_video_path(path: str, mime: Optional[str]) -> str:
    """확장자 없는 저장 키로 오면 mime 기준 확장자를 가진 링크를 /tmp 에 만들어 준다 (영상 백엔드가 확장자로 mime 을 정한다)."""
    source = Path(path)
    if source.suffix.lower() in _VIDEO_SUFFIXES:
        return str(source)
    suffix = _MIME_SUFFIX.get((mime or "").split(";")[0].strip().lower(), ".mp4")
    target_dir = agent_tmp_dir() / "videos"
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(str(source.resolve()).encode("utf-8")).hexdigest()[:16]
        target = target_dir / f"{digest}{suffix}"
        if target.exists() or target.is_symlink():
            target.unlink()
        try:
            target.symlink_to(source.resolve())
        except OSError:
            shutil.copyfile(source, target)
        return str(target)
    except OSError as exc:  # noqa: BLE001
        log.warning("video path preparation failed (%s); using the original path", exc)
        return str(source)


class RealAgent:
    """백엔드 계약 구현체. `__init__` 은 즉시 끝나고 무거운 준비는 첫 호출 때 한다."""

    def __init__(self, *, master: Optional[MasterAccidentAgent] = None, settings: Optional[Settings] = None, cache: Optional[JudgeCache] = None):
        self._master = master
        self._settings = settings
        self._cache = cache
        self._lock = threading.Lock()
        if master is not None:
            master.defer_conclusions = True

    # ------------------------------------------------------------------ lazy deps
    @property
    def settings(self) -> Settings:
        if self._settings is None:
            self._settings = get_settings()
        return self._settings

    @property
    def master(self) -> MasterAccidentAgent:
        if self._master is None:
            with self._lock:
                if self._master is None:
                    self._master = MasterAccidentAgent(settings=self.settings, defer_conclusions=True)
        return self._master

    @property
    def cache(self) -> JudgeCache:
        if self._cache is None:
            self._cache = JudgeCache()
        return self._cache

    # ------------------------------------------------------------------ contract: analyze
    def analyze(self, inp) -> dict[str, Any]:
        video_path = prepare_video_path(inp.video_path, getattr(inp, "video_mime", None))
        description = (inp.description or "").strip()
        state, response = self.master.create_case(video_path=video_path, initial_description=description)
        questions = [Question.model_validate(item) for item in (response.data.get("questions") or [])] if response.action in {"ASK_USER", "SHOW_SIMILAR_CASES"} else []
        summary_text, question_cards = split_initial_response(response.message, questions)
        if not summary_text:
            summary_text = self.master.video_intro(state)
        return {
            "summary_text": readable_lines(summary_text),
            "facts": pack_facts(state, verdict_version=0, extra=self._claim_extra(state, {})),
            "questions": [readable_lines(card) for card in question_cards],
            "title": build_case_title(state),
            "video_meta": build_video_meta(state),
        }

    # ------------------------------------------------------------------ contract: chat
    def chat(self, inp) -> dict[str, Any]:
        facts = dict(inp.facts or {})
        if STATE_KEY not in facts and not inp.has_video:
            return {"reply": NO_VIDEO_REPLY, "next_action": "none", "fact_updates": {}}
        state = self._load_state(facts, inp.messages)
        verdict_version = self._sync_verdict(state, inp.verdict, facts)
        state, response = self.master.chat(state, (inp.new_message or "").strip())

        next_action = "none"
        reply = response.message
        # 판정이 이미 있는데 이번 턴에 상대 보험사 주장 비율이 새로 들어왔으면, 카드에 비교로 붙는다는 것을 알려 준다
        claim_after = parse_opponent_claim(state.opponent_claim, facts.get("opponent_claim"))
        if inp.verdict is not None and claim_after and claim_after != facts.get("opponent_claim") and response.action not in {"REQUEST_ASSESSMENT", "REQUEST_DOCUMENT"}:
            reply = f"상대 보험사 주장({ratio_text(claim_after['mine'], claim_after['other'])})을 판정 카드에 함께 표시했어요.\n" + reply
        if response.action == "REQUEST_ASSESSMENT":
            next_action = "rejudge" if inp.verdict is not None else "verdict"
        elif response.action == "REQUEST_DOCUMENT":
            kind = response.data.get("kind")
            if kind == "report":
                next_action = "create_report"
            elif kind == "rebuttal":
                next_action = "create_rebuttal"
                if not inp.has_report:
                    # 백엔드가 '경위서 먼저' 잠금 카드를 붙이므로 여기서는 이유만 짧게
                    reply = "반박의견서에는 사건경위서가 첨부돼요. 먼저 사건경위서를 만들어야 보낼 수 있어요."
        return {
            "reply": readable_lines(reply),
            "next_action": next_action,
            "fact_updates": pack_facts(state, verdict_version=verdict_version, extra=self._claim_extra(state, facts)),
        }

    # ------------------------------------------------------------------ contract: judge
    def judge(self, inp) -> dict[str, Any]:
        facts = dict(inp.facts or {})
        state = self._load_state(facts, inp.messages)
        previous = inp.previous_verdict
        self._sync_verdict(state, previous, facts)
        invalidation_reasons = list(state.assessment_invalidation_reasons)
        events: list[str] = []
        sufficiency = check_information_sufficiency(state, None, use_llm=False, run_logger=self.master.run_logger)
        assessment = self.master.assess(state, events, force_provisional=not sufficiency.ready_for_assessment)
        if assessment is None:
            raise RuntimeError("유사 심의사례·인정기준을 찾지 못해 예상 과실비율을 계산할 수 없어요: " + "; ".join(events[-3:]))
        version = (previous.version + 1) if previous is not None else 1
        self.cache.save(state, version)

        cases = ordered_cases(state, assessment, top_k=self.settings.rag.final_top_k)
        precedents = [{"id": case.case_id, "title": precedent_title(case), "body_text": precedent_body_text(case, assessment)} for case in cases]
        claim = parse_opponent_claim(state.opponent_claim, facts.get("opponent_claim"))
        return {
            "ratio_mine": assessment.fault_ratio.user,
            "ratio_other": assessment.fault_ratio.opponent,
            "summary": readable_lines(judge_summary(assessment, state)),
            "change_reason": readable_lines(change_reason_text(previous, assessment, invalidation_reasons)),
            "opponent_claim": claim,
            "basis": {"chart": build_chart(state, assessment, cases), "precedents": precedents},
        }

    # ------------------------------------------------------------------ contract: write
    def write(self, inp) -> dict[str, Any]:
        facts = dict(inp.facts or {})
        state = self._load_state(facts, inp.messages)
        self._sync_verdict(state, inp.verdict, facts)
        document_agent = self.master.document_agent
        if inp.kind == "report":
            document = document_agent.generate_incident_report(
                state, revision_request=inp.revision_request, previous_sections=sections_to_dict(inp.previous_sections),
            )
            sections = report_sections_for_server(document.sections)
            for section in sections:
                # 문서도 문장마다 줄을 나눠 화면에서 읽기 쉽게 (PDF·프론트는 줄바꿈을 그대로 보여준다)
                section["body"] = readable_lines(section.get("body", "")) or ""
            return {
                "sections": sections,
                # grounding 가드가 문장을 지웠으면(근거 없는 속도 수치·미확인 심의번호) 그 사실을 caveat 에 같이 보여 준다.
                # 안 보여 주면 사용자는 "다시 써도 안 바뀐다"고만 느낀다(실서버 제보).
                "caveat": readable_lines(NL.join(
                    part for part in [(document.caveat or "").strip(), *[w for w in (document.warnings or []) if "제거" in w]] if part
                )) or None,
                "page_count": estimate_page_count(sections),
            }
        document = document_agent.generate_rebuttal_opinion(
            state,
            report_sections=sections_to_dict(inp.report_sections),
            revision_request=inp.revision_request,
            previous_sections=sections_to_dict(inp.previous_sections),
        )
        body = readable_lines(compose_mail_body(document, max_chars=MAIL_BODY_MAX_CHARS)) or ""
        return {"body": body[:MAIL_BODY_MAX_CHARS]}

    # ------------------------------------------------------------------ contract: explain (선택)
    def explain(self, inp) -> dict[str, Any]:
        facts = dict(inp.facts or {})
        packed = facts.get(STATE_KEY)
        if isinstance(packed, dict):
            try:
                state = unpack_state(packed)
            except Exception:  # noqa: BLE001
                state = None
            if state is not None:
                for case in state.retrieved_cases:
                    if case.case_id == inp.precedent_id:
                        return {"body_text": precedent_body_text(case, state.fault_assessment)}
        return {"body_text": "해당 심의사례 설명을 찾지 못했어요."}

    # ------------------------------------------------------------------ helpers
    def _load_state(self, facts: dict[str, Any], messages: list[Any]) -> CaseState:
        packed = facts.get(STATE_KEY)
        state: Optional[CaseState] = None
        if isinstance(packed, dict):
            try:
                state = unpack_state(packed)
            except Exception as exc:  # noqa: BLE001
                log.warning("stored case state could not be restored (%s); rebuilding from messages", exc)
        if state is None:
            state = self._rebuild_state(facts, messages)
        sync_conversation(state, messages)
        return state

    def _rebuild_state(self, facts: dict[str, Any], messages: list[Any]) -> CaseState:
        """분석 결과가 이 구현체의 것이 아닐 때(예: MockAgent 로 분석된 사건) 대화 내용만으로 상태를 새로 만든다."""
        user_texts = [getattr(item, "text", "") for item in messages if getattr(item, "role", None) == "user" and getattr(item, "text", "")]
        plain = {key: value for key, value in facts.items() if not key.startswith("_") and isinstance(value, (str, int, float, bool))}
        description = "\n".join(user_texts)
        if plain:
            description += "\n" + "\n".join(f"{key}: {value}" for key, value in plain.items())
        state, _ = self.master.create_case(video_path=None, initial_description=description.strip())
        state.notes.append("백엔드 facts 에 Case State 가 없어 대화 내용으로 재구성함")
        return state

    def _sync_verdict(self, state: CaseState, verdict: Any, facts: dict[str, Any]) -> int:
        """백엔드의 활성 판정 스냅샷을 Case State 에 맞춘다. 돌려주는 값은 facts 에 기록할 동기화 버전."""
        stored = stored_verdict_version(facts)
        if verdict is None:
            return stored
        version = int(getattr(verdict, "version", 0) or 0)
        if state.fault_assessment is not None and stored >= version:
            return stored  # 이미 같은(또는 더 새로운) 판정을 반영한 상태. 새 사실로 무효화됐다면 그대로 둔다
        restored = False
        cached = self.cache.load(state.case_id, version)
        if cached is not None:
            try:
                judged = unpack_state(cached)
            except Exception:  # noqa: BLE001
                judged = None
            if judged is not None and judged.fault_assessment is not None:
                state.fault_assessment = judged.fault_assessment
                state.retrieved_cases = judged.retrieved_cases or state.retrieved_cases
                state.rag_query = judged.rag_query or state.rag_query
                state.rag_tier = judged.rag_tier or state.rag_tier
                for key, value in judged.review_answers.items():
                    state.review_answers.setdefault(key, value)
                restored = True
        if not restored:
            state.fault_assessment = assessment_from_snapshot(verdict)
        state.assessment_invalidated = False
        state.assessment_invalidation_reasons = []
        state.case_review_done = True
        state.pending_questions = [item for item in state.pending_questions if item.phase != "case_review"]
        if state.current_stage not in {"REPORT_COMPLETE", "REBUTTAL_COMPLETE"}:
            state.set_stage("ASSESSMENT_COMPLETE")
        claim = getattr(verdict, "opponent_claim", None)
        if not state.opponent_claim and isinstance(claim, dict) and claim.get("mine") is not None:
            state.opponent_claim = f"상대 보험사 주장: {ratio_text(int(claim['mine']), int(claim['other']))}"
        return version

    @staticmethod
    def _claim_extra(state: CaseState, facts: dict[str, Any]) -> dict[str, Any]:
        claim = parse_opponent_claim(state.opponent_claim, facts.get("opponent_claim"))
        return {"opponent_claim": claim} if claim else {}


__all__ = ["RealAgent", "prepare_video_path"]
