"""사건경위서 생성 (가이드 2.1-F / 17 / 91~92절, 백엔드 명세서 3.1의 4개 섹션 구성)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from common.jsonutil import compact_json
from document.grounding import SECTION_TITLES, ground_document
from document.package import VerifiedCasePackage
from models.clients import TextClient
from prompts.loader import load_prompt, wrap_user_text
from state.case_state import DocumentResult
from telemetry import RunLogger, get_run_logger

# 명세서 3.1: 1 일시·장소 / 2 사고 경위 / 3 영상 분석 결과 / 4 주장 요지
INCIDENT_SECTIONS = [
    "datetime_location",
    "accident_process",
    "video_analysis",
    "claim_summary",
]


class _DocumentOutput(BaseModel):
    title: str = "교통사고 사건경위서"
    sections: dict[str, str] = Field(default_factory=dict)
    caveat: str = ""
    cited_case_ids: list[str] = Field(default_factory=list)
    text: str = ""


def _ordered_sections(raw: dict[str, str], order: list[str]) -> dict[str, str]:
    ordered = {key: str(raw.get(key, "") or "") for key in order}
    for key, value in raw.items():
        if key not in ordered:
            ordered[key] = str(value or "")
    return ordered


def revision_block(revision_request: Optional[str], previous_sections: Optional[dict[str, str]]) -> str:
    """다시 쓰기 요청이 있을 때 프롬프트에 붙는 블록. 없으면 빈 문자열."""
    if not revision_request:
        return ""
    previous = "\n".join(
        f"[{SECTION_TITLES.get(key, key)}]\n{value}" for key, value in (previous_sections or {}).items() if value
    ) or "(이전 원고 없음)"
    return (
        "<PREVIOUS_DRAFT>\n" + previous + "\n</PREVIOUS_DRAFT>\n"
        + wrap_user_text(revision_request, tag="REVISION_REQUEST") + "\n"
        "다시 쓰기: REVISION_REQUEST가 요구한 부분만 고치고 나머지 내용과 사실은 PREVIOUS_DRAFT를 유지한다. "
        "요청이 입력에 없는 사실을 덧붙이라는 것이면 그 사실은 넣지 않는다."
    )


def _uncertainty_caveat(package: VerifiedCasePackage) -> str:
    items = [item for item in package.uncertainties if item][:2]
    if not items:
        return ""
    return "아직 확인되지 않은 부분(" + "; ".join(item[:40] for item in items) + ")은 본문에 단정해서 쓰지 않았어요."


def deterministic_incident_report(package: VerifiedCasePackage) -> DocumentResult:
    """LLM 없이 검증된 사실만으로 만드는 초안 (fallback)."""
    date = package.accident_datetime.get("date", "UNKNOWN")
    time = package.accident_datetime.get("time", "UNKNOWN")
    user = package.user_vehicle
    opponent = package.opponent_vehicle
    assessment = package.fault_assessment or {}
    ratio = assessment.get("fault_ratio") or {}
    timeline = " ".join(f"{item.get('time') or ''} {item['event']}".strip() for item in package.timeline[:6])
    sections = {
        "datetime_location": (
            f"사고 일시: {date if date != 'UNKNOWN' else '확인되지 않음'} {time if time != 'UNKNOWN' else ''}".strip()
            + f". 사고 장소: {package.location or '확인되지 않음'}. 도로 유형: {package.road.get('road_type', 'UNKNOWN')}, 신호등: {package.road.get('signal_present', 'UNKNOWN')}."
        ),
        "accident_process": (
            f"본 차량은 영상 속 {user.get('vehicle_id') or '미확인'} 차량({user.get('description') or ''})이며, 영상 출처는 {package.video_source.get('vehicle_owner', 'UNKNOWN')} 블랙박스입니다. "
            f"본 차량 진행: {user.get('movement', 'UNKNOWN')}, 상대 차량 진행: {opponent.get('movement', 'UNKNOWN')}, 상대 차량 진입 방향: {opponent.get('entry_direction', 'UNKNOWN')}. "
            f"충돌 형태: {package.collision.get('type', 'UNKNOWN')}, 본 차량 충돌 부위: {package.collision.get('ego_collision_part', 'UNKNOWN')}, 상대 차량 충돌 부위: {package.collision.get('other_collision_part', 'UNKNOWN')}. "
            + (f"영상 기준 경과: {timeline}." if timeline else "")
        ).strip(),
        "video_analysis": " ".join(f"- {fact}" for fact in package.verified_facts) or "영상에서 확정된 사실이 없습니다.",
        "claim_summary": (
            f"영상에서 확인된 사실을 근거로 본 차량 {ratio.get('user')} : 상대 차량 {ratio.get('opponent')}의 과실비율 적용을 요청드립니다. 이는 예상 비율이며 확정 판단이 아닙니다."
            if ratio
            else "확인된 사실을 바탕으로 과실비율 재검토를 요청드립니다."
        ),
    }
    document = DocumentResult(
        document_type="incident_report", title="교통사고 사건경위서", sections=sections,
        caveat=_uncertainty_caveat(package), generation_method="deterministic_fallback",
    )
    return ground_document(document, fact_blob=package.fact_blob(), allowed_case_ids=package.allowed_case_ids())


def generate_incident_report(
    client: Optional[TextClient],
    package: VerifiedCasePackage,
    *,
    run_logger: Optional[RunLogger] = None,
    temperature: float = 0.2,
    revision_request: Optional[str] = None,
    previous_sections: Optional[dict[str, str]] = None,
) -> DocumentResult:
    logger = run_logger or get_run_logger()
    if client is None:
        return deterministic_incident_report(package)
    system = load_prompt("document_agent", "system")
    task = load_prompt("document_agent", "incident_report")
    payload = package.model_dump(exclude={"retrieved_cases", "fault_assessment", "adjustment_factors"})
    assessment = package.fault_assessment
    assessment_payload = (
        {
            "fault_ratio": assessment.get("fault_ratio"),
            "assessment_type": assessment.get("assessment_type"),
            "primary_case_ids": assessment.get("primary_case_ids"),
            "core_facts": assessment.get("core_facts"),
            "reasoning_summary": assessment.get("reasoning_summary"),
            "explanation": assessment.get("explanation"),
        }
        if assessment
        else None
    )
    user = task.render(
        verified_case_state=compact_json(payload, max_chars=14000),
        fault_assessment=compact_json(assessment_payload, max_chars=3000) if assessment_payload else "(없음)",
        revision_block=revision_block(revision_request, previous_sections),
    )
    try:
        response = client.generate_json(system=system.text, user=user, schema=_DocumentOutput, task=task.task, temperature=temperature)
        output = _DocumentOutput.model_validate(response.data)
        document = DocumentResult(
            document_type="incident_report",
            title=output.title or "교통사고 사건경위서",
            sections=_ordered_sections(output.sections, INCIDENT_SECTIONS),
            caveat=output.caveat.strip(),
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
            extra={"revision": bool(revision_request)},
        )
    except Exception as exc:  # noqa: BLE001
        logger.log(agent="document_agent", task=task.task, case_id=package.case_id, extra={"error": str(exc)[:300], "fallback": "deterministic_incident_report"})
        return deterministic_incident_report(package)
    if not document.caveat:
        document.caveat = _uncertainty_caveat(package)
    return ground_document(document, fact_blob=package.fact_blob(), allowed_case_ids=package.allowed_case_ids())
