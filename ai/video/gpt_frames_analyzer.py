"""Video Path B — GPT Vision Frame Analysis (가이드 11.2 / 49.2~49.5절).

MP4 → 0.5초 간격 프레임(약 20장) → GPT Vision.
모델이 충돌 후보 구간 재추출(recommended_dense_sampling)을 요청하면
해당 구간만 0.1초 간격으로 재추출하여 충돌 pair를 재검증한다 (Fast Path / Focus Re-analysis).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from models.clients import GPTVisionClient
from prompts.loader import load_prompt
from settings import VideoSettings, get_settings
from telemetry import RunLogger
from video.base import BaseVideoAnalyzer, ProgressCallback, build_focus_prompt
from video.frame_sampler import SampledFrame, sample_frames
from video.merge import merge_video_results
from video.schemas import VideoObservation, VideoResult
from video.validation import FocusTarget, evaluate_video_completion, validate_video_result


class GPTFrameVideoAnalyzer(BaseVideoAnalyzer):
    backend = "gpt_frames"

    def __init__(
        self,
        client: Optional[GPTVisionClient] = None,
        *,
        settings: Optional[VideoSettings] = None,
        run_logger: Optional[RunLogger] = None,
        interval_sec: Optional[float] = None,
        auto_dense_resampling: bool = True,
        max_full_frames: int = 60,
        max_dense_frames: int = 40,
    ):
        super().__init__(settings=settings, run_logger=run_logger)
        app = get_settings()
        self.client = client or GPTVisionClient(self.settings.gpt_vision_model, api_key=app.openai_api_key)
        self.model = getattr(self.client, "model", self.settings.gpt_vision_model)
        self.interval_sec = interval_sec or self.settings.default_frame_interval_sec
        self.auto_dense_resampling = auto_dense_resampling
        self.max_full_frames = max_full_frames
        self.max_dense_frames = max_dense_frames

    def _frames(self, video_path, *, interval: float, start: Optional[float], end: Optional[float], video_hash: str, max_frames: int) -> list[SampledFrame]:
        return sample_frames(
            video_path,
            interval_sec=interval,
            start_sec=start,
            end_sec=end,
            max_width=self.settings.frame_max_width,
            max_frames=max_frames,
            video_hash=video_hash,
        )

    def _call(self, *, system_text: str, prompt_text: str, frames: list[SampledFrame], task: str, progress: ProgressCallback):
        if progress:
            progress(f"{self.model}로 프레임 {len(frames)}장 분석 중 ({task})")
        return self.client.analyze_frames(
            system=system_text,
            prompt=prompt_text,
            frames=frames,
            schema=VideoObservation,
            task=task,
            detail=self.settings.frame_detail,
        )

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
        video_hash, duration = self.video_identity(video_path)
        system = self.system_prompt()

        if focus is None:
            frames = self._frames(
                video_path,
                interval=self.interval_sec,
                start=None,
                end=None,
                video_hash=video_hash,
                max_frames=self.max_full_frames,
            )
            task = load_prompt("video_agent", "gpt_frames_full")
            prompt_text = task.render(
                duration_sec=f"{duration:.1f}" if duration else "10",
                interval_sec=f"{self.interval_sec:g}",
                frame_count=len(frames),
                extra_context=extra_context or "없음",
            )
            prompt_version = f"{system.version_id}+{task.version_id}"
            pass_type = "full"
        else:
            start, end = focus.start_sec, focus.end_sec
            interval = self.settings.dense_frame_interval_sec if (start is not None and end is not None) else self.settings.high_res_frame_interval_sec
            frames = self._frames(
                video_path,
                interval=interval,
                start=start,
                end=end,
                video_hash=video_hash,
                max_frames=self.max_dense_frames,
            )
            focus_text, focus_versions = build_focus_prompt(focus, previous_result)
            prompt_text = (
                f"다음 이미지는 동일한 사고 영상에서 {interval:g}초 간격으로 추출한 프레임이다"
                + (f" (구간 {start:.1f}s~{end:.1f}s)." if start is not None and end is not None else ".")
                + " 각 프레임의 timestamp를 사용하여 연속된 장면으로 해석하라.\n\n"
                + focus_text
            )
            prompt_version = f"{system.version_id}+{focus_versions}"
            pass_type = "focus"

        if tracking_context is not None:
            tracking_text, tracking_version = self.tracking_prompt(tracking_context)
            prompt_text = f"{prompt_text}\n\n{tracking_text}"
            prompt_version += f"+{tracking_version}"
            pass_type = "tracking"

        response = self._call(
            system_text=system.text,
            prompt_text=prompt_text,
            frames=frames,
            task=f"video_{pass_type}",
            progress=progress,
        )
        result = self.finalize(
            response.data,
            video_path=video_path,
            video_hash=video_hash,
            duration_sec=duration,
            prompt_version=prompt_version,
            metrics=response.metrics,
            pass_type=pass_type,
            focus=focus,
            frame_count=len(frames),
            previous_result=previous_result,
        )
        self.log(
            task=f"video_{pass_type}",
            prompt_version=prompt_version,
            metrics=response.metrics,
            case_id=case_id,
            extra={
                "video_hash": video_hash,
                "backend": self.backend,
                "frame_count": len(frames),
                "interval_sec": self.interval_sec if focus is None else None,
                "focus": focus.kind if focus else None,
                "completion": result.analysis_completion.model_dump(),
            },
        )

        if focus is None and self.auto_dense_resampling:
            result = self._maybe_dense_resample(video_path, result, video_hash, progress=progress, case_id=case_id)
        return result

    def _maybe_dense_resample(
        self,
        video_path: str | Path,
        first: VideoResult,
        video_hash: str,
        *,
        progress: ProgressCallback,
        case_id: Optional[str],
    ) -> VideoResult:
        """가이드 49.5: 모델이 요청한 충돌 후보 구간만 0.1초 간격으로 재추출해 pair를 재검증한다."""
        recommendation = first.recommended_dense_sampling
        valid, _ = validate_video_result(first, self.settings.collision_pair_confidence_threshold)
        if recommendation is None or (valid and first.collision_window.detected()):
            return first
        start = max(0.0, float(recommendation.start_sec))
        end = float(recommendation.end_sec)
        if first.duration_sec:
            end = min(end, float(first.duration_sec))
        if end - start <= 0.05:
            return first
        focus = FocusTarget(
            kind="collision_pair",
            question=recommendation.reason or "충돌 구간 dense 프레임으로 충돌 pair와 시각을 재검증하라.",
            prompt_name="collision_focus",
            start_sec=start,
            end_sec=end,
            reasons=["recommended_dense_sampling"],
        )
        second = self.analyze(
            video_path,
            focus=focus,
            previous_result=first,
            progress=progress,
            case_id=case_id,
        )
        merged = merge_video_results(first, second)
        merged.analysis_completion = evaluate_video_completion(
            merged,
            threshold=self.settings.collision_pair_confidence_threshold,
            score_threshold=self.settings.completion_score_threshold,
        )
        merged.fault_relevant_factors_checked = merged.analysis_completion.gate.get("fault_relevant_factor_check", False)
        return merged
