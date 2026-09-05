"""테스트용 Fake Client / Analyzer / RAG Tool (네트워크 호출 없음)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from models.clients import JSONResponse, TextResponse
from rag.reranker import rerank_cases
from rag.tool import SimilarCaseRagTool
from state.case_state import CaseMetadata, RagQuery, RagResult, RetrievedCase
from telemetry import CallMetrics, RunLogger
from video.base import BaseVideoAnalyzer
from video.schemas import (
    CollisionDetail,
    CollisionPair,
    CollisionWindow,
    FaultRelevantFactor,
    Observation,
    PairScore,
    ParticipantPart,
    RoadEnvironment,
    SignalObservation,
    TimelineEvent,
    VehicleEntry,
    VideoObservation,
)


def quiet_logger(tmp_path: Path) -> RunLogger:
    return RunLogger(path=tmp_path / "runs.jsonl", enabled=False)


# --------------------------------------------------------------------------- video


def sample_observation(*, pair_confidence: float = 0.92, three_vehicles: bool = True, signal: bool = True, turn_signal_unknown: bool = False, lane_marking_unknown: bool = False) -> VideoObservation:
    vehicles = [
        VehicleEntry(
            id="vehicle_1",
            description="블랙박스 촬영 차량",
            is_ego=True,
            first_seen="00:00.0",
            movement="straight",
            lane="2차로",
            braking=Observation(value="true", status="CONFIRMED", confidence=0.8, timestamp="00:05.5"),
            entered_intersection_first=Observation(value="true", status="INFERRED", confidence=0.7),
            speed_qualitative=Observation(value="normal", status="INFERRED", confidence=0.6),
        ),
        VehicleEntry(
            id="vehicle_3",
            description="우측 진입로에서 등장한 노란색 택시",
            vehicle_type="taxi",
            color="yellow",
            first_seen="00:03.1",
            movement="straight",
            entry_direction="right_side_road",
            lane="1차로",
            turn_signal=Observation(value="false", status="INFERRED", confidence=0.55),
            entered_intersection_first=Observation(value="false", status="INFERRED", confidence=0.7),
        ),
    ]
    if three_vehicles:
        vehicles.insert(
            1,
            VehicleEntry(id="vehicle_2", description="전방 좌측 흰색 승용차", color="white", first_seen="00:00.8", movement="straight"),
        )
    pair_scores = [
        PairScore(pair=["vehicle_1", "vehicle_3"], collision_probability=pair_confidence, reason="접촉 순간 카메라 흔들림"),
    ]
    if three_vehicles:
        pair_scores += [
            PairScore(pair=["vehicle_1", "vehicle_2"], collision_probability=0.05, reason="거리 유지"),
            PairScore(pair=["vehicle_2", "vehicle_3"], collision_probability=0.1, reason="교차 없음"),
        ]
    return VideoObservation(
        short_summary="블랙박스 차량이 사거리 교차로를 직진하던 중 우측 도로에서 진입한 노란색 택시와 충돌했다.",
        detailed_description="00:00~00:03 자차(vehicle_1)가 2차로로 교차로에 접근. 00:03 우측 도로에서 vehicle_3 등장. 00:05.8 자차 전면과 vehicle_3 좌측면 충돌.",
        road_environment=RoadEnvironment(
            road_type=Observation(value="intersection", status="CONFIRMED", confidence=0.95),
            intersection_type=Observation(value="four_way", status="CONFIRMED", confidence=0.9),
            lane_count=Observation(value="3", status="CONFIRMED", confidence=0.8),
            stop_line=Observation(value="present", status="CONFIRMED", confidence=0.8),
            signal_present=Observation(value="true" if signal else "false", status="CONFIRMED", confidence=0.9),
            signal_observations=(
                [SignalObservation(time="00:04.0", applies_to="vehicle_1 진행방향", color="green", status="CONFIRMED", confidence=0.85)]
                if signal
                else []
            ),
        ),
        vehicles=vehicles,
        ego_vehicle_id="vehicle_1",
        vehicle_identity_consistent=True,
        collision_window=CollisionWindow(start="00:05.4", end="00:06.1", most_likely_timestamp="00:05.8", confidence=0.9, evidence=["카메라 충격"]),
        collision_pair=CollisionPair(
            participants=["vehicle_1", "vehicle_3"],
            non_participants=["vehicle_2"] if three_vehicles else [],
            timestamp="00:05.8",
            confidence=pair_confidence,
            reasoning=["vehicle_3가 자차 진행 경로로 진입 후 접촉"],
        ),
        pair_scores=pair_scores,
        collision=CollisionDetail(
            timestamp="00:05.8",
            collision_type="front_to_left_side",
            relative_direction="right",
            participant_parts=[ParticipantPart(vehicle_id="vehicle_1", part="front"), ParticipantPart(vehicle_id="vehicle_3", part="left_side")],
        ),
        timeline=[
            TimelineEvent(start_time="00:00.0", end_time="00:03.0", event="자차가 2차로를 따라 교차로 방향으로 진행", vehicles=["vehicle_1"], confidence=0.96),
            TimelineEvent(start_time="00:03.0", end_time="00:05.0", event="우측 진입로에서 vehicle_3 등장", vehicles=["vehicle_3"], confidence=0.94),
            TimelineEvent(start_time="00:05.8", end_time="00:05.8", event="자차 전면부와 vehicle_3 좌측면 충돌", vehicles=["vehicle_1", "vehicle_3"], confidence=0.88),
        ],
        confirmed_facts=["사거리 교차로에서 발생", "vehicle_3가 우측 도로에서 교차로로 진입", "자차 전면과 vehicle_3 좌측면 충돌"],
        inferred_facts=["자차가 교차로에 먼저 진입한 것으로 보임"],
        unknown_or_unobservable=["상대 차량 방향 신호등은 영상에서 직접 보이지 않음", "정확한 차량 속도는 영상만으로 측정할 수 없음"],
        fault_relevant_factors=[
            FaultRelevantFactor(factor="신호등 유무 및 자차 신호", observability="CONFIRMED", note="자차 방향 녹색", vehicle_ids=["vehicle_1"]),
            FaultRelevantFactor(factor="상대 차량 신호", observability="UNKNOWN", note="화면에 보이지 않음", vehicle_ids=["vehicle_3"]),
            FaultRelevantFactor(factor="교차로 선진입", observability="INFERRED", note="자차 선진입 추정"),
            FaultRelevantFactor(factor="정지선", observability="CONFIRMED", note="자차 정지선 통과 시 녹색"),
            FaultRelevantFactor(factor="진행 방향", observability="CONFIRMED", note="양 차량 직진"),
        ],
        uncertain_facts=["충돌 직전 양 차량의 정확한 속도는 확정 어려움"]
        + (["상대 차량(vehicle_3)의 방향지시등 점등 여부는 화각상 확인 불가", "양 차량의 교차로 진입 순서는 영상 이전 시점이라 확인할 수 없음"] if turn_signal_unknown else [])
        + (["상대 차량이 차로를 변경한 지점의 노면 차선이 실선인지 점선인지 화질상 불명확"] if lane_marking_unknown else []),
    )


class FakeVideoAnalyzer(BaseVideoAnalyzer):
    backend = "fake"

    def __init__(self, *, first_confidence: float = 0.92, focus_confidence: float = 0.93, run_logger: Optional[RunLogger] = None, fail: bool = False, **kwargs):
        super().__init__(run_logger=run_logger)
        self.model = "fake-video-model"
        self.first_confidence = first_confidence
        self.focus_confidence = focus_confidence
        self.fail = fail
        self.calls: list[dict[str, Any]] = []
        self.observation_kwargs = kwargs

    def analyze(self, video_path, *, focus=None, previous_result=None, extra_context="", tracking_context=None, progress=None, case_id=None):
        self.calls.append({"focus": focus.kind if focus else None, "tracking": tracking_context is not None})
        if self.fail:
            raise RuntimeError("fake video backend failure")
        confidence = self.focus_confidence if focus is not None else self.first_confidence
        observation = sample_observation(pair_confidence=confidence, **self.observation_kwargs)
        if focus is not None:
            observation.changes_from_previous = ["collision pair 재검증: vehicle_1 ↔ vehicle_3 유지"]
            if "실선" in focus.question or "점선" in focus.question or "노면" in focus.question:
                # 재분석(개별 또는 일괄 sweep)으로 노면 차선을 확인한 상황을 흉내 낸다
                observation.uncertain_facts = [item for item in observation.uncertain_facts if "실선" not in item]
                observation.confirmed_facts.append("상대 차량이 차로를 변경한 지점의 노면 차선은 점선 구간")
                observation.road_environment.lane_marking_at_lane_change = Observation(value="dashed", status="CONFIRMED", confidence=0.85, timestamp="00:05.0")
                observation.fault_relevant_factors.append(FaultRelevantFactor(factor="실선구간 진로변경", observability="CONFIRMED", note="점선 구간으로 해당 없음"))
                observation.changes_from_previous.append("노면 차선: 점선 구간으로 확인")
            if "0.5초 단위" in focus.question or "서술 보완" in focus.question:
                # enrichment: 충돌 직전 timeline이 촘촘해진다
                observation.timeline.append(TimelineEvent(start_time="00:05.0", end_time="00:05.5", event="vehicle_3가 자차 우측 전방 약 1차로 폭 거리까지 접근, 자차 감속 시작", vehicles=["vehicle_1", "vehicle_3"], confidence=0.85))
                observation.timeline.append(TimelineEvent(start_time="00:05.5", end_time="00:05.8", event="자차 제동등 점등 상태로 접촉 직전 조향 없이 직진", vehicles=["vehicle_1"], confidence=0.8))
                observation.changes_from_previous.append("충돌 직전 0.5초 단위 timeline 보완")
        video_hash = "fakehash" + Path(str(video_path)).stem
        return self.finalize(
            observation,
            video_path=video_path,
            video_hash=video_hash,
            duration_sec=10.0,
            prompt_version="video_agent/system_v1+fake",
            metrics=CallMetrics(model=self.model, latency_sec=0.01, input_tokens=10, output_tokens=5, total_tokens=15),
            pass_type="focus" if focus else ("tracking" if tracking_context is not None else "full"),
            focus=focus,
            previous_result=previous_result,
        )


# --------------------------------------------------------------------------- text client


def _user_message(user: str) -> str:
    match = re.search(r"<USER_MESSAGE>\s*(.*?)\s*</USER_MESSAGE>", user, re.DOTALL)
    return match.group(1) if match else ""


def _case_ids(user: str) -> list[str]:
    """RETRIEVED_CASES / SIMILAR_CASES 블록 안의 case_id만 순서대로 반환한다."""
    block = re.search(r"<(?:RETRIEVED_CASES|SIMILAR_CASES)>(.*?)</(?:RETRIEVED_CASES|SIMILAR_CASES)>", user, re.DOTALL)
    haystack = block.group(1) if block else user
    ids = re.findall(r'"case_id":\s*"([^"]+)"', haystack)
    return list(dict.fromkeys(item for item in ids if not item.startswith("case_")))


class FakeTextClient:
    """task 이름으로 라우팅되는 결정적 LLM 대체물."""

    def __init__(self, *, ratio: tuple[int, int] = (30, 70), fail_tasks: Optional[set[str]] = None):
        self.model = "fake-text-model"
        self.ratio = ratio
        self.fail_tasks = fail_tasks or set()
        self.calls: list[tuple[str, str]] = []

    def _metrics(self) -> CallMetrics:
        return CallMetrics(model=self.model, latency_sec=0.001, input_tokens=100, output_tokens=50, total_tokens=150, structured_mode="fake")

    def generate_text(self, *, system, user, task="", temperature=None, **_):
        self.calls.append((task, user))
        return TextResponse(text="fake", metrics=self._metrics())

    def generate_json(self, *, system, user, schema=None, task="", temperature=None, **_) -> JSONResponse:
        self.calls.append((task, user))
        if task in self.fail_tasks:
            raise RuntimeError(f"simulated failure for {task}")
        handler = getattr(self, f"_{task}", None)
        data = handler(user) if handler else {}
        return JSONResponse(data=data, metrics=self._metrics(), raw_text=json.dumps(data, ensure_ascii=False))

    # -- master ------------------------------------------------------------
    def _master_intent(self, user: str) -> dict:
        message = _user_message(user)
        intent = "provide_facts"
        if re.search(r"어떻게 해야|방법|뭐야\??|뭔가요", message):
            intent = "general_question"
        elif "경위서" in message:
            intent = "request_incident_report"
        elif "반박" in message:
            intent = "request_rebuttal"
        elif "몇 대 몇" in message or "과실비율" in message:
            intent = "request_fault_assessment"
        elif "유사" in message:
            intent = "request_similar_cases"
        elif "왜" in message:
            intent = "ask_explanation"
        elif "직전에 Agent가 사용자에게 한 질문: (없음)" not in user:
            intent = "answer_question"
        return {"primary_intent": intent, "wants_ratio_now": intent == "request_fault_assessment", "confidence": 0.9}

    def _master_fact_extraction(self, user: str) -> dict:
        message = _user_message(user)
        facts = []
        ignored = []
        if re.search(r"모르|기억 안|기억이 안", message):
            # 직전 질문 field를 '모름'으로 답한 것으로 처리 (실제 프롬프트 규칙과 동일)
            pending = re.findall(r"^- \[([^\]]+)\]", user, re.MULTILINE)
            if pending:
                facts.append({"field": None, "value": "unknown", "fact": f"사용자가 {pending[0]}를 모른다고 답함", "confidence": 0.9, "verification": "unverified", "answers_field": pending[0]})
                return {"new_facts": facts, "conflicts": [], "opponent_claim": None, "ignored_opinions": [], "no_new_facts": False}
        if "상대" in message and "블랙박스" in message and "내 차" not in message:
            facts.append({"field": "video_source.vehicle_owner", "value": "opponent", "fact": "업로드 영상은 상대 차량 블랙박스 영상이다.", "confidence": 0.9, "verification": "not_visible_in_video", "answers_field": "video_source.vehicle_owner"})
            facts.append({"field": "video_source.type", "value": "dashcam", "fact": "업로드 영상은 블랙박스 영상이다.", "confidence": 0.9, "verification": "not_visible_in_video"})
        elif "내 차" in message:
            facts.append({"field": "video_source.vehicle_owner", "value": "user", "fact": "업로드 영상은 사용자 차량 블랙박스 영상이다.", "confidence": 0.95, "verification": "not_visible_in_video", "answers_field": "video_source.vehicle_owner"})
            facts.append({"field": "video_source.type", "value": "dashcam", "fact": "업로드 영상은 블랙박스 영상이다.", "confidence": 0.9, "verification": "not_visible_in_video"})
        if "2026년 8월 22일" in message:
            facts.append({"field": "accident_datetime.date", "value": "2026-08-22", "fact": "사고 발생일은 2026-08-22이다.", "confidence": 0.95, "verification": "not_visible_in_video", "answers_field": "accident_datetime.date"})
        if "빨간불" in message:
            facts.append({"field": "other_vehicle.signal", "value": "red", "fact": "상대 차량이 적색 신호에 진입했다는 사용자 진술", "confidence": 0.6, "verification": "not_visible_in_video"})
        if "깜빡이" in message:
            facts.append({"field": "other_vehicle.turn_signal", "value": "false", "fact": "상대 차량이 방향지시등을 켜지 않았다는 사용자 진술", "confidence": 0.6, "verification": "not_visible_in_video"})
        if "좌회전" in message and "내가" in message:
            facts.append({"field": "ego_vehicle.movement", "value": "left_turn", "fact": "사용자 차량은 좌회전 중이었다는 진술", "confidence": 0.7, "verification": "contradicts_video"})
        if "실선" in message and not re.search(r"뭐야|뭔가요|어떻게|\?", message):
            value = "false" if ("아니" in message or "점선" in message) else "true"
            facts.append({"field": None, "value": value, "fact": f"차로 변경 지점 실선 여부: {value} (사용자 진술)", "confidence": 0.8, "verification": "not_visible_in_video", "answers_field": "review.solid_line_lane_change"})
        if "먼저 들어" in message or "먼저 진입" in message:
            facts.append({"field": "ego_vehicle.entered_first", "value": "true", "fact": "사용자 차량이 먼저 진입했다는 진술", "confidence": 0.7, "verification": "not_visible_in_video", "answers_field": "ego_vehicle.entered_first"})
        if "무시" in message or "100:0" in message or "잘못" in message:
            ignored.append(message)
        claim = None
        if "보험사" in message and "주장" in message:
            claim = message
        return {"new_facts": facts, "conflicts": [], "opponent_claim": claim, "ignored_opinions": ignored, "no_new_facts": not facts}

    def _master_sufficiency(self, user: str) -> dict:
        return {"ready_for_rag": True, "ready_for_assessment": True, "missing_information": [], "user_answerable": [], "video_reanalysis_targets": [], "reasons": []}

    def _master_followup_question(self, user: str) -> dict:
        """Agent가 고른 질문을 흉내 낸다: forced 항목이 있으면 그것, 없으면 영상 미확인 항목 중 방향지시등 → 선진입 순."""
        from case.questions import DEFAULT_QUESTIONS

        forced = re.search(r"반드시 먼저 물어야 하는 항목\(있으면 이 항목을 첫 질문으로 한다\):\s*\n(\S+)", user)
        asked_match = re.search(r"이미 질문했거나 확인된 항목과 그 답\(.*?\):\s*\n(.*?)\n\n", user, re.DOTALL)
        asked = asked_match.group(1) if asked_match else ""
        unknown_match = re.search(r"영상 분석이 확인하지 못했거나 추정만 한 항목:\s*\n(.*?)\n\n", user, re.DOTALL)
        unknowns = unknown_match.group(1) if unknown_match else ""
        history_match = re.search(r"이미 영상 focus 재분석을 수행한 쟁점.*?:\s*\n(.*?)\n\n", user, re.DOTALL)
        history = history_match.group(1) if history_match else ""
        questions = []
        rechecks = []
        reasoning = ["사고 구조: 교차로 측면 충돌", "판정 요소: 선진입, 신호, 방향지시등, 실선/점선"]
        if "실선" in unknowns and "실선" not in history and "점선" not in history:
            reasoning.append("실선 여부는 노면이 화면에 찍히므로 영상 재분석 (A)")
            rechecks.append({"focus": "충돌 직전 상대 차량이 차로를 변경한 지점의 노면 차선이 실선인지 점선인지 확인하라.", "why": "실선구간 진로변경 수정요소"})
        if forced and forced.group(1) != "(없음)":
            field = forced.group(1)
            questions.append({"field": field, "question": DEFAULT_QUESTIONS.get(field, field), "importance": "critical", "why": "판정 방향(사용자/상대)을 정하는 기준"})
        elif "방향지시등" in unknowns and "other_vehicle.turn_signal" not in asked:
            reasoning.append("영상 미확인: 상대 방향지시등 → 사용자가 봤을 수 있음")
            questions.append({"field": "other_vehicle.turn_signal", "question": "상대 차량(vehicle_3)이 진입할 때 방향지시등을 켠 것을 보셨나요?", "importance": "high", "why": "방향지시등 미점등은 상대 과실을 가산하는 수정요소입니다."})
        elif "진입 순서" in unknowns and "ego_vehicle.entered_first" not in asked:
            reasoning.append("영상 미확인: 진입 순서 → 사용자가 알 수 있음")
            questions.append({"field": "ego_vehicle.entered_first", "question": "본인 차량이 먼저 교차로에 진입해 있었나요?", "importance": "high", "why": "선진입 여부는 기본과실을 정하는 핵심 요소입니다."})
        else:
            reasoning.append("더 물어볼 가치가 있는 미확인 사실 없음")
        questions.append({"field": "review.blame", "question": "상대 차량이 무리하게 들어왔다고 생각하시나요?", "importance": "high", "why": "주관 질문(필터 대상)"})
        return {"reasoning": reasoning, "video_recheck_targets": rechecks, "intro": "영상에서는 사거리 교차로에서 우측 진입 차량과의 충돌이 확인됩니다. 먼저 한 가지 확인할게요.", "questions": questions}

    def _master_video_gap_plan(self, user: str) -> dict:
        """1차 영상 결과를 보고 Agent가 2차 분석 지시서를 쓰는 것을 흉내 낸다."""
        items = []
        if "실선" in user or "노면" in user or '"lane_marking_at_lane_change": null' in user or "lane_marking_at_lane_change" not in user:
            items.append({"item": "00:03.5~00:05.5 구간에서 vehicle_3가 진입한 지점의 노면 차선이 실선인지 점선인지 확인하고 timestamp를 기록하라", "time_window": "00:03.5~00:05.5", "target_field": "road_environment.lane_marking_at_lane_change", "why": "실선구간 진로변경 수정요소"})
        items.append({"item": "00:04.0~00:05.8 구간에서 vehicle_1의 제동·감속 여부를 확인하라", "time_window": "00:04.0~00:05.8", "target_field": "vehicles[vehicle_1].braking", "why": "회피 조치"})
        return {
            "reasoning": ["사고 구조: 사거리 교차로 직진 대 우측 진입", "판정 요소: 신호, 선진입, 차선 종류, 제동", "1차 결과 대조: 신호 CONFIRMED / 차선 종류 UNKNOWN / 제동 INFERRED", "다시 볼 것: 차선 종류, 제동"],
            "recheck_items": items,
            "enrichment": "충돌 직전 3초간 두 차량의 상대 위치와 거리 변화를 0.5초 단위로 기록하라.",
            "skipped": [{"item": "vehicle_3 방향지시등", "reason": "화각 밖"}],
        }

    def _master_case_review_questions(self, user: str) -> dict:
        # 프롬프트의 "이미 질문했거나 확인된 항목" 줄만 보고 중복을 피한다 (템플릿 예시 문구와 구분)
        asked_match = re.search(r"이미 질문했거나 확인된 항목과 그 답\(.*?\):\s*\n(.*?)\n\n", user, re.DOTALL)
        asked = asked_match.group(1) if asked_match else ""
        history_match = re.search(r"이미 영상 focus 재분석을 수행한 쟁점.*?:\s*\n(.*?)\n\n", user, re.DOTALL)
        history = history_match.group(1) if history_match else ""
        state_match = re.search(r"<CASE_STATE>(.*?)</CASE_STATE>", user, re.DOTALL)
        state_text = state_match.group(1) if state_match else ""
        questions = []
        rechecks = []
        # 실선/점선은 화면에 찍히므로 사용자에게 묻지 않고 재분석을 요청한다 (확인되기 전까지 한 번)
        if "점선" not in state_text and "실선" not in history:
            rechecks.append({"focus": "충돌 직전 상대 차량이 차로를 변경한 지점의 노면 차선이 실선인지 점선인지 확인하라.", "why": "실선구간 진로변경 수정요소"})
        if "other_vehicle.turn_signal" not in asked:
            questions.append({"field": "other_vehicle.turn_signal", "question": "상대 차량이 진입할 때 방향지시등을 켠 것을 보셨나요?", "importance": "high", "why": "방향지시등 미점등 수정요소", "related_case_ids": ["2019-034537"]})
        elif "ego_vehicle.entered_first" not in asked:
            questions.append({"field": "ego_vehicle.entered_first", "question": "본인 차량이 상대 차량보다 먼저 교차로에 진입해 있었나요?", "importance": "high", "why": "선진입 관계", "related_case_ids": ["2018-070162"]})
        questions.append({"field": "review.blame", "question": "상대가 무리하게 들어왔다고 생각하시나요?", "importance": "low", "why": "주관 질문(필터 대상)"})
        # 검토 질문은 한 턴에 하나씩: 가장 중요한 것부터
        return {
            "reasoning": ["2018-070162 결정 30:70 = 기본 30:70", "현재 사건: 실선 여부 미확인(노면 → 재분석), 방향지시등 미확인(화각 밖 → 사용자)"],
            "summary": "가장 유사한 사례는 실선구간 진로변경 여부에 따라 비율이 달라졌습니다.",
            "video_recheck_targets": rechecks,
            "questions": questions,
        }

    def _master_rag_query(self, user: str) -> dict:
        return {
            "structured_query": "사거리 신호교차로 직진 대 직진 측면 진입 선진입 A전면 B좌측면 충돌",
            "detailed_query": "신호기 있는 사거리 교차로에서 A 차량이 녹색신호 직진 중 우측 도로에서 진입한 B 차량과 충돌",
            "key_factors": ["사거리 교차로", "직진 대 직진", "우측 진입"],
            "filters": {"road_type": "intersection", "intersection_type": "four_way", "signal_present": True, "accident_target": "차대차"},
        }

    def _master_case_validation(self, user: str) -> dict:
        validations = []
        for index, case_id in enumerate(_case_ids(user)):
            validations.append({
                "case_id": case_id,
                "relevance": 0.9 - index * 0.2,
                "matched_factors": ["사거리 교차로", "직진 대 직진"],
                "different_factors": ["상대 신호 확인 불가"],
                "usable_as_primary_reference": index == 0,
                "basic_ratio_applicable": index == 0,
            })
        validations.append({"case_id": "9999-999999", "relevance": 0.99, "matched_factors": [], "different_factors": [], "usable_as_primary_reference": True})
        return {"validations": validations}

    def _master_fault_assessment(self, user: str) -> dict:
        ids = _case_ids(user)
        primary = ids[0] if ids else "2018-070162"
        user_ratio, opponent_ratio = self.ratio
        adjustments = [{"factor": "B 차량 방향지시등 미점등", "direction": "user_down", "percentage": 10, "source": "rag", "applies": False, "note": "영상 확인 불가"}]
        if getattr(self, "confirmed_adjustment", False):
            adjustments.append({"factor": "실선구간 진로변경 아님 (USER_CONFIRMED)", "direction": "user_down", "percentage": 10, "source": "user", "applies": True, "note": "사용자 확인"})
        return {
            "fault_ratio": {"user": user_ratio, "opponent": opponent_ratio},
            "assessment_type": "estimated",
            "possible_range": ["30:70", "20:80"],
            "confidence": 0.78,
            "primary_case_ids": [primary, "invented-0001"],
            "core_facts": ["A 차량이 교차로에 선진입", "B 차량이 우측 도로에서 진입"],
            "matched_cases": [{"case_id": primary, "decision_ratio": "30:70", "basic_ratio": "30:70", "relevance": 0.9, "role": "primary"}],
            "adjustment_factors": adjustments,
            "reasoning_summary": ["A 차량 선진입", "유사 심의사례와 구조 유사"],
            "uncertainties": ["상대 차량 신호 확인 불가"],
            "ratio_dependencies": ["상대 차량 적색 신호가 확인되면 상대 과실 증가"],
            "explanation": f"가장 비슷한 심의사례 {primary}의 결정비율을 기준으로 보면 나 {user_ratio} : 상대 {opponent_ratio} 정도의 과실비율이 예상돼요. 상대 차량 신호가 확인되면 달라질 수 있어요.",
        }

    def _master_respond(self, user: str) -> dict:
        return {"message": "현재 사건 정보를 기준으로 답변드립니다. 영상에서 확인된 사실과 사용자 진술을 구분하여 기록했습니다.", "follow_up_needed": False}

    # -- document ----------------------------------------------------------
    def _document_incident_report(self, user: str) -> dict:
        revision = "<REVISION_REQUEST>" in user
        process = (
            "본 차량은 블랙박스 촬영 차량(vehicle_1)이며 본인 차량 블랙박스 영상입니다. 본 차량은 약 42 km/h로 2차로를 직진하고 있었습니다. "
            "본 차량은 2차로를 따라 교차로에 접근하였습니다. 우측 도로에서 상대 차량(vehicle_3)이 교차로로 진입하였고, "
            "본 차량 전면과 상대 차량 좌측면이 충돌하였습니다. 충돌 후 양 차량은 교차로 내에 정지하였습니다."
        )
        if revision:
            process = "본 차량은 2차로를 직진하던 중 우측에서 진입한 상대 차량(vehicle_3)과 충돌하였습니다. (다시 씀)"
        return {
            "title": "교통사고 사건경위서",
            "sections": {
                "datetime_location": "2026년 8월 22일, 신호기가 설치된 사거리 교차로에서 사고가 발생하였습니다.",
                "accident_process": process,
                "video_analysis": "영상에서 본 차량 진행 방향 신호가 녹색인 점이 확인됩니다. 상대 차량이 일부러 무리하게 진입한 것으로 보입니다. 상대 차량 방향 신호는 영상에서 확인되지 않습니다.",
                "claim_summary": "영상에서 확인된 사실을 근거로 본 차량 30 : 상대 차량 70의 과실비율 적용을 요청드립니다. 이는 예상 비율입니다.",
            },
            "caveat": "상대 차량 방향의 신호는 아직 확인되지 않아서 본문에 쓰지 않았어요.",
            "text": "",
        }

    def _document_rebuttal_opinion(self, user: str) -> dict:
        ids = _case_ids(user)
        primary = ids[0] if ids else "2018-070162"
        with_report = "<INCIDENT_REPORT>\n(없음)" not in user
        return {
            "title": "과실비율 반박의견서",
            "sections": {
                "overview": "본 사고는 사거리 신호교차로에서 발생한 직진 대 직진 사고입니다.",
                "opponent_claim": "상대방은 본 차량 과실 50%를 주장합니다.",
                "objective_facts": "영상에서 본 차량 방향 녹색 신호가 확인됩니다.",
                "key_issues": "선진입 여부와 상대 차량 신호가 핵심 쟁점입니다.",
                "similar_cases": f"심의번호 {primary} 및 심의번호 1234-567890 참조.",
                "commonalities": "사거리 교차로, 직진 대 직진 구조가 동일합니다.",
                "differences": "상대 차량 신호가 영상에서 확인되지 않습니다.",
                "basic_ratio_review": "기본과실 30:70 적용이 타당합니다.",
                "adjustment_factor_review": "방향지시등 미점등은 영상 확인이 어려워 적용하지 않았습니다.",
                "final_opinion": "예상 과실비율 30:70이 타당하다고 판단됩니다.",
            },
            "mail_body": (
                "안녕하세요. 본 사고 건의 과실비율 재검토를 요청드립니다. 블랙박스 영상에서 본 차량 방향 녹색 신호가 확인됩니다. "
                f"심의사례 {primary}에 비추어 본 차량 30 : 상대 차량 70이 타당합니다."
                + (" (사건경위서 참조)" if with_report else "")
            ),
            "cited_case_ids": [primary, "1234-567890"],
            "text": "",
        }


# --------------------------------------------------------------------------- rag


def sample_cases() -> list[RetrievedCase]:
    return [
        RetrievedCase(
            case_id="2018-070162",
            title="차대차 직진 대 직진 사고 - 사거리 교차로(상대 차량이 측면 방향에서 진입)",
            accident_type="차대차 직진 대 직진 사고",
            chart_number="203",
            decision_ratio="30:70",
            basic_ratio="30:70",
            accident_description="신호기 있는 사거리 교차로에서 청구차량이 황색신호에 직진하던 중 좌측도로에서 적색신호에 직진하는 피청구차량과 충돌",
            key_issues=["황색신호 직진 여부"],
            decision_reasons=["203도표 기본과실 적용"],
            modification_factors=["현저한 과실 +10"],
            similarity=0.9,
            metadata=CaseMetadata(road_type="intersection", intersection_type="four_way", signal_present=True, accident_target="차대차"),
        ),
        RetrievedCase(
            case_id="2019-034537",
            title="차대차 직진 대 직진 사고 - 사거리 교차로",
            decision_ratio="20:80",
            basic_ratio="30:70",
            accident_description="사거리 교차로 직진 대 직진",
            similarity=0.7,
            metadata=CaseMetadata(road_type="intersection", intersection_type="four_way", signal_present=True, accident_target="차대차"),
        ),
        RetrievedCase(
            case_id="203",
            source_type="fault_standard",
            title="과실비율 인정기준 도표 203",
            basic_ratio="30:70",
            accident_description="신호등 있는 사거리 황색 직진 적색 직진",
            modification_factors=["대형차 +5", "현저한 과실 +10"],
            similarity=0.6,
        ),
    ]


class FakeRagTool(SimilarCaseRagTool):
    def __init__(self, client=None, *, run_logger=None, cases: Optional[list[RetrievedCase]] = None):
        super().__init__(client, run_logger=run_logger, auto_build_index=False, case_docs={})
        self.cases = cases if cases is not None else sample_cases()
        self.searches = 0

    def ensure_index(self, progress=None):
        return None

    def search(self, state, *, query: Optional[RagQuery] = None, progress=None) -> RagResult:
        self.searches += 1
        rag_query = query or (self.build_query(state) if self.client else RagQuery(structured_query="fake"))
        candidates = [item.model_copy(deep=True) for item in self.cases]
        ranked = rerank_cases(self.client, state, candidates, top_k=3, run_logger=self.run_logger)
        return RagResult(query=rag_query, candidates=candidates, cases=ranked, embedding_model="fake-embedding")
