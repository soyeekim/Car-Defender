"""RAG Query 생성 (가이드 15.4 / 80절).

Case State의 객관적 사고 구조만 사용하여 짧은 structured query와 dense retrieval용
detailed query를 만든다. 사용자 주장·감정은 포함하지 않는다.
"""

from __future__ import annotations

from typing import Optional

from common.jsonutil import compact_json
from models.clients import TextClient
from prompts.loader import load_prompt
from state.case_state import CaseMetadata, CaseState, RagQuery
from state.updater import get_slot
from telemetry import RunLogger, get_run_logger

_MOVEMENT_KO = {
    "straight": "직진",
    "left_turn": "좌회전",
    "right_turn": "우회전",
    "u_turn": "유턴",
    "lane_change_left": "차로변경",
    "lane_change_right": "차로변경",
    "lane_change": "차로변경",
    "reversing": "후진",
    "stopped": "정차",
    "parking": "주차",
    "overtaking": "추월",
}
_ROAD_KO = {
    "intersection": "교차로",
    "four_way": "사거리 교차로",
    "three_way": "삼거리 교차로",
    "t_intersection": "삼거리(T자) 교차로",
    "roundabout": "회전교차로",
    "straight": "직선도로",
    "parking_lot": "주차장",
    "highway": "고속도로",
    "alley": "이면도로",
    "ramp": "진출입로",
    "crosswalk": "횡단보도",
}
_DIRECTION_KO = {
    "right": "우측 도로에서 진입",
    "left": "좌측 도로에서 진입",
    "opposite": "맞은편(대향) 방향",
    "same_direction": "동일 방향",
    "rear": "후방에서 접근",
    "front": "전방",
    "side": "측면 방향에서 진입",
}
_PART_KO = {
    "front": "전면",
    "front_left": "좌측 전면",
    "front_right": "우측 전면",
    "left_side": "좌측면",
    "right_side": "우측면",
    "rear": "후면",
    "rear_left": "좌측 후면",
    "rear_right": "우측 후면",
}


def _ko(mapping: dict[str, str], value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    lowered = str(value).lower()
    for key, label in mapping.items():
        if key in lowered:
            return label
    return value


def _value(state: CaseState, path: str) -> Optional[str]:
    slot = get_slot(state, path)
    return slot.value if slot and slot.is_known() else None


def deterministic_query(state: CaseState) -> RagQuery:
    road_type = _value(state, "road.road_type")
    intersection = _value(state, "road.intersection_type")
    signal_present = _value(state, "road.signal_present")
    ego_move = _ko(_MOVEMENT_KO, _value(state, "ego_vehicle.movement"))
    other_move = _ko(_MOVEMENT_KO, _value(state, "other_vehicle.movement"))
    entry = _ko(_DIRECTION_KO, _value(state, "other_vehicle.entry_direction") or _value(state, "collision.relative_direction"))
    ego_part = _ko(_PART_KO, _value(state, "collision.ego_collision_part"))
    other_part = _ko(_PART_KO, _value(state, "collision.other_collision_part"))
    ego_first = _value(state, "ego_vehicle.entered_first")
    other_first = _value(state, "other_vehicle.entered_first")
    lane_change_ego = _value(state, "ego_vehicle.lane_change")
    lane_change_other = _value(state, "other_vehicle.lane_change")
    turn_signal_other = _value(state, "other_vehicle.turn_signal")
    ego_signal = _value(state, "ego_vehicle.signal")
    other_signal = _value(state, "other_vehicle.signal")

    place = _ko(_ROAD_KO, intersection) if intersection and intersection not in {"none", "unknown"} else _ko(_ROAD_KO, road_type)
    signal_text = None
    if signal_present == "true":
        signal_text = "신호기 있는"
    elif signal_present == "false":
        signal_text = "신호기 없는"

    factors: list[str] = []
    if place:
        factors.append(f"{signal_text} {place}".strip() if signal_text else place)
    if ego_move or other_move:
        factors.append(f"{ego_move or '진행'} 대 {other_move or '진행'}")
    if entry:
        factors.append(f"상대 차량 {entry}")
    if ego_first == "true":
        factors.append("A 차량 선진입")
    elif other_first == "true":
        factors.append("B 차량 선진입")
    if lane_change_ego == "true":
        factors.append("A 차량 차로변경")
    if lane_change_other == "true":
        factors.append("B 차량 차로변경")
        if turn_signal_other == "false":
            factors.append("방향지시등 미점등")
    if ego_signal:
        factors.append(f"A 신호 {ego_signal}")
    if other_signal:
        factors.append(f"B 신호 {other_signal}")
    if ego_part or other_part:
        factors.append(f"A {ego_part or '충돌부위 미확인'} B {other_part or '충돌부위 미확인'} 충돌")
    collision_type = _value(state, "collision.type")
    if collision_type:
        factors.append(collision_type)

    filters = CaseMetadata(
        road_type=("roundabout" if place and "회전교차로" in place else "intersection" if place and "교차로" in place else ("straight" if place == "직선도로" else ("parking_lot" if place == "주차장" else ("highway" if place == "고속도로" else None)))),
        intersection_type=("four_way" if place and "사거리" in place else "three_way" if place and "삼거리" in place else ("roundabout" if place and "회전교차로" in place else None)),
        signal_present=(True if signal_present == "true" else False if signal_present == "false" else None),
        accident_target="차대차",
        movement_a=ego_move,
        movement_b=other_move,
        lane_change=(True if lane_change_ego == "true" or lane_change_other == "true" else None),
        keywords=[],
    )
    structured = " ".join(factors) or "교통사고 차대차 충돌 과실비율"
    detailed_parts = []
    if place:
        detailed_parts.append(f"{signal_text + ' ' if signal_text else ''}{place}에서 발생한 차대차 사고.")
    if ego_move:
        detailed_parts.append(f"A(청구·사용자) 차량은 {ego_move} 중이었다.")
    if other_move or entry:
        detailed_parts.append(f"B(피청구·상대) 차량은 {entry + ' ' if entry else ''}{other_move or '진행'} 중이었다.")
    if ego_first == "true" or other_first == "true":
        detailed_parts.append(f"{'A' if ego_first == 'true' else 'B'} 차량이 교차로에 먼저 진입하였다.")
    if ego_part or other_part:
        detailed_parts.append(f"A 차량 {ego_part or '(미확인)'}과 B 차량 {other_part or '(미확인)'}이 충돌하였다.")
    if state.video_analysis and state.video_analysis.detailed_description:
        detailed_parts.append(state.video_analysis.detailed_description[:600])
    return RagQuery(
        structured_query=structured,
        detailed_query=" ".join(detailed_parts) or structured,
        key_factors=factors,
        filters=filters,
        generation_method="deterministic",
    )


def build_rag_query(
    client: Optional[TextClient],
    state: CaseState,
    *,
    run_logger: Optional[RunLogger] = None,
) -> RagQuery:
    fallback = deterministic_query(state)
    if client is None:
        return fallback
    logger = run_logger or get_run_logger()
    system = load_prompt("master_agent", "system")
    task = load_prompt("master_agent", "rag_query")
    user = task.render(
        case_state=compact_json(state.compact(include_timeline=True), max_chars=9000),
        video_description=(state.video_analysis.detailed_description if state.video_analysis else "(없음)"),
    )
    try:
        response = client.generate_json(system=system.text, user=user, schema=RagQuery, task=task.task)
        query = RagQuery.model_validate(response.data)
        logger.log(
            agent="master_agent",
            task=task.task,
            case_id=state.case_id,
            model=response.metrics.model,
            prompt_version=f"{system.version_id}+{task.version_id}",
            metrics=response.metrics,
        )
    except Exception as exc:  # noqa: BLE001
        logger.log(agent="master_agent", task=task.task, case_id=state.case_id, extra={"error": str(exc)[:300], "fallback": "deterministic_query"})
        return fallback
    query.generation_method = "llm"
    if not query.structured_query.strip():
        query.structured_query = fallback.structured_query
    if not query.detailed_query.strip():
        query.detailed_query = fallback.detailed_query
    if not query.key_factors:
        query.key_factors = fallback.key_factors
    # 확실한 deterministic 필터는 유지한다 (LLM이 비워도 됨)
    for name in ("road_type", "intersection_type", "signal_present", "accident_target", "lane_change"):
        if getattr(query.filters, name) is None and getattr(fallback.filters, name) is not None:
            setattr(query.filters, name, getattr(fallback.filters, name))
    return query
