"""User message → 객관적 사실 추출 (가이드 15.1 / 75절).

LLM Structured Output으로 추출하고, 호출 실패 시 규칙 기반 추출로 대체한다.
사용자 입력은 항상 <USER_MESSAGE> 태그로 감싼 데이터로만 전달한다 (Prompt Injection 방지).
"""

from __future__ import annotations

import re
from typing import Optional

from common.jsonutil import compact_json
from models.clients import TextClient
from prompts.loader import load_prompt, wrap_user_text
from state.case_state import CaseState
from state.updater import ExtractedFact, UserFactExtraction, get_slot
from telemetry import RunLogger, get_run_logger, input_hash

OPINION_MARKERS = ("잘못", "과실이 없", "과실 없", "책임", "100:0", "100대0", "억울", "무리하게", "일부러", "고의")
_DATE_PATTERNS = [
    re.compile(r"(20\d{2})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})일?"),
    re.compile(r"(\d{1,2})월\s*(\d{1,2})일"),
]
_TIME_PATTERN = re.compile(r"((?:오전|오후|새벽|저녁|아침|낮)?\s*\d{1,2}\s*시(?:\s*\d{1,2}\s*분)?(?:\s*경|쯤|정도)?|\d{1,2}:\d{2})")


def _video_vehicle_lines(state: CaseState) -> str:
    if not state.video_analysis:
        return "(영상 분석 전)"
    return "\n".join(
        f"- {vehicle.id}: {vehicle.description or '설명 없음'}{' (블랙박스 촬영 차량)' if vehicle.is_ego else ''}"
        for vehicle in state.video_analysis.vehicles
    ) or "(식별된 차량 없음)"


def _pending_lines(state: CaseState) -> str:
    if not state.pending_questions:
        return "(없음)"
    return "\n".join(f"- [{item.field}] {item.question}" for item in state.pending_questions)


def rule_based_extraction(state: CaseState, message: str) -> UserFactExtraction:
    """LLM 없이도 핵심 답변(영상 소유, 영상 유형, 일시, 모름)을 최소한으로 추출한다."""
    text = message.strip()
    lowered = text.lower()
    facts: list[ExtractedFact] = []
    pending_fields = [item.field for item in state.pending_questions]

    def pending(field: str) -> bool:
        return field in pending_fields

    if any(marker in lowered for marker in ("모르", "기억 안", "기억이 안", "잘 모름")) and pending_fields:
        for field in pending_fields[:1]:
            facts.append(ExtractedFact(field=field, value="unknown", fact=f"사용자가 {field}를 모른다고 답함", answers_field=field, confidence=0.9))
        return UserFactExtraction(new_facts=facts)

    if any(marker in lowered for marker in ("내 차", "제 차", "본인", "우리 차", "내차", "제차", "내꺼", "제꺼")) and "상대" not in lowered:
        facts.append(ExtractedFact(field="video_source.vehicle_owner", value="user", fact="업로드 영상은 사용자 차량의 블랙박스 영상이다.", verification="not_visible_in_video", answers_field="video_source.vehicle_owner", confidence=0.9))
        if "블랙박스" in lowered or pending("video_source.vehicle_owner"):
            facts.append(ExtractedFact(field="video_source.type", value="dashcam", fact="업로드 영상은 블랙박스 영상이다.", verification="not_visible_in_video", confidence=0.85))
    elif any(marker in lowered for marker in ("상대", "상대방", "저쪽")) and ("블랙박스" in lowered or "영상" in lowered or pending("video_source.vehicle_owner")):
        facts.append(ExtractedFact(field="video_source.vehicle_owner", value="opponent", fact="업로드 영상은 상대 차량의 블랙박스 영상이다.", verification="not_visible_in_video", answers_field="video_source.vehicle_owner", confidence=0.85))
        facts.append(ExtractedFact(field="video_source.type", value="dashcam", fact="업로드 영상은 블랙박스 영상이다.", verification="not_visible_in_video", confidence=0.8))
    elif pending("video_source.vehicle_owner") and lowered in {"응", "네", "예", "어", "맞아", "맞아요", "yes", "y", "ㅇㅇ"}:
        facts.append(ExtractedFact(field="video_source.vehicle_owner", value="user", fact="업로드 영상은 사용자 차량의 블랙박스 영상이다.", verification="not_visible_in_video", answers_field="video_source.vehicle_owner", confidence=0.8))
        facts.append(ExtractedFact(field="video_source.type", value="dashcam", fact="업로드 영상은 블랙박스 영상이다.", verification="not_visible_in_video", confidence=0.75))

    if "cctv" in lowered:
        facts.append(ExtractedFact(field="video_source.type", value="cctv", fact="업로드 영상은 도로 CCTV 영상이다.", verification="not_visible_in_video", answers_field="video_source.type", confidence=0.85))

    if state.video_analysis:
        match = re.search(r"vehicle[_\s]?(\d+)", lowered)
        if match and any(marker in lowered for marker in ("내 차", "제 차", "본인", "우리 차", "내차")):
            facts.append(ExtractedFact(field="ego_vehicle.vehicle_id", value=f"vehicle_{match.group(1)}", fact=f"영상 속 vehicle_{match.group(1)}이 사용자 차량이다.", verification="not_visible_in_video", answers_field="ego_vehicle.vehicle_id", confidence=0.9))

    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            groups = match.groups()
            if len(groups) == 3:
                value = f"{groups[0]}-{int(groups[1]):02d}-{int(groups[2]):02d}"
            else:
                value = f"{int(groups[0]):02d}-{int(groups[1]):02d} (연도 미확인)"
            facts.append(ExtractedFact(field="accident_datetime.date", value=value, fact=f"사고 발생 날짜: {value}", verification="not_visible_in_video", answers_field="accident_datetime.date", confidence=0.85))
            break
    time_match = _TIME_PATTERN.search(text)
    if time_match:
        value = time_match.group(1).strip()
        facts.append(ExtractedFact(field="accident_datetime.time", value=value, fact=f"사고 발생 시각: {value}", verification="not_visible_in_video", answers_field="accident_datetime.time", confidence=0.8))

    opponent_claim = None
    claim_match = re.search(r"(상대(?:방|측)?(?:\s*보험사)?(?:이|가|는|은)?\s*[^.\n]*?(\d{1,3})\s*[:대]\s*(\d{1,3})[^.\n]*)", text)
    if claim_match and ("주장" in text or "보험사" in text):
        opponent_claim = claim_match.group(1).strip()

    ignored = [text] if any(marker in lowered for marker in OPINION_MARKERS) and not facts else []
    return UserFactExtraction(new_facts=facts, opponent_claim=opponent_claim, ignored_opinions=ignored, no_new_facts=not facts and not opponent_claim)


def _filter_opinions(extraction: UserFactExtraction) -> UserFactExtraction:
    kept: list[ExtractedFact] = []
    for item in extraction.new_facts:
        lowered = (item.fact or "").lower()
        if item.field is None and any(marker in lowered for marker in OPINION_MARKERS):
            extraction.ignored_opinions.append(item.fact)
            continue
        kept.append(item)
    extraction.new_facts = kept
    return extraction


def extract_case_facts(
    client: Optional[TextClient],
    state: CaseState,
    user_message: str,
    *,
    run_logger: Optional[RunLogger] = None,
) -> UserFactExtraction:
    """사용자 메시지에서 객관적 사실만 추출한다. 실패 시 규칙 기반으로 대체."""
    logger = run_logger or get_run_logger()
    if client is None:
        return _filter_opinions(rule_based_extraction(state, user_message))

    system = load_prompt("master_agent", "system")
    task = load_prompt("master_agent", "fact_extraction")
    user = task.render(
        user_message=wrap_user_text(user_message),
        pending_questions=_pending_lines(state),
        case_state=compact_json(state.compact(include_timeline=False), max_chars=9000),
        video_vehicles=_video_vehicle_lines(state),
    )
    try:
        response = client.generate_json(system=system.text, user=user, schema=UserFactExtraction, task=task.task)
        extraction = UserFactExtraction.model_validate(response.data)
        logger.log(
            agent="master_agent",
            task=task.task,
            case_id=state.case_id,
            model=response.metrics.model,
            prompt_version=f"{system.version_id}+{task.version_id}",
            metrics=response.metrics,
            input_digest=input_hash(user_message, state.case_id),
            extra={"new_facts": len(extraction.new_facts), "conflicts": len(extraction.conflicts)},
        )
    except Exception as exc:  # noqa: BLE001
        logger.log(
            agent="master_agent",
            task=task.task,
            case_id=state.case_id,
            prompt_version=f"{system.version_id}+{task.version_id}",
            extra={"error": str(exc)[:300], "fallback": "rule_based_extraction"},
        )
        extraction = rule_based_extraction(state, user_message)

    # 이미 확정된 슬롯과 같은 값을 다시 진술한 경우 사실 목록에서 중복을 막는다.
    deduped = []
    for item in extraction.new_facts:
        slot = get_slot(state, item.field) if item.field else None
        if slot and slot.is_known() and slot.source == "user" and str(slot.value).lower() == str(item.value or "").lower():
            continue
        deduped.append(item)
    extraction.new_facts = deduped
    return _filter_opinions(extraction)
