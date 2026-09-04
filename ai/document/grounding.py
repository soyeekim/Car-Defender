"""문서 Grounding 검사 (가이드 2.1-G / 91 / 94 / 104~105절).

- 입력에 없는 속도 수치(km/h) 제거
- 상대방 의도·고의 추정 표현 완화 및 경고
- 확정적 법 위반·판결 표현 경고
- 제공되지 않은 심의번호 인용 제거
"""

from __future__ import annotations

import re
from typing import Iterable

from state.case_state import DocumentResult, GroundingReport

_SPEED = re.compile(r"(?:약\s*)?\d{2,3}\s*km/?h|시속\s*\d{2,3}\s*(?:km|킬로)?")
_CASE_ID = re.compile(r"(?<!\d)(\d{4}-\d{5,6})(?!\d)")
_INTENT_WORDS = ("고의로", "일부러", "의도적으로", "무리하게", "난폭하게", "고의적")
_CERTAINTY_WORDS = ("명백한 신호위반", "명백히 위반", "명백한 과실", "확정적으로", "판결", "무조건", "전적으로 상대")
_SENTENCE_SPLIT = re.compile(r"(?<=[.。!?])\s+|(?<=다\.)\s*")


def _sentences(text: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE_SPLIT.split(text) if part and part.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def ground_document(
    document: DocumentResult,
    *,
    fact_blob: str,
    allowed_case_ids: Iterable[str],
    opponent_claim_provided: bool = True,
) -> DocumentResult:
    allowed = set(allowed_case_ids)
    report = GroundingReport()
    blob_speeds = set(match.group(0).replace(" ", "") for match in _SPEED.finditer(fact_blob))
    cited: set[str] = set()

    for key, section in list(document.sections.items()):
        if not section:
            continue
        kept: list[str] = []
        for sentence in _sentences(section):
            modified = sentence
            speeds = [match.group(0) for match in _SPEED.finditer(sentence)]
            if speeds and any(speed.replace(" ", "") not in blob_speeds for speed in speeds):
                report.removed_sentences.append(sentence)
                continue
            for word in _INTENT_WORDS:
                if word in modified:
                    report.flagged_claims.append(f"[{key}] 의도 추정 표현 '{word}' 완화: {sentence[:80]}")
                    modified = modified.replace(word + " ", "").replace(word, "")
            for word in _CERTAINTY_WORDS:
                if word in modified:
                    report.flagged_claims.append(f"[{key}] 확정적 표현 '{word}' 확인 필요: {sentence[:80]}")
            for case_id in _CASE_ID.findall(modified):
                if case_id in allowed:
                    cited.add(case_id)
                else:
                    report.invalid_case_citations.append(case_id)
                    modified = modified.replace(case_id, "(심의번호 미확인)")
            kept.append(modified.strip())
        document.sections[key] = " ".join(kept).strip()

    if not opponent_claim_provided and "opponent_claim" in document.sections:
        document.sections["opponent_claim"] = "상대방 주장은 현재 제공되지 않았습니다. 추후 확인 시 보완이 필요합니다."

    document.cited_case_ids = sorted(cited | {case_id for case_id in document.cited_case_ids if case_id in allowed})
    document.grounding = report
    if report.removed_sentences:
        document.warnings.append(f"근거 없는 속도 수치가 포함된 문장 {len(report.removed_sentences)}개 제거")
    if report.invalid_case_citations:
        document.warnings.append("제공되지 않은 심의번호 인용 제거: " + ", ".join(sorted(set(report.invalid_case_citations))))
    if report.flagged_claims:
        document.warnings.append(f"검토 필요 표현 {len(report.flagged_claims)}건")
    document.text = render_text(document)
    return document


SECTION_TITLES = {
    "date_time": "1. 사고 일시",
    "location": "2. 사고 장소",
    "vehicles": "3. 차량 및 영상 기준",
    "pre_collision": "4. 사고 직전 진행 상황",
    "collision_process": "5. 사고 발생 과정",
    "collision": "6. 충돌 내용",
    "post_collision": "7. 사고 직후 상황",
    "objective_evidence": "8. 영상에서 확인되는 주요 사실",
    "notes": "9. 참고사항",
    "overview": "1. 사건 개요",
    "opponent_claim": "2. 상대방 주장",
    "objective_facts": "3. 영상에서 확인되는 객관적 사실",
    "key_issues": "4. 과실비율 판단의 핵심 쟁점",
    "similar_cases": "5. 유사 심의사례",
    "commonalities": "6. 본 사고와 심의사례의 공통점",
    "differences": "7. 본 사고와 심의사례의 차이점",
    "basic_ratio_review": "8. 기본 과실비율 검토",
    "adjustment_factor_review": "9. 수정요소 검토",
    "final_opinion": "10. 최종 의견",
}


def render_text(document: DocumentResult) -> str:
    lines = [document.title.strip() or ("사건경위서" if document.document_type == "incident_report" else "반박의견서"), ""]
    for key, body in document.sections.items():
        title = SECTION_TITLES.get(key, key)
        lines.append(title)
        lines.append(body.strip() if body else "(해당 없음 / 확인되지 않음)")
        lines.append("")
    if document.document_type == "rebuttal_opinion":
        lines.append("※ 본 문서의 과실비율은 영상·확인된 사실·유사 심의사례를 기준으로 한 예상치이며 확정 판단이 아닙니다.")
    return "\n".join(lines).strip()
