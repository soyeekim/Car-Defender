"""영상 확정 사실 → 사건 현황판 칩 라벨 (LLM 이 줄이고, 코드가 근거·길이·중복을 검사한다).

슬롯 값만으로 만든 칩은 "교차로 / 신호등 있음 / 내 차 제동" 정도에 그친다. 영상 분석이 확정한 사실 문장("맞은편 정체 차로
사이에서 좌회전 진입")은 슬롯에 담기지 않으므로, 그 문장들을 짧은 라벨로 줄여 칩에 더한다. 영상 CONFIRMED 사실만 입력한다."""

from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field

from models.clients import TextClient
from prompts.loader import load_prompt
from state.case_state import CaseState, FactLabel
from telemetry import RunLogger, get_run_logger

MAX_LABELS = 10
MAX_LABEL_CHARS = 16
_JUDGEMENT_WORDS = ("과실", "책임", "위반", "무리", "부주의", "잘못", "추정", "보임", "가능성")


class _LabelItem(BaseModel):
    index: int
    label: str = ""


class _Labels(BaseModel):
    labels: list[_LabelItem] = Field(default_factory=list)


def _vehicle_names(state: CaseState) -> dict[str, str]:
    names: dict[str, str] = {}
    video = state.video_analysis
    if video is None:
        return names
    for vehicle in video.vehicles:
        if vehicle.is_ego or vehicle.id == state.ego_vehicle.vehicle_id:
            names[vehicle.id] = "내 차"
        elif vehicle.id == state.other_vehicle.vehicle_id:
            names[vehicle.id] = "상대 차"
        else:
            names[vehicle.id] = (vehicle.description or "다른 차량")[:12]
    return names


def humanize_fact(state: CaseState, text: str) -> str:
    """'블랙박스 차량(vehicle_1)' → '내 차', 'vehicle_2' → '상대 차'."""
    names = _vehicle_names(state)
    text = re.sub(r"\s*\((vehicle_\d+)\)", "", text)
    text = re.sub(r"(?<![A-Za-z_])(vehicle_\d+)(?![A-Za-z0-9_])", lambda m: names.get(m.group(1), "다른 차량"), text)
    text = text.replace("블랙박스 차량", "내 차").replace("자차", "내 차")
    return re.sub(r"\s{2,}", " ", text).strip()


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。])\s+")


def confirmed_fact_texts(state: CaseState, limit: int = 30) -> list[str]:
    """영상 CONFIRMED 사실 문장 + 영상 요약 문장(확정 사실 목록이 빈 부실 응답에 대비). 라벨 검사에서 추정 표현은 걸러진다."""
    seen: list[str] = []
    for fact in state.video_confirmed_facts():
        text = humanize_fact(state, fact.fact)
        if text and text not in seen:
            seen.append(text)
    video = state.video_analysis
    if video is not None and video.short_summary:
        for sentence in _SENTENCE_SPLIT.split(video.short_summary.strip()):
            text = humanize_fact(state, sentence.strip())
            if len(text) >= 8 and text not in seen:
                seen.append(text)
    return seen[:limit]


def _acceptable(label: str, existing: list[str], chosen: list[str]) -> bool:
    if not (2 <= len(label) <= MAX_LABEL_CHARS):
        return False
    if any(word in label for word in _JUDGEMENT_WORDS) or "vehicle_" in label:
        return False
    compact = label.replace(" ", "")
    for other in existing + chosen:
        other_compact = other.replace(" ", "")
        if compact == other_compact or (len(compact) >= 4 and (compact in other_compact or other_compact in compact)):
            return False
    return True


def generate_fact_labels(
    client: Optional[TextClient],
    state: CaseState,
    *,
    existing_labels: Optional[list[str]] = None,
    run_logger: Optional[RunLogger] = None,
    max_labels: int = MAX_LABELS,
) -> list[FactLabel]:
    facts = confirmed_fact_texts(state)
    existing = list(existing_labels or [])
    if client is None or not facts:
        return []
    logger = run_logger or get_run_logger()
    system = load_prompt("master_agent", "system")
    task = load_prompt("master_agent", "fact_labels")
    user = task.render(
        facts="\n".join(f"{index}. {text}" for index, text in enumerate(facts, start=1)),
        existing_labels=", ".join(existing) or "(없음)",
        max_chars=MAX_LABEL_CHARS - 2,
        max_labels=max_labels,
    )
    try:
        response = client.generate_json(system=system.text, user=user, schema=_Labels, task=task.task)
        parsed = _Labels.model_validate(response.data)
    except Exception as exc:  # noqa: BLE001
        logger.log(agent="master_agent", task=task.task, case_id=state.case_id, extra={"error": str(exc)[:300]})
        return []
    labels: list[FactLabel] = []
    chosen: list[str] = []
    dropped: list[str] = []
    for item in parsed.labels:
        label = item.label.strip().rstrip(".。")
        if not (1 <= item.index <= len(facts)) or not _acceptable(label, existing, chosen):
            dropped.append(f"{item.index}:{label}")
            continue
        labels.append(FactLabel(label=label, fact=facts[item.index - 1]))
        chosen.append(label)
        if len(labels) >= max_labels:
            break
    logger.log(agent="master_agent", task=task.task, case_id=state.case_id, model=response.metrics.model,
               prompt_version=f"{system.version_id}+{task.version_id}", metrics=response.metrics,
               extra={"labels": chosen, "dropped": dropped[:6], "facts": len(facts)})
    return labels
