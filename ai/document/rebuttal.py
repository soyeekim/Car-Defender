"""반박의견서 생성 (가이드 2.1-G / 18 / 93~94절)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from common.jsonutil import compact_json
from document.grounding import SECTION_TITLES, ground_document
from document.incident_report import revision_block
from document.package import VerifiedCasePackage
from models.clients import TextClient
from prompts.loader import load_prompt, sanitize_user_text
from state.case_state import DocumentResult
from telemetry import RunLogger, get_run_logger

REBUTTAL_SECTIONS = [
    "overview",
    "opponent_claim",
    "objective_facts",
    "key_issues",
    "similar_cases",
    "commonalities",
    "differences",
    "basic_ratio_review",
    "adjustment_factor_review",
    "final_opinion",
]
_NO_CLAIM = "상대방 주장은 현재 제공되지 않았습니다. 추후 확인 시 보완이 필요합니다."
MAIL_BODY_MAX_CHARS = 5000  # 백엔드 계약: 반박의견서 본문은 5000자 이내


class _DocumentOutput(BaseModel):
    title: str = "과실비율 반박의견서"
    sections: dict[str, str] = Field(default_factory=dict)
    mail_body: str = ""
    cited_case_ids: list[str] = Field(default_factory=list)
    text: str = ""


def _ordered_sections(raw: dict[str, str]) -> dict[str, str]:
    ordered = {key: str(raw.get(key, "") or "") for key in REBUTTAL_SECTIONS}
    for key, value in raw.items():
        if key not in ordered:
            ordered[key] = str(value or "")
    return ordered


def _report_block(report_sections: Optional[dict[str, str]]) -> str:
    if not report_sections:
        return "(없음)"
    return "\n".join(f"[{SECTION_TITLES.get(key, key)}]\n{value}" for key, value in report_sections.items() if value) or "(없음)"


def compose_mail_body(document: DocumentResult, *, max_chars: int = MAIL_BODY_MAX_CHARS) -> str:
    """LLM이 mail_body를 주지 않았을 때 섹션을 이어 붙여 메일 본문을 만든다. 길면 덜 중요한 섹션부터 뺀다."""
    if document.mail_body.strip():
        body = document.mail_body.strip()
    else:
        priority_drop = ["commonalities", "differences", "similar_cases", "key_issues", "adjustment_factor_review", "overview"]
        keys = [key for key in REBUTTAL_SECTIONS if document.sections.get(key)]

        def render(selected: list[str]) -> str:
            lines = []
            for key in selected:
                lines.append(SECTION_TITLES.get(key, key))
                lines.append(document.sections[key].strip())
                lines.append("")
            lines.append("※ 본 문서의 과실비율은 영상·확인된 사실·유사 심의사례를 기준으로 한 예상치이며 확정 판단이 아닙니다.")
            return "\n".join(lines).strip()

        body = render(keys)
        for key in priority_drop:
            if len(body) <= max_chars:
                break
            if key in keys:
                keys.remove(key)
                body = render(keys)
    if len(body) > max_chars:
        cut = body[:max_chars]
        boundary = max(cut.rfind("다."), cut.rfind("\n"))
        body = cut[: boundary + 2] if boundary > max_chars // 2 else cut
    return body.strip()


def deterministic_rebuttal(package: VerifiedCasePackage) -> DocumentResult:
    assessment = package.fault_assessment or {}
    ratio = assessment.get("fault_ratio") or {}
    primary_ids = assessment.get("primary_case_ids") or []
    cases = [case for case in package.retrieved_cases if case.get("case_id") in primary_ids] or package.retrieved_cases[:2]
    case_lines = [
        f"심의번호 {case['case_id']}: {case.get('title') or ''} (기본비율 {case.get('basic_ratio') or '미상'}, 결정비율 {case.get('decision_ratio') or '미상'})"
        for case in cases
    ]
    common = [f"{case['case_id']}: {', '.join(case.get('matched_factors') or [])}" for case in cases if case.get("matched_factors")]
    diff = [f"{case['case_id']}: {', '.join(case.get('different_factors') or [])}" for case in cases if case.get("different_factors")]
    adjustments = [
        f"{item.get('factor')} ({item.get('direction')}, {'적용 가능' if item.get('applies') else '확인 불가'})" for item in package.adjustment_factors
    ]
    sections = {
        "overview": f"본 사고는 {package.road.get('road_type', 'UNKNOWN')} 유형 도로에서 본 차량({package.user_vehicle.get('movement', 'UNKNOWN')})과 상대 차량({package.opponent_vehicle.get('movement', 'UNKNOWN')}) 사이에 발생한 {package.collision.get('type', 'UNKNOWN')} 형태의 충돌 사고입니다.",
        "opponent_claim": package.opponent_claim or _NO_CLAIM,
        "objective_facts": " ".join(f"- {fact}" for fact in package.verified_facts) or "영상에서 확정된 사실이 없습니다.",
        "key_issues": " ".join(f"- {item}" for item in (assessment.get("reasoning_summary") or [])) or "핵심 쟁점은 추가 분석이 필요합니다.",
        "similar_cases": " ".join(case_lines) or "인용 가능한 유사 심의사례가 없습니다.",
        "commonalities": " ".join(common) or "공통점은 사례 검증 결과가 없어 기재하지 않습니다.",
        "differences": " ".join(diff) or "차이점은 사례 검증 결과가 없어 기재하지 않습니다.",
        "basic_ratio_review": (
            f"유사 심의사례의 기본비율을 참고하면 본 차량 {ratio.get('user')} : 상대 차량 {ratio.get('opponent')} 수준이 예상됩니다."
            if ratio
            else "기본 과실비율 검토를 위해서는 과실비율 판정이 먼저 필요합니다."
        ),
        "adjustment_factor_review": " ".join(adjustments) or "확인된 사실로 적용 가능한 수정요소는 현재 없습니다.",
        "final_opinion": (assessment.get("explanation") or "예상 과실비율 판정이 완료된 뒤 최종 의견을 작성할 수 있습니다.")
        + " " + " ".join(f"확인되지 않은 사항: {item}" for item in (assessment.get("uncertainties") or [])[:3]),
    }
    claim_line = f"귀사가 제시하신 과실비율({package.opponent_claim})에 대해 " if package.opponent_claim else "제시하신 과실비율에 대해 "
    mail_body = (
        f"안녕하세요. 본 사고 건과 관련하여 {claim_line}재검토를 요청드립니다.\n\n"
        + ("블랙박스 영상에서 다음 사실이 확인됩니다.\n" + "\n".join(f"- {fact}" for fact in package.verified_facts[:3]) + "\n\n" if package.verified_facts else "")
        + (("참고 심의사례: " + ", ".join(case['case_id'] for case in cases) + "\n\n") if cases else "")
        + (f"이에 비추어 본 차량 {ratio.get('user')} : 상대 차량 {ratio.get('opponent')}의 과실비율 적용이 타당하다고 판단됩니다. " if ratio else "")
        + "확인 후 회신 부탁드립니다. 감사합니다."
    )
    document = DocumentResult(document_type="rebuttal_opinion", title="과실비율 반박의견서", sections=sections, mail_body=mail_body, generation_method="deterministic_fallback")
    return ground_document(
        document,
        fact_blob=package.fact_blob(),
        allowed_case_ids=package.allowed_case_ids(),
        opponent_claim_provided=bool(package.opponent_claim),
    )


def generate_rebuttal_opinion(
    client: Optional[TextClient],
    package: VerifiedCasePackage,
    *,
    run_logger: Optional[RunLogger] = None,
    temperature: float = 0.2,
    report_sections: Optional[dict[str, str]] = None,
    revision_request: Optional[str] = None,
    previous_sections: Optional[dict[str, str]] = None,
) -> DocumentResult:
    logger = run_logger or get_run_logger()
    if client is None:
        return deterministic_rebuttal(package)
    system = load_prompt("document_agent", "system")
    task = load_prompt("document_agent", "rebuttal_opinion")
    state_payload = package.model_dump(exclude={"retrieved_cases", "fault_assessment", "adjustment_factors", "opponent_claim"})
    user = task.render(
        verified_case_state=compact_json(state_payload, max_chars=12000),
        fault_assessment=compact_json({"assessment": package.fault_assessment, "adjustment_factors": package.adjustment_factors}, max_chars=6000),
        similar_cases=compact_json(package.retrieved_cases, max_chars=16000),
        opponent_claim=sanitize_user_text(package.opponent_claim) or "(제공되지 않음)",
        report_sections=_report_block(report_sections),
        revision_block=revision_block(revision_request, previous_sections),
    )
    try:
        response = client.generate_json(system=system.text, user=user, schema=_DocumentOutput, task=task.task, temperature=temperature)
        output = _DocumentOutput.model_validate(response.data)
        document = DocumentResult(
            document_type="rebuttal_opinion",
            title=output.title or "과실비율 반박의견서",
            sections=_ordered_sections(output.sections),
            mail_body=output.mail_body.strip(),
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
            extra={"revision": bool(revision_request), "with_report": report_sections is not None},
        )
    except Exception as exc:  # noqa: BLE001
        logger.log(agent="document_agent", task=task.task, case_id=package.case_id, extra={"error": str(exc)[:300], "fallback": "deterministic_rebuttal"})
        return deterministic_rebuttal(package)
    document = ground_document(
        document,
        fact_blob=package.fact_blob(),
        allowed_case_ids=package.allowed_case_ids(),
        opponent_claim_provided=bool(package.opponent_claim),
    )
    if not package.opponent_claim and document.mail_body:
        # 상대 주장이 없는데 메일 본문이 특정 비율을 "제시하셨다"고 쓰면 사실이 아니다
        document.mail_body = document.mail_body.replace("귀사가 제시하신", "제시하신")
    return document
