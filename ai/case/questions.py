"""객관적 추가 질문 생성 (가이드 2.1-C / 15.3 / 74절).

- 1턴당 최대 3개
- 사용자의 주관적 과실 판단을 묻지 않음 (필터로 강제)
- 이미 확인·질문한 항목 제외
"""

from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field

from common.jsonutil import compact_json
from models.clients import TextClient
from prompts.loader import load_prompt
from settings import get_settings
from state.case_state import CaseState, MissingInformation, Question
from state.updater import get_slot, set_slot
from telemetry import RunLogger, get_run_logger

SUBJECTIVE_MARKERS = (
    "생각하시나요",
    "생각하세요",
    "잘못",
    "무리하게",
    "과실이 없",
    "과실 없",
    "책임이",
    "느끼",
    "억울",
    "피할 수 있었",
    "누가 더",
)

DEFAULT_QUESTIONS = {
    "video_source.vehicle_owner": "업로드하신 영상은 본인 차량 블랙박스인가요, 상대 차량 블랙박스인가요?",
    "video_source.type": "이 영상은 차량 블랙박스 영상인가요, 도로 CCTV 또는 제3자가 촬영한 영상인가요?",
    "accident_datetime.date": "사고 발생 날짜는 언제인가요? (예: 2026-08-22)",
    "accident_datetime.time": "사고 발생 시각은 대략 몇 시였나요?",
    "road.location_name": "사고 장소(도로명이나 교차로 이름)를 알려주실 수 있나요?",
    "road.road_type": "사고 장소는 사거리 교차로, 삼거리, 회전교차로, 직선도로, 주차장 중 어디에 해당하나요?",
    "ego_vehicle.vehicle_id": "영상 속 차량 중 어느 차량이 본인 차량인가요? ({options})",
    "other_vehicle.vehicle_id": "영상 속 차량 중 어느 차량이 상대 차량인가요? ({options})",
    "collision.participants_confirmed": "영상에 보이는 차량 중 실제로 충돌한 두 차량은 어느 것인가요? ({options})",
    "opponent_claim": "상대방 또는 상대 보험사가 주장하는 과실비율이나 사고 경위가 있다면 알려주세요.",
    "road.signal_present": "사고 지점에 신호등이 있었나요?",
    "road.signal_state": "사고 당시 본인 진행 방향 신호가 어떤 색이었는지 기억하시나요?",
    "other_vehicle.signal": "상대 차량 진행 방향의 신호가 어떤 색이었는지 보셨나요? (모르시면 '모름'이라고 답해 주세요)",
    "other_vehicle.turn_signal": "상대 차량이 차로를 바꾸거나 진입할 때 방향지시등을 켠 것을 보셨나요?",
    "ego_vehicle.turn_signal": "본인 차량은 차로 변경이나 회전 시 방향지시등을 켜고 있었나요?",
    "ego_vehicle.entered_first": "본인 차량이 상대 차량보다 먼저 교차로(회전교차로)에 진입해 있었나요?",
    "review.pre_video_situation": "영상 시작 이전에 이미 차로 변경이나 회전을 시작한 상태였나요? 영상 시작 전 상황을 알려주세요.",
    "review.secondary_collision": "영상에 보이지 않는 별도의 충돌(2차 충돌)이 있었나요?",
}

# 영상 분석이 '확인 불가'로 남긴 사실 중 사용자가 직접 보았거나 알 수 있는 것 → LLM 실패 시 fallback 질문 후보
# (노면 실선/점선, 정지선, 본인 신호 색, 제동 등 화면에 찍히는 것은 여기 두지 않고 영상 재분석으로 보낸다)
VIDEO_GAP_RULES: list[tuple[tuple[str, ...], str, str, str]] = [
    (("방향지시등", "깜빡이", "turn_signal", "turn signal"), "other_vehicle.turn_signal", "high", "방향지시등 미점등 수정요소"),
    (("선진입", "진입 순서", "먼저 진입", "진입 시점", "entered_first"), "ego_vehicle.entered_first", "high", "선진입 관계"),
    (("영상 시작 전", "영상 이전", "영상 시작 이전", "before the video"), "review.pre_video_situation", "medium", "영상 시작 전 상황"),
    (("상대 차량 신호", "상대 신호", "상대 차량 방향 신호", "상대 차량 방향의 신호", "opponent signal"), "other_vehicle.signal", "medium", "상대 신호 조건"),
    (("2차 충돌", "별도 충돌", "secondary"), "review.secondary_collision", "medium", "2차 충돌 여부"),
]

# 화면에 찍히는 사실 → 사용자에게 묻지 않고 Video Agent focus 재분석 (가이드 15.3, 87절)
VIDEO_OBSERVABLE_TOPICS: list[tuple[tuple[str, ...], str]] = [
    (("실선", "점선", "차선 종류", "노면", "lane marking", "solid line"), "차로 변경 지점의 노면 차선이 실선인지 점선인지"),
    (("정지선", "stop line"), "정지선 위치와 각 차량의 정지선 통과 시점"),
    (("횡단보도", "crosswalk"), "횡단보도 위치와 차량 통과 시점"),
    (("본인 진행 방향 신호", "본인 방향 신호", "본인 차량 신호", "사용자 차량 신호", "블랙박스 차량 신호", "본인 신호"), "블랙박스 차량 진행 방향 신호등 색과 변경 시점"),
    (("몇 차로", "차로 위치", "어느 차로", "차로에 있었"), "충돌 직전 각 차량의 차로 위치"),
    (("제동", "브레이크", "감속"), "충돌 직전 블랙박스 차량의 제동·감속 여부"),
    (("충돌 부위", "어디에 부딪", "어느 부분"), "각 차량의 충돌 부위"),
]
_UNOBSERVABLE_MARKERS = ("화각", "보이지 않", "보이지않", "찍히지", "프레임 밖", "화면 밖", "영상 이전", "영상 시작 전", "영상 밖", "시야 밖", "사각")


class RecheckTarget(BaseModel):
    focus: str
    why: str = ""


def video_observable_topic(text: str) -> Optional[str]:
    lowered = text.lower()
    for keywords, focus in VIDEO_OBSERVABLE_TOPICS:
        if any(keyword.lower() in lowered for keyword in keywords):
            return focus
    return None


def video_declared_unobservable(state: CaseState, text: str) -> bool:
    """영상 분석이 해당 주제를 '화각 밖/보이지 않음'으로 명시했으면 사용자에게 물어도 된다."""
    topic = video_observable_topic(text)
    if topic is None:
        return False
    keywords = next(keys for keys, focus in VIDEO_OBSERVABLE_TOPICS if focus == topic)
    for fact in state.uncertain_facts:
        lowered = fact.lower()
        if any(keyword.lower() in lowered for keyword in keywords) and any(marker in fact for marker in _UNOBSERVABLE_MARKERS):
            return True
    return False


def recheck_already_done(state: CaseState, focus: str, *, threshold: float = 0.4) -> bool:
    """같은 쟁점을 이미 재분석했는지 — 일괄 재확인(sweep) 문장에 여러 주제가 섞여 있어도 키워드로 잡는다."""
    grams = _bigrams(focus)
    topic_new = video_observable_topic(focus)
    keywords = next((keys for keys, topic in VIDEO_OBSERVABLE_TOPICS if topic == topic_new), ())
    for previous in state.recheck_focus_history:
        prev = _bigrams(previous)
        if grams and prev and len(grams & prev) / len(grams | prev) >= threshold:
            return True
        lowered = previous.lower()
        if keywords and any(keyword.lower() in lowered for keyword in keywords):
            return True
    return False


def video_already_confirmed_topic(state: CaseState, text: str) -> bool:
    """영상(재분석 포함)이 이미 CONFIRMED로 확인한 주제면 사용자에게 물을 필요가 없다."""
    topic = video_observable_topic(text)
    if topic is None:
        return False
    keywords = next(keys for keys, focus in VIDEO_OBSERVABLE_TOPICS if focus == topic)
    blob = " ".join(fact.fact for fact in state.video_confirmed_facts()).lower()
    if topic.startswith("차로 변경 지점의 노면 차선") and state.road.lane_marking.is_known() and state.road.lane_marking.status == "CONFIRMED":
        return True
    return any(keyword.lower() in blob for keyword in keywords)


# 사고 정황(상대 차량이 어느 쪽에서 와서 어떻게 움직였는지)은 영상 agent 의 몫이다 — 영상이 못 봤다고 사용자에게 진행 방향을 묻지 않는다
# (사용자 요청 2026-09-06: 상대 차량 식별은 물어도 되지만 방향·경로는 묻지 말 것)
_NEVER_ASK_USER = re.compile(r"(진행\s*방향|진입\s*방향|진행\s*경로|어느\s*(방향|쪽)에서|어떻게\s*(진행|움직|주행)|직진,?\s*좌회전,?\s*우회전)")


def classify_video_topic(state: CaseState, question: Question, *, max_rechecks: Optional[int] = None) -> tuple[str, Optional[RecheckTarget]]:
    """guardrail 분류: ask(사용자에게 물음) | recheck(영상 재분석) | drop(영상이 이미 확인함 / 사용자에게 물을 성격이 아님).

    max_rechecks: 대화 중 허용된 영상 재분석 횟수(Master Agent 설정). 없으면 전역 설정값."""
    if not state.video_analyzed or not state.video_path:
        return "ask", None
    text = f"{question.question} {question.why or ''}"
    if _NEVER_ASK_USER.search(question.question) and question.field not in {"collision.participants_confirmed", "other_vehicle.vehicle_id", "ego_vehicle.vehicle_id"}:
        return "drop", None
    topic = video_observable_topic(text)
    if topic is None:
        return "ask", None
    if video_already_confirmed_topic(state, text):
        return "drop", None
    if video_declared_unobservable(state, text):
        return "ask", None
    focus = f"{topic}을(를) 충돌 직전 구간에서 집중 확인하라."
    if recheck_already_done(state, focus):
        return "ask", None
    budget = max_rechecks if max_rechecks is not None else get_settings().agent.max_master_rechecks
    if state.video_reanalysis_count >= budget:
        # 재분석 예산이 없으면(기본 0) '재분석'으로 보내도 실행되지 않고 질문만 사라진다 → 사용자에게 묻는다
        return "ask", None
    return "recheck", RecheckTarget(focus=focus, why=question.why or topic)


def must_recheck_video_instead(state: CaseState, question: Question, *, max_rechecks: Optional[int] = None) -> Optional[RecheckTarget]:
    decision, target = classify_video_topic(state, question, max_rechecks=max_rechecks)
    return target if decision == "recheck" else None


def unregister_questions(state: CaseState, questions: list[Question], *, review: bool = False) -> None:
    """재분석 후 다시 판단하기 위해 방금 등록한 질문을 취소한다."""
    for question in questions:
        state.pending_questions = [item for item in state.pending_questions if item.field != question.field]
        if question.field in state.asked_fields and question.field not in state.review_answers:
            state.asked_fields.remove(question.field)
        if question.question in state.asked_questions:
            state.asked_questions.remove(question.question)
    if review and questions:
        state.review_rounds = max(0, state.review_rounds - 1)
    state.touch()


# 방향지시등은 차로 변경·회전·진입 동작이 있을 때만 과실 요소다. 직진·차로 유지로 확인된 차량에겐 묻지 않는다
# (실서버 대화: 차로를 유지한 블랙박스 차량에게 방향지시등을 물어봄).
_TURNING_WORDS = ("좌회전", "우회전", "회전", "유턴", "u-turn", "turn", "진입", "enter", "merge", "차로 변경", "차선 변경", "lane_change", "change")
_STRAIGHT_WORDS = ("직진", "straight", "유지", "keep")
_LANE_KEEP_ANSWER = re.compile(
    r"(차선|차로)\s*(을|를)?\s*(유지|그대로|안\s*바꿨|바꾸지\s*않|변경\s*(안|하지))"
    r"|제\s*(차선|차로)(에서|으로|대로)\s*(주행|가고|달리|계속)"
    r"|직진\s*(중|했|하고)"
    r"|(차선|차로)\s*변경\s*(없|안)"
)


def _maneuver_excluded(vehicle, *, is_ego: bool) -> bool:
    """이 차량이 차로 변경·회전 없이 직진(차로 유지)했다고 볼 수 있나.

    상대 차량은 영상이 '차로 변경 없음'으로 명시 확인한 경우에만 제외한다(직진처럼 보여도 화각 밖 차로 변경이 있을 수 있어
    방향지시등을 묻는 것이 기존 동작). 블랙박스 차량은 자기 진행이 영상에 확실히 남으므로 직진이면 제외한다."""
    lane_change = vehicle.lane_change
    movement = vehicle.movement
    lc_value = str(lane_change.value).strip().lower() if lane_change.is_known() else ""
    mv_value = str(movement.value).strip().lower() if movement.is_known() else ""
    if lc_value == "true" or any(word in mv_value for word in _TURNING_WORDS):
        return False
    if lc_value == "false":
        return True
    return is_ego and any(word in mv_value for word in _STRAIGHT_WORDS)


def turn_signal_irrelevant(state: CaseState, field: Optional[str]) -> bool:
    """차로 변경·회전·진입 동작이 없다고 확인된 차량의 방향지시등 질문은 판정에 영향이 없다 — 묻지 않는다."""
    if not field or not field.endswith(".turn_signal"):
        return False
    side = field.split(".", 1)[0]
    vehicle = getattr(state, side, None)
    return vehicle is not None and hasattr(vehicle, "lane_change") and _maneuver_excluded(vehicle, is_ego=(side == "ego_vehicle"))


def resolve_pending_by_implication(state: CaseState, message: str) -> list[str]:
    """답이 질문 필드에 직접 매핑되진 않지만 그 질문을 무의미하게 만드는 진술을 코드가 처리한다.

    방향지시등 질문에 "제 차선에서 주행 중이었어요"라고 답하면 본인 차량 차로 변경 없음으로 기록하고 질문을 닫는다.
    안 그러면 "확인했어요"라고 해 놓고 같은 질문을 그대로 되묻는다(실서버 대화)."""
    if not state.pending_questions or not _LANE_KEEP_ANSWER.search(message):
        return []
    set_slot(state, "ego_vehicle.lane_change", "false", source="user", status="CONFIRMED", confidence=0.8, note="사용자 진술: 차로 유지")
    resolved: list[str] = []
    for question in list(state.pending_questions):
        if question.field != "ego_vehicle.turn_signal":
            continue
        state.review_answers[question.field] = "차로 변경 없음 — 방향지시등 무관"
        if question.field not in state.asked_fields:
            state.asked_fields.append(question.field)
        state.pending_questions = [item for item in state.pending_questions if item.field != question.field]
        resolved.append(question.field)
    if resolved:
        state.touch()
    return resolved


def video_gap_candidates(state: CaseState, *, max_items: int = 2) -> list[MissingInformation]:
    """영상 분석의 unknown/uncertain 항목을 사용자가 답할 수 있는 질문 후보로 바꾼다."""
    if not state.video_analyzed:
        return []
    blob = " ".join(state.uncertain_facts).lower()
    candidates: list[MissingInformation] = []
    for keywords, field, importance, reason in VIDEO_GAP_RULES:
        if len(candidates) >= max_items:
            break
        if not any(keyword.lower() in blob for keyword in keywords):
            continue
        if field in state.asked_fields or field in state.review_answers:
            continue
        if turn_signal_irrelevant(state, field):
            continue
        slot = get_slot(state, field)
        if slot is not None and slot.is_known() and slot.status == "CONFIRMED":
            continue
        candidates.append(
            MissingInformation(
                field=field,
                importance=importance,  # type: ignore[arg-type]
                reason=f"영상에서 확인 불가 — {reason} (사용자가 직접 확인 가능)",
                user_answerable=True,
                video_recheckable=False,
            )
        )
    return candidates


class _LLMQuestions(BaseModel):
    reasoning: list[str] = Field(default_factory=list, description="Agent가 무엇을 왜 물을지 추론한 과정")
    video_recheck_targets: list["RecheckTarget"] = Field(default_factory=list)
    intro: str = ""
    questions: list[Question] = Field(default_factory=list)


def is_objective_question(text: str) -> bool:
    lowered = text.lower()
    return not any(marker in lowered for marker in SUBJECTIVE_MARKERS)


def _bigrams(text: str) -> set[str]:
    compact = re.sub(r"[\s\W_]+", "", text.lower())
    return {compact[i : i + 2] for i in range(len(compact) - 1)}


def question_is_duplicate(state: CaseState, question: str, *, threshold: float = 0.55) -> bool:
    """이름만 바꿔 같은 내용을 다시 묻는 것을 문장 유사도로 잡는다."""
    grams = _bigrams(question)
    if not grams:
        return False
    for previous in state.asked_questions:
        prev = _bigrams(previous)
        if not prev:
            continue
        if len(grams & prev) / len(grams | prev) >= threshold:
            return True
    return False


_VEHICLE_FIELD = re.compile(r"^(?:vehicles\.)?(vehicle_(\d+)|ego|self|my_vehicle|user_vehicle|other|opponent|other_vehicle|ego_vehicle)\.([a-z_]+)$")


def normalize_field(state: CaseState, field: Optional[str]) -> Optional[str]:
    """LLM이 고른 field 이름을 Case State 슬롯 경로로 맞춘다.

    영상 스키마 식으로 'vehicles.vehicle_2.turn_signal' 이라고 쓰면 그대로는 슬롯이 없어 질문이 조용히 버려졋다.
    상대 차량 ID면 other_vehicle.<leaf>, 블랙박스 차량 ID면 ego_vehicle.<leaf> 로 바꾸고,
    그래도 슬롯이 없으면 'review.<leaf>' 로 바꿔 질문 자체는 살린다 (프롬프트 규칙: 슬롯이 없으면 review.* 를 쓴다)."""
    if not field:
        return field
    field = field.strip()
    if get_slot(state, field) is not None or field.startswith("review.") or field in DEFAULT_QUESTIONS:
        return field
    match = _VEHICLE_FIELD.match(field)
    if match:
        who, number, leaf = match.group(1), match.group(2), match.group(3)
        ego_id = state.ego_vehicle.vehicle_id or "vehicle_1"
        other_id = state.other_vehicle.vehicle_id
        if who in {"ego", "self", "my_vehicle", "user_vehicle", "ego_vehicle"} or (number and f"vehicle_{number}" == ego_id and who != other_id):
            side = "ego_vehicle"
        else:
            side = "other_vehicle"
        candidate = f"{side}.{leaf}"
        if get_slot(state, candidate) is not None or candidate in DEFAULT_QUESTIONS:
            return candidate
        return f"review.{side}_{leaf}"
    leaf = field.rsplit(".", 1)[-1]
    return f"review.{leaf}" if leaf else field


def field_is_askable(state: CaseState, field: Optional[str], question: Optional[str] = None) -> bool:
    """Agent가 고른 field가 유효하고 아직 확정/질문되지 않았는지 (변형 이름·유사 문장 포함)."""
    if not field:
        return False
    if field in state.asked_fields or field in state.review_answers:
        return False
    if turn_signal_irrelevant(state, field):
        return False
    # other_vehicle.turn_signal_right 처럼 이미 물은 항목에 접미사만 붙인 변형
    for asked in state.asked_fields:
        if field.startswith(asked + "_") or asked.startswith(field + "_"):
            return False
    if question and question_is_duplicate(state, question):
        return False
    if field.startswith("review."):
        return True
    slot = get_slot(state, field)
    if slot is None:
        return field in DEFAULT_QUESTIONS
    return not (slot.is_known() and slot.status == "CONFIRMED")


def asked_fields_text(state: CaseState) -> str:
    """이미 물은 항목을 답과 함께 보여준다 (LLM이 다른 이름으로 다시 묻는 것을 막기 위해)."""
    lines = []
    for field in state.asked_fields:
        if field in state.review_answers:
            value = state.review_answers[field]
        else:
            slot = get_slot(state, field)
            value = slot.value if slot and slot.is_known() else None
        if value is None:
            value = "사용자가 모른다고 답함" if any(field in item for item in state.uncertain_facts) else "답변 대기/미확인"
        lines.append(f"- {field} = {value}")
    return "\n".join(lines) or "(없음)"


# 활용형 포함 (모릅니다·몰라요·몰랐어요·기억이 안 나요·기억나지 않아요·못 봤어요·확인 안 됐어요·알 수 없어요·패스)
UNKNOWN_ANSWER_PATTERN = re.compile(
    r"(모릅|모르|몰라|몰랐|기억(이|은)?\s*(안|나지|못)|못\s*(봤|보았|확인)|확인\s*(못|안|불가)|알\s*수\s*없|잘\s*(안\s*보|모)|글쎄|패스)"
)


def mark_pending_unknown(state: CaseState, message: str) -> list[str]:
    """'잘 모르겠어요' 같은 답은 코드 레벨에서 pending 질문에 대한 '모름' 답변으로 처리한다."""
    if not state.pending_questions or not UNKNOWN_ANSWER_PATTERN.search(message):
        return []
    marked = []
    for question in list(state.pending_questions):
        note = f"사용자가 {question.field}에 대해 모른다고 답함"
        # '추가 정황 있나요?' 같은 열린 질문(review.*)에 '없어요'라고 한 것은 미확인 사실이 아니다
        if note not in state.uncertain_facts and not question.field.startswith("review."):
            state.uncertain_facts.append(note)
        if question.field not in state.asked_fields:
            state.asked_fields.append(question.field)
        marked.append(question.field)
    state.pending_questions = []
    state.touch()
    return marked


def video_unknowns_text(state: CaseState) -> str:
    lines = [f"- (확인 불가) {item}" for item in state.uncertain_facts[:10]]
    video = state.video_analysis
    if video is not None:
        for factor in video.fault_relevant_factors:
            if factor.observability == "UNKNOWN":
                lines.append(f"- (확인 불가) {factor.factor}: {factor.note}")
            elif factor.observability == "INFERRED":
                lines.append(f"- (추정만 됨) {factor.factor}: {factor.note}")
    for fact in state.video_inferred_facts()[:6]:
        lines.append(f"- (추정만 됨) {fact.fact}")
    return "\n".join(dict.fromkeys(lines)) or "(없음)"


def factor_checklist_text(state: CaseState) -> tuple[str, str]:
    from video.validation import FACTOR_CHECKLISTS, accident_profile

    video = state.video_analysis
    if video is None:
        return "unknown", "(영상 분석 없음)"
    profile = accident_profile(video)
    names = [name for name, _keywords in FACTOR_CHECKLISTS.get(profile, [])]
    return profile, ", ".join(names) or "(없음)"


def _vehicle_options(state: CaseState) -> str:
    if not state.video_analysis:
        return ""
    return ", ".join(
        f"{vehicle.id}: {vehicle.description or '설명 없음'}{' (블랙박스 차량)' if vehicle.is_ego else ''}"
        for vehicle in state.video_analysis.vehicles
    )


def default_question(field: str, state: CaseState) -> Optional[str]:
    template = DEFAULT_QUESTIONS.get(field)
    if template is None:
        return None
    return template.replace("{options}", _vehicle_options(state) or "차량 목록 없음")


MAX_ASK_COUNT = 2


def _candidates(state: CaseState, missing: list[MissingInformation]) -> list[MissingInformation]:
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    pending = {item.field: item for item in state.pending_questions}
    candidates = []
    for item in missing:
        if not item.user_answerable:
            continue
        previous = pending.get(item.field)
        if item.field in state.asked_fields and previous is None:
            continue
        if previous is not None and previous.ask_count >= MAX_ASK_COUNT:
            # 두 번 물어도 답이 없으면 더 묻지 않고 미확인으로 남긴다.
            note = f"사용자가 {item.field} 질문에 답하지 않아 미확인으로 처리"
            if note not in state.uncertain_facts:
                state.uncertain_facts.append(note)
            state.pending_questions = [q for q in state.pending_questions if q.field != item.field]
            continue
        slot = get_slot(state, item.field)
        # 영상 추론(INFERRED)만 있는 값은 확정이 아니므로 사용자에게 물을 수 있다
        if slot is not None and slot.is_known() and slot.status == "CONFIRMED":
            continue
        candidates.append(item)
    return sorted(candidates, key=lambda item: order.get(item.importance, 9))


def recheck_history_text(state: CaseState) -> str:
    return "\n".join(f"- {item}" for item in state.recheck_focus_history) or "(없음)"


def split_video_rechecks(
    state: CaseState, questions: list[Question], rechecks: list[RecheckTarget], *, max_rechecks: Optional[int] = None
) -> tuple[list[Question], list[RecheckTarget]]:
    """guardrail: 화면에 찍히는 사실을 묻는 질문은 재분석 요청으로 옮긴다 (재분석 예산이 있을 때만; 없으면 사용자에게 묻는다)."""
    kept: list[Question] = []
    for question in questions:
        decision, target = classify_video_topic(state, question, max_rechecks=max_rechecks)
        if decision == "drop":
            continue  # 영상이 이미 확인한 사실 — 물을 필요 없음
        if decision == "recheck" and target is not None:
            if not any(video_observable_topic(target.focus) == video_observable_topic(existing.focus) for existing in rechecks):
                rechecks.append(target)
            continue
        kept.append(question)
    deduped: list[RecheckTarget] = []
    for target in rechecks:
        text = f"{target.focus} {target.why}"
        if video_already_confirmed_topic(state, text):
            continue  # 1차 분석/일괄 재확인에서 이미 CONFIRMED — 다시 볼 필요 없음
        if recheck_already_done(state, target.focus):
            continue
        if any(video_observable_topic(target.focus) and video_observable_topic(target.focus) == video_observable_topic(existing.focus) for existing in deduped):
            continue
        deduped.append(target)
    return kept, deduped


def generate_followup_questions(
    client: Optional[TextClient],
    state: CaseState,
    missing: list[MissingInformation],
    *,
    max_questions: int = 1,
    run_logger: Optional[RunLogger] = None,
    intro_default: Optional[str] = None,
    allow_agent_choice: bool = True,
    use_llm_intro: bool = True,
    max_rechecks: Optional[int] = None,
    rechecks_unavailable: bool = False,
) -> tuple[str, list[Question], list[RecheckTarget]]:
    """다음 행동을 Agent가 스스로 추론해서 고른다: 영상 재분석 요청 또는 사용자 질문 하나.

    - LLM에는 질문 후보·체크리스트를 주지 않는다. 사건 상태와 영상 미확인 항목만 보고
      "판정에 영향을 주는데 확인되지 않은 요소"를 추론(reasoning)한 뒤,
      화면에 찍히는 것은 video_recheck_targets로, 사용자만 아는 것은 질문으로 낸다.
    - 코드 guardrail: critical 항목(영상 소유 관계 등)은 반드시 먼저, 주관 질문·중복 금지,
      화면에 찍히는 사실(노면 차선, 정지선, 본인 신호, 차로 위치, 제동)은 질문 대신 재분석.
    - 코드 후보(missing)는 LLM 호출이 실패했을 때만 fallback으로 쓴다.
    - LLM이 "더 물을 것이 없다"고 판단하면 빈 목록을 그대로 돌려준다.
    """
    logger = run_logger or get_run_logger()
    candidates = _candidates(state, missing)
    forced = next((item for item in candidates if item.importance == "critical"), None)
    if not candidates and not (allow_agent_choice and client is not None and state.video_analyzed):
        return "", [], []

    intro = intro_default or ""
    questions: list[Question] = []
    rechecks: list[RecheckTarget] = []
    llm_decided = False
    if client is not None and allow_agent_choice:
        system = load_prompt("master_agent", "system")
        task = load_prompt("master_agent", "followup_question")
        user = task.render(
            case_state=compact_json(state.compact(include_timeline=False), max_chars=8000),
            video_unknowns=video_unknowns_text(state),
            recheck_history=recheck_history_text(state),
            forced_field=(f"{forced.field} — {forced.reason}" if forced else "(없음)"),
            asked_fields=asked_fields_text(state),
            video_summary=(state.video_analysis.short_summary if state.video_analysis else "(영상 분석 결과 없음)"),
            max_questions=max_questions,
            recheck_availability=(
                "이번 턴에는 영상 재분석을 할 수 없다(재분석 예산 소진). (A) 항목은 미확인으로 두고, (B) 항목이 있으면 반드시 하나를 질문으로 낸다."
                if rechecks_unavailable or (max_rechecks is not None and state.video_reanalysis_count >= max_rechecks)
                else "영상 재분석 요청 가능."
            ),
        )
        try:
            response = client.generate_json(system=system.text, user=user, schema=_LLMQuestions, task=task.task)
            generated = _LLMQuestions.model_validate(response.data)
            logger.log(
                agent="master_agent",
                task=task.task,
                case_id=state.case_id,
                model=response.metrics.model,
                prompt_version=f"{system.version_id}+{task.version_id}",
                metrics=response.metrics,
                extra={"reasoning": generated.reasoning, "chosen": [q.field for q in generated.questions], "rechecks": [t.focus for t in generated.video_recheck_targets], "forced": forced.field if forced else None},
            )
            dropped: list[str] = []
            for item in generated.questions:
                field = normalize_field(state, item.field)
                if not field_is_askable(state, field, item.question) or not is_objective_question(item.question):
                    dropped.append(f"{item.field}→{field}")
                    continue
                if field in {q.field for q in questions}:
                    continue
                questions.append(Question(field=field, question=item.question.strip(), importance=item.importance, why=(item.why or "").strip() or None, reasoning=list(generated.reasoning)))
            if dropped:
                # 질문이 조용히 사라지면 대화가 갑자기 판정으로 넘어간다 → 왜 버렸는지 로그에 남긴다
                logger.log(agent="master_agent", task=task.task, case_id=state.case_id, extra={"dropped_questions": dropped, "kept": [q.field for q in questions]})
            rechecks = [target for target in generated.video_recheck_targets if target.focus.strip()]
            if use_llm_intro:
                intro = generated.intro.strip() or intro
            llm_decided = True
        except Exception as exc:  # noqa: BLE001
            logger.log(agent="master_agent", task=task.task, case_id=state.case_id, extra={"error": str(exc)[:300], "fallback": "deterministic_candidates"})

    # guardrail: critical 항목(영상 소유 관계 등)은 반드시 첫 질문
    if forced is not None and (not questions or questions[0].field != forced.field):
        phrased = next((q for q in questions if q.field == forced.field), None)
        questions = [q for q in questions if q.field != forced.field]
        if phrased is None:
            text = default_question(forced.field, state)
            phrased = Question(field=forced.field, question=text or forced.reason, importance="critical", why=forced.reason or None)
        questions.insert(0, phrased)

    # fallback: LLM 호출이 실패했을 때만 코드 후보로 채운다 (Agent가 '없음'으로 판단한 경우는 그대로 둔다)
    if not llm_decided:
        covered = {item.field for item in questions}
        for item in candidates:
            if len(questions) >= max_questions:
                break
            if item.field in covered:
                continue
            text = default_question(item.field, state)
            if text and is_objective_question(text):
                questions.append(Question(field=item.field, question=text, importance=item.importance, why=item.reason or None))
                covered.add(item.field)

    # guardrail: 화면에 찍히는 사실은 사용자에게 묻지 않고 영상 재분석으로 보낸다 (critical 항목은 제외)
    forced_questions = [q for q in questions if forced is not None and q.field == forced.field]
    other_questions = [q for q in questions if not (forced is not None and q.field == forced.field)]
    other_questions, rechecks = split_video_rechecks(state, other_questions, rechecks, max_rechecks=max_rechecks)
    questions = forced_questions + other_questions

    questions = questions[:max_questions]
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
    state.touch()
    return intro, questions, rechecks


_LLMQuestions.model_rebuild()


def _with_why(question: Question) -> str:
    # 영상 소유 관계 같은 코드 필수 질문을 제외하고는 Agent가 왜 묻는지 항상 보여준다
    if question.why and question.field not in {"video_source.vehicle_owner", "ego_vehicle.vehicle_id", "collision.participants_confirmed"}:
        return f"{question.question}\n(확인 이유: {question.why})"
    return question.question


def format_questions(intro: str, questions: list[Question]) -> str:
    lines = [intro.strip()] if intro else []
    if len(questions) == 1:
        lines.append(_with_why(questions[0]))
    else:
        for index, question in enumerate(questions, start=1):
            lines.append(f"{index}. {_with_why(question)}")
    return "\n".join(line for line in lines if line)
