"""Video Path A — Gemini Native Video (가이드 11.1 / 49.1 / 51절).

10초 내외 블랙박스 MP4를 직접 입력하여 1회 통합 분석을 수행한다.
focus 재분석은 같은 영상에 focus 프롬프트와 더 높은 fps를 적용한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from models.clients import GeminiVideoClient
from prompts.loader import load_prompt
from settings import VideoSettings, get_settings
from telemetry import RunLogger
from common.timeutil import format_timestamp
from video.clip import ClipWindow, cut_clip, plan_clip_window, shift_data, shift_focus, shift_result
from video.base import BaseVideoAnalyzer, ProgressCallback, build_focus_prompt
from video.schemas import VideoObservation, VideoResult
from video.validation import FocusTarget


class GeminiVideoAnalyzer(BaseVideoAnalyzer):
    backend = "gemini_native"

    def __init__(
        self,
        client: Optional[GeminiVideoClient] = None,
        *,
        settings: Optional[VideoSettings] = None,
        run_logger: Optional[RunLogger] = None,
        clip_focus_window: Optional[bool] = None,
    ):
        super().__init__(settings=settings, run_logger=run_logger)
        app = get_settings()
        self.client = client or GeminiVideoClient(
            self.settings.gemini_model,
            api_key=app.gemini_api_key,
            fps=self.settings.gemini_fps,
            inline_max_mb=self.settings.inline_upload_max_mb,
            processing_timeout=self.settings.processing_timeout_sec,
            retries=self.settings.retries,
            use_remote_schema=self.settings.gemini_remote_schema,
            max_output_tokens=self.settings.gemini_max_output_tokens,
        )
        self.model = getattr(self.client, "model", self.settings.gemini_model)
        # 긴 영상(settings.clip_min_duration_sec 이상)의 focus 재분석은 충돌 앞뒤 구간만 잘라 보낸다.
        # 클립 기준으로 적힌 시각은 video/clip.py 가 원본 기준으로 되돌린다.
        self.clip_focus_window = self.settings.clip_focus_window if clip_focus_window is None else clip_focus_window

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
        start_sec = end_sec = None
        fps = self.settings.gemini_fps
        clip: Optional[ClipWindow] = None

        if focus is None:
            task = load_prompt("video_agent", "full_analysis")
            prompt_text = task.render(
                duration_sec=f"{duration:.1f}" if duration else "약 10",
                extra_context=extra_context or "없음",
            )
            prompt_version = f"{system.version_id}+{task.version_id}"
            pass_type = "full"
        else:
            clip = self._maybe_clip(video_path, focus, previous_result, duration, video_hash, progress=progress, case_id=case_id)
            prompt_focus, prompt_previous = focus, previous_result
            if clip is not None:
                # 모델은 클립 시작을 00:00.0 으로 보므로 지시서·이전 결과의 시각을 클립 기준으로 옮겨 넣고, 지시서 구간은 클립 안으로 맞춘다
                prompt_focus = shift_focus(focus, -clip.start_sec)
                prompt_focus.start_sec = 0.0 if prompt_focus.start_sec is None else min(prompt_focus.start_sec, clip.length_sec)
                prompt_focus.end_sec = clip.length_sec if prompt_focus.end_sec is None else min(prompt_focus.end_sec, clip.length_sec)
                prompt_previous = shift_result(previous_result, -clip.start_sec) if previous_result is not None else None
            prompt_text, focus_versions = build_focus_prompt(prompt_focus, prompt_previous)
            prompt_version = f"{system.version_id}+{focus_versions}"
            if clip is not None:
                note = load_prompt("video_agent", "clip_note")
                prompt_text += "\n\n" + note.render(
                    original_duration=f"{duration:.1f}" if duration else "?",
                    clip_start=format_timestamp(clip.start_sec),
                    clip_end=format_timestamp(clip.end_sec),
                    clip_length=f"{clip.length_sec:.1f}",
                )
                prompt_version += f"+{note.version_id}"
            pass_type = "focus"
            fps = self.settings.gemini_focus_fps

        if tracking_context is not None:
            tracking_text, tracking_version = self.tracking_prompt(tracking_context)
            prompt_text = f"{prompt_text}\n\n{tracking_text}"
            prompt_version += f"+{tracking_version}"
            pass_type = "tracking"

        response = self.client.analyze_video(
            video_path=clip.path if clip is not None else video_path,
            system=system.text,
            prompt=prompt_text,
            schema=VideoObservation,
            task=f"video_{pass_type}",
            start_sec=start_sec,
            end_sec=end_sec,
            fps=fps,
            progress=progress,
        )
        observation = response.data
        if clip is not None:
            observation = shift_data(observation, clip.start_sec)  # 클립 기준 시각 → 원본 기준
        result = self.finalize(
            observation,
            video_path=video_path,
            video_hash=video_hash,
            duration_sec=duration,
            prompt_version=prompt_version,
            metrics=response.metrics,
            pass_type=pass_type,
            focus=focus,
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
                "focus": focus.kind if focus else None,
                "clip": clip.label if clip is not None else None,
                "completion": result.analysis_completion.model_dump(),
            },
        )
        return result

    def _maybe_clip(self, video_path, focus: FocusTarget, previous_result: Optional[VideoResult], duration: Optional[float], video_hash: str, *, progress: ProgressCallback, case_id: Optional[str]) -> Optional[ClipWindow]:
        """긴 영상의 focus 재분석이면 충돌 앞뒤 구간을 잘라 둔다. 실패하면 전체 영상으로 진행한다."""
        if not self.clip_focus_window:
            return None
        window = plan_clip_window(
            focus, previous_result, duration,
            min_duration_sec=self.settings.clip_min_duration_sec,
            impact_pre_roll_sec=self.settings.clip_impact_pre_roll_sec,
            impact_post_roll_sec=self.settings.clip_impact_post_roll_sec,
            factor_pre_roll_sec=self.settings.clip_factor_pre_roll_sec,
            factor_post_roll_sec=self.settings.clip_factor_post_roll_sec,
        )
        if window is None:
            return None
        try:
            path = cut_clip(video_path, window[0], window[1], out_dir=self.settings.clips_dir, video_hash=video_hash)
        except Exception as exc:  # noqa: BLE001
            self.run_logger.log(agent="video_agent", task="video_clip_error", case_id=case_id, model=self.model, extra={"error": str(exc)[:300], "window": list(window)})
            return None
        clip = ClipWindow(window[0], window[1], path)
        if progress:
            progress(f"{clip.label} 구간만 잘라 재분석")
        return clip
