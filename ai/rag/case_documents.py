"""심의사례 PDF → Case-level Document (가이드 77~79, 108~109절).

기존 `rag/pdf_index.py`가 만든 Parent(심의번호 기준으로 병합된 페이지 묶음)를
사건 단위 JSON(`CaseDocument`)으로 구조화한다.

우선순위: 심의번호 → 동일 사건 페이지 병합(Parent가 이미 수행) → 사고내용/결정비율 추출
→ 쟁점/결정이유/수정요소 → 검색 metadata → 사건 단위 JSON.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Optional

from pydantic import BaseModel, Field

from rag.pdf_index import DEFAULT_INDEX_DIR, load_parent_documents
from state.case_state import CaseMetadata, ChartModifier, ChartVariant, RetrievedCase

_NOISE_LINES = (
    "목차보기",
    "자동차사고 과실비율분쟁 심의 사례",
    "1. 자동차와 자동차의 사고",
    "2. 자동차와 이륜차의 사고",
    "3. 고속도로의 사고",
    "4. 자동차와 보행자의 사고",
    "5. 자동차와 자전거의 사고",
)
_PAGE_MARKER = re.compile(r"^\[PDF \d+쪽\]$")
_SECTION_MARKERS = [
    ("overview", re.compile(r"^사례\s*개요")),
    ("accident", re.compile(r"^사고\s*내용")),
    ("reference", re.compile(r"^참고\s*(?:인정)?\s*기준")),
    ("basic_ratio", re.compile(r"^기본\s*비율")),
    ("arguments", re.compile(r"^주장\s*내용")),
    ("evidence", re.compile(r"^입증\s*자료")),
    ("issues", re.compile(r"^주요\s*쟁점")),
    ("basis", re.compile(r"^결정\s*근거")),
    ("reasons", re.compile(r"^결정\s*이유")),
    ("modification", re.compile(r"^수정\s*요소")),
    ("related", re.compile(r"^관련\s*(?:심의)?\s*사례")),
]
_TITLE_PATTERN = re.compile(
    r"^(차대차|차대이륜차|차대보행자|차대자전거|고속도로|이륜차|자전거|보행자)[^\n]{0,80}?사고[^\n]*$"
)
_DECISION = re.compile(r"결정비율[^\n]{0,60}?=\s*(\d{1,3})\s*[:：]\s*(\d{1,3})")
_BASIC = re.compile(r"기본\s*비율[^\n]{0,40}?=\s*(\d{1,3})\s*[:：]\s*(\d{1,3})")
_BASIC_STANDARD = re.compile(r"기본\s*과실비율[^\n]{0,80}?(?:A|레드)\s*(\d{1,3})\s*[:：]?\s*(?:B|블루)\s*(\d{1,3})")
_BULLET = re.compile(r"^[•●▪◦\-·]\s*")
_MODIFIER_LINE = re.compile(r"([+\-±]\s?\d{1,2}0?\s*%?|가산|감산|가감)")
# 좌표 기반 도표 추출(rag/chart_layout.py)이 내는 줄 형식
_CHART_HEAD = re.compile(r"^((?:보|거|차)\d+(?:-\d+)?|회전-\d+)\s+(.*)$")
_ROLE_LINE = re.compile(r"^(A|B):\s*(.*)$")
_BASIC_LINE = re.compile(r"^기본 과실비율(?: (\([가-힣]\)))? A (\d{1,3}) : B (\d{1,3})$")
_MODIFIER_ROW = re.compile(r"^- (A|B) (.+?) ([+\-]\d{1,3})$")
_LEGACY_LINE = re.compile(r"^舊 기준 (.+)$")
_VARIANT_DESC = re.compile(r"\(([가-힣])\)\s*([^()]*?)(?=\s*\([가-힣]\)|$)")
_CHART_SECTION = re.compile(
    r"^(사고\s*상황|기본\s*과실비율\s*해설|기본\s*과실비율|수정요소.*해설|수정요소\(.*|활용시\s*참고\s*사항|관련\s*법규|참고\s*판례|참고\s*사항)$"
)


class CaseDocument(BaseModel):
    case_id: str
    parent_id: str
    unit_type: str = "case"
    source_type: str = "deliberation_case"
    source_file: str = ""
    page_start: int = 1
    page_end: int = 1
    title: str = ""
    accident_type: Optional[str] = None
    chart_number: Optional[str] = None
    decision_ratio: Optional[str] = None
    basic_ratio: Optional[str] = None
    accident_description: str = ""
    reference_standard: str = ""
    arguments_text: str = ""
    claimant_argument: str = ""
    respondent_argument: str = ""
    evidence: list[str] = Field(default_factory=list)
    key_issues: list[str] = Field(default_factory=list)
    decision_basis: list[str] = Field(default_factory=list)
    decision_reasons: list[str] = Field(default_factory=list)
    modification_factors: list[str] = Field(default_factory=list)
    related_cases: list[str] = Field(default_factory=list)
    metadata: CaseMetadata = Field(default_factory=CaseMetadata)
    combined_text: str = ""
    # 인정기준 도표만: 분류(장·절 제목), A·B 역할, 변형별 기본비율, 구조화된 수정요소, 옛 도표 번호, 해설
    category: str = ""
    role_a: str = ""
    role_b: str = ""
    chart_variants: list[ChartVariant] = Field(default_factory=list)
    chart_modifiers: list[ChartModifier] = Field(default_factory=list)
    legacy_chart_numbers: list[str] = Field(default_factory=list)
    basic_ratio_explanation: str = ""
    modifier_explanation: str = ""

    def to_retrieved_case(self, *, similarity: float = 0.0, semantic: Optional[float] = None, lexical: Optional[float] = None, excerpt: str = "") -> RetrievedCase:
        return RetrievedCase(
            case_id=self.case_id,
            source_type=self.source_type,  # type: ignore[arg-type]
            title=self.title,
            accident_type=self.accident_type,
            chart_number=self.chart_number,
            decision_ratio=self.decision_ratio,
            basic_ratio=self.basic_ratio,
            accident_description=self.accident_description,
            claimant_argument=self.claimant_argument or self.arguments_text,
            respondent_argument=self.respondent_argument,
            key_issues=self.key_issues,
            decision_reasons=self.decision_reasons or self.decision_basis,
            recognized_facts=self.decision_basis,
            modification_factors=self.modification_factors,
            role_a=self.role_a,
            role_b=self.role_b,
            chart_variants=self.chart_variants,
            chart_modifiers=self.chart_modifiers,
            legacy_chart_numbers=self.legacy_chart_numbers,
            source_file=self.source_file,
            source_pages=list(range(self.page_start, self.page_end + 1)),
            similarity=similarity,
            semantic_score=semantic,
            lexical_score=lexical,
            excerpt=excerpt or self.combined_text[:1500],
            metadata=self.metadata,
        )


# --------------------------------------------------------------------------- text utils


def _clean_lines(full_text: str) -> list[str]:
    lines = []
    for raw in full_text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line or _PAGE_MARKER.match(line):
            continue
        if any(noise in line for noise in _NOISE_LINES):
            continue
        if re.fullmatch(r"\d{1,4}", line):
            # 페이지 번호·도표 번호 단독 라인
            continue
        lines.append(line)
    return lines


def _split_sections(lines: list[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"header": []}
    current = "header"
    for line in lines:
        matched = None
        for name, pattern in _SECTION_MARKERS:
            if pattern.match(line):
                matched = name
                break
        if matched in {"accident", "reference", "basic_ratio"} and current == "header":
            # '사례 개요' 표 안의 라벨들. 표가 시작되기 전(상단 제목·참고기준 상자)에 나온 같은 글자는 라벨이 아니다
            matched = None
        if matched:
            current = matched
            sections.setdefault(current, [])
            remainder = re.sub(r"^[가-힣\s]+?(?:개요|내용|기준|비율|자료|쟁점|근거|이유|요소|사례)\s*", "", line, count=1).strip()
            if remainder and remainder != line:
                sections[current].append(remainder)
            continue
        sections.setdefault(current, []).append(line)
    return sections


def _bullets(lines: list[str]) -> list[str]:
    items: list[str] = []
    for line in lines:
        if _BULLET.match(line):
            items.append(_BULLET.sub("", line).strip())
        elif items and not re.match(r"^\d+\.", line):
            items[-1] = f"{items[-1]} {line}".strip()
        elif line:
            items.append(line)
    return [item for item in items if len(item) > 3]


def _join(lines: list[str]) -> str:
    return " ".join(_BULLET.sub("", line).strip() for line in lines).strip()


def _strip_layout_noise(text: str, chart_number: Optional[str]) -> str:
    """2단 레이아웃에서 본문 사이에 끼어든 '참고 / 인정기준 / 도표번호' 조각을 제거한다."""
    cleaned = text.replace(" 참고 ", "").replace(" 인정기준 ", "")
    cleaned = re.sub(r"\s*\(기본과실\)\s*", " ", cleaned)
    if chart_number:
        cleaned = re.sub(rf"(?<!\d)\s{re.escape(chart_number)}(?!\d)", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _title_from(lines: list[str]) -> str:
    for line in lines[:12]:
        if _TITLE_PATTERN.match(line):
            return line
    for line in lines[:12]:
        if "사고" in line and len(line) < 90:
            return line
    return ""


def _ratio(pattern: re.Pattern, text: str) -> Optional[str]:
    match = pattern.search(text)
    return f"{int(match.group(1))}:{int(match.group(2))}" if match else None


def classify_metadata(title: str, description: str) -> CaseMetadata:
    blob = f"{title} {description}"
    metadata = CaseMetadata()
    for label in ("차대차", "차대이륜차", "차대보행자", "차대자전거"):
        if label in blob:
            metadata.accident_target = label
            break
    else:
        if "보행자" in blob:
            metadata.accident_target = "차대보행자"
        elif "이륜차" in blob or "오토바이" in blob:
            metadata.accident_target = "차대이륜차"
        elif "자전거" in blob:
            metadata.accident_target = "차대자전거"
    if "회전교차로" in blob:
        metadata.road_type, metadata.intersection_type = "roundabout", "roundabout"
    elif "사거리" in blob:
        metadata.road_type, metadata.intersection_type = "intersection", "four_way"
    elif "삼거리" in blob or "T자" in blob or "T형" in blob:
        metadata.road_type, metadata.intersection_type = "intersection", "three_way"
    elif "교차로" in blob:
        metadata.road_type = "intersection"
    elif "주차장" in blob:
        metadata.road_type = "parking_lot"
    elif "고속도로" in blob or "자동차전용도로" in blob:
        metadata.road_type = "highway"
    elif "횡단보도" in blob:
        metadata.road_type = "crosswalk"
    elif any(word in blob for word in ("직선", "동일방향", "동일 방향", "추돌", "진로변경", "차로변경")):
        metadata.road_type = "straight"
    if any(word in blob for word in ("신호기 없는", "신호등 없", "신호기가 없", "신호 없는", "교통정리가 이루어지고 있지 않", "교통정리가 이루어지지 않")):
        metadata.signal_present = False
    elif any(word in blob for word in ("신호기 있는", "신호등 있", "신호기가 있", "신호 있는", "교통정리가 이루어지고 있는", "신호기에 의해 교통정리")):
        metadata.signal_present = True
    movement_match = re.search(r"(직진|좌회전|우회전|유턴|후진|차로변경|진로변경|추월|정차|출발)\s*대\s*(직진|좌회전|우회전|유턴|후진|차로변경|진로변경|추월|정차|출발)", blob)
    if movement_match:
        metadata.movement_a, metadata.movement_b = movement_match.group(1), movement_match.group(2)
    if any(word in blob for word in ("차로변경", "진로변경", "차선변경")):
        metadata.lane_change = True
    keywords = []
    for word in ("선진입", "동시진입", "황색", "적색", "녹색", "정지선", "횡단보도", "중앙선", "급차로변경", "방향지시등", "서행", "과속", "야간", "음주", "무면허", "후진", "개문", "이면도로", "대로", "소로", "긴급자동차"):
        if word in blob:
            keywords.append(word)
    metadata.keywords = keywords
    return metadata


# --------------------------------------------------------------------------- parsers


def parse_case_parent(parent: dict) -> CaseDocument:
    lines = _clean_lines(parent.get("full_text", ""))
    sections = _split_sections(lines)
    header = sections.get("header", [])
    title = _title_from(header) or _title_from(lines)
    full_text = "\n".join(lines)

    # 2단 레이아웃 때문에 '사고내용' 마커가 첫 불릿 뒤에 오므로 사례 개요의 불릿을 앞에 붙인다.
    overview_bullets = [line for line in sections.get("overview", []) if _BULLET.match(line)]
    accident_description = _strip_layout_noise(
        _join(overview_bullets + sections.get("accident", [])), parent.get("chart_number")
    )
    reference = _strip_layout_noise(_join(sections.get("reference", [])), parent.get("chart_number"))
    argument_lines = sections.get("arguments", [])
    arguments = _join(argument_lines)
    # 좌표 기반 추출(rag/pdf_layout.py)은 두 단을 "[청구인] …" / "[피청구인] …" 줄로 나눠 준다
    claimant = _join([line[len("[청구인]"):].strip() for line in argument_lines if line.startswith("[청구인]")])
    respondent = _join([line[len("[피청구인]"):].strip() for line in argument_lines if line.startswith("[피청구인]")])
    modification = _bullets(sections.get("modification", []))
    if not modification:
        modification = [line for line in lines if "수정요소" in line or _MODIFIER_LINE.search(line) and ("가산" in line or "감산" in line)]
    decision_ratio = parent.get("decision_ratio") or _ratio(_DECISION, full_text)
    basic_ratio = _ratio(_BASIC, full_text)
    metadata = classify_metadata(title, accident_description)
    key_issues = _bullets(sections.get("issues", []))
    decision_basis = _bullets(sections.get("basis", []))
    decision_reasons = _bullets(sections.get("reasons", []))
    combined = "\n".join(
        part
        for part in (
            title,
            f"사고내용: {accident_description}" if accident_description else "",
            f"참고 인정기준: {reference}" if reference else "",
            f"주요 쟁점: {' / '.join(key_issues)}" if key_issues else "",
            f"결정 이유: {' / '.join(decision_reasons)}" if decision_reasons else "",
            f"기본비율 {basic_ratio}" if basic_ratio else "",
            f"결정비율 {decision_ratio}" if decision_ratio else "",
        )
        if part
    )[:3000]
    return CaseDocument(
        case_id=parent.get("case_number") or parent["unit_key"].split(":", 1)[-1],
        parent_id=parent["parent_id"],
        unit_type=parent.get("unit_type") or "case",
        source_type=parent.get("source_type", "deliberation_case"),
        source_file=parent.get("source_file", ""),
        page_start=int(parent.get("page_start", 1)),
        page_end=int(parent.get("page_end", 1)),
        title=title,
        accident_type=title.split(" - ")[0].strip() if title else None,
        chart_number=parent.get("chart_number"),
        decision_ratio=decision_ratio,
        basic_ratio=basic_ratio,
        accident_description=accident_description,
        reference_standard=reference,
        arguments_text=arguments,
        claimant_argument=claimant,
        respondent_argument=respondent,
        evidence=_bullets(sections.get("evidence", [])),
        key_issues=key_issues,
        decision_basis=decision_basis,
        decision_reasons=decision_reasons,
        modification_factors=modification[:12],
        related_cases=re.findall(r"\d{4}-\d{5,6}", _join(sections.get("related", []))),
        metadata=metadata,
        combined_text=combined,
    )


def _variant_descriptions(role_a: str, role_b: str) -> dict[str, str]:
    """'우측도로에서 직진 (가)동시 (나)선진입' 꼴의 역할 글에서 변형별 설명을 뽑는다 → {'(가)': 'A 동시 / B 동시'}."""
    out: dict[str, list[str]] = {}
    for side, text in (("A", role_a), ("B", role_b)):
        for label, desc in _VARIANT_DESC.findall(text):
            desc = desc.strip(" ,;")
            if desc:
                out.setdefault(f"({label})", []).append(f"{side} {desc}")
    return {label: " / ".join(parts) for label, parts in out.items()}


def _strip_variants(text: str) -> str:
    return re.sub(r"\s*\([가-힣]\)\s*[^()]*", "", text).strip(" ,") or text.strip()


def _chart_sections(lines: list[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: Optional[str] = None
    for line in lines:
        if _CHART_SECTION.match(line) and len(line) <= 42:
            name = re.sub(r"\s+", "", line)
            if name.startswith("사고상황"):
                current = "situation"
            elif name.startswith("기본과실비율"):
                current = "basic"
            elif name.startswith("수정요소"):
                current = "modifier"
            elif name.startswith("활용시"):
                current = "usage"
            else:
                current = "other"
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return sections


def parse_standard_parent(parent: dict) -> CaseDocument:
    lines = _clean_lines(parent.get("full_text", ""))
    full_text = "\n".join(lines)
    unit_id = parent["unit_key"].split(":", 1)[-1]

    # 좌표 기반 추출(rag/chart_layout.py)의 구조화된 줄: 배지+제목 → A:/B: 역할 → 기본 과실비율 → 수정요소 표 → 舊 기준
    head_index = next((i for i, line in enumerate(lines) if (m := _CHART_HEAD.match(line)) and m.group(1) == unit_id), None)
    title = ""
    category = ""
    role_a = role_b = ""
    variants: list[ChartVariant] = []
    modifiers: list[ChartModifier] = []
    legacy: list[str] = []
    body_lines: list[str] = lines
    if head_index is not None:
        # 한 상자에 도표 여러 개가 겹쳐 있으면(거43-1~3) 제목에 다음 배지가 이어 붙는다 → 첫 도표 제목만
        title = re.split(r"\s(?:보|거|차)\d+(?:-\d+)?\s", _CHART_HEAD.match(lines[head_index]).group(2).strip())[0].strip()
        headings = [line for line in lines[:head_index] if len(line) <= 60 and not _BULLET.match(line)]
        category = " > ".join(headings[-3:])
        rest = lines[head_index + 1:]
        consumed = 0
        in_table = False
        for line in rest:
            if (m := _ROLE_LINE.match(line)):
                if m.group(1) == "A":
                    role_a = m.group(2).strip()
                else:
                    role_b = m.group(2).strip()
            elif (m := _BASIC_LINE.match(line)):
                variants.append(ChartVariant(label=m.group(1) or "", basic_ratio=f"{int(m.group(2))}:{int(m.group(3))}"))
            elif line == "수정요소 표":
                in_table = True
            elif in_table and (m := _MODIFIER_ROW.match(line)):
                modifiers.append(ChartModifier(side=m.group(1), label=m.group(2).strip(), delta=int(m.group(3))))
            elif (m := _LEGACY_LINE.match(line)):
                legacy = [item.strip() for item in m.group(1).split(",") if item.strip()]
            else:
                break
            consumed += 1
        body_lines = rest[consumed:]
        descriptions = _variant_descriptions(role_a, role_b)
        for variant in variants:
            variant.description = descriptions.get(variant.label, "")
    if not title:
        for line in lines[:15]:
            if ("사고" in line or "도표" in line) and 6 < len(line) < 100 and not line.startswith("기본"):
                title = line
                break

    sections = _chart_sections(body_lines)
    situation_items = [_BULLET.sub("", item).lstrip("⊙ ").strip() for item in sections.get("situation", [])]
    # 회전교차로 도표는 '적용 조건' 문단(회전교차로 구조 설명)이 먼저 오고 실제 상황은 "구체적인 사고 상황은 …" 에 있다
    # → 검색·검증 프롬프트가 앞부분만 보므로 실제 상황을 앞으로 옮긴다
    specific = [item for item in situation_items if item.startswith("구체적인 사고 상황")]
    if specific:
        situation_items = specific + [item for item in situation_items if item not in specific]
    situation = " ".join(situation_items)
    basic_explanation = " ".join(item.lstrip("⊙ ").strip() for item in sections.get("basic", []))
    modifier_explanation = " ".join(item.lstrip("⊙ ").strip() for item in sections.get("modifier", []) + sections.get("usage", []))
    basic = (variants[0].basic_ratio if variants else None) or _ratio(_BASIC_STANDARD, full_text) or parent.get("decision_ratio")
    modifier_texts = [item.text() for item in modifiers] or [line for line in lines if _MODIFIER_LINE.search(line) and len(line) < 120][:15]
    if situation:
        description = situation
    else:
        description = " ".join([line for line in lines[:40] if len(line) > 15][:6])
    prefix = re.match(r"[가-힣]+", unit_id)
    metadata = classify_metadata(f"{category} {title}", description)
    if prefix:
        metadata.accident_target = {"보": "차대보행자", "거": "차대자전거"}.get(prefix.group(0), "차대차")
    roles_text = " / ".join(part for part in ((f"A: {role_a}" if role_a else ""), (f"B: {role_b}" if role_b else "")) if part)
    combined = "\n".join(
        part
        for part in (
            f"도표 {unit_id} {title}".strip(),
            category,
            roles_text,
            f"사고 상황: {situation}" if situation else description,
            " / ".join(f"기본 과실비율{(' ' + v.label) if v.label else ''} {v.basic_ratio}" for v in variants) if variants else (f"기본과실비율 {basic}" if basic else ""),
            "수정요소: " + " / ".join(modifier_texts[:12]) if modifier_texts else "",
            f"舊 기준 {', '.join(legacy)}" if legacy else "",
        )
        if part
    )[:3000]
    return CaseDocument(
        case_id=unit_id,
        parent_id=parent["parent_id"],
        unit_type=parent.get("unit_type") or "standard_chart",
        source_type=parent.get("source_type", "fault_standard"),
        source_file=parent.get("source_file", ""),
        page_start=int(parent.get("page_start", 1)),
        page_end=int(parent.get("page_end", 1)),
        title=title or f"과실비율 인정기준 도표 {unit_id}",
        accident_type=(category.split(" > ")[-1].strip() if category else None) or title or None,
        chart_number=parent.get("chart_number") or unit_id,
        decision_ratio=None,
        basic_ratio=basic,
        accident_description=description,
        decision_reasons=[item for item in (basic_explanation, modifier_explanation) if item],
        modification_factors=modifier_texts[:15],
        metadata=metadata,
        combined_text=combined,
        category=category,
        role_a=role_a,
        role_b=role_b,
        chart_variants=variants,
        chart_modifiers=modifiers,
        legacy_chart_numbers=legacy,
        basic_ratio_explanation=basic_explanation,
        modifier_explanation=modifier_explanation,
    )


def parse_parent(parent: dict) -> Optional[CaseDocument]:
    unit_type = parent.get("unit_type")
    if unit_type == "case":
        return parse_case_parent(parent)
    if unit_type in {"standard_chart", "roundabout_chart"}:
        return parse_standard_parent(parent)
    return None


# --------------------------------------------------------------------------- build / load


def build_case_documents(
    index_dir: str | Path = DEFAULT_INDEX_DIR,
    *,
    output_path: Optional[str | Path] = None,
    parents: Optional[Iterable[dict]] = None,
) -> list[CaseDocument]:
    directory = Path(index_dir).expanduser().resolve()
    source = list(parents) if parents is not None else load_parent_documents(directory)
    documents = [doc for doc in (parse_parent(parent) for parent in source) if doc is not None]
    target = Path(output_path) if output_path else directory / "case_documents.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text("\n".join(json.dumps(doc.model_dump(), ensure_ascii=False) for doc in documents) + "\n", encoding="utf-8")
    temporary.replace(target)
    return documents


def load_case_documents(
    index_dir: str | Path = DEFAULT_INDEX_DIR,
    *,
    path: Optional[str | Path] = None,
    rebuild: bool = False,
) -> dict[str, CaseDocument]:
    """parent_id → CaseDocument. 파일이 없으면 parents.jsonl에서 생성한다."""
    directory = Path(index_dir).expanduser().resolve()
    target = Path(path) if path else directory / "case_documents.jsonl"
    if rebuild or not target.is_file():
        documents = build_case_documents(directory, output_path=target)
    else:
        documents = [
            CaseDocument.model_validate_json(line)
            for line in target.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    return {doc.parent_id: doc for doc in documents}
