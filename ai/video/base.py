"""Video Backend 추상화 (가이드 11.4 / 120절).

`BaseVideoAnalyzer.analyze(video_path, focus=None)` 하나의 인터페이스 아래
Gemini Native / GPT Frames 두 경로를 두고 `VIDEO_BACKEND` 값으로 교체한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from common.jsonutil import compact_json
from prompts.loader import Prompt, load_prompt
from settings import VideoSettings, get_settings
from telemetry import CallMetrics, RunLogger, get_run_logger
from video.frame_sampler import compute_video_hash, probe_duration
from video.schemas import AnalysisPassRecord, VideoObservation, VideoResult
from video.validation import FocusTarget, evaluate_video_completion

ProgressCallback = Optional[Callable[[str], None]]


def compact_previous_result(result: Optional[VideoResult]) -> dict[str, Any]:
    if result is None:
        return {}
    return {
        "road_type": result.road_environment.road_type.value,
        "intersection_type": result.road_environment.intersection_type.value,
        "signal_present": result.road_environment.signal_present.value,
        "vehicles": [
            {
                "id": vehicle.id,
                "description": vehicle.description,
                "is_ego": vehicle.is_ego,
                "movement": vehicle.movement,
                "entry_direction": vehicle.entry_direction,
                "lane": vehicle.lane,
            }
            for vehicle in result.vehicles
        ],
        "ego_vehicle_id": result.ego_vehicle_id,
        "collision_window": result.collision_window.model_dump(),
        "collision_pair": result.collision_pair.model_dump(),
        "pair_scores": [item.model_dump() for item in result.pair_scores],
        "collision": result.collision.model_dump(exclude={"evasive_action", "braking_before_collision", "secondary_collision"}),
        "confirmed_facts": result.confirmed_facts[:15],
        "unknown_or_unobservable": result.unknown_or_unobservable[:10],
        "signal_observations": [item.model_dump() for item in result.road_environment.signal_observations[:6]],
        # 재분석이 기존 timeline을 유지·확장할 수 있도록 함께 넘긴다
        "timeline": [event.model_dump(include={"start_time", "end_time", "event", "vehicles", "status"}) for event in result.timeline[:20]],
    }


def _format_window(focus: FocusTarget) -> str:
    if focus.start_sec is None and focus.end_sec is None:
        return "영상 전체"
    start = f"{focus.start_sec:.1f}s" if focus.start_sec is not None else "시작"
    end = f"{focus.end_sec:.1f}s" if focus.end_sec is not None else "끝"
    return f"{start} ~ {end}"


def build_focus_question(focus: FocusTarget, previous: Optional[VideoResult]) -> tuple[str, list[Prompt]]:
    """focus 종류별 전용 템플릿(signal/collision/lane)을 focus_question 문장으로 만든다."""
    used: list[Prompt] = []
    vehicle_ids = previous.vehicle_ids() if previous else []
    ego = previous.dashcam_vehicle_id() if previous else None
    other = previous.other_participant(ego) if previous and ego else None
    if focus.prompt_name == "collision_focus":
        template = load_prompt("video_agent", "collision_focus")
        pairs = []
        for index, left in enumerate(vehicle_ids):
            for right in vehicle_ids[index + 1 :]:
                pairs.append(f"{left} ↔ {right}")
        window = previous.collision_window if previous else None
        window_text = (
            f"{window.start or '?'} ~ {window.end or '?'}" if window and window.detected() else "미확정"
        )
        used.append(template)
        return (
            template.render(
                vehicle_ids=", ".join(vehicle_ids) or "미확인",
                collision_window=window_text,
                pair_list="\n".join(pairs) or "(차량 2대 미만)",
            ).strip()
            + "\n\n추가 쟁점: "
            + focus.question,
            used,
        )
    if focus.prompt_name == "signal_focus":
        template = load_prompt("video_agent", "signal_focus")
        used.append(template)
        return template.render(ego_vehicle_id=ego or "블랙박스 차량", other_vehicle_id=other or "상대 차량").strip(), used
    if focus.prompt_name == "lane_focus":
        template = load_prompt("video_agent", "lane_focus")
        used.append(template)
        return template.render(ego_vehicle_id=ego or "블랙박스 차량", other_vehicle_id=other or "상대 차량").strip(), used
    return focus.question, used


def build_focus_prompt(focus: FocusTarget, previous: Optional[VideoResult]) -> tuple[str, str]:
    """focus_analysis 템플릿에 focus 문장과 이전 결과를 채운 Task Prompt와 prompt version id."""
    template = load_prompt("video_agent", "focus_analysis")
    question, used = build_focus_question(focus, previous)
    text = template.render(
        focus_question=question,
        time_window=_format_window(focus),
        previous_result=compact_json(compact_previous_result(previous), indent=None, max_chars=6000),
    )
    versions = "+".join([template.version_id] + [item.version_id for item in used])
    return text, versions


class BaseVideoAnalyzer:
    backend: str = "unavailable"

    def __init__(self, settings: Optional[VideoSettings] = None, run_logger: Optional[RunLogger] = None):
        self.settings = settings or get_settings().video
        self.run_logger = run_logger or get_run_logger()
        self.model = ""

    # -- public interface ----------------------------------------------------
    def analyze(
        self,
        video_path: str | Path,
        *,
        focus: Optional[FocusTarget] = None,
        previous_result: Optional[VideoResult] = None,
        extra_context: str = "",
        tracking_context: Optional[Any] = None,
        progress: ProgressCallback = None,
        case_id: Optional[str] = None,
    ) -> VideoResult:
        raise NotImplementedError

    def reanalyze(
        self,
        video_path: str | Path,
        previous_result: VideoResult,
        focus: FocusTarget | str,
        *,
        progress: ProgressCallback = None,
        case_id: Optional[str] = None,
    ) -> VideoResult:
        if isinstance(focus, str):
            focus = FocusTarget(kind="custom", question=focus)
        return self.analyze(
            video_path,
            focus=focus,
            previous_result=previous_result,
            progress=progress,
            case_id=case_id,
        )

    def analyze_with_tracking(
        self,
        video_path: str | Path,
        tracking_context: Any,
        *,
        previous_result: Optional[VideoResult] = None,
        progress: ProgressCallback = None,
        case_id: Optional[str] = None,
    ) -> VideoResult:
        return self.analyze(
            video_path,
            previous_result=previous_result,
            tracking_context=tracking_context,
            progress=progress,
            case_id=case_id,
        )

    # -- helpers -------------------------------------------------------------
    def video_identity(self, video_path: str | Path) -> tuple[str, Optional[float]]:
        path = Path(video_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"영상 파일을 찾을 수 없습니다: {path}")
        return compute_video_hash(path), probe_duration(path)

    def system_prompt(self) -> Prompt:
        return load_prompt("video_agent", "system")

    def tracking_prompt(self, tracking_context: Any) -> tuple[str, str]:
        template = load_prompt("video_agent", "tracking_context")
        payload = tracking_context.to_prompt_json() if hasattr(tracking_context, "to_prompt_json") else compact_json(tracking_context)
        return template.render(tracked_vehicles=payload), template.version_id

    def finalize(
        self,
        observation: VideoObservation | dict[str, Any],
        *,
        video_path: str | Path,
        video_hash: str,
        duration_sec: Optional[float],
        prompt_version: str,
        metrics: CallMetrics,
        pass_type: str,
        focus: Optional[FocusTarget],
        frame_count: int = 0,
        previous_result: Optional[VideoResult] = None,
    ) -> VideoResult:
        if isinstance(observation, dict):
            observation = VideoObservation.model_validate(observation)
        record = AnalysisPassRecord(
            pass_type=pass_type,  # type: ignore[arg-type]
            backend=self.backend,  # type: ignore[arg-type]
            model=self.model,
            prompt_version=prompt_version,
            focus=focus.question if focus else None,
            latency_sec=round(metrics.latency_sec, 3),
            frame_count=frame_count,
            token_usage={
                "input": metrics.input_tokens,
                "output": metrics.output_tokens,
                "total": metrics.total_tokens,
            },
        )
        result = VideoResult.from_observation(
            observation,
            video_backend=self.backend,
            model=self.model,
            prompt_version=prompt_version,
            video_hash=video_hash,
            video_path=str(video_path),
            duration_sec=duration_sec,
            analysis_passes=[record],
        )
        if not result.ego_vehicle_id:
            result.ego_vehicle_id = result.dashcam_vehicle_id()
        if pass_type == "focus" and previous_result is not None and not result.vehicles:
            result.vehicles = [item.model_copy(deep=True) for item in previous_result.vehicles]
        result.analysis_completion = evaluate_video_completion(
            result,
            threshold=self.settings.collision_pair_confidence_threshold,
            score_threshold=self.settings.completion_score_threshold,
        )
        result.fault_relevant_factors_checked = result.analysis_completion.gate.get("fault_relevant_factor_check", False)
        return result

    def log(self, *, task: str, prompt_version: str, metrics: CallMetrics, case_id: Optional[str], extra: Optional[dict] = None) -> None:
        self.run_logger.log(
            agent="video_agent",
            task=task,
            case_id=case_id,
            model=self.model,
            prompt_version=prompt_version,
            metrics=metrics,
            extra=extra,
        )


def get_video_analyzer(
    backend: Optional[str] = None,
    *,
    settings: Optional[VideoSettings] = None,
    run_logger: Optional[RunLogger] = None,
    client: Any = None,
) -> BaseVideoAnalyzer:
    """`VIDEO_BACKEND` 값(gemini_native | gpt_frames)에 따라 analyzer를 생성한다."""
    video_settings = settings or get_settings().video
    chosen = (backend or video_settings.default_backend or "gemini_native").strip().lower()
    if chosen in {"gemini", "gemini_native"}:
        from video.gemini_analyzer import GeminiVideoAnalyzer

        return GeminiVideoAnalyzer(client=client, settings=video_settings, run_logger=run_logger)
    if chosen in {"gpt", "gpt_frames", "openai_frames"}:
        from video.gpt_frames_analyzer import GPTFrameVideoAnalyzer

        return GPTFrameVideoAnalyzer(client=client, settings=video_settings, run_logger=run_logger)
    raise ValueError(f"지원하지 않는 VIDEO_BACKEND: {chosen}")
