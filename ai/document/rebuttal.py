"""반박의견서 생성 (가이드 2.1-G / 18 / 93~94절)."""

from __future__ import annotations

import re
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


_SLOT_META = re.compile(r"\s*\[[^\]]*\]\s*$")
_UNCERTAIN = re.compile(r"불명확|확인이 필요|추가적인 확인|추가 확인|확인되지 않|확인 불가|불확실|알 수 없|미확인|UNKNOWN|추정됩니다|추정된다|가능성이 (높|있)")
_CLAIM_RATIO = re.compile(r"나\s*(\d{1,3})\s*[:대]\s*상대\s*(\d{1,3})|(\d{1,3})\s*[:대]\s*(\d{1,3})")


def _slot_value(block: dict, key: str) -> str:
    raw = str(block.get(key) or "").strip()
    if not raw or raw.upper() == "UNKNOWN":
        return ""
    return _SLOT_META.sub("", raw).strip()


def _claim_ratio_text(claim: Optional[str]) -> str:
    """'상대 보험사 주장: 나 40 : 상대 60' / '50:50을 주장' → '나 40 : 상대 60'. 못 읽으면 원문 요약."""
    if not claim:
        return ""
    match = _CLAIM_RATIO.search(claim)
    if match:
        mine, other = (match.group(1), match.group(2)) if match.group(1) else (match.group(3), match.group(4))
        return f"나 {int(mine)} : 상대 {int(other)}"
    return claim.strip()[:80]


def _certain(items: list[str], *, limit: int, humanize=None) -> list[str]:
    """보험사 담당자가 확인할 수 없는 '불명확·추가 확인 필요' 류는 메일에 싣지 않는다."""
    out: list[str] = []
    for item in items:
        text = str(item or "").strip().rstrip(".")
        if humanize is not None:
            text = humanize(text)
        if text and not _UNCERTAIN.search(text) and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


_STATUS_TOKEN = re.compile(r"\s*\((?:USER_CONFIRMED|VIDEO_CONFIRMED|CONFIRMED|INFERRED|UNKNOWN|user_confirmed|video_confirmed)\)")
_DIRECTION_LABELS = {"user_up": "본인 과실 가산 요소", "user_down": "본인 과실 감산 요소", "neutral": "중립 요소"}
_META_STATEMENT = re.compile(r"사고일|사고 일시|사고 날짜|블랙박스|촬영 차량")
_ISO_DATE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")


def _vehicle_humanizer(package: VerifiedCasePackage):
    """영상 사실에 섞인 vehicle_N / '자차' 를 제출 문서용 표현(본 차량·상대 차량)으로 바꾼다."""
    ego = _slot_value(package.user_vehicle, "vehicle_id")
    opponent = _slot_value(package.opponent_vehicle, "vehicle_id")
    names = {ego: "본 차량", opponent: "상대 차량"}

    def humanize(text: str) -> str:
        text = re.sub(r"\s*\((vehicle_\d+)\)", "", text)
        text = re.sub(r"(?<![A-Za-z_])(vehicle_\d+)(?![A-Za-z0-9_])", lambda m: names.get(m.group(1), "다른 차량"), text)
        text = re.sub(r"(?<![가-힣])자차(?![가-힣])", "본 차량", text)
        # 'vehicle_3가' → '상대 차량가' 처럼 조사가 어긋나는 것을 받침(량)에 맞게 고친다
        text = re.sub(r"(차량)(가|를|는|와)(?![가-힣])", lambda m: m.group(1) + {"가": "이", "를": "을", "는": "은", "와": "과"}[m.group(2)], text)
        return _STATUS_TOKEN.sub("", text).strip()

    return humanize


def _date_label(value: str) -> str:
    match = _ISO_DATE.match(value.strip())
    if match:
        year, month, day = (int(part) for part in match.groups())
        return f"{year}년 {month}월 {day}일"
    return value


def build_mail_body(package: VerifiedCasePackage, *, report_attached: bool = False) -> str:
    """본인 가입 보험사 담당자에게 보내는 반박의견서 메일 본문 (합니다체).

    구조: 주장 비율 → 근거(영상 사실 / 유사 심의사례 요약 / 수정요소) → 결론. 확정된 사실과 입력된 심의사례만 쓰고,
    '불명확하니 확인해 달라'는 식의 문장은 넣지 않는다 (받는 쪽이 확인할 수 있는 내용이 아니다)."""
    assessment = package.fault_assessment or {}
    ratio = assessment.get("fault_ratio") or {}
    mine, other = ratio.get("user"), ratio.get("opponent")
    claimed = _claim_ratio_text(package.opponent_claim)
    humanize = _vehicle_humanizer(package)

    date = _date_label(_slot_value(package.accident_datetime, "date"))
    time = _slot_value(package.accident_datetime, "time")
    when = " ".join(part for part in (date, time) if part)
    where = (package.location or "").strip()
    incident = ((when + " ") if when else "") + ((where + "에서 ") if where else "") + "발생한 사고"

    lines = ["담당자님께,", "", f"본인은 귀사 자동차보험 계약자로서, {incident}의 과실비율에 대해 아래와 같이 의견을 드립니다.", ""]

    lines.append("1. 주장하는 과실비율")
    if claimed:
        lines.append(f"- 현재 제시된 과실비율: {claimed}")
    if mine is not None and other is not None:
        lines.append(f"- 본인이 주장하는 과실비율: 나 {mine} : 상대 {other}")
    lines.append("- 요청 사항: 위 비율을 기준으로 상대 보험사와 재협의해 주시고, 협의가 어려우면 과실비율 분쟁심의위원회 심의 청구를 검토해 주시기 바랍니다.")
    lines.append("")

    lines.append("2. 주장 근거")
    lines.append("가. 블랙박스 영상에서 확인된 사실")
    facts = _certain(package.verified_facts, limit=4, humanize=humanize)
    if not facts:
        facts = _certain([package.video_summary], limit=1, humanize=humanize)
    lines.extend(f"- {fact}" for fact in facts)
    statements = _certain(
        [re.sub(r"\s*\(영상 확인:[^)]*\)\s*$", "", item) for item in package.user_confirmed_facts if not _META_STATEMENT.search(item)],
        limit=2, humanize=humanize,
    )
    if statements:
        lines.append("나. 당사자 진술")
        lines.extend(f"- {item}" for item in statements)
        case_label, factor_label = "다", "라"
    else:
        case_label, factor_label = "나", "다"

    primary_ids = [str(item) for item in (assessment.get("primary_case_ids") or [])]
    cases = [case for case in package.retrieved_cases if case.get("case_id") in primary_ids]
    cases += [case for case in package.retrieved_cases if case not in cases]
    cases = [case for case in cases if case.get("source_type", "deliberation_case") == "deliberation_case"][:3]
    lines.append(f"{case_label}. 유사 심의사례")
    if not cases:
        lines.append("- 인용 가능한 심의사례가 없어 과실비율 인정기준 도표를 기준으로 판단하였습니다.")
    for case in cases:
        head = f"- 심의사례 {case.get('case_id')}"
        title = str(case.get("title") or "").strip()
        if title:
            head += f" ({title[:60]})"
        if case.get("decision_ratio"):
            head += f" · 결정비율 {case['decision_ratio']}"
        elif case.get("basic_ratio"):
            head += f" · 기본비율 {case['basic_ratio']}"
        lines.append(head)
        description = " ".join(str(case.get("accident_description") or "").split())
        if description:
            lines.append("  사고 개요: " + description[:160] + ("…" if len(description) > 160 else ""))
        reasons = _certain([str(item) for item in (case.get("decision_reasons") or [])], limit=2)
        if reasons:
            lines.append("  심의 이유: " + "; ".join(item[:100] for item in reasons))
        matched = [str(item).strip() for item in (case.get("matched_factors") or []) if str(item).strip()][:3]
        if matched:
            lines.append("  본 사고와의 공통점: " + ", ".join(matched))

    applied = [item for item in package.adjustment_factors if item.get("applies") and item.get("factor")]
    lines.append(f"{factor_label}. 과실 수정요소")
    if applied:
        for item in applied[:4]:
            label = _DIRECTION_LABELS.get(str(item.get("direction") or "").strip(), "")
            lines.append(f"- {humanize(str(item['factor']))}" + (f" ({label})" if label else ""))
    else:
        lines.append("- 확인된 사실만으로는 별도 수정요소를 요청하지 않으며, 유사 심의사례의 비율을 그대로 적용하는 것이 타당하다고 판단됩니다.")
    lines.append("")

    lines.append("3. 결론")
    if mine is not None and other is not None:
        lines.append(f"위 사실과 심의사례에 비추어 본 사고의 과실비율은 나 {mine} : 상대 {other}이 타당하다고 판단됩니다. 재검토를 요청드립니다.")
    else:
        lines.append("위 사실과 심의사례에 비추어 과실비율 재검토를 요청드립니다.")
    lines.append("")
    lines.append("감사합니다.")
    return "\n".join(lines)


def compose_mail_body(document: DocumentResult, *, max_chars: int = MAIL_BODY_MAX_CHARS) -> str:
    """mail_body 가 있으면 그것을(길면 잘라서), 없으면 섹션을 이어 붙여 메일 본문을 만든다. 길면 덜 중요한 섹션부터 뺀다."""
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


def deterministic_rebuttal(package: VerifiedCasePackage, *, report_attached: bool = False) -> DocumentResult:
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
    document = DocumentResult(
        document_type="rebuttal_opinion", title="과실비율 반박의견서", sections=sections,
        mail_body=build_mail_body(package, report_attached=report_attached), generation_method="deterministic_fallback",
    )
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
    report_attached = report_sections is not None
    if client is None:
        return deterministic_rebuttal(package, report_attached=report_attached)
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
            # 메일 본문은 구조(주장 → 근거 → 결론)를 보장하기 위해 코드가 조립한다. LLM 은 섹션(검토 문서)만 쓴다.
            mail_body=build_mail_body(package, report_attached=report_attached),
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
        return deterministic_rebuttal(package, report_attached=report_attached)
    return ground_document(
        document,
        fact_blob=package.fact_blob(),
        allowed_case_ids=package.allowed_case_ids(),
        opponent_claim_provided=bool(package.opponent_claim),
    )
