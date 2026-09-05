"""사건경위서 생성 (가이드 2.1-F / 17 / 91~92절)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from common.jsonutil import compact_json
from document.grounding import ground_document
from document.package import VerifiedCasePackage
from models.clients import TextClient
from prompts.loader import load_prompt
from state.case_state import DocumentResult
from telemetry import RunLogger, get_run_logger

INCIDENT_SECTIONS = [
    "date_time",
    "location",
    "vehicles",
    "pre_collision",
    "collision_process",
    "collision",
    "post_collision",
    "objective_evidence",
    "notes",
]


class _DocumentOutput(BaseModel):
    title: str = "교통사고 사건경위서"
    sections: dict[str, str] = Field(default_factory=dict)
    cited_case_ids: list[str] = Field(default_factory=list)
    text: str = ""


def _ordered_sections(raw: dict[str, str], order: list[str]) -> dict[str, str]:
    ordered = {key: str(raw.get(key, "") or "") for key in order}
    for key, value in raw.items():
        if key not in ordered:
            ordered[key] = str(value or "")
    return ordered


def deterministic_incident_report(package: VerifiedCasePackage) -> DocumentResult:
    """LLM 없이 검증된 사실만으로 만드는 초안 (fallback)."""
    date = package.accident_datetime.get("date", "UNKNOWN")
    time = package.accident_datetime.get("time", "UNKNOWN")
    user = package.user_vehicle
    opponent = package.opponent_vehicle
    sections = {
        "date_time": f"사고 일시: {date if date != 'UNKNOWN' else '확인되지 않음'} {time if time != 'UNKNOWN' else ''}".strip(),
        "location": f"사고 장소: {package.location or '확인되지 않음'}. 도로 유형: {package.road.get('road_type', 'UNKNOWN')}, 신호등: {package.road.get('signal_present', 'UNKNOWN')}.",
        "vehicles": f"본 차량(사용자 차량): {user.get('vehicle_id') or '미확인'} ({user.get('description') or ''}). 상대 차량: {opponent.get('vehicle_id') or '미확인'} ({opponent.get('description') or ''}). 영상 출처: {package.video_source.get('type', 'UNKNOWN')}, 촬영 차량: {package.video_source.get('vehicle_owner', 'UNKNOWN')}.",
        "pre_collision": " ".join(f"{item.get('time') or ''} {item['event']}".strip() for item in package.timeline[:4]) or "영상에서 확인된 사고 직전 진행 상황이 없습니다.",
        "collision_process": f"본 차량 진행: {user.get('movement', 'UNKNOWN')}, 상대 차량 진행: {opponent.get('movement', 'UNKNOWN')}, 상대 차량 진입 방향: {opponent.get('entry_direction', 'UNKNOWN')}.",
        "collision": f"충돌 형태: {package.collision.get('type', 'UNKNOWN')}, 본 차량 충돌 부위: {package.collision.get('ego_collision_part', 'UNKNOWN')}, 상대 차량 충돌 부위: {package.collision.get('other_collision_part', 'UNKNOWN')}, 충돌 시각(영상 기준): {package.collision.get('timestamp', 'UNKNOWN')}.",
        "post_collision": " ".join(f"{item.get('time') or ''} {item['event']}".strip() for item in package.timeline[4:]) or "충돌 이후 상황은 영상에서 추가로 확인되지 않았습니다.",
        "objective_evidence": " ".join(f"- {fact}" for fact in package.verified_facts) or "영상에서 확정된 사실이 없습니다.",
        "notes": " ".join(
            [f"당사자 진술: {fact}" for fact in package.user_confirmed_facts]
            + [f"확인되지 않은 사항: {item}" for item in package.uncertainties]
            + [f"영상과 진술의 차이: {item}" for item in package.conflicts]
        ) or "특이사항 없음.",
    }
    document = DocumentResult(document_type="incident_report", title="교통사고 사건경위서", sections=sections, generation_method="deterministic_fallback")
    return ground_document(document, fact_blob=package.fact_blob(), allowed_case_ids=package.allowed_case_ids())


def generate_incident_report(
    client: Optional[TextClient],
    package: VerifiedCasePackage,
    *,
    run_logger: Optional[RunLogger] = None,
    temperature: float = 0.2,
) -> DocumentResult:
    logger = run_logger or get_run_logger()
    if client is None:
        return deterministic_incident_report(package)
    system = load_prompt("document_agent", "system")
    task = load_prompt("document_agent", "incident_report")
    payload = package.model_dump(exclude={"retrieved_cases", "fault_assessment", "adjustment_factors"})
    user = task.render(verified_case_state=compact_json(payload, max_chars=14000))
    try:
        response = client.generate_json(system=system.text, user=user, schema=_DocumentOutput, task=task.task, temperature=temperature)
        output = _DocumentOutput.model_validate(response.data)
        document = DocumentResult(
            document_type="incident_report",
            title=output.title or "교통사고 사건경위서",
            sections=_ordered_sections(output.sections, INCIDENT_SECTIONS),
            cited_case_ids=output.cited_case_ids,
            model=response.metrics.model,
            prompt_version=f"{system.version_id}+{task.version_id}",
        )
        logger.log(
            agent="document_agent",
            task=task.task,
            case_id=package.case_id,
            model=response.metrics.model,
            prompt_version=document.prompt_version,
            metrics=response.metrics,
        )
    except Exception as exc:  # noqa: BLE001
        logger.log(agent="document_agent", task=task.task, case_id=package.case_id, extra={"error": str(exc)[:300], "fallback": "deterministic_incident_report"})
        return deterministic_incident_report(package)
    return ground_document(document, fact_blob=package.fact_blob(), allowed_case_ids=package.allowed_case_ids())
