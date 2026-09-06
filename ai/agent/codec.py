"""Case State ↔ 백엔드 `facts` 직렬화.

백엔드 계약(server/docs/agent-interface.md)은 Agent가 상태를 갖지 않기를 요구한다. 대신 `facts`(자유 JSON)가
매 호출에 통째로 왕복하고 `fact_updates`가 최상위 키 단위로 얕게 병합된다. 그래서 Case State 전체를
`facts["_case_state"]` 한 키에 넣어 왕복시키고, 사람이 읽을 요약은 별도 최상위 키로 둔다.

- JSON 직렬화 가능한 값만 담는다 (`model_dump(mode="json")`).
- 대화 기록은 백엔드가 `messages`로 다시 주므로 넣지 않는다. 문서 원문도 백엔드가 보관하므로 넣지 않는다.
- 부피가 큰 영상 추적 타임라인·긴 발췌문은 잘라서 작게 유지한다.
"""

from __future__ import annotations

from typing import Any, Optional

from agent.presenters import fact_chips
from state.case_state import CaseState, Message

SCHEMA_VERSION = 1
STATE_KEY = "_case_state"
META_KEY = "_agent"
IMPL_NAME = "ai.agent.real:RealAgent"

_EXCLUDE = {"conversation_history", "incident_report", "rebuttal_opinion", "missing_information"}


def pack_state(state: CaseState) -> dict[str, Any]:
    data = state.model_dump(mode="json", exclude=_EXCLUDE)
    video = data.get("video_analysis")
    if video:
        video["tracking_timeline"] = []
        video["detailed_description"] = (video.get("detailed_description") or "")[:3000]
        video["timeline"] = (video.get("timeline") or [])[:40]
    for case in data.get("retrieved_cases") or []:
        case["excerpt"] = (case.get("excerpt") or "")[:1200]
        case["accident_description"] = (case.get("accident_description") or "")[:1500]
    data["notes"] = (data.get("notes") or [])[-30:]
    data["facts"] = (data.get("facts") or [])[-80:]
    return data


def unpack_state(data: dict[str, Any]) -> CaseState:
    return CaseState.model_validate(data)


def sync_conversation(state: CaseState, messages: list[Any]) -> None:
    """백엔드가 준 최근 대화(text 메시지)를 Case State의 대화 기록으로 삼는다."""
    history: list[Message] = []
    for index, item in enumerate(messages):
        role = getattr(item, "role", None) or (item.get("role") if isinstance(item, dict) else None)
        text = getattr(item, "text", None) or (item.get("text") if isinstance(item, dict) else None)
        if role not in {"user", "assistant"} or not text:
            continue
        history.append(Message(role=role, content=str(text), turn=index))
    state.conversation_history = history


def _known(slot) -> Optional[str]:
    return slot.value if slot is not None and slot.is_known() else None


def readable_facts(state: CaseState) -> dict[str, Any]:
    """백엔드 개발자·운영자가 DB에서 바로 읽을 수 있는 요약 키 (작게 유지)."""
    video = state.video_analysis
    summary: dict[str, Any] = {
        "video_summary": video.short_summary[:400] if video else "",
        "stage": state.current_stage,
        "vehicles": {
            "mine": {"id": state.ego_vehicle.vehicle_id, "description": state.ego_vehicle.description, "movement": _known(state.ego_vehicle.movement)},
            "other": {"id": state.other_vehicle.vehicle_id, "description": state.other_vehicle.description, "movement": _known(state.other_vehicle.movement)},
        },
        "road": {
            "type": _known(state.road.road_type),
            "signal_present": _known(state.road.signal_present),
            "lane_marking": _known(state.road.lane_marking),
        },
        "collision": {"type": _known(state.collision.type), "at": _known(state.collision.timestamp), "my_part": _known(state.collision.ego_collision_part)},
        "key_facts": [fact.fact[:120] for fact in state.video_confirmed_facts()[:12]],
        "user_facts": [fact.fact[:120] for fact in state.user_confirmed_facts[-8:]],
        "unconfirmed": [item[:120] for item in state.uncertain_facts[:6]],
        "similar_case_ids": [case.case_id for case in state.retrieved_cases[:3]],
        # 사건 현황판 '확인된 사실' 칩 — 백엔드가 GET /cases/{id} 의 facts 로 그대로 내려준다
        "fact_chips": fact_chips(state),
    }
    if state.fault_assessment is not None:
        summary["fault_ratio"] = {"mine": state.fault_assessment.fault_ratio.user, "other": state.fault_assessment.fault_ratio.opponent}
    return summary


def pack_facts(state: CaseState, *, verdict_version: int = 0, extra: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    facts: dict[str, Any] = {
        META_KEY: {
            "impl": IMPL_NAME,
            "schema": SCHEMA_VERSION,
            "case_id": state.case_id,
            "stage": state.current_stage,
            "verdict_version": verdict_version,
            "updated_at": state.updated_at,
        },
        STATE_KEY: pack_state(state),
    }
    facts.update(readable_facts(state))
    if extra:
        facts.update(extra)
    return facts


def stored_verdict_version(facts: dict[str, Any]) -> int:
    meta = facts.get(META_KEY) if isinstance(facts, dict) else None
    try:
        return int((meta or {}).get("verdict_version") or 0)
    except (TypeError, ValueError):
        return 0
