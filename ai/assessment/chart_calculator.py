"""인정기준 도표 계산기 — 심의사례가 없을 때의 예비 경로.

역할 분담
- agent(LLM): 어느 도표·변형이 맞는지, 사건의 나·상대가 도표의 A·B 중 누구인지, 확인된 사실로 적용되는 수정요소 행이 무엇인지 고른다.
- 코드: 고른 행의 ±값을 기본비율에 더하고 빼서 최종 비율을 내고(0~100 고정), A·B 를 나·상대로 옮기고, 계산식을 남긴다.

LLM 이 숫자를 직접 적지 않으므로 "10 감산했다"고 써 놓고 숫자에 반영되지 않는 일이 없다.
"""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field

from common.jsonutil import compact_json
from models.clients import TextClient
from prompts.loader import load_prompt
from state.case_state import AdjustmentFactor, CaseState, ChartModifier, FaultAssessment, FaultRatio, MatchedCaseSummary, RetrievedCase
from telemetry import RunLogger, get_run_logger

STANDARD_SOURCE_TYPES = {"fault_standard", "roundabout_special_standard"}
MAX_CHART_CONFIDENCE = 0.8
_RATIO = re.compile(r"(\d{1,3})\s*[:대]\s*(\d{1,3})")
_CONFIRMED_SOURCES = {"video", "user"}


class ChartModifierChoice(BaseModel):
    id: str
    source: str = "video"
    evidence: str = ""


class ChartRejectedModifier(BaseModel):
    id: str
    reason: str = ""


class ChartSelection(BaseModel):
    """LLM 의 판단 결과 (숫자 없음)."""

    chart_id: str = ""
    variant: Optional[str] = None
    user_is: Optional[Literal["A", "B"]] = None
    orientation_reason: str = ""
    applied_modifiers: list[ChartModifierChoice] = Field(default_factory=list)
    rejected_modifiers: list[ChartRejectedModifier] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    uncertainties: list[str] = Field(default_factory=list)
    ratio_dependencies: list[str] = Field(default_factory=list)
    explanation_facts: list[str] = Field(default_factory=list)


class ChartCalculation(BaseModel):
    chart_id: str
    variant: Optional[str] = None
    user_is: Literal["A", "B"]
    basic_a: int
    basic_b: int
    applied: list[ChartModifier] = Field(default_factory=list)
    final_a: int
    final_b: int
    user: int
    opponent: int
    formula: str


def is_chart(case: RetrievedCase) -> bool:
    return case.source_type in STANDARD_SOURCE_TYPES and bool(case.chart_variants or case.basic_ratio)


def _parse_ratio(text: Optional[str]) -> Optional[tuple[int, int]]:
    match = _RATIO.search(str(text or ""))
    return (int(match.group(1)), int(match.group(2))) if match else None


def modifier_ids(case: RetrievedCase) -> list[tuple[str, ChartModifier]]:
    return [(f"m{index}", item) for index, item in enumerate(case.chart_modifiers, start=1)]


def chart_for_prompt(case: RetrievedCase) -> dict:
    variants = [
        {"label": item.label, "description": item.description, "basic_ratio(A:B)": item.basic_ratio} for item in case.chart_variants
    ] or ([{"label": "", "basic_ratio(A:B)": case.basic_ratio}] if case.basic_ratio else [])
    return {
        "chart_id": case.case_id,
        "source_type": case.source_type,
        "title": case.title,
        "role_a": case.role_a,
        "role_b": case.role_b,
        "situation": (case.accident_description or case.excerpt)[:700],
        "explanations": [item[:300] for item in case.decision_reasons[:2]],
        "variants": variants,
        "modifiers": [{"id": key, "side": item.side, "label": item.label, "delta": item.delta} for key, item in modifier_ids(case)],
        "validation": case.relevance.model_dump(include={"relevance", "matched_factors", "different_factors", "usable_as_primary_reference", "note"}) if case.relevance else None,
    }


def basic_ratio_of(case: RetrievedCase, variant_label: Optional[str]) -> tuple[Optional[tuple[int, int]], Optional[str]]:
    """(A:B 기본비율, 실제 쓴 변형 라벨). 변형 라벨이 없거나 못 찾으면 첫 변형(또는 기본비율)."""
    variants = {item.label: item for item in case.chart_variants}
    if variant_label and variant_label in variants:
        return _parse_ratio(variants[variant_label].basic_ratio), variant_label
    if case.chart_variants:
        first = case.chart_variants[0]
        return _parse_ratio(first.basic_ratio), (first.label or None)
    return _parse_ratio(case.basic_ratio), None


def compute_chart_ratio(
    case: RetrievedCase,
    *,
    user_is: Literal["A", "B"],
    variant_label: Optional[str] = None,
    applied: Optional[list[ChartModifier]] = None,
) -> Optional[ChartCalculation]:
    """기본비율 ± 적용 수정요소 → 최종 A:B → 나:상대. 도표에 비율 정보가 없으면 None."""
    basic, used_variant = basic_ratio_of(case, variant_label)
    if basic is None:
        return None
    basic_a, basic_b = basic
    a = basic_a
    steps = [f"기본{(' ' + used_variant) if used_variant else ''} A {basic_a} : B {basic_b}"]
    for item in applied or []:
        a += item.delta if item.side == "A" else -item.delta
        steps.append(item.text())
    a = max(0, min(100, a))
    b = 100 - a
    user, opponent = (a, b) if user_is == "A" else (b, a)
    other = "B" if user_is == "A" else "A"
    steps.append(f"A {a} : B {b}")
    steps.append(f"나({user_is}) {user} : 상대({other}) {opponent}")
    return ChartCalculation(
        chart_id=case.case_id,
        variant=used_variant,
        user_is=user_is,
        basic_a=basic_a,
        basic_b=basic_b,
        applied=list(applied or []),
        final_a=a,
        final_b=b,
        user=user,
        opponent=opponent,
        formula=" → ".join(steps),
    )


def select_chart(
    client: TextClient,
    state: CaseState,
    charts: list[RetrievedCase],
    *,
    run_logger: Optional[RunLogger] = None,
) -> Optional[ChartSelection]:
    logger = run_logger or get_run_logger()
    system = load_prompt("master_agent", "system")
    task = load_prompt("master_agent", "chart_assessment")
    user = task.render(
        case_state=compact_json(state.compact(), max_chars=12000),
        charts=compact_json([chart_for_prompt(item) for item in charts], max_chars=24000),
    )
    try:
        response = client.generate_json(system=system.text, user=user, schema=ChartSelection, task=task.task)
        selection = ChartSelection.model_validate(response.data)
        logger.log(
            agent="master_agent",
            task=task.task,
            case_id=state.case_id,
            model=response.metrics.model,
            prompt_version=f"{system.version_id}+{task.version_id}",
            metrics=response.metrics,
            extra={"chart_id": selection.chart_id, "user_is": selection.user_is, "variant": selection.variant, "applied": [item.id for item in selection.applied_modifiers]},
        )
        return selection
    except Exception as exc:  # noqa: BLE001
        logger.log(agent="master_agent", task=task.task, case_id=state.case_id, extra={"error": str(exc)[:300]})
        return None


def _direction(item: ChartModifier, user_is: str) -> Literal["user_up", "user_down"]:
    raises_user = (item.side == user_is) == (item.delta > 0)
    return "user_up" if raises_user else "user_down"


def build_chart_assessment(
    state: CaseState,
    charts: list[RetrievedCase],
    selection: ChartSelection,
    *,
    model: str = "",
    prompt_version: str = "",
) -> Optional[FaultAssessment]:
    """LLM 판단(어느 도표·역할·행) + 코드 계산 → 판정. 도표를 사건에 대응시키지 못했으면 None."""
    by_id = {item.case_id: item for item in charts}
    chart = by_id.get(selection.chart_id) or next((item for item in charts if is_chart(item)), None)
    if chart is None or selection.user_is is None:
        return None
    ids = dict(modifier_ids(chart))
    applied: list[ChartModifier] = []
    factors: list[AdjustmentFactor] = []
    seen: set[str] = set()
    for choice in selection.applied_modifiers:
        item = ids.get(choice.id)
        if item is None or choice.id in seen:
            continue
        seen.add(choice.id)
        if choice.source not in _CONFIRMED_SOURCES:
            factors.append(AdjustmentFactor(factor=item.text(), direction="unknown", percentage=abs(item.delta), source="rag", applies=False, note="확인된 사실(영상·진술)이 아니어서 적용하지 않음"))
            continue
        applied.append(item)
        factors.append(
            AdjustmentFactor(
                factor=item.text(),
                direction=_direction(item, selection.user_is),
                percentage=abs(item.delta),
                source=choice.source,  # type: ignore[arg-type]
                applies=True,
                note=choice.evidence,
            )
        )
    for rejected in selection.rejected_modifiers:
        item = ids.get(rejected.id)
        if item is None or rejected.id in seen:
            continue
        seen.add(rejected.id)
        factors.append(AdjustmentFactor(factor=item.text(), direction="unknown", percentage=abs(item.delta), source="rag", applies=False, note=rejected.reason or "확인 불가"))

    calc = compute_chart_ratio(chart, user_is=selection.user_is, variant_label=selection.variant, applied=applied)
    if calc is None:
        return None
    user_role = chart.role_a if selection.user_is == "A" else chart.role_b
    other_side = "B" if selection.user_is == "A" else "A"
    other_role = chart.role_b if selection.user_is == "A" else chart.role_a
    basic_user, basic_opponent = (calc.basic_a, calc.basic_b) if selection.user_is == "A" else (calc.basic_b, calc.basic_a)
    variant_text = ""
    if calc.variant:
        description = next((item.description for item in chart.chart_variants if item.label == calc.variant), "")
        variant_text = f"{calc.variant} {description}".strip()

    reasoning = [
        f"꼭 맞는 심의사례가 없어서 과실비율 인정기준 도표 {chart.case_id}({chart.title})로 계산했어요.",
        f"이 도표에서 내 차량은 {selection.user_is}({user_role or '역할 미상'}), 상대 차량은 {other_side}({other_role or '역할 미상'})예요. {selection.orientation_reason}".strip(),
    ]
    if variant_text:
        reasoning.append(f"적용한 변형: {variant_text}")
    reasoning.append(f"계산: {calc.formula}")
    reasoning.extend(item for item in selection.explanation_facts if item)

    applied_text = ", ".join(f"{item.label} {item.delta:+d}" for item in applied)
    explanation = (
        f"꼭 맞는 심의사례가 없어서 과실비율 인정기준 도표 {chart.case_id}({chart.title})를 기준으로 계산했어요. "
        f"이 도표에서 내 차량은 '{user_role or selection.user_is}', 상대 차량은 '{other_role or other_side}'에 해당해요. "
        + (
            f"기본 과실비율 나 {basic_user} : 상대 {basic_opponent}에 {applied_text}을(를) 적용해 "
            if applied
            else f"기본 과실비율 나 {basic_user} : 상대 {basic_opponent}에서 확인된 수정요소가 없어 그대로 "
        )
        + f"나 {calc.user} : 상대 {calc.opponent} 정도로 예상돼요."
    )
    if selection.explanation_facts:
        explanation += " " + " ".join(selection.explanation_facts[:2])
    if selection.ratio_dependencies:
        explanation += " " + selection.ratio_dependencies[0].rstrip(".") + " 등이 확인되면 달라질 수 있어요."

    more_at_fault = "equal" if calc.user == calc.opponent else ("user" if calc.user > calc.opponent else "opponent")
    return FaultAssessment(
        fault_ratio=FaultRatio(user=calc.user, opponent=calc.opponent),
        more_at_fault=more_at_fault,
        assessment_type="estimated",
        confidence=min(MAX_CHART_CONFIDENCE, selection.confidence),
        anchor_case_id=chart.case_id,
        anchor_ratio=f"{basic_user}:{basic_opponent}",
        anchor_enforced=False,
        calculation=calc.formula,
        primary_case_ids=[chart.case_id],
        core_facts=[fact.fact for fact in state.video_confirmed_facts()[:8]],
        matched_cases=[
            MatchedCaseSummary(case_id=item.case_id, decision_ratio=item.decision_ratio, basic_ratio=item.basic_ratio, relevance=item.relevance.relevance if item.relevance else None, role="primary" if item.case_id == chart.case_id else "supporting")
            for item in charts[:3]
        ],
        adjustment_factors=factors,
        reasoning_summary=reasoning,
        uncertainties=list(selection.uncertainties),
        ratio_dependencies=list(selection.ratio_dependencies),
        explanation=explanation,
        model=model,
        prompt_version=prompt_version,
    )


def assess_with_chart(
    client: TextClient,
    state: CaseState,
    cases: list[RetrievedCase],
    *,
    run_logger: Optional[RunLogger] = None,
) -> Optional[FaultAssessment]:
    """도표 경로 판정. LLM 이 실패했거나 도표를 사건에 대응시키지 못하면 None (호출자가 일반 경로로 넘어간다)."""
    charts = [item for item in cases if is_chart(item)][:3]
    if not charts:
        return None
    selection = select_chart(client, state, charts, run_logger=run_logger)
    if selection is None:
        return None
    task = load_prompt("master_agent", "chart_assessment")
    return build_chart_assessment(state, charts, selection, prompt_version=task.version_id)
